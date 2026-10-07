"""Pinned Imagenette download, safe extraction and stratified split identities."""
import argparse
from collections import defaultdict
from pathlib import Path
import random
import tarfile
import urllib.request
from .common import ROOT, sha, utc, write_json

URL = "https://s3.amazonaws.com/fast-ai-imageclas/imagenette2-320.tgz"

def download(url, path):
    path = Path(path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as out:
        total = 0
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
            total += len(chunk)
            if total % (50 * 1024 * 1024) == 0:
                print(f"downloaded {total // (1024 * 1024)} MiB", flush=True)
    part.rename(path)

def labels():
    import timm
    info = timm.data.ImageNetInfo()
    return [{"index": i, "wnid": info.index_to_label_name(i), "name": info.index_to_description(i)} for i in range(1000)]

def prepare(cache, output):
    cache, output = Path(cache), Path(output)
    archive = cache.parent / "imagenette2-320.tgz"
    download(URL, archive)
    if not cache.exists():
        # Validate all names before extraction; reject links and non-regular payloads.
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            for m in members:
                target = (cache.parent / m.name).resolve()
                if not target.is_relative_to(cache.parent.resolve()) or m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
                    raise ValueError("Unsafe archive member: " + m.name)
            tf.extractall(cache.parent, members=members, filter="data")
        extracted = cache.parent / "imagenette2-320"
        if extracted != cache:
            if cache.exists():
                raise FileExistsError(cache)
            extracted.rename(cache)
    table = labels()
    index = {r["wnid"]: r["index"] for r in table}
    items, duplicate = {}, []
    for split in ("train", "val"):
        items[split] = []
        for p in sorted((cache / split).rglob("*")):
            if p.suffix.lower() not in (".jpeg", ".jpg", ".png"):
                continue
            rel = p.relative_to(cache).as_posix()
            items[split].append({"path": rel, "image_sha256": sha(p), "bytes": p.stat().st_size,
                                 "wnid": p.parent.name, "label": index[p.parent.name]})
    # Remove train content overlapping val before any sampling; keep original val.
    valhash = {r["image_sha256"] for r in items["val"]}
    for row in items["train"]:
        if row["image_sha256"] in valhash:
            duplicate.append(row)
    train = [r for r in items["train"] if r["image_sha256"] not in valhash]
    groups = defaultdict(list)
    for row in train:
        groups[row["wnid"]].append(row)
    rng = random.Random(20260930)
    calibration, selection, regression = [], [], []
    for i, wnid in enumerate(sorted(groups)):
        population = sorted(groups[wnid], key=lambda r: r["path"])
        # Also avoid identical content within the sampling population.
        population = list({r["image_sha256"]: r for r in reversed(population)}.values())
        population.sort(key=lambda r: r["path"])
        sampled = rng.sample(population, (26 if i < 6 else 25) + 10)
        c = 26 if i < 6 else 25
        calibration += sampled[:c]
        selection += sampled[c:]
        regression += sampled[c:c + 2]
    valgroups = defaultdict(list)
    for row in items["val"]:
        valgroups[row["wnid"]].append(row)
    fast = []
    for wnid in sorted(valgroups):
        fast += rng.sample(sorted(valgroups[wnid], key=lambda r: r["path"]), 50)
    result = {"schema": "b_dataset_splits_v1", "created_utc": utc(), "seed": 20260930,
              "source_url": URL, "source_documentation": "https://github.com/fastai/imagenette",
              "archive_sha256": sha(archive), "archive_bytes": archive.stat().st_size,
              "labels_source": "timm==1.0.20 ImageNetInfo; full 1000 indices", "prediction_classes": 1000,
              "noisy_labels": "NOT_USED", "pretrained_subset_limitation": True,
              "removed_train_val_duplicate_content": duplicate,
              "raw_counts": {k: len(v) for k, v in items.items()},
              "splits": {"calibration": calibration, "selection": selection, "regression": regression,
                         "validation_fast": fast, "validation_full": items["val"]},
              "gate": {"top1_max_drop_pp": 1.0, "logits_atol": 1e-4, "logits_rtol": 1e-4},
              "val_predictions": "NOT_RUN; train selection required before access"}
    write_json(output / "labels_imagenet1k.json", table)
    write_json(output / "dataset_inventory.json", items)
    write_json(output / "splits.json", result)
    write_json(output / "dataset_source.json", {"source_url": URL, "archive_sha256": sha(archive),
               "documentation": "https://github.com/fastai/imagenette", "repository_license": "Apache-2.0",
               "data_origin": "ImageNet subset; original image rights remain with their owners", "created_utc": utc()})
    print(f"dataset PASS: {result['raw_counts']}; splits 256/100/20/500/{len(items['val'])}", flush=True)

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    prepare(a.cache, a.output)
