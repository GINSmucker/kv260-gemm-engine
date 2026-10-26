"""Bidirectional H576 layer sensitivity, offline, without model-file writes.

Runs 36 single-layer pruning and 36 single-layer restoration interventions in
FP32 and W8A8, using the existing masks. Changes only IN-MEMORY graph copies.
Creates a new evidence directory and a provisional selection proposal, not a
deployment candidate. No training, source modification, downloads or board use.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import traceback

import numpy as np
import onnx
import onnxruntime as ort
from PIL import __version__ as pillow_version

from diagnose_pruning import audit_candidate, metric_counts, verify_receipt
from eval_models import PREPROCESS_SPEC, load_samples, positive_int, preprocess_image, rank_logits, sha_file
from prune_h576 import (ARCHIVE_SHA256, arrays, compare_unmodified, ort_session,
                        read_source, replace_initializers, write_json)

ROOT = Path(__file__).resolve().parent
PRECISIONS = ("fp32", "w8a8")
POLICY = {
    "status": "EXPLORATORY_HEURISTIC_PREDECLARED_NOT_STATISTICAL_ACCEPTANCE",
    "max_pilot_layers": 4,
    "single_max_top1_drop_pp": 1,
    "single_max_top5_drop_pp": 2,
    "single_max_top1_new_errors": 2,
    "restore_max_top1_gain_pp": 1,
    "restore_max_top5_gain_pp": 2,
    "dense_priority_top1_pp": 3,
    "dense_priority_top5_pp": 4,
    "both_precisions_must_pass": True,
    "ranking": "worst single new Top1 errors, worst nonnegative Top1 effect, worst nonnegative Top5 effect, worst single logit relative L2, layer index",
    "combination": "Test the first at most 4 once; if gate fails, propose only first singleton, no subset search",
    "warning": "These thresholds are research-screening choices on 100 development images, not confidence bounds or a quality target",
}


def changed_weight_name(layer, precision):
    if precision == "fp32":
        return layer["layer_id"] + ".weight"
    if precision == "w8a8":
        return f"BQL_{layer['layer_index']}_Wf"
    raise ValueError("Unexpected precision")


def variant_model(source, layer, precision, replacement):
    name = changed_weight_name(layer, precision)
    model = onnx.ModelProto()
    model.CopyFrom(source)
    replace_initializers(model, {name: replacement})
    compare_unmodified(source.SerializeToString(), model, {name})
    return model


def select_shortlist(records, policy=POLICY):
    eligible, dense_priority = [], []
    for layer in records:
        singles = [layer["single"][p] for p in PRECISIONS]
        restores = [layer["restore"][p] for p in PRECISIONS]
        if (any(max(s["top1_drop_pp"], r["top1_gain_vs_pl36_pp"]) >= policy["dense_priority_top1_pp"]
                or max(s["top5_drop_pp"], r["top5_gain_vs_pl36_pp"]) >= policy["dense_priority_top5_pp"]
                for s, r in zip(singles, restores))):
            dense_priority.append(layer["layer_index"])
        if all(s["top1_drop_pp"] <= policy["single_max_top1_drop_pp"]
               and s["top5_drop_pp"] <= policy["single_max_top5_drop_pp"]
               and s["top1_new_errors"] <= policy["single_max_top1_new_errors"]
               and r["top1_gain_vs_pl36_pp"] <= policy["restore_max_top1_gain_pp"]
               and r["top5_gain_vs_pl36_pp"] <= policy["restore_max_top5_gain_pp"]
               for s, r in zip(singles, restores)):
            risk = (max(s["top1_new_errors"] for s in singles),
                    max(0, *(s["top1_drop_pp"] for s in singles), *(r["top1_gain_vs_pl36_pp"] for r in restores)),
                    max(0, *(s["top5_drop_pp"] for s in singles), *(r["top5_gain_vs_pl36_pp"] for r in restores)),
                    max(s["logit_relative_l2_vs_dense"] for s in singles), layer["layer_index"])
            eligible.append((risk, layer["layer_index"]))
    eligible.sort()
    return {"eligible_in_risk_order": [i for _, i in eligible],
            "initial_pilot_indices": [i for _, i in eligible[:policy["max_pilot_layers"]]],
            "dense_priority_indices": sorted(dense_priority)}


def combined_passes(metrics, policy=POLICY):
    return set(metrics) == set(PRECISIONS) and all(
        m["top1_drop_pp"] <= policy["single_max_top1_drop_pp"]
        and m["top5_drop_pp"] <= policy["single_max_top5_drop_pp"]
        and m["top1_new_errors"] <= policy["single_max_top1_new_errors"]
        for m in metrics.values())


def run_model(model, tensors):
    serialized = model.SerializeToString()
    identity = hashlib.sha256(serialized).hexdigest()
    engine = ort_session(model)
    inputs, outputs = engine.get_inputs(), engine.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1:
        raise ValueError("Expected complete H576 classifier")
    result = []
    for tensor in tensors:
        logits = engine.run([outputs[0].name], {inputs[0].name: tensor})[0]
        rank_logits(logits)
        if logits.shape != (1, 1000):
            raise ValueError("Unexpected classifier output")
        result.append(logits[0].copy())
    return np.stack(result), identity


def measure(logits, labels, dense_logits, pl36_logits, mode):
    metric, ranks = metric_counts(logits, labels, dense_logits)
    norm = float(np.linalg.norm(dense_logits.astype(np.float64)))
    if norm == 0:
        raise ValueError("Cannot use zero-norm Dense logits for sensitivity ranking")
    metric["logit_relative_l2_vs_dense"] = float(np.linalg.norm(
        logits.astype(np.float64) - dense_logits)) / norm
    if mode == "restore":
        against_full, _ = metric_counts(logits, labels, pl36_logits)
        metric["top1_gain_vs_pl36_pp"] = -against_full["top1_drop_pp"]
        metric["top5_gain_vs_pl36_pp"] = -against_full["top5_drop_pp"]
        metric["top1_recovered_vs_pl36"] = against_full["top1_recovered_errors"]
        metric["top1_new_errors_vs_pl36"] = against_full["top1_new_errors"]
    return metric, ranks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--archive", type=Path, default=ROOT / "inputs/h576-runtime-public-ui.tar.gz")
    parser.add_argument("--candidate", type=Path, default=ROOT / "candidates/h576_sparse24_pl36_v1")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "inputs/imagenet100_dev100")
    parser.add_argument("--previous-control", type=Path, default=ROOT / "eval_results/fp32_control_imagenet100_train100_v1")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    created = False
    try:
        if output.exists():
            raise ValueError("Output exists; choose a NEW directory")
        if ort.__version__ != "1.22.1" or np.__version__ != "1.26.4":
            raise ValueError("Use the frozen project NumPy/ORT environment")
        protected = {}
        # Preserve every pre-existing root source/document as well as model inputs.
        for pattern in ("*.py", "*.md", "*.txt", ".gitignore"):
            for path in ROOT.glob(pattern):
                protected[path.resolve()] = sha_file(path)
        source_paths = [args.archive, args.candidate / "candidate.json", args.data_dir / "labels.csv",
                        args.data_dir / "dataset_manifest.json", args.previous_control / "summary.json"]
        for path in source_paths:
            protected[path.resolve(strict=True)] = sha_file(path)
        manifest = json.loads((args.candidate / "candidate.json").read_text(encoding="utf-8"))
        verify_receipt(args.candidate, manifest, protected)
        previous = json.loads((args.previous_control / "summary.json").read_text(encoding="utf-8"))
        if previous["status"] != "COMPLETE_DEVELOPMENT_CONTROL" or previous["environment"]["threads"] != 4:
            raise ValueError("Unexpected previous control")
        for name, digest in previous["artifacts"].items():
            path = (args.previous_control / name).resolve(strict=True)
            if not path.is_relative_to(args.previous_control.resolve()) or sha_file(path) != digest:
                raise ValueError("Previous control changed")
            protected[path] = digest
        print("Verifying source and original PL36 masks ...", flush=True)
        payload, layers, fp32, w8 = read_source(args.archive)
        audit, weights = audit_candidate(payload, layers, fp32, w8, args.candidate)
        selected = [layer for layer in layers if layer["family"] in {"qkv", "fc1", "fc2"}]
        if len(selected) != 36:
            raise ValueError("Expected 36 target layers")
        base = {"fp32": fp32, "w8a8": w8}
        full = {"fp32": onnx.load(args.candidate / "pruned_fp32.onnx"),
                "w8a8": onnx.load(args.candidate / "pruned_w8a8_reference.onnx")}
        base_arrays = {p: arrays(model) for p, model in base.items()}
        samples = load_samples(args.data_dir, expected_count=100)
        with (args.data_dir / "labels.csv").open(encoding="utf-8-sig", newline="") as stream:
            expected = {row["image"]: row["image_sha256"] for row in csv.DictReader(stream)}
        tensors, identities = [], []
        for sample in samples:
            data = sample.path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected[sample.image]:
                raise ValueError("Image changed: " + sample.image)
            tensor = preprocess_image(io.BytesIO(data))
            tensor.flags.writeable = False
            tensors.append(tensor)
            identities.append({"image": sample.image, "label": sample.label, "sha256": digest})
            protected[sample.path] = digest
        dataset_sha = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
        if dataset_sha != previous["ordered_dataset_sha256"]:
            raise ValueError("Not the original ordered development set")
        labels = np.array([s.label for s in samples], np.int64)
        output.mkdir(parents=True, exist_ok=False)
        created = True
        write_json(output / "selection_policy.json", POLICY)
        write_json(output / "audit.json", audit)
        logits_by_name, models = {}, {}
        with np.load(args.previous_control / "logits.npz", allow_pickle=False) as old:
            for precision in PRECISIONS:
                for scope, source in (("dense", base), ("pl36", full)):
                    name = f"{scope}_{precision}"
                    logits, digest = run_model(source[precision], tensors)
                    previous_name = f"{'dense' if scope == 'dense' else 'pruned'}_{precision}"
                    np.testing.assert_array_equal(logits, old[previous_name])
                    logits_by_name[name] = logits
                    models[name] = {"mode": "control", "precision": precision, "serialized_model_sha256": digest}
        records = []
        for layer in selected:
            i = layer["layer_index"]
            record = {"layer_index": i, "layer_id": layer["layer_id"], "block": i // 4,
                      "family": layer["family"], "single": {}, "restore": {}}
            for mode in ("single", "restore"):
                for precision in PRECISIONS:
                    weight_name = changed_weight_name(layer, precision)
                    sparse_weight = (np.ascontiguousarray(weights[i]["W_fp32"].T) if precision == "fp32"
                                     else weights[i]["Wq"].astype(np.float32))
                    replacement = sparse_weight if mode == "single" else base_arrays[precision][weight_name]
                    source = base[precision] if mode == "single" else full[precision]
                    model = variant_model(source, layer, precision, replacement)
                    name = f"{mode}_L{i:02d}_{precision}"
                    logits, digest = run_model(model, tensors)
                    metric, _ = measure(logits, labels, logits_by_name[f"dense_{precision}"],
                                        logits_by_name[f"pl36_{precision}"], mode)
                    record[mode][precision] = metric
                    logits_by_name[name] = logits
                    models[name] = {"mode": mode, "precision": precision, "layer_index": i,
                                    "layer_id": layer["layer_id"], "changed_initializer": weight_name,
                                    "replacement_sha256": hashlib.sha256(replacement.tobytes()).hexdigest(),
                                    "serialized_model_sha256": digest}
            records.append(record)
            print(f"LAYER {len(records):02d}/36 {layer['layer_id']}: "
                  f"single Top1 FP32/W8={record['single']['fp32']['top1_pct']:.0f}/{record['single']['w8a8']['top1_pct']:.0f}; "
                  f"restore gains={record['restore']['fp32']['top1_gain_vs_pl36_pp']:+.0f}/{record['restore']['w8a8']['top1_gain_vs_pl36_pp']:+.0f} pp", flush=True)
        selection = select_shortlist(records)
        initial = selection["initial_pilot_indices"]
        combined_metrics = {}
        if initial:
            for precision in PRECISIONS:
                model = onnx.ModelProto()
                model.CopyFrom(base[precision])
                replacements = {}
                for layer in selected:
                    i = layer["layer_index"]
                    if i in initial:
                        replacements[changed_weight_name(layer, precision)] = (
                            np.ascontiguousarray(weights[i]["W_fp32"].T) if precision == "fp32"
                            else weights[i]["Wq"].astype(np.float32))
                replace_initializers(model, replacements)
                compare_unmodified(base[precision].SerializeToString(), model, set(replacements))
                name = f"combined_screen_{precision}"
                logits, digest = run_model(model, tensors)
                combined_metrics[precision], _ = measure(logits, labels, logits_by_name[f"dense_{precision}"],
                                                         logits_by_name[f"pl36_{precision}"], "combined")
                logits_by_name[name] = logits
                models[name] = {"mode": "combined_screen", "precision": precision, "layer_indices": initial,
                                "serialized_model_sha256": digest}
        combined_ok = combined_passes(combined_metrics)
        proposed = initial if combined_ok else initial[:1]
        by_index = {layer["layer_index"]: layer for layer in layers}
        proposal = {
            "status": "PROPOSAL_ONLY_REQUIRES_USER_APPROVAL_NOT_APPLIED",
            "policy": POLICY, "selection": selection,
            "combined_screen": {"tested_indices": initial, "passed_development_screen": combined_ok,
                                "metrics": combined_metrics},
            "proposed_sparse_finetune_layers": [by_index[i] for i in proposed],
            "proposed_dense_layers": [layer for layer in layers if layer["layer_index"] not in proposed],
            "fallback": "NONE" if combined_ok else ("SINGLE_LAYER_RESEARCH_PILOT_ONLY" if initial else "NO_LAYER_PASSED_SCREEN_KEEP_DENSE"),
            "mask_rule": "Reuse existing K-axis 2-of-4 masks, no mask regrowth or reselection during future training",
            "training": "NOT_STARTED; requires separate training data excluding these dev images and a later independent evaluation",
            "dense_structure_is_not_training_freeze": "Dense layers stay structurally dense; whether their weights train remains a later decision",
            "deployment_or_hls_change": "NONE", "onnx_model_files_written": 0,
        }
        for name, info in models.items():
            if info["mode"] == "control":
                metric, _ = measure(logits_by_name[name], labels, logits_by_name[f"dense_{info['precision']}"],
                                    logits_by_name[f"pl36_{info['precision']}"], "control")
            elif info["mode"] == "combined_screen":
                metric = combined_metrics[info["precision"]]
            else:
                record = next(r for r in records if r["layer_index"] == info["layer_index"])
                metric = record[info["mode"]][info["precision"]]
            info["metrics"] = metric
        if any(sha_file(path) != digest for path, digest in protected.items()):
            raise ValueError("A protected source changed during diagnosis")
        with (output / "logits.npz").open("xb") as stream:
            np.savez_compressed(stream, **logits_by_name, labels=labels,
                                images=np.array([s["image"] for s in identities]),
                                image_sha256=np.array([s["sha256"] for s in identities]))
        fields = ["model", "image", "image_sha256", "label", "top1", "top5", "top1_correct", "top5_correct"]
        with (output / "predictions.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for name, values in logits_by_name.items():
                for i, row in enumerate(values):
                    rank = rank_logits(row[None])
                    writer.writerow({"model": name, "image": identities[i]["image"],
                                     "image_sha256": identities[i]["sha256"], "label": int(labels[i]),
                                     "top1": int(rank[0]), "top5": json.dumps(rank.tolist()),
                                     "top1_correct": int(rank[0] == labels[i]), "top5_correct": int(labels[i] in rank)})
        write_json(output / "layers.json", records)
        write_json(output / "proposal.json", proposal)
        summary = {
            "schema": "h576_bidirectional_layer_sensitivity_v1", "status": "COMPLETE_DIAGNOSIS_PROPOSAL_NOT_APPLIED",
            "created_utc": datetime.now(timezone.utc).isoformat(), "source_archive_sha256": ARCHIVE_SHA256,
            "images": 100, "classes_covered": len(set(labels.tolist())), "layers_tested": len(records),
            "layer_interventions": 144, "control_logits_bitwise_reproduced": True,
            "ordered_dataset_sha256": dataset_sha, "models": models,
            "source_hashes": {str(path): digest for path, digest in protected.items() if path not in {s.path for s in samples}},
            "environment": {"python": platform.python_version(), "numpy": np.__version__, "onnx": onnx.__version__,
                            "onnxruntime": ort.__version__, "Pillow": pillow_version,
                            "intra_threads": 4, "inter_threads": 1, "graph_optimization": "disabled", "spinning": False},
            "preprocessing": PREPROCESS_SPEC, "onnx_model_files_written": 0,
            "artifacts": {path.name: sha_file(path) for path in sorted(output.iterdir())},
            "limits": ["100 public train development images, 65 classes; selected repeatedly, not independent validation",
                       "Single-prune loss and restoration gain are different contexts and not additive causal contributions",
                       "Selection cutoffs are exploratory, not statistical proof of safety; 0-1pp differences are not stable rankings",
                       "Only in-memory intervention graphs; no source models edited, no training or HLS/RTL/board changes"],
        }
        write_json(output / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "selection": selection,
                          "combined_screen": proposal["combined_screen"],
                          "proposed_sparse_layer_indices": proposed,
                          "proposed_sparse_layer_ids": [by_index[i]["layer_id"] for i in proposed],
                          "output": str(output)}, indent=2), flush=True)
        return 0
    except Exception as error:
        if created and not (output / "failure.json").exists() and not (output / "summary.json").exists():
            write_json(output / "failure.json", {"status": "FAIL", "traceback": traceback.format_exc()})
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
