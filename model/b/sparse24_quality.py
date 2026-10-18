"""ARM CPU quality for one frozen sparse candidate; never accesses PL."""
import argparse
import json
import platform
import sys
import time
from pathlib import Path
import numpy as np
import onnxruntime as ort
from .common import read_json, sha, utc, write_json
from .preprocess import preprocess


def session(path):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def quality(candidate, bundle, data, reference_run, output):
    if platform.machine().lower() not in ("aarch64", "arm64"):
        raise ValueError("Formal quality requires ARM")
    output.mkdir(parents=True, exist_ok=False)
    freeze = read_json(candidate / "freeze.json")
    if sha(__file__) != freeze["quality_runner_sha256"]:
        raise ValueError("Frozen quality runner identity mismatch")
    for name, expected in freeze["files"].items():
        if sha(candidate / name) != expected:
            raise ValueError("Frozen candidate identity mismatch: " + name)
    split = read_json(bundle / "splits.json")
    rows = split["splits"]["validation_full"]
    if sha(bundle / "splits.json") != freeze["splits_sha256"] or sha(bundle / "quant_protocol.json") != freeze["quant_protocol_sha256"]:
        raise ValueError("Reference data/protocol identity mismatch")
    summary = read_json(reference_run / "accuracy.json")
    raw_path = reference_run / "predictions.jsonl"
    if sha(raw_path) != summary["predictions_sha256"] or summary["samples"] != len(rows):
        raise ValueError("Historical reference identity mismatch")
    references = [json.loads(line) for line in raw_path.open(encoding="utf-8")]
    original_graph = bundle / "graphs/model_fp32.onnx"
    original_sha = sha(original_graph)
    if original_sha != freeze["original_fp32_sha256"]:
        raise ValueError("Original model identity differs from candidate freeze")
    for i, record in enumerate(references):
        base = record["results"]["arm_fp32"]
        if record["image"] != rows[i] or base["graph_sha256"] != original_sha or base["ORT"] != ort.__version__ or base["threads"] != 4:
            raise ValueError("Historical reference configuration mismatch")
    original = session(original_graph)
    probe_indices = np.linspace(0, len(rows)-1, 20, dtype=int).tolist()
    probes = []
    for i in probe_indices:
        x = preprocess(data / rows[i]["path"])
        actual = original.run(None, {"image": x})[0][0]
        expected = np.array(references[i]["results"]["arm_fp32"]["logits"], dtype=np.float32)
        probes.append({"index": i, "bitwise_equal": actual.tobytes() == expected.tobytes(), "max_abs": float(np.max(np.abs(actual-expected)))})
    reuse = all(row["bitwise_equal"] for row in probes)
    write_json(output / "reference_reuse.json", {"status": "REUSE_MATCHED_REFERENCE" if reuse else "RECOMPUTE_REFERENCE",
               "probe": probes, "original_run": str(reference_run), "predictions_sha256": sha(raw_path),
               "graph_sha256": original_sha, "ORT": ort.__version__, "utc": utc(), "historical_result_date": "2026-10-01"})
    engines = {mode: session(candidate / (mode + ".onnx")) for mode in ("pruned_fp32", "pruned_w8a8")}
    if not reuse:
        engines["original_fp32"] = original
    else:
        del original
    outputs = {name: np.empty((len(rows),1000), np.float32) for name in ("original_fp32", "pruned_fp32", "pruned_w8a8")}
    labels = np.array([r["label"] for r in rows])
    fast_paths = {r["path"] for r in split["splits"]["validation_fast"]}
    fast_indices = [i for i,r in enumerate(rows) if r["path"] in fast_paths]
    order = fast_indices + [i for i,r in enumerate(rows) if r["path"] not in fast_paths]
    with (output / "samples.jsonl").open("w", encoding="utf-8") as raw:
        for completed, i in enumerate(order, 1):
            row = rows[i]
            if sha(data / row["path"]) != row["image_sha256"]:
                raise ValueError("Image content mismatch: " + row["path"])
            x = preprocess(data / row["path"])
            import hashlib
            input_sha = hashlib.sha256(x.tobytes()).hexdigest()
            if input_sha != references[i]["results"]["arm_fp32"]["input_tensor_sha256"]:
                raise ValueError("Preprocessing identity differs from reference: " + row["path"])
            if reuse:
                outputs["original_fp32"][i] = references[i]["results"]["arm_fp32"]["logits"]
            times = {}
            for name, engine in engines.items():
                start = time.perf_counter()
                try:
                    logits = engine.run(None, {"image": x})[0]
                except Exception as error:
                    write_json(output / "failure.json", {"status": "FAIL", "index": i, "image": row["path"],
                               "model": name, "error": repr(error), "utc": utc()})
                    raise
                times[name] = 1000*(time.perf_counter()-start)
                if logits.shape != (1,1000) or not np.isfinite(logits).all():
                    raise ValueError("Invalid candidate logits")
                outputs[name][i] = logits[0]
            raw.write(json.dumps({"index": i, "path": row["path"], "input_sha256": input_sha, "times_ms": times,
                                 "top5": {k: np.argsort(-v[i], kind="stable")[:5].tolist() for k,v in outputs.items()}})+"\n")
            if completed == len(fast_indices):
                ids = np.array(fast_indices)
                counts = {k: int(np.sum(np.argmax(v[ids],axis=1) == labels[ids])) for k,v in outputs.items()}
                write_json(output / "fast_checkpoint.json", {"samples": len(ids), "top1_correct": counts,
                           "status": "PASS" if all(100*(counts["original_fp32"]-counts[k]) <= len(ids) for k in engines) else "FAIL",
                           "candidate_frozen": True, "full_remaining": len(rows)-len(ids), "utc": utc()})
            if completed % 100 == 0:
                raw.flush()
                write_json(output / "progress.json", {"status": "RUNNING", "completed": completed, "total": len(rows), "utc": utc()})
                print("QUALITY", completed, len(rows), flush=True)
    sets = {"validation_fast": np.array([i for i,r in enumerate(rows) if r["path"] in fast_paths]), "validation_full": np.arange(len(rows))}
    results = {}
    for name, ids in sets.items():
        counts = {}
        for mode, logits in outputs.items():
            top = np.argsort(-logits[ids], axis=1, kind="stable")[:, :5]
            counts[mode] = {"top1_correct": int(np.sum(top[:,0] == labels[ids])),
                            "top5_correct": int(np.sum(np.any(top == labels[ids,None], axis=1)))}
        base = counts["original_fp32"]["top1_correct"]
        for mode in ("pruned_fp32", "pruned_w8a8"):
            delta = base-counts[mode]["top1_correct"]
            counts[mode].update(top1_drop_pp=100*delta/len(ids), budget_pass=(100*delta <= len(ids)))
            original_top = np.argmax(outputs["original_fp32"][ids], axis=1)
            candidate_top = np.argmax(outputs[mode][ids], axis=1)
            counts[mode]["changed_prediction_indices"] = ids[candidate_top != original_top].tolist()
            counts[mode]["lost_correct_indices"] = ids[(original_top == labels[ids]) & (candidate_top != labels[ids])].tolist()
            counts[mode]["gained_correct_indices"] = ids[(original_top != labels[ids]) & (candidate_top == labels[ids])].tolist()
        results[name] = {"samples": len(ids), "models": counts,
                         "status": "PASS" if all(counts[m]["budget_pass"] for m in ("pruned_fp32", "pruned_w8a8")) else "FAIL"}
    for name, logits in outputs.items():
        np.save(output / (name + "_logits.npy"), logits, allow_pickle=False)
    write_json(output / "quality.json", {"status": "PASS" if all(r["status"] == "PASS" for r in results.values()) else "FAIL",
               "results": results, "freeze_sha256": sha(candidate / "freeze.json"), "reference_reused": reuse,
               "samples": len(rows), "missing_samples": 0, "quality_max_drop_pp": 1.0, "prediction_classes": 1000,
               "host": platform.node(), "architecture": platform.machine(), "ORT": ort.__version__, "numpy": np.__version__,
               "python": sys.version, "utc": utc(), "quality_scope": "Imagenette labelled ImageNet subset",
               "PL": "NOT_RUN", "files": {p.name: sha(p) for p in output.iterdir() if p.is_file() and p.name != "progress.json"}})
    write_json(output / "progress.json", {"status": "COMPLETE", "completed": len(rows), "utc": utc()})


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "bundle", "data", "reference-run", "output"):
        p.add_argument("--"+name, type=Path, required=True)
    a = p.parse_args()
    try:
        quality(a.candidate, a.bundle, a.data, a.reference_run, a.output)
    except Exception as error:
        if not (a.output / "failure.json").exists():
            write_json(a.output / "failure.json", {"status": "FAIL", "error": repr(error), "utc": utc()})
        raise
