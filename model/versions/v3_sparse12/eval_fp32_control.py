"""Frozen H576 four-model development control; no training or board access.

Reuses eval_models preprocessing, CPU session settings and tie-breaking.
Creates a NEW result directory only; original models/data/results stay unchanged.
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

import numpy as np
import onnxruntime as ort
from PIL import __version__ as pillow_version

from eval_models import (OnnxClassifier, PREPROCESS_SPEC, load_samples,
                         positive_int, preprocess_image, rank_logits,
                         save_errors, sha_file)

ROOT = Path(__file__).resolve().parent
MODEL_NAMES = ("dense_fp32", "pruned_fp32", "dense_w8a8", "pruned_w8a8")
ARCHIVE_SHA = "c29a006fe472fd2bfb545d7bbf77f34a5090ff9315043cdd7e5830e518d61f7c"


def summarize(logits, labels):
    """Compute paired counts; signed deltas are descriptive, NOT causal shares."""
    labels = np.asarray(labels)
    if labels.ndim != 1 or not len(labels) or labels.dtype.kind not in "iu":
        raise ValueError("Expected nonempty integer labels")
    ranks, metrics = {}, {}
    classes = None
    for name in MODEL_NAMES:
        values = logits[name]
        if values.ndim != 2 or values.shape[0] != len(labels):
            raise ValueError("One logits row per image required")
        if classes is not None and values.shape[1] != classes:
            raise ValueError("Model class counts differ")
        classes = values.shape[1]
        if np.any(labels < 0) or np.any(labels >= classes):
            raise ValueError("Label outside output class range")
        ranks[name] = np.stack([rank_logits(row[None]) for row in values])
        top1 = ranks[name][:, 0] == labels
        top5 = np.any(ranks[name] == labels[:, None], axis=1)
        metrics[name] = {"top1_correct": int(top1.sum()), "top5_correct": int(top5.sum()),
                         "top1_pct": 100 * float(top1.mean()),
                         "top5_pct": 100 * float(top5.mean())}
    comparisons = {}
    for title, left, right in (
        ("fp32_pruning", "dense_fp32", "pruned_fp32"),
        ("w8a8_pruning", "dense_w8a8", "pruned_w8a8"),
        ("dense_quantization", "dense_fp32", "dense_w8a8"),
        ("pruned_quantization", "pruned_fp32", "pruned_w8a8"),
    ):
        a, b = ranks[left][:, 0] == labels, ranks[right][:, 0] == labels
        comparisons[title] = {
            "left": left, "right": right,
            "top1_drop_pp": 100 * (metrics[left]["top1_correct"] - metrics[right]["top1_correct"]) / len(labels),
            "top5_drop_pp": 100 * (metrics[left]["top5_correct"] - metrics[right]["top5_correct"]) / len(labels),
            "both_correct": int((a & b).sum()),
            "new_errors": int((a & ~b).sum()),
            "recovered_errors": int((~a & b).sum()),
            "both_wrong": int((~a & ~b).sum()),
        }
    return {"images": len(labels), "classes": classes, "models": metrics,
            "comparisons": comparisons,
            "quantization_interaction_pp": {
                metric: comparisons["w8a8_pruning"][metric + "_drop_pp"]
                - comparisons["fp32_pruning"][metric + "_drop_pp"]
                for metric in ("top1", "top5")},
            "interpretation": "Signed left-minus-right deltas; interaction is descriptive, not a causal attribution."}, ranks


def error_rows(identities, logits, ranks, left, right):
    rows = []
    for i, sample in enumerate(identities):
        label = sample["label"]
        if ranks[left][i, 0] == label and ranks[right][i, 0] != label:
            rows.append({
                "image": sample["image"], "image_sha256": sample["sha256"], "label": label,
                "dense_top1": int(ranks[left][i, 0]), "pruned_top1": int(ranks[right][i, 0]),
                "dense_top5": json.dumps(ranks[left][i].tolist()),
                "pruned_top5": json.dumps(ranks[right][i].tolist()),
                "pruned_top5_correct": int(label in ranks[right][i]),
                "dense_true_logit": float(logits[left][i, label]),
                "pruned_true_logit": float(logits[right][i, label]),
            })
    return rows


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "inputs/imagenet100_dev100")
    parser.add_argument("--baseline-dir", type=Path, default=ROOT / "inputs/baseline")
    parser.add_argument("--candidate-dir", type=Path, default=ROOT / "candidates/h576_sparse24_pl36_v1")
    parser.add_argument("--previous-w8-errors", type=Path,
                        help="Optional previous CSV; require all fields to reproduce exactly")
    parser.add_argument("--expected-count", type=positive_int, default=100)
    parser.add_argument("--threads", type=positive_int, default=4)
    args = parser.parse_args(argv)
    try:
        output = args.output_dir.resolve()
        if output.exists():
            raise ValueError("Output directory exists; select a NEW name (no overwrite)")
        manifest_path = args.candidate_dir / "candidate.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["source"]["archive_sha256"] != ARCHIVE_SHA:
            raise ValueError("Not the frozen H576 candidate source")
        specs = {
            "dense_fp32": (args.baseline_dir / "h576_fp32.onnx", manifest["source"]["fp32_sha256"]),
            "pruned_fp32": (args.candidate_dir / "pruned_fp32.onnx", manifest["files"]["pruned_fp32.onnx"]["sha256"]),
            "dense_w8a8": (args.baseline_dir / "h576_w8.onnx", manifest["source"]["w8_sha256"]),
            "pruned_w8a8": (args.candidate_dir / "pruned_w8a8_reference.onnx", manifest["files"]["pruned_w8a8_reference.onnx"]["sha256"]),
        }
        labels_path = args.data_dir / "labels.csv"
        protected = {p.resolve(): sha_file(p) for p in
                     (manifest_path, labels_path, Path(__file__), ROOT / "eval_models.py")}
        if args.previous_w8_errors is not None:
            protected[args.previous_w8_errors.resolve()] = sha_file(args.previous_w8_errors)
        samples = load_samples(args.data_dir, expected_count=args.expected_count)
        with labels_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if any(not row.get("image_sha256") for row in rows):
            raise ValueError("Control requires downloader's image_sha256 column")
        expected_images = {Path(row["image"]).as_posix(): row["image_sha256"] for row in rows}
        models = {}
        for name, (path, digest) in specs.items():
            if sha_file(path) != digest:
                raise ValueError(f"Model identity mismatch: {name}")
            protected[path.resolve()] = digest
            models[name] = OnnxClassifier(path, args.threads)
        all_logits = {name: [] for name in MODEL_NAMES}
        identities = []
        print("Four-model CPU control on development images; NOT training, board timing or quality acceptance.", flush=True)
        for number, sample in enumerate(samples, 1):
            data = sample.path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected_images[sample.image]:
                raise ValueError(f"Image hash mismatch: {sample.image}")
            tensor = preprocess_image(io.BytesIO(data))
            tensor.flags.writeable = False
            for name, model in models.items():
                values = model(tensor)
                rank_logits(values)
                if values.shape != (1, 1000):
                    raise ValueError(f"{name}: frozen H576 must output [1,1000]")
                all_logits[name].append(values[0].copy())
            identities.append({"image": sample.image, "label": sample.label, "sha256": digest})
            protected[sample.path] = digest
            if number % 10 == 0 or number == len(samples):
                print(f"Evaluated {number}/{len(samples)} across all four models", flush=True)
        logits = {name: np.stack(values) for name, values in all_logits.items()}
        labels = np.array([sample.label for sample in samples], dtype=np.int64)
        summary, ranks = summarize(logits, labels)
        errors = {
            "fp32": error_rows(identities, logits, ranks, "dense_fp32", "pruned_fp32"),
            "w8a8": error_rows(identities, logits, ranks, "dense_w8a8", "pruned_w8a8"),
        }
        if args.previous_w8_errors is not None:
            with args.previous_w8_errors.open(encoding="utf-8-sig", newline="") as stream:
                previous = list(csv.DictReader(stream))
            if previous != [{key: str(value) for key, value in row.items()} for row in errors["w8a8"]]:
                raise ValueError("Previous W8A8 new-error CSV did not reproduce exactly")
        if any(sha_file(path) != digest for path, digest in protected.items()):
            raise ValueError("Input or implementation changed during evaluation; no results saved")
        dataset_sha = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
        summary.update({"schema": "h576_four_model_control_v1", "status": "COMPLETE_DEVELOPMENT_CONTROL",
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                        "ordered_dataset_sha256": dataset_sha,
                        "previous_w8_errors_exact_match": True if args.previous_w8_errors else None,
                        "preprocessing": PREPROCESS_SPEC,
                        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                                        "onnxruntime": ort.__version__, "Pillow": pillow_version,
                                        "threads": args.threads, "inter_threads": 1, "optimization": "disabled",
                                        "execution": "sequential", "spinning": False, "provider": "CPU"},
                        "model_identities": {name: {"path": str(path.resolve()), "sha256": digest}
                                             for name, (path, digest) in specs.items()},
                        "source_hashes": {str(path): digest for path, digest in protected.items()
                                          if path not in {sample.path for sample in samples}},
                        "classes_covered": len(set(labels.tolist())),
                        "limits": ["Public train-side development subset; not balanced or independent acceptance data",
                                   "Source images resized to short side 160; not official ImageNet validation",
                                   "No training, mask changes, private holdout, HLS/RTL/PL or speed benchmark"]})
        output.mkdir(parents=True, exist_ok=False)
        fields = ["image", "image_sha256", "label"] + [f"{name}_{field}" for name in MODEL_NAMES
                  for field in ("top1", "top5", "top1_correct", "top5_correct", "true_logit")]
        with (output / "predictions.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for i, sample in enumerate(identities):
                row = {"image": sample["image"], "image_sha256": sample["sha256"], "label": sample["label"]}
                for name in MODEL_NAMES:
                    row.update({f"{name}_top1": int(ranks[name][i, 0]),
                                f"{name}_top5": json.dumps(ranks[name][i].tolist()),
                                f"{name}_top1_correct": int(ranks[name][i, 0] == labels[i]),
                                f"{name}_top5_correct": int(labels[i] in ranks[name][i]),
                                f"{name}_true_logit": float(logits[name][i, labels[i]])})
                writer.writerow(row)
        with (output / "logits.npz").open("xb") as stream:
            np.savez_compressed(stream, **logits, labels=labels,
                                images=np.array([s["image"] for s in identities]),
                                image_sha256=np.array([s["sha256"] for s in identities]))
        for name, rows in errors.items():
            save_errors(output / f"{name}_new_errors.csv", rows)
        summary["artifacts"] = {path.name: sha_file(path) for path in sorted(output.iterdir())}
        # Completion marker is written LAST; partial folders never have a COMPLETE summary.
        write_json(output / "summary.json", summary)
        print(json.dumps({"models": summary["models"], "comparisons": summary["comparisons"],
                          "quantization_interaction_pp": summary["quantization_interaction_pp"],
                          "ordered_dataset_sha256": dataset_sha, "output_dir": str(output)}, indent=2))
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
