"""Offline H576 pruning audit and family-only ablations on a fixed dev set.

No training, downloads, HLS or board access. All original inputs are read-only.
Creates TWO new directories: diagnostic models and evaluation evidence.
Reuses the original candidate masks; does not search or change the algorithm.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import itertools
import json
from pathlib import Path
import platform
import sys
import traceback

import numpy as np
import onnx
import onnxruntime as ort
from PIL import __version__ as pillow_version

from eval_models import (OnnxClassifier, PREPROCESS_SPEC, load_samples,
                         positive_int, preprocess_image, rank_logits, sha_file)
from prune_h576 import (ARCHIVE_SHA256, FP32_SHA256, W8_SHA256, arrays,
                        compare_unmodified, read_source, replace_initializers, write_json)

ROOT = Path(__file__).resolve().parent
FAMILIES = ("qkv", "fc1", "fc2")


def independent_positions(weight):
    """Enumerate all six 2-of-4 choices, not the production argsort code."""
    if (weight.ndim != 2 or weight.dtype != np.float32 or not weight.size
            or weight.shape[0] % 4 or not np.isfinite(weight).all()):
        raise ValueError("Expected finite FP32 W[K,N], K divisible by 4")
    groups = weight.reshape(-1, 4, weight.shape[1]).transpose(0, 2, 1).astype(np.float64)
    pairs = np.array(list(itertools.combinations(range(4), 2)), np.uint8)
    scores = np.stack([np.abs(groups[:, :, a]) + np.abs(groups[:, :, b])
                       for a, b in pairs], axis=-1)
    # Lexicographically ordered pairs break equal-score ties by smaller K indices.
    return pairs[np.argmax(scores, axis=-1)]


def mask_from_positions(positions, shape):
    k, n = shape
    mask = np.zeros((k, n), dtype=bool)
    for slot in (0, 1):
        mask[4 * np.arange(k // 4)[:, None] + positions[:, :, slot], np.arange(n)[None]] = True
    return mask


def metric_counts(logits, labels, baseline):
    if not len(labels) or len(logits) != len(labels) or logits.shape != baseline.shape:
        raise ValueError("Mismatched sample/logits inventory")
    ranks = np.stack([rank_logits(row[None]) for row in logits])
    base_ranks = np.stack([rank_logits(row[None]) for row in baseline])
    if np.any(labels < 0) or np.any(labels >= logits.shape[1]):
        raise ValueError("Label outside output classes")
    metrics = {"images": len(labels)}
    for top in (1, 5):
        good = np.any(ranks[:, :top] == labels[:, None], axis=1)
        base_good = np.any(base_ranks[:, :top] == labels[:, None], axis=1)
        correct, base_correct = int(good.sum()), int(base_good.sum())
        metrics[f"top{top}_correct"] = correct
        metrics[f"top{top}_pct"] = 100 * correct / len(labels)
        metrics[f"top{top}_drop_pp"] = 100 * (base_correct - correct) / len(labels)
        metrics[f"top{top}_new_errors"] = int((base_good & ~good).sum())
        metrics[f"top{top}_recovered_errors"] = int((~base_good & good).sum())
        metrics[f"top{top}_both_correct"] = int((base_good & good).sum())
        metrics[f"top{top}_both_wrong"] = int((~base_good & ~good).sum())
    metrics["top1_prediction_changes"] = int((ranks[:, 0] != base_ranks[:, 0]).sum())
    return metrics, ranks


def verify_receipt(folder, manifest, protected):
    for name, record in manifest["files"].items():
        path = (folder / name).resolve(strict=True)
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            raise ValueError("Candidate artifact outside its directory")
        if path.stat().st_size != record["bytes"] or sha_file(path) != record["sha256"]:
            raise ValueError("Candidate identity mismatch: " + name)
        protected[path] = record["sha256"]
    for name, digest in manifest["implementation"].items():
        path = (ROOT / name).resolve(strict=True)
        if not path.is_relative_to(ROOT) or sha_file(path) != digest:
            raise ValueError("Original pruning implementation changed: " + name)
        protected[path] = digest


def audit_candidate(payload, layers, original_fp, original_q, folder):
    original_f, original_w8 = arrays(original_fp), arrays(original_q)
    candidate_fp = onnx.load(folder / "pruned_fp32.onnx")
    candidate_q = onnx.load(folder / "pruned_w8a8_reference.onnx")
    candidate_f, candidate_w8 = arrays(candidate_fp), arrays(candidate_q)
    records, weights = [], {}
    fp_changes, q_changes = set(), set()
    for layer in layers:
        if layer["family"] not in FAMILIES:
            continue
        i, m, k, n = (layer[key] for key in ("layer_index", "M", "K", "N"))
        name = layer["layer_id"] + ".weight"
        original = np.ascontiguousarray(original_f[name].T)
        with np.load(folder / "weights" / f"layer_{i:02d}.npz", allow_pickle=False) as stored:
            w = {key: stored[key].copy() for key in stored.files}
        expected_positions = independent_positions(original)
        np.testing.assert_array_equal(w["positions"], expected_positions)
        expected_mask = mask_from_positions(expected_positions, (k, n))
        np.testing.assert_array_equal(w["mask"], expected_mask)
        np.testing.assert_array_equal(expected_mask.reshape(k // 4, 4, n).sum(axis=1), 2)
        wp = np.where(expected_mask, original, np.float32(0))
        np.testing.assert_array_equal(w["W_fp32"], wp)
        np.testing.assert_array_equal(candidate_f[name], wp.T)
        original_int8 = original_w8[f"BQL_{i}_Wf"].astype(np.int8)
        original_scale = original_w8[f"BQL_{i}_sf"]
        expected_q = np.clip(np.rint(wp / original_scale[None]), -127, 127).astype(np.int8)
        np.testing.assert_array_equal(w["sw"].ravel(), original_scale)
        np.testing.assert_array_equal(w["Wq"], expected_q)
        np.testing.assert_array_equal(w["Wq"], np.where(expected_mask, original_int8, 0))
        rebuilt = np.zeros((k, n), dtype=np.int8)
        for slot in (0, 1):
            rebuilt[4 * np.arange(k // 4)[:, None] + expected_positions[:, :, slot],
                    np.arange(n)[None]] = w["values"][:, :, slot]
        np.testing.assert_array_equal(rebuilt, expected_q)
        np.testing.assert_array_equal(candidate_w8[f"BQL_{i}_Wf"], expected_q.astype(np.float32))
        np.testing.assert_array_equal(candidate_w8[f"BQL_{i}_sf"], original_scale)
        np.testing.assert_array_equal(w["bias"], original_w8[f"BQL_{i}_bf"])
        # Local response uses the same original Dense Xq snapshot for both weights.
        # It does not measure propagated activations or end-to-end classification.
        x = np.frombuffer(payload[f"assets/layer_inputs/{i}_Xq.bin"], np.int8).reshape(m, k)
        base_y = x.astype(np.float64) @ original_int8.astype(np.float64)
        new_y = x.astype(np.float64) @ expected_q.astype(np.float64)
        norm = float(np.linalg.norm(base_y))
        energy = float(np.sum(original.astype(np.float64) ** 2))
        removed_energy = float(np.sum((original.astype(np.float64) - wp) ** 2))
        records.append({"layer_index": i, "layer_id": layer["layer_id"], "family": layer["family"],
                        "block": i // 4, "shape_MKN": [m, k, n],
                        "groups_checked": k * n // 4, "mask_slots": int(expected_mask.sum()),
                        "removed_weight_energy_fraction": removed_energy / energy if energy else 0,
                        "snapshot_unscaled_int32_relative_l2": float(np.linalg.norm(new_y - base_y)) / norm if norm else None,
                        "weight_scale_identical": True, "retained_int8_values_identical": True,
                        "mask_positions_weights_bias": "PASS"})
        fp_changes.add(name)
        q_changes.add(f"BQL_{i}_Wf")
        weights[i] = w
    compare_unmodified(payload["assets/h576_fp32.onnx"], candidate_fp, fp_changes)
    compare_unmodified(payload["assets/h576_w8.onnx"], candidate_q, q_changes)
    if len(records) != 36:
        raise ValueError("Expected precisely 36 selected layers")
    return {"status": "PASS_BOUND_TO_EXISTING_RULE_NOT_QUALITY_ACCEPTANCE", "layers": records,
            "pruned_layers": 36, "groups_checked": sum(r["groups_checked"] for r in records),
            "graph_topology_biases_and_other_initializers": "UNCHANGED",
            "grouping": "Per output column n, consecutive K positions W[4g:4g+4,n]",
            "independent_check": "Enumerate all 6 two-position choices; maximize absolute sum with lexicographic ties",
            "local_response_limit": "Original Dense Xq snapshots, same Xq for both weights; unscaled linear response only, not propagated feature quality"}, weights


def create_family_models(payload, layers, original_fp, original_q, weights, destination):
    specs = {}
    for family in FAMILIES:
        selected = [layer for layer in layers if layer["family"] == family]
        if len(selected) != 12:
            raise ValueError("Unexpected family inventory")
        for precision, original_model, source_name in (
            ("fp32", original_fp, "assets/h576_fp32.onnx"),
            ("w8a8", original_q, "assets/h576_w8.onnx"),
        ):
            model = onnx.ModelProto()
            model.CopyFrom(original_model)
            replacements = {}
            for layer in selected:
                i = layer["layer_index"]
                if precision == "fp32":
                    replacements[layer["layer_id"] + ".weight"] = np.ascontiguousarray(weights[i]["W_fp32"].T)
                else:
                    replacements[f"BQL_{i}_Wf"] = weights[i]["Wq"].astype(np.float32)
            replace_initializers(model, replacements)
            compare_unmodified(payload[source_name], model, set(replacements))
            path = destination / f"{family}_only_{precision}.onnx"
            # The containing directory is exclusively created by this run.
            if path.exists():
                raise ValueError("Refusing model overwrite")
            onnx.save(model, path)
            specs[f"{family}_only_{precision}"] = {"path": path, "sha256": sha_file(path),
                                                  "family": family, "precision": precision,
                                                  "pruned_layers": 12}
    return specs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "inputs/h576-runtime-public-ui.tar.gz")
    parser.add_argument("--candidate", type=Path, default=ROOT / "candidates/h576_sparse24_pl36_v1")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "inputs/imagenet100_dev100")
    parser.add_argument("--previous-control", type=Path, default=ROOT / "eval_results/fp32_control_imagenet100_train100_v1")
    parser.add_argument("--model-output", type=Path, required=True)
    parser.add_argument("--result-output", type=Path, required=True)
    parser.add_argument("--threads", type=positive_int, default=4)
    args = parser.parse_args(argv)
    models_dir, results_dir = args.model_output.resolve(), args.result_output.resolve()
    created_results = False
    try:
        if (models_dir.exists() or results_dir.exists() or models_dir == results_dir
                or models_dir.is_relative_to(results_dir) or results_dir.is_relative_to(models_dir)):
            raise ValueError("Choose TWO distinct NEW non-nested output directories; no overwrite")
        if ort.__version__ != "1.22.1" or np.__version__ != "1.26.4":
            raise ValueError("Use frozen project ORT 1.22.1 and NumPy 1.26.4")
        source_paths = [args.archive, args.candidate / "candidate.json", args.data_dir / "labels.csv",
                        args.data_dir / "dataset_manifest.json", args.previous_control / "summary.json",
                        args.previous_control / "logits.npz", Path(__file__), ROOT / "eval_models.py"]
        protected = {p.resolve(strict=True): sha_file(p) for p in source_paths}
        previous = json.loads((args.previous_control / "summary.json").read_text(encoding="utf-8"))
        if previous["status"] != "COMPLETE_DEVELOPMENT_CONTROL" or previous["environment"]["threads"] != args.threads:
            raise ValueError("Previous control settings differ")
        for name, digest in previous["artifacts"].items():
            if sha_file(args.previous_control / name) != digest:
                raise ValueError("Previous control artifact changed")
        manifest = json.loads((args.candidate / "candidate.json").read_text(encoding="utf-8"))
        if manifest["source"]["archive_sha256"] != ARCHIVE_SHA256:
            raise ValueError("Unexpected candidate source")
        verify_receipt(args.candidate, manifest, protected)
        print("Checking frozen source archive and all 49 Dense layer identities ...", flush=True)
        payload, layers, base_fp, base_q = read_source(args.archive)
        audit, weights = audit_candidate(payload, layers, base_fp, base_q, args.candidate)
        print(f"Audit PASS: {audit['pruned_layers']} layers, {audit['groups_checked']} K-axis groups", flush=True)
        samples = load_samples(args.data_dir, expected_count=100)
        with (args.data_dir / "labels.csv").open(encoding="utf-8-sig", newline="") as stream:
            expected = {row["image"]: row["image_sha256"] for row in csv.DictReader(stream)}
        identities, tensors = [], []
        for sample in samples:
            data = sample.path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected[sample.image]:
                raise ValueError("Image identity mismatch: " + sample.image)
            tensor = preprocess_image(io.BytesIO(data))
            tensor.flags.writeable = False
            tensors.append(tensor)
            identities.append({"image": sample.image, "label": sample.label, "sha256": digest})
            protected[sample.path] = digest
        dataset_sha = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
        if dataset_sha != previous["ordered_dataset_sha256"]:
            raise ValueError("Not the exact same ordered development dataset")
        labels = np.array([s.label for s in samples], np.int64)
        models_dir.mkdir(parents=True, exist_ok=False)
        results_dir.mkdir(parents=True, exist_ok=False)
        created_results = True
        write_json(results_dir / "audit.json", audit)
        specs = create_family_models(payload, layers, base_fp, base_q, weights, models_dir)
        # Baselines come from the original archive; equality is checked before use.
        baseline_specs = {
            "dense_fp32": (ROOT / "inputs/baseline/h576_fp32.onnx", FP32_SHA256, "fp32", 0),
            "dense_w8a8": (ROOT / "inputs/baseline/h576_w8.onnx", W8_SHA256, "w8a8", 0),
            "pl36_fp32": (args.candidate / "pruned_fp32.onnx", manifest["files"]["pruned_fp32.onnx"]["sha256"], "fp32", 36),
            "pl36_w8a8": (args.candidate / "pruned_w8a8_reference.onnx", manifest["files"]["pruned_w8a8_reference.onnx"]["sha256"], "w8a8", 36),
        }
        controls = {}
        for name, (path, digest, precision, count) in baseline_specs.items():
            if sha_file(path) != digest:
                raise ValueError("Baseline/candidate file changed")
            protected[path.resolve()] = digest
            controls[name] = {"path": path, "sha256": digest, "family": "none" if count == 0 else "pl36",
                              "precision": precision, "pruned_layers": count}
        specs = {**controls, **specs}
        write_json(models_dir / "candidate.json", {
            "schema": "h576_family_ablation_models_v1", "status": "DIAGNOSTIC_ONLY_NOT_DEPLOYMENT",
            "source_archive_sha256": ARCHIVE_SHA256, "parent_candidate_sha256": sha_file(args.candidate / "candidate.json"),
            "implementation_sha256": sha_file(__file__), "treatment": "Same validated PL36 masks; only one family is applied",
            "models": {name: {**record, "path": record["path"].name} for name, record in specs.items() if name not in controls},
            "training": "NOT_RUN", "HLS_RTL_PL": "NOT_RUN"})
        all_logits = {}
        for name, record in specs.items():
            engine = OnnxClassifier(record["path"], args.threads)
            values = []
            for tensor in tensors:
                logits = engine(tensor)
                rank_logits(logits)
                if logits.shape != (1, 1000):
                    raise ValueError("Expected H576 [1,1000] logits")
                values.append(logits[0].copy())
            all_logits[name] = np.stack(values)
            print(f"EVALUATED {name}: {len(values)} images", flush=True)
            del engine
        with np.load(args.previous_control / "logits.npz", allow_pickle=False) as old:
            for name, old_name in (("dense_fp32", "dense_fp32"), ("dense_w8a8", "dense_w8a8"),
                                   ("pl36_fp32", "pruned_fp32"), ("pl36_w8a8", "pruned_w8a8")):
                np.testing.assert_array_equal(all_logits[name], old[old_name])
        metrics, ranks = {}, {}
        for name, record in specs.items():
            metrics[name], ranks[name] = metric_counts(all_logits[name], labels,
                                                     all_logits["dense_" + record["precision"]])
            metrics[name].update(family=record["family"], precision=record["precision"],
                                 pruned_layers=record["pruned_layers"])
        if any(sha_file(path) != digest for path, digest in protected.items()):
            raise ValueError("Protected source changed during diagnostics")
        for record in specs.values():
            if sha_file(record["path"]) != record["sha256"]:
                raise ValueError("Diagnostic model changed during evaluation")
        with (results_dir / "logits.npz").open("xb") as stream:
            np.savez_compressed(stream, **all_logits, labels=labels,
                                images=np.array([s["image"] for s in identities]),
                                image_sha256=np.array([s["sha256"] for s in identities]))
        fields = ["model", "image", "image_sha256", "label", "top1", "top5", "top1_correct", "top5_correct"]
        with (results_dir / "predictions.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for name in specs:
                for i, identity in enumerate(identities):
                    writer.writerow({"model": name, "image": identity["image"], "image_sha256": identity["sha256"],
                                     "label": identity["label"], "top1": int(ranks[name][i, 0]),
                                     "top5": json.dumps(ranks[name][i].tolist()),
                                     "top1_correct": int(ranks[name][i, 0] == labels[i]),
                                     "top5_correct": int(labels[i] in ranks[name][i])})
        source_hashes = {str(path): digest for path, digest in protected.items() if path not in {s.path for s in samples}}
        summary = {
            "schema": "h576_pruning_family_diagnosis_v1", "status": "COMPLETE_DEVELOPMENT_DIAGNOSIS",
            "created_utc": datetime.now(timezone.utc).isoformat(), "images": len(samples),
            "classes_covered": len(set(labels.tolist())), "ordered_dataset_sha256": dataset_sha,
            "control_logits_bitwise_reproduced": True, "models": metrics,
            "model_identities": {name: {**record, "path": str(record["path"].resolve())} for name, record in specs.items()},
            "environment": {"python": platform.python_version(), "numpy": np.__version__, "onnx": onnx.__version__,
                            "onnxruntime": ort.__version__, "Pillow": pillow_version, "intra_threads": args.threads,
                            "inter_threads": 1, "execution": "sequential", "optimization": "disabled", "spinning": False},
            "preprocessing": PREPROCESS_SPEC, "source_hashes": source_hashes,
            "model_manifest_sha256": sha_file(models_dir / "candidate.json"),
            "artifacts": {path.name: sha_file(path) for path in sorted(results_dir.iterdir())},
            "limits": ["Same 100 public train images, 65 classes; exploratory development selection, not independent validation",
                       "One family changes 12 layers; combined PL36 changes 36. Losses are nonlinear and not additive causal contributions",
                       "No training, new masking algorithm, board execution, HLS/RTL or speed measurements"],
        }
        # Completion marker is written last; failures cannot produce this receipt.
        write_json(results_dir / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "models": metrics,
                          "result_output": str(results_dir), "model_output": str(models_dir)}, indent=2), flush=True)
        return 0
    except Exception as error:
        if created_results and not (results_dir / "summary.json").exists() and not (results_dir / "failure.json").exists():
            write_json(results_dir / "failure.json", {"status": "FAIL", "traceback": traceback.format_exc()})
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
