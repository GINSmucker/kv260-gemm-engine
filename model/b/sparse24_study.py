"""Bounded software pruning study using the frozen complete ORT graphs."""
import argparse
import hashlib
import platform
import sys
import time
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper as NH

from .common import ROOT, read_json, sha, utc, write_json
from .preprocess import preprocess
from .quantization import quantize
from .runtime import verify_bundle
from .sparse24 import prune, logical_pack, logical_unpack, slot_gemm
from .golden_int64 import gemm

BUNDLE = ROOT / "build/model_assets/b_model/B_MODEL_DENSE_V1_20260930"
DATA = ROOT / "build/model_assets/imagenette2_320"
FAMILIES = ("qkv", "proj", "fc1", "fc2", "head")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(output):
    output.mkdir(parents=True, exist_ok=False)
    identity = verify_bundle(BUNDLE)
    split = read_json(BUNDLE / "splits.json")["splits"]
    label_by_wnid = {r["wnid"]: r["index"] for r in read_json(BUNDLE / "labels_imagenet1k.json")}
    inventory = {}
    for part in ("train", "val"):
        rows = []
        for p in sorted((DATA / part).rglob("*")):
            if p.suffix.lower() in (".jpeg", ".jpg", ".png"):
                rows.append({"path": p.relative_to(DATA).as_posix(), "image_sha256": sha(p),
                             "bytes": p.stat().st_size, "wnid": p.parent.name, "label": label_by_wnid[p.parent.name]})
        inventory[part] = rows
    actual = {r["path"]: r for rows in inventory.values() for r in rows}
    for rows in split.values():
        for row in rows:
            if actual.get(row["path"]) != row:
                raise ValueError("Frozen image identity changed: " + row["path"])
    excluded = {r["image_sha256"] for key in ("calibration", "regression", "validation_full") for r in split[key]}
    selected = [r for r in split["selection"] if r["image_sha256"] not in excluded]
    kept = len(selected)
    used = excluded | {r["image_sha256"] for r in selected}
    for wnid in sorted({r["wnid"] for r in inventory["train"]}):
        available = sorted((r for r in inventory["train"] if r["wnid"] == wnid),
                           key=lambda r: (digest(("sparse24_selection_20261012:" + r["image_sha256"]).encode()), r["path"]))
        needed = 100 - sum(r["wnid"] == wnid for r in selected)
        for row in available:
            if needed and row["image_sha256"] not in used:
                selected.append(row)
                used.add(row["image_sha256"])
                needed -= 1
        if needed:
            raise ValueError("Insufficient unique training data")
    selected.sort(key=lambda r: (r["wnid"], r["path"]))
    assert len(selected) == len({r["image_sha256"] for r in selected}) == 1000
    assert not {r["image_sha256"] for r in selected} & excluded
    write_json(output / "dataset_inventory.json", inventory)
    write_json(output / "selection.json", selected)
    write_json(output / "data_verification.json", {
        "status": "PASS", "utc": utc(), "bundle": identity, "raw_counts": {k: len(v) for k, v in inventory.items()},
        "frozen_splits_sha256": sha(BUNDLE / "splits.json"), "all_frozen_rows_identity_match": True,
        "selection_count": 1000, "selection_sha256": sha(output / "selection.json"), "old_selection_retained": kept,
        "old_selection_excluded_regression": 100-kept, "new_rows": 1000-kept,
        "class_counts": {w: sum(r["wnid"] == w for r in selected) for w in sorted({r["wnid"] for r in selected})},
        "overlap_calibration_regression_val": 0, "selection_rule": "per-class 100; retain eligible old selection; sha256 seed+image content order",
        "val_predictions": "NOT_RUN", "C_private_material": "NOT_ACCESSED"})
    write_json(output / "study_protocol.json", {
        "status": "FROZEN_BEFORE_SELECTION_PREDICTIONS", "utc": utc(), "quality_max_drop_pp": 1.0,
        "selection_margin_pp": 0.5, "family_order": list(FAMILIES), "max_candidates": 46,
        "ranking": ["w8a8_top1_loss", "logits_mse", "descending_existing_PL_budget"],
        "partial_rule": "single-layer FP32 and W8 loss <= 0.3pp; combine all eligible, then lower-error half if needed",
        "formal_candidates_max": 1, "formal_host": "aarch64", "formal_splits": ["validation_fast", "validation_full"],
        "format": "logical_values_and_two_uint8_positions_ONLY", "source_sha256": sha(__file__),
        "pruning_source_sha256": sha(Path(__file__).with_name("sparse24.py")), "threads": 4,
        "ORT": ort.__version__, "graph_optimization": "DISABLE_ALL", "spinning": 0})
    layers = read_json(BUNDLE / "layers.json")
    fp = onnx.load(BUNDLE / "graphs/model_fp32.onnx")
    initializers = {t.name: NH.to_array(t) for t in fp.graph.initializer}
    evidence = []
    rng = np.random.default_rng(20261012)
    total_outputs = 0
    for layer in layers:
        weight = initializers[layer["layer_id"] + ".weight"]
        assert digest(weight.tobytes()) == layer["original_weight_sha256"]
        wp, pos, mask = prune(np.ascontiguousarray(weight.T))
        wq, sw = quantize(wp, 0)
        values = logical_pack(wq, pos)
        assert np.array_equal(logical_unpack(values, pos), wq)
        folder = output / "weights" / f"{layer['layer_index']:02d}"
        folder.mkdir(parents=True)
        for name, data in (("pruned_fp32", wp), ("positions", pos), ("mask", mask), ("Wq", wq), ("sw", sw), ("values", values)):
            np.save(folder / (name + ".npy"), data, allow_pickle=False)
        x = rng.integers(-127, 128, (layer["M"], layer["K"]), dtype=np.int8)
        ref = gemm(x, wq)
        assert np.array_equal(slot_gemm(x, values, pos), ref)
        np.save(folder / "integer_input.npy", x, allow_pickle=False)
        np.save(folder / "integer_reference.npy", ref, allow_pickle=False)
        total_outputs += ref.size
        evidence.append({"layer_id": layer["layer_id"], "shape": [layer[k] for k in ("M", "K", "N")],
                         "files": {p.name: sha(p) for p in sorted(folder.iterdir())},
                         "mask_slots": int(mask.sum()), "integer_differences": 0,
                         "removed_weight_energy_fraction": float(np.sum((weight.T.astype(np.float64)-wp)**2)/np.sum(weight.astype(np.float64)**2))})
    write_json(output / "pruning_verification.json", {"status": "PASS", "layers": evidence, "integer_outputs": total_outputs,
                                                     "source": "seeded_public_software_inputs", "hardware": "NOT_RUN"})
    cached = np.lib.format.open_memmap(output / "selection_inputs.npy", mode="w+", dtype=np.float32, shape=(1000,3,224,224))
    for i, row in enumerate(selected):
        cached[i] = preprocess(DATA / row["path"])[0]
    cached.flush()
    print("PREPARE_PASS", kept, total_outputs, flush=True)


def graph(output, indices, mode):
    source = BUNDLE / "graphs" / ("model_fp32.onnx" if mode == "fp32" else "model_w8a8_reference.onnx")
    model = onnx.load(source)
    layers = read_json(BUNDLE / "layers.json")
    replacements = {}
    for index in indices:
        folder = output / "weights" / f"{index:02d}"
        if mode == "fp32":
            replacements[layers[index]["layer_id"] + ".weight"] = np.ascontiguousarray(np.load(folder / "pruned_fp32.npy").T)
        else:
            replacements[f"BQL_{index}_Wf"] = np.load(folder / "Wq.npy").astype(np.float32)
            replacements[f"BQL_{index}_sf"] = np.load(folder / "sw.npy").reshape(-1)
    count = 0
    for tensor in model.graph.initializer:
        if tensor.name in replacements:
            tensor.CopyFrom(NH.from_array(replacements[tensor.name], tensor.name))
            count += 1
    assert count == len(replacements)
    onnx.checker.check_model(model)
    return model.SerializeToString()


def session(graph_bytes):
    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    so.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(graph_bytes, so, providers=["CPUExecutionProvider"])


def evaluate(output, candidate, indices, mode, tensors, labels, baseline=None):
    folder = output / "selection_results" / candidate
    folder.mkdir(parents=True, exist_ok=True)
    result_path = folder / (mode + ".json")
    data = graph(output, indices, mode)
    if result_path.exists():
        result = read_json(result_path)
        if result["graph_sha256"] != digest(data) or result["selection_sha256"] != sha(output / "selection.json"):
            raise ValueError("Existing result identity mismatch")
        return result, np.load(folder / (mode + "_logits.npy"))
    engine = session(data)
    logits = np.empty((len(labels), 1000), dtype=np.float32)
    elapsed = np.empty(len(labels), dtype=np.float64)
    for i, tensor in enumerate(tensors):
        start = time.perf_counter()
        value = engine.run(None, {"image": tensor[None]})[0]
        elapsed[i] = time.perf_counter() - start
        if value.shape != (1,1000) or not np.isfinite(value).all():
            raise ValueError("Nonfinite/invalid logits")
        logits[i] = value[0]
        if (i+1) % 100 == 0:
            write_json(output / "progress.json", {"status": "RUNNING", "candidate": candidate, "mode": mode, "completed": i+1, "total": len(labels), "utc": utc()})
    top = np.argsort(-logits, axis=1, kind="stable")[:, :5]
    result = {"status": "COMPLETE", "candidate": candidate, "pruned_layer_indices": indices, "mode": mode,
              "samples": len(labels), "top1_correct": int(np.sum(top[:,0] == labels)),
              "top5_correct": int(np.sum(np.any(top == labels[:,None], axis=1))),
              "graph_sha256": digest(data), "selection_sha256": sha(output / "selection.json"),
              "host": platform.node(), "architecture": platform.machine(), "ORT": ort.__version__, "threads": 4,
              "utc": utc(), "seconds": float(elapsed.sum()), "PL": "NOT_RUN", "validation": "NOT_RUN"}
    if baseline is not None:
        result["logits_mse"] = float(np.mean((logits.astype(np.float64)-baseline)**2))
        result["top1_changes"] = np.flatnonzero(top[:,0] != np.argmax(baseline, axis=1)).tolist()
    np.save(folder / (mode + "_logits.npy"), logits, allow_pickle=False)
    np.save(folder / (mode + "_times.npy"), elapsed, allow_pickle=False)
    result["logits_sha256"] = sha(folder / (mode + "_logits.npy"))
    write_json(result_path, result)
    print(candidate, mode, result["top1_correct"], result["top5_correct"], round(result["seconds"],2), flush=True)
    return result, logits


def study(output):
    rows = read_json(output / "selection.json")
    labels = np.array([r["label"] for r in rows])
    tensors = np.load(output / "selection_inputs.npy", mmap_mode="r")
    base, base_logits = evaluate(output, "baseline", [], "fp32", tensors, labels)
    evaluate(output, "baseline", [], "w8a8", tensors, labels, base_logits)
    layers = read_json(BUNDLE / "layers.json")
    cache = {}
    summaries = []

    def candidate(name, indices):
        indices = sorted(indices)
        key = tuple(indices)
        if key in cache:
            return cache[key]
        results = {mode: evaluate(output, name, indices, mode, tensors, labels, base_logits)[0] for mode in ("fp32", "w8a8")}
        result = {"name": name, "indices": indices, "results": results,
                  "eligible": all(base["top1_correct"] - r["top1_correct"] <= 5 for r in results.values())}
        cache[key] = result
        summaries.append(result)
        write_json(output / "sensitivity.json", {"baseline": base, "candidates": summaries})
        return result

    families = {f: candidate("family_"+f, [l["layer_index"] for l in layers if l["family"] == f]) for f in FAMILIES}
    budget = {"qkv": 1.618456, "fc1": 1.931909, "fc2": 1.835943}
    order = sorted(budget, key=lambda f: (-families[f]["results"]["w8a8"]["top1_correct"], families[f]["results"]["w8a8"]["logits_mse"], -budget[f]))
    accepted = []
    failed = []
    for family in order:
        result = candidate("combined_"+family, accepted + families[family]["indices"])
        if result["eligible"]:
            accepted = result["indices"]
        else:
            failed.append(family)
    singles = []
    for family in failed:
        for index in families[family]["indices"]:
            single = candidate(f"single_{index:02d}", [index])
            if all(base["top1_correct"]-r["top1_correct"] <= 3 for r in single["results"].values()):
                singles.append(single)
    if singles:
        singles.sort(key=lambda r: (-r["results"]["w8a8"]["top1_correct"], r["results"]["w8a8"]["logits_mse"], r["indices"]))
        result = candidate("partial_all", accepted + [r["indices"][0] for r in singles])
        if not result["eligible"]:
            candidate("partial_half", accepted + [r["indices"][0] for r in singles[:max(1,len(singles)//2)]])
    eligible = [r for r in summaries if r["eligible"] and all(layers[i]["family"] in budget for i in r["indices"])]
    # Prefer useful PL coverage, then quality; this rule is fixed before formal val.
    eligible.sort(key=lambda r: (-sum(budget[layers[i]["family"]] for i in r["indices"]), -r["results"]["w8a8"]["top1_correct"], r["name"]))
    choice = eligible[0] if eligible else None
    write_json(output / "selection_decision.json", {"status": "CANDIDATE_SELECTED" if choice else "NO_QUALITY_CANDIDATE",
               "candidate": choice, "family_order": order, "unique_candidates": len(summaries), "utc": utc(),
               "validation_predictions": "NOT_RUN", "quality_max_drop_pp": 1.0})
    write_json(output / "progress.json", {"status": "SELECTION_COMPLETE", "candidate": choice["name"] if choice else None, "utc": utc()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "study"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        (prepare if args.action == "prepare" else study)(args.output.resolve())
    except Exception as exc:
        write_json(args.output / "failure.json", {"status": "FAIL", "utc": utc(), "error": repr(exc)})
        raise
