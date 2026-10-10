"""Verify fixed public Imagenette data and regenerate existing regression inputs; no inference/PL."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

SYSTEM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SYSTEM))
from model.b.common import read_json, safe_path, sha


def load(root):
    root = root.resolve()
    lock = read_json(SYSTEM / "docs/locks/DATASET.lock.json")
    if sha(root / "asset_manifest.json") != lock["asset_manifest_sha256"]:
        raise ValueError("Dataset payload manifest identity mismatch")
    manifest = read_json(root / "asset_manifest.json")
    for name, spec in lock["metadata"].items():
        file = safe_path(root, name)
        if file.stat().st_size != spec["bytes"] or sha(file) != spec["sha256"]:
            raise ValueError("Frozen metadata mismatch: " + name)
    splits = read_json(root / "splits.json")
    labels = read_json(root / "labels_imagenet1k.json")
    if splits["seed"] != 20260930 or splits["prediction_classes"] != 1000 or len(labels) != 1000:
        raise ValueError("Wrong split/label contract")
    for name, count in lock["split_counts"].items():
        if len(splits["splits"][name]) != count:
            raise ValueError("Wrong split size: " + name)
    rows = {}
    for name in ("calibration", "selection", "validation_fast"):
        for row in splits["splits"][name]:
            safe_path(root, row["path"])
            if labels[row["label"]]["wnid"] != row["wnid"]:
                raise ValueError("Wrong ImageNet-1k label")
            if row["path"] in rows and rows[row["path"]] != row:
                raise ValueError("Conflicting image identity")
            rows[row["path"]] = row
    if len(rows) != lock["packaged_images"]:
        raise ValueError("Wrong packaged image count")
    return root, lock, manifest, splits, rows


def verify_rows(root, rows):
    for row in rows:
        file = safe_path(root, row["path"])
        if not file.is_file():
            raise FileNotFoundError("Required split image absent: " + row["path"] +
                                    "; validation_full requires the official full archive")
        if file.stat().st_size != row["bytes"] or sha(file) != row["image_sha256"]:
            raise ValueError("Image identity mismatch: " + row["path"])


def verify(args):
    root, lock, manifest, splits, rows = load(args.root)
    if args.split:
        selected = splits["splits"][args.split]
        images = args.images_root.resolve() if args.images_root else root
        verify_rows(images, selected)
        return {"status": "PASS_FIXED_SPLIT_IMAGES", "split": args.split,
                "images": len(selected), "PL_starts": 0}
    if args.images_root:
        raise ValueError("--images-root requires an explicit --split")
    for name, item in manifest["files"].items():
        file = safe_path(root, name)
        if not file.is_file() or file.stat().st_size != item["bytes"] or sha(file) != item["sha256"]:
            raise ValueError("Dataset payload mismatch: " + name)
    verify_rows(root, rows.values())
    return {"status": "PASS_DATASET_PAYLOAD", "images": len(rows),
            "payload_files": len(manifest["files"]), "split_counts": lock["split_counts"],
            "validation_full_included": False, "PL_starts": 0}


def check_regression(args):
    root, lock, _, splits, _ = load(args.root)
    if sha(args.bundle / "bundle_manifest.json") != lock["B_manifest_sha256"]:
        raise ValueError("Wrong frozen B model bundle")
    model_manifest = read_json(args.bundle / "bundle_manifest.json")
    import numpy as np
    import PIL
    from model.b import preprocess as module
    if np.__version__ != lock["versions"]["NumPy"] or PIL.__version__ != lock["versions"]["Pillow"]:
        raise ValueError("Use pinned NumPy/Pillow before numerical qualification")
    if sha(Path(module.__file__)) != lock["preprocess_source_sha256"]:
        raise ValueError("Preprocessor source identity changed")
    if module.SPEC != read_json(root / "preprocess.json"):
        raise ValueError("Preprocessing specification changed")
    refs = read_json(root / "regression_refs.json")["rows"]
    verify_rows(root, splits["splits"]["regression"])
    for i, (row, ref) in enumerate(zip(splits["splits"]["regression"], refs)):
        if ref["index"] != i or ref["image_path"] != row["path"]:
            raise ValueError("Regression order changed")
        file = safe_path(args.bundle, ref["frozen_tensor"])
        if sha(file) != ref["frozen_tensor_file_sha256"] or sha(file) != model_manifest["files"][ref["frozen_tensor"]]["sha256"]:
            raise ValueError("Frozen reference changed")
        expected = np.load(file, allow_pickle=False)
        actual = module.preprocess(safe_path(root, row["path"]))
        if actual.shape != (1, 3, 224, 224) or actual.dtype != np.float32 or not np.isfinite(actual).all():
            raise ValueError("Wrong input contract")
        if actual.tobytes() != expected.tobytes() or hashlib.sha256(actual.tobytes()).hexdigest() != ref["array_sha256"]:
            raise ValueError("Preprocessing byte mismatch: " + str(i))
    if len(refs) != 20:
        raise ValueError("Twenty regression references required")
    return {"status": "PASS_20_PREPROCESS_BYTE_IDENTITIES", "images": 20,
            "inference": "NOT_RUN", "PL_starts": 0}


def unpack_full(args):
    lock = read_json(SYSTEM / "docs/locks/DATASET.lock.json")
    spec = lock["source_archive"]
    if args.archive.stat().st_size != spec["bytes"] or sha(args.archive) != spec["sha256"]:
        raise ValueError("Official archive size/SHA mismatch")
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError("Official extraction requires a fresh destination")
    with tarfile.open(args.archive, "r:gz") as archive:
        seen = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or ":" in member.name or
                    "\\" in member.name or not path.parts or path.parts[0] != spec["root_prefix"] or
                    not (member.isfile() or member.isdir()) or member.name.casefold() in seen):
                raise ValueError("Unsafe official archive member")
            if not (destination / member.name).resolve().is_relative_to(destination):
                raise ValueError("Archive path escapes destination")
            seen.add(member.name.casefold())
        destination.mkdir(parents=True)
        archive.extractall(destination, filter="data")
    return {"status": "PASS_OFFICIAL_ARCHIVE_EXTRACT", "root": str(destination / spec["root_prefix"]),
            "source_sha256": spec["sha256"], "PL_starts": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--root", type=Path, required=True)
    verify_parser.add_argument("--images-root", type=Path)
    verify_parser.add_argument("--split", choices=("calibration", "selection", "regression",
                                                  "validation_fast", "validation_full"))
    regression = sub.add_parser("check-regression")
    regression.add_argument("--root", type=Path, required=True)
    regression.add_argument("--bundle", type=Path, required=True)
    full = sub.add_parser("unpack-full")
    full.add_argument("--archive", type=Path, required=True)
    full.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"verify": verify, "check-regression": check_regression, "unpack-full": unpack_full}[args.command](args)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
