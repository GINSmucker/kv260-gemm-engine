"""Offline downloader tests: synthetic HTTP responses and synthetic images only."""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from download_imagenet100 import (
    CONFIG, DATASET, HUB_API, VIEWER, build_label_map, download,
    fetch_row, select_rows, sha_file, validate_image,
)
from eval_models import load_samples


REVISION = "a" * 40


def fixtures():
    labels = [{"index": i, "wnid": f"n{i:08d}", "name": f"Canonical class {i}"}
              for i in range(1000)]
    names = [labels[(i * 7 + 439) % 1000]["name"] for i in range(100)]
    mapping = build_label_map(names, labels)
    return {"dataset": DATASET, "config": CONFIG, "revision": REVISION,
            "split": "train", "total_rows": 126689, "names": names,
            "model_labels_sha256": "b" * 64, "label_map": mapping}


class FakeClient:
    def __init__(self, details, fail_after=None, duplicate_images=False):
        self.details, self.fail_after = details, fail_after
        self.downloads = 0
        self.duplicate_images = duplicate_images

    def json(self, endpoint, **params):
        if endpoint == HUB_API:
            return {"id": DATASET, "sha": REVISION, "private": False, "gated": False}
        if endpoint == VIEWER + "/rows":
            index = params["offset"]
            url = (f"{VIEWER}/cached-assets/{DATASET}/--/{REVISION}/--/{CONFIG}/train/"
                   f"{index}/image/image.png?Expires=9999999999&Signature=synthetic")
            return {"partial": False, "num_rows_total": self.details["total_rows"],
                    "features": [{"name": "label", "type": {
                        "_type": "ClassLabel", "names": self.details["names"]}}],
                    "rows": [{"row_idx": index, "truncated_cells": [], "row": {
                        "label": index % 100, "image": {"src": url, "width": 160, "height": 161}}}]}
        raise AssertionError("Unexpected fake endpoint: " + endpoint)

    def get(self, url, max_bytes):
        if self.fail_after is not None and self.downloads >= self.fail_after:
            raise RuntimeError("Simulated network interruption")
        self.downloads += 1
        index = int(url.split("/train/", 1)[1].split("/", 1)[0])
        if self.duplicate_images:
            index = 0
        image = Image.new("RGB", (160, 161), (index & 255, (index >> 8) & 255, (index >> 16) & 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        if len(data) > max_bytes:
            raise ValueError("Response exceeds size budget")
        return data


class DownloaderTests(unittest.TestCase):
    def test_remaps_output_indices_not_subset_indices(self):
        details = fixtures()
        self.assertEqual(details["label_map"][0]["label"], 439)
        self.assertEqual(details["label_map"][1]["label"], 446)
        self.assertEqual(len({v["label"] for v in details["label_map"]}), 100)

    def test_unknown_or_ambiguous_names_rejected(self):
        labels = [{"index": i, "wnid": f"n{i:08d}", "name": f"Class {i}"} for i in range(1000)]
        names = [v["name"] for v in labels[:100]]
        names[0] = "Unknown class"
        with self.assertRaisesRegex(ValueError, "Unmatched"):
            build_label_map(names, labels)
        names[0] = "Class 0"
        labels[999]["name"] = "Class 0"
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            build_label_map(names, labels)

    def test_sampling_deterministic_unique_and_not_first_n(self):
        first = select_rows(126689, 100, 20261021)
        self.assertEqual(first, select_rows(126689, 100, 20261021))
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(len(first), 100)
        self.assertNotEqual(first, list(range(100)))
        self.assertTrue(0 <= min(first) <= max(first) < 126689)
        for count in (0, 1001, 126690):
            with self.assertRaises(ValueError):
                select_rows(126689, count, 1)

    def test_fetch_row_checks_revision_index_and_label_order(self):
        details = fixtures()
        client = FakeClient(details)
        result = fetch_row(client, "train", 123, REVISION, details["total_rows"], details["names"])
        self.assertEqual(result["source_label"], 23)
        with self.assertRaisesRegex(ValueError, "revision"):
            fetch_row(client, "train", 123, "c" * 40, details["total_rows"], details["names"])
        with self.assertRaisesRegex(ValueError, "label order"):
            fetch_row(client, "train", 123, REVISION, details["total_rows"], details["names"][::-1])

    def test_100_image_download_exports_usable_labels(self):
        with tempfile.TemporaryDirectory(prefix="h576-download-100-") as folder:
            output = Path(folder) / "dataset"
            details = fixtures()
            client = FakeClient(details)
            with contextlib.redirect_stdout(io.StringIO()):
                download(client, details, output, 100, 20261021)
            samples = load_samples(output)
            self.assertEqual(len(samples), 100)
            self.assertEqual(client.downloads, 100)
            with (output / "labels.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                self.assertEqual(int(row["label"]), details["label_map"][int(row["source_label"])]["label"])
            manifest = json.loads((output / "dataset_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "COMPLETE_PUBLIC_DEVELOPMENT_SUBSET")
            self.assertEqual(manifest["labels_csv_sha256"], sha_file(output / "labels.csv"))
            with self.assertRaisesRegex(ValueError, "already complete"):
                download(FakeClient(details), details, output, 100, 20261021, resume=True)

    def test_resume_preserves_verified_images(self):
        with tempfile.TemporaryDirectory(prefix="h576-download-resume-") as folder:
            output, details = Path(folder) / "dataset", fixtures()
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "interruption"):
                download(FakeClient(details, fail_after=2), details, output, 5, 99)
            before = {p.name: sha_file(p) for p in (output / "images").iterdir()}
            self.assertEqual(len(before), 2)
            self.assertFalse((output / "labels.csv").exists())
            client = FakeClient(details)
            with contextlib.redirect_stdout(io.StringIO()):
                download(client, details, output, 5, 99, resume=True)
            self.assertEqual(client.downloads, 3)
            for name, digest in before.items():
                self.assertEqual(sha_file(output / "images" / name), digest)
            self.assertEqual(len(load_samples(output, expected_count=5)), 5)

    def test_changed_resume_plan_and_duplicate_content_rejected(self):
        with tempfile.TemporaryDirectory(prefix="h576-download-guards-") as folder:
            output, details = Path(folder) / "dataset", fixtures()
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                download(FakeClient(details, fail_after=1), details, output, 5, 99)
            with self.assertRaisesRegex(ValueError, "settings"):
                download(FakeClient(details), details, output, 5, 100, resume=True)
            duplicate = Path(folder) / "duplicate"
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "Duplicate image"):
                download(FakeClient(details, duplicate_images=True), details, duplicate, 5, 99)
            self.assertFalse((duplicate / "labels.csv").exists())

    def test_image_dimensions_and_corruption(self):
        image = Image.new("RGB", (160, 161))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertEqual(validate_image(buffer.getvalue(), 160, 161), ".png")
        with self.assertRaisesRegex(ValueError, "dimensions"):
            validate_image(buffer.getvalue(), 161, 160)
        with self.assertRaises(Exception):
            validate_image(b"not an image", 160, 161)


if __name__ == "__main__":
    unittest.main(verbosity=2)
