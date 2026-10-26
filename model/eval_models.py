"""Compare two complete ONNX classifiers on the same labeled development images.

Expected folder: labels.csv with image,label columns and relative image paths.
Labels are zero-based MODEL OUTPUT indices, not ImageFolder's sorted class IDs.
Preprocessing matches the project's frozen model/b/preprocess.py transform.
No download, training, board access, image/model modification, or silent skipping.
"""
import argparse
import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import sys

import numpy as np
import onnxruntime as ort
from PIL import Image


PREPROCESS_SPEC = {
    "input_shape": [1, 3, 224, 224],
    "mode": "RGB",
    "resize_short_side": 248,
    "interpolation": "Pillow BICUBIC",
    "center_crop": "224x224; round((dimension-224)/2), ties-to-even",
    "mean": [0.485, 0.456, 0.406],
    "std": [0.229, 0.224, 0.225],
    "exif": "NO automatic transpose; matches frozen project preprocessing",
    "layout": "C-contiguous FP32 NCHW",
}
CSV_FIELDS = [
    "image", "image_sha256", "label", "dense_top1", "pruned_top1",
    "dense_top5", "pruned_top5", "pruned_top5_correct",
    "dense_true_logit", "pruned_true_logit",
]


@dataclass(frozen=True)
class Sample:
    image: str
    label: int
    path: Path


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_samples(data_dir, labels_path=None, expected_count=100):
    root = Path(data_dir).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("--data-dir must be a folder")
    if expected_count < 1:
        raise ValueError("--expected-count must be positive")
    manifest = Path(labels_path) if labels_path is not None else root / "labels.csv"
    if not manifest.is_absolute():
        manifest = root / manifest
    samples, seen = [], set()
    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not {"image", "label"}.issubset(reader.fieldnames):
            raise ValueError("Label CSV must have image,label columns")
        for line, row in enumerate(reader, 2):
            if None in row or not row.get("image") or not row.get("label"):
                raise ValueError(f"Invalid label row {line}")
            relative = Path(row["image"])
            if relative.is_absolute():
                raise ValueError(f"Row {line}: image paths must be relative to --data-dir")
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Row {line}: image must be a file inside --data-dir")
            if path in seen:
                raise ValueError(f"Row {line}: duplicate image path: {row['image']}")
            try:
                label = int(row["label"])
            except (ValueError, TypeError) as error:
                raise ValueError(f"Row {line}: label must be an integer model class index") from error
            if label < 0:
                raise ValueError(f"Row {line}: label must be zero-based and nonnegative")
            seen.add(path)
            samples.append(Sample(path.relative_to(root).as_posix(), label, path))
    if len(samples) != expected_count:
        raise ValueError(f"Expected exactly {expected_count} labeled images, got {len(samples)}; "
                         "no automatic truncation or skipping")
    return samples


def preprocess_image(image):
    """Accept a Path or binary stream; follow the frozen transform exactly."""
    with Image.open(image) as original:
        if original.format not in {"JPEG", "PNG", "BMP", "WEBP"}:
            raise ValueError(f"Unsupported image format: {original.format}")
        original.load()
        rgb = original.convert("RGB")
    width, height = rgb.size
    if min(width, height) <= 0:
        raise ValueError("Empty image")
    if (width <= height and width != 248) or (height < width and height != 248):
        size = ((248, int(248 * height / width)) if width <= height
                else (int(248 * width / height), 248))
        rgb = rgb.resize(size, resample=Image.Resampling.BICUBIC)
    width, height = rgb.size
    left, top = round((width - 224) / 2), round((height - 224) / 2)
    rgb = rgb.crop((left, top, left + 224, top + 224))
    x = np.asarray(rgb, dtype=np.uint8).transpose(2, 0, 1).astype(np.float32) / np.float32(255)
    mean = np.array(PREPROCESS_SPEC["mean"], np.float32)[:, None, None]
    std = np.array(PREPROCESS_SPEC["std"], np.float32)[:, None, None]
    return np.ascontiguousarray(((x - mean) / std)[None], dtype=np.float32)


class OnnxClassifier:
    def __init__(self, path, threads=4):
        self.path = Path(path).resolve(strict=True)
        self.sha256 = sha_file(self.path)
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.session = ort.InferenceSession(str(self.path), options,
                                            providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError(f"{self.path.name}: expected one image input and one logits output; "
                             "use the complete W8A8 reference graph, not the PL/custom-op graph")
        self.input, self.output = inputs[0], outputs[0]
        expected = PREPROCESS_SPEC["input_shape"]
        if (self.input.type != "tensor(float)" or len(self.input.shape) != 4 or
                any(isinstance(actual, int) and actual != wanted
                    for actual, wanted in zip(self.input.shape, expected))):
            raise ValueError(f"{self.path.name}: expected FP32 input [1,3,224,224]")
        if self.output.type != "tensor(float)" or len(self.output.shape) != 2:
            raise ValueError(f"{self.path.name}: expected FP32 logits [1,classes]")
        batch, classes = self.output.shape
        if isinstance(batch, int) and batch != 1:
            raise ValueError(f"{self.path.name}: expected output batch size 1")
        if isinstance(classes, int) and classes < 5:
            raise ValueError(f"{self.path.name}: Top5 needs at least five output classes")
        self.classes = classes if isinstance(classes, int) else None

    def __call__(self, tensor):
        return self.session.run([self.output.name], {self.input.name: tensor})[0]


def rank_logits(logits):
    values = np.asarray(logits)
    if (values.dtype != np.float32 or values.ndim != 2 or values.shape[0] != 1
            or values.shape[1] < 5 or not np.isfinite(values).all()):
        raise ValueError("Expected finite FP32 logits with shape [1,classes>=5]")
    # Softmax is unnecessary for ranking. Stable sort resolves ties by class index.
    return np.argsort(-values[0], kind="stable")[:5]


def evaluate(samples, dense, pruned, progress_every=10):
    if not samples:
        raise ValueError("No evaluation samples")
    dense_top1 = dense_top5 = pruned_top1 = pruned_top5 = recovered = 0
    errors, identities, class_count = [], [], None
    for number, sample in enumerate(samples, 1):
        try:
            data = sample.path.read_bytes()
            image_sha = hashlib.sha256(data).hexdigest()
            tensor = preprocess_image(io.BytesIO(data))
            # Preprocess ONCE, then send the very same FP32 input to both models.
            dense_logits, pruned_logits = dense(tensor), pruned(tensor)
            dense_rank, pruned_rank = rank_logits(dense_logits), rank_logits(pruned_logits)
            if dense_logits.shape != pruned_logits.shape:
                raise ValueError("Dense and pruned class counts differ")
            classes = dense_logits.shape[1]
            if class_count is not None and classes != class_count:
                raise ValueError("Output class count changed between images")
            class_count = classes
            if sample.label >= classes:
                raise ValueError(f"Label {sample.label} outside model class range 0..{classes-1}")
            dense_ok, pruned_ok = int(dense_rank[0]) == sample.label, int(pruned_rank[0]) == sample.label
            dense_top1 += int(dense_ok)
            pruned_top1 += int(pruned_ok)
            dense_top5 += int(sample.label in dense_rank)
            pruned_top5 += int(sample.label in pruned_rank)
            recovered += int(not dense_ok and pruned_ok)
            identities.append({"image": sample.image, "label": sample.label, "sha256": image_sha})
            if dense_ok and not pruned_ok:
                errors.append({
                    "image": sample.image, "image_sha256": image_sha, "label": sample.label,
                    "dense_top1": int(dense_rank[0]), "pruned_top1": int(pruned_rank[0]),
                    "dense_top5": json.dumps(dense_rank.tolist()),
                    "pruned_top5": json.dumps(pruned_rank.tolist()),
                    "pruned_top5_correct": int(sample.label in pruned_rank),
                    "dense_true_logit": float(dense_logits[0, sample.label]),
                    "pruned_true_logit": float(pruned_logits[0, sample.label]),
                })
        except Exception as error:
            raise ValueError(f"Image {number}/{len(samples)} failed ({sample.image}): {error}; "
                             "evaluation aborted, no samples skipped") from error
        if progress_every and (number % progress_every == 0 or number == len(samples)):
            print(f"Evaluated {number}/{len(samples)}", flush=True)
    total = len(samples)
    dataset_sha = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "images": total, "classes": class_count,
        "dense_top1_correct": dense_top1, "dense_top5_correct": dense_top5,
        "pruned_top1_correct": pruned_top1, "pruned_top5_correct": pruned_top5,
        "dense_top1_pct": 100 * dense_top1 / total,
        "dense_top5_pct": 100 * dense_top5 / total,
        "pruned_top1_pct": 100 * pruned_top1 / total,
        "pruned_top5_pct": 100 * pruned_top5 / total,
        "top1_drop_pp": 100 * (dense_top1 - pruned_top1) / total,
        "top5_drop_pp": 100 * (dense_top5 - pruned_top5) / total,
        "new_errors": errors, "recovered_errors": recovered,
        "ordered_dataset_sha256": dataset_sha,
    }


def save_errors(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 BOM for Windows Excel; x mode refuses to replace an existing CSV.
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-model", required=True, type=Path, help="Complete Dense W8A8 ONNX reference")
    parser.add_argument("--pruned-model", required=True, type=Path, help="Complete 2:4 W8A8 ONNX reference")
    parser.add_argument("--data-dir", required=True, type=Path, help="Folder containing images and labels.csv")
    parser.add_argument("--labels", type=Path, help="CSV; relative paths resolve against --data-dir")
    parser.add_argument("--output-csv", type=Path, default=Path("eval_results/new_errors.csv"))
    parser.add_argument("--expected-count", type=positive_int, default=100)
    parser.add_argument("--threads", type=positive_int, default=4)
    args = parser.parse_args(argv)
    try:
        output = args.output_csv.resolve()
        if output.exists():
            raise ValueError(f"Output CSV already exists; choose a new filename: {output}")
        samples = load_samples(args.data_dir, args.labels, args.expected_count)
        dense = OnnxClassifier(args.dense_model, args.threads)
        pruned = OnnxClassifier(args.pruned_model, args.threads)
        if dense.classes and pruned.classes and dense.classes != pruned.classes:
            raise ValueError("Dense and pruned output class counts differ")
        for model in (dense, pruned):
            if model.classes and any(sample.label >= model.classes for sample in samples):
                raise ValueError(f"Labels must be model output indices in 0..{model.classes-1}")
        print("CPU development-set comparison; NOT a board benchmark or formal quality acceptance.")
        print(f"Images: {len(samples)}; threads: {args.threads}; ORT: {ort.__version__}")
        print(f"Dense SHA256:  {dense.sha256}\nPruned SHA256: {pruned.sha256}")
        result = evaluate(samples, dense, pruned)
        if sha_file(dense.path) != dense.sha256 or sha_file(pruned.path) != pruned.sha256:
            raise ValueError("A model file changed during evaluation; results not saved")
        save_errors(output, result["new_errors"])
        print("\nModel       Top1                 Top5")
        for name, prefix in (("Dense", "dense"), ("Pruned", "pruned")):
            print(f"{name:<11} {result[prefix+'_top1_correct']}/{result['images']} "
                  f"({result[prefix+'_top1_pct']:.2f}%)       "
                  f"{result[prefix+'_top5_correct']}/{result['images']} "
                  f"({result[prefix+'_top5_pct']:.2f}%)")
        print(f"Accuracy drop (Dense - Pruned): Top1 {result['top1_drop_pp']:+.2f} pp; "
              f"Top5 {result['top5_drop_pp']:+.2f} pp")
        print(f"New errors: {len(result['new_errors'])}; recovered errors: {result['recovered_errors']}")
        print(f"Ordered dataset SHA256: {result['ordered_dataset_sha256']}")
        print(f"New-error CSV: {output}")
        print("COMPLETE: all labeled images evaluated; zero silently skipped samples.")
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
