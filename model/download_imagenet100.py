"""Download a small clane9/imagenet-100 development subset for eval_models.py.

Uses the public Dataset Viewer /rows API and its revision-bound cached images,
not a full Parquet download. Images are already resized by the dataset publisher
and Viewer may re-encode them. This is NOT original-resolution ImageNet data.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import random
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image


DATASET = "clane9/imagenet-100"
CONFIG = "default"
HUB_API = "https://huggingface.co/api/datasets/" + DATASET
VIEWER = "https://datasets-server.huggingface.co"
HERE = Path(__file__).resolve().parent
ARCHIVE_SHA256 = "c29a006fe472fd2bfb545d7bbf77f34a5090ff9315043cdd7e5830e518d61f7c"
LABEL_MEMBER = "v5_h576_ui_20261017_01/assets/labels_imagenet1k.json"
LICENSE_URL = "https://huggingface.co/datasets/clane9/imagenet-100#licensing-information"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


class HttpClient:
    def __init__(self, timeout=30, retries=3):
        self.timeout, self.retries = timeout, retries

    def get(self, url, max_bytes=2 * 1024 * 1024):
        require(urllib.parse.urlsplit(url).scheme == "https", "HTTPS is required")
        request = urllib.request.Request(url, headers={
            "User-Agent": "H576-ImageNet100-development-downloader/1.0",
            "Cache-Control": "no-cache",
        })
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    require(urllib.parse.urlsplit(response.geturl()).scheme == "https", "Non-HTTPS redirect")
                    length = response.headers.get("Content-Length")
                    if length is not None:
                        require(int(length) <= max_bytes, "Response exceeds the size limit")
                    data = response.read(max_bytes + 1)
                    require(len(data) <= max_bytes, "Response exceeds the size limit")
                    return data
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):
                    raise RuntimeError("Access denied by Hugging Face; check access/terms or retry later; "
                                       "the script does not bypass access controls") from error
                if error.code not in (429, 500, 502, 503, 504) or attempt + 1 == self.retries:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt + 1 == self.retries:
                    raise
            print(f"Network retry {attempt + 1}/{self.retries - 1}: "
                  f"{urllib.parse.urlsplit(url).hostname}", file=sys.stderr, flush=True)
            time.sleep(min(2 ** attempt, 4))
        raise RuntimeError("Download retries exhausted")

    def json(self, endpoint, **params):
        url = endpoint + ("?" + urllib.parse.urlencode(params) if params else "")
        return json.loads(self.get(url).decode("utf-8"))


def model_classes(archive):
    require(sha_file(archive) == ARCHIVE_SHA256, "Unexpected original H576 archive SHA256")
    with tarfile.open(archive, "r:gz") as stream:
        member = stream.getmember(LABEL_MEMBER)
        require(member.isfile() and member.size < 1024 * 1024, "Invalid model label member")
        data = stream.extractfile(member).read()
    labels = json.loads(data)
    require(len(labels) == 1000 and [v["index"] for v in labels] == list(range(1000)),
            "Expected H576 ImageNet-1k output labels 0..999")
    require(len({v["wnid"] for v in labels}) == 1000, "Duplicate model synset IDs")
    return labels, sha(data)


def normalize_name(text):
    require(isinstance(text, str) and text.strip(), "Empty class name")
    return " ".join(text.split()).casefold()


def build_label_map(names, labels):
    require(len(names) == 100 and len({normalize_name(v) for v in names}) == 100,
            "Expected exactly 100 distinct dataset class names")
    by_name = defaultdict(list)
    for item in labels:
        by_name[normalize_name(item["name"])].append(item)
    mapping = []
    for index, name in enumerate(names):
        matches = by_name[normalize_name(name)]
        require(len(matches) == 1, f"Unmatched or ambiguous class {index}: {name}; no fuzzy mapping allowed")
        item = matches[0]
        mapping.append({"source_label": index, "source_name": name,
                        "label": item["index"], "wnid": item["wnid"], "model_name": item["name"]})
    require(len({v["label"] for v in mapping}) == 100, "Dataset labels map to duplicate output classes")
    return mapping


def viewer_names(features):
    labels = [f["type"] for f in features if f.get("name") == "label"]
    require(len(labels) == 1 and labels[0].get("_type") == "ClassLabel", "Invalid Viewer label feature")
    return labels[0]["names"]


def metadata(client, split, archive):
    hub = client.json(HUB_API)
    require(hub.get("id") == DATASET and not hub.get("private") and not hub.get("gated"),
            "Dataset is no longer public/ungated; request appropriate access, do not bypass it")
    revision = hub["sha"]
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "Invalid dataset revision")
    info = client.json(VIEWER + "/info", dataset=DATASET, config=CONFIG)
    require(info.get("partial") is False, "Dataset metadata is partial")
    info = info["dataset_info"]
    names = info["features"]["label"]["names"]
    total = info["splits"][split]["num_examples"]
    labels, labels_sha = model_classes(archive)
    mapping = build_label_map(names, labels)
    require(isinstance(total, int) and total > 0, "Invalid split row count")
    # Validate one public metadata record, without fetching the image bytes.
    row = fetch_row(client, split, 0, revision, total, names)
    return {"dataset": DATASET, "config": CONFIG, "revision": revision,
            "split": split, "total_rows": total, "names": names,
            "model_labels_sha256": labels_sha, "label_map": mapping,
            "sample_source_label": row["source_label"]}


def fetch_row(client, split, index, revision, total, names):
    page = client.json(VIEWER + "/rows", dataset=DATASET, config=CONFIG,
                       split=split, offset=index, length=1)
    require(page.get("partial") is False and page["num_rows_total"] == total,
            "Viewer row count changed or data is partial")
    require(viewer_names(page["features"]) == names, "Viewer label order changed")
    require(len(page["rows"]) == 1, f"Expected one row at index {index}")
    entry = page["rows"][0]
    require(entry["row_idx"] == index and not entry.get("truncated_cells"), "Wrong or truncated row")
    row = entry["row"]
    label, image = row["label"], row["image"]
    require(type(label) is int and 0 <= label < 100, "Invalid source label")
    url = image["src"]
    parsed = urllib.parse.urlsplit(url)
    prefix = f"/cached-assets/{DATASET}/--/{revision}/--/{CONFIG}/{split}/{index}/image/"
    require(parsed.scheme == "https" and parsed.netloc == "datasets-server.huggingface.co"
            and parsed.path.startswith(prefix), "Image URL does not match the requested dataset revision/row")
    require(type(image["width"]) is int and type(image["height"]) is int
            and image["width"] > 0 and image["height"] > 0, "Invalid image dimensions")
    return {"row_index": index, "source_label": label, "url": url,
            "width": image["width"], "height": image["height"]}


def select_rows(total, count, seed):
    require(1 <= count <= min(total, 1000), "--count must be 1..1000 and fit in the selected split")
    # Uniform row sample, NOT first-N (the source is class-ordered).
    return sorted(random.Random(seed).sample(range(total), count))


def validate_image(data, width, height):
    with Image.open(io.BytesIO(data)) as image:
        require(image.size == (width, height) and width * height <= 20_000_000,
                "Downloaded image dimensions do not match metadata or exceed the safety bound")
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp"}.get(image.format)
        require(suffix is not None, "Unsupported downloaded image format")
        image.load()
    return suffix


def download(client, details, output, count, seed, resume=False, max_total_mb=100):
    indices = select_rows(details["total_rows"], count, seed)
    plan = {"schema": "imagenet100_development_download_v1", "dataset": DATASET,
            "revision": details["revision"], "split": details["split"],
            "count": count, "seed": seed, "row_indices": indices,
            "model_labels_sha256": details["model_labels_sha256"],
            "label_map": details["label_map"], "max_total_mb": max_total_mb,
            "scope": "PUBLIC_DEVELOPMENT_ONLY; no formal quality or holdout disjointness claim"}
    if output.exists():
        require(resume, "Output exists; use --resume for this exact plan or choose a new folder")
        require(not (output / "dataset_manifest.json").exists(), "Dataset already complete; preserve it")
        previous = json.loads((output / "download_plan.json").read_text(encoding="utf-8"))
        require(previous == plan, "Resume settings or source revision differ from the saved plan")
    else:
        require(not resume, "--resume needs an existing partial download folder")
        output.mkdir(parents=True, exist_ok=False)
        (output / "images").mkdir()
        (output / ".state").mkdir()
        write_json(output / "download_plan.json", plan)
    records, seen, total_bytes = [], set(), 0
    for number, index in enumerate(indices, 1):
        receipt = output / ".state" / f"row_{index:07d}.json"
        if receipt.exists():
            record = json.loads(receipt.read_text(encoding="utf-8"))
            mapped = details["label_map"][record["source_label"]]
            require(record["row_index"] == index and record["label"] == mapped["label"]
                    and record["wnid"] == mapped["wnid"], "Invalid saved row receipt")
            path = (output / record["image"]).resolve()
            require(path.is_relative_to((output / "images").resolve()), "Saved image path escapes output")
            data = path.read_bytes()
            require(sha(data) == record["image_sha256"], "Saved image checksum mismatch")
            validate_image(data, record["width"], record["height"])
        else:
            row = fetch_row(client, details["split"], index, details["revision"],
                            details["total_rows"], details["names"])
            remaining = int(max_total_mb * 1024 * 1024) - total_bytes
            require(remaining > 0, "Total download byte budget exhausted")
            data = client.get(row["url"], max_bytes=min(8 * 1024 * 1024, remaining))
            suffix = validate_image(data, row["width"], row["height"])
            relative = f"images/row_{index:07d}{suffix}"
            path = output / relative
            if path.exists():
                require(path.read_bytes() == data, "Partial image differs; refusing overwrite")
            else:
                with path.open("xb") as stream:
                    stream.write(data)
            mapped = details["label_map"][row["source_label"]]
            record = {"image": relative, "image_sha256": sha(data), "bytes": len(data),
                      "row_index": index, "source_label": row["source_label"],
                      "label": mapped["label"], "wnid": mapped["wnid"], "class_name": mapped["model_name"],
                      "width": row["width"], "height": row["height"],
                      "asset_url_without_signature": row["url"].split("?", 1)[0]}
            write_json(receipt, record)
        require(record["image_sha256"] not in seen, "Duplicate image content in selected rows; not silently replaced")
        seen.add(record["image_sha256"])
        total_bytes += len(data)
        require(total_bytes <= max_total_mb * 1024 * 1024, "Total image byte budget exceeded")
        records.append(record)
        print(f"Downloaded/verified {number}/{count}: row={index} "
              f"source_label={record['source_label']} -> model_label={record['label']}", flush=True)
    require(client.json(HUB_API)["sha"] == details["revision"], "Dataset revision changed during download")
    label_fields = ["image", "label", "source_label", "wnid", "class_name", "row_index", "image_sha256"]
    labels_path = output / "labels.csv"
    # If interrupted after CSV publication, resume checks it byte-for-byte.
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=label_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    csv_bytes = text.getvalue().encode("utf-8-sig")
    if labels_path.exists():
        require(labels_path.read_bytes() == csv_bytes, "Existing labels.csv differs; refusing overwrite")
    else:
        with labels_path.open("xb") as stream:
            stream.write(csv_bytes)
    manifest = {"status": "COMPLETE_PUBLIC_DEVELOPMENT_SUBSET", "plan": plan,
                "rows": records, "downloaded_image_bytes": total_bytes,
                "selected_class_count": len({r["label"] for r in records}),
                "class_counts": dict(sorted(Counter(r["label"] for r in records).items())),
                "labels_csv_sha256": sha(csv_bytes),
                "sampling": "Uniform random distinct row indices; not class-balanced; order sorted by source row",
                "image_source": "Hugging Face Dataset Viewer cached assets; may be re-encoded",
                "upstream_resolution": "Dataset publisher resized short side to 160 pixels",
                "holdout_overlap": "NOT_CHECKED; no access to project private/reserved material",
                "license_url": LICENSE_URL}
    write_json(output / "dataset_manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "images": count,
                      "classes_covered": manifest["selected_class_count"],
                      "labels": str(labels_path), "image_bytes": total_bytes}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "inputs/imagenet100_dev100")
    parser.add_argument("--archive", type=Path, default=HERE / "inputs/h576-runtime-public-ui.tar.gz",
                        help="Original local H576 package containing its ImageNet-1k labels")
    parser.add_argument("--split", choices=("train", "validation"), default="train")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--max-total-mb", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true", help="Check metadata/mapping only; no images or local writes")
    parser.add_argument("--accept-dataset-terms", action="store_true", help="Confirm you have read and accept dataset terms")
    args = parser.parse_args(argv)
    try:
        require(1 <= args.count <= 1000 and args.max_total_mb > 0, "Invalid count or byte budget")
        if not args.check:
            require(args.accept_dataset_terms, "Read " + LICENSE_URL + " then pass --accept-dataset-terms")
        client = HttpClient()
        details = metadata(client, args.split, args.archive.resolve())
        select_rows(details["total_rows"], args.count, args.seed)
        if args.check:
            mapped = details["label_map"][details["sample_source_label"]]
            print(json.dumps({"status": "CHECK_PASS_NO_IMAGE_DOWNLOAD_OR_LOCAL_WRITES",
                              "dataset": DATASET, "revision": details["revision"],
                              "split": args.split, "available_rows": details["total_rows"],
                              "mapped_classes": len(details["label_map"]),
                              "example_mapping": mapped, "selected_count": args.count,
                              "note": "100 classes is not 100 images; source short side is 160 pixels"}, indent=2))
            return 0
        download(client, details, args.output.resolve(), args.count, args.seed,
                 args.resume, args.max_total_mb)
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("No model or board changes. Partial files are preserved; use --resume with identical settings.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
