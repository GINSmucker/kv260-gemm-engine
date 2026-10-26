"""Synthetic fixtures only; these tests are NOT H576 classification results."""
import contextlib
import csv
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from PIL import Image

from eval_models import (
    CSV_FIELDS, OnnxClassifier, evaluate, load_samples, main,
    preprocess_image, rank_logits, save_errors,
)


def write_labels(folder, rows):
    path = folder / "labels.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["image", "label"])
        writer.writerows(rows)
    return path


def make_image(path, number=0):
    Image.new("RGB", (17 + number % 3, 19 + number % 5),
              (number % 256, (number * 3) % 256, (number * 7) % 256)).save(path)


def constant_classifier(path, logits):
    node = helper.make_node("Constant", [], ["logits"],
                            value=numpy_helper.from_array(np.array([logits], np.float32)))
    graph = helper.make_graph(
        [node], "synthetic_eval_fixture",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, 224, 224])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, len(logits)])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
    onnx.checker.check_model(model)
    onnx.save(model, path)


class EvaluationTests(unittest.TestCase):
    def test_preprocessing_matches_torchvision(self):
        from torchvision import transforms
        from torchvision.transforms import InterpolationMode
        independent = transforms.Compose([
            transforms.Resize(248, interpolation=InterpolationMode.BICUBIC),
            transforms.CenterCrop(224), transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        rng = np.random.default_rng(123)
        with tempfile.TemporaryDirectory(prefix="h576-eval-transform-") as folder:
            for number, shape in enumerate(((301, 401, 3), (401, 301, 3), (249, 248, 3),
                                            (31, 37, 3), (257, 257, 3))):
                path = Path(folder) / f"image_{number}.png"
                Image.fromarray(rng.integers(0, 256, shape, np.uint8)).save(path)
                actual = preprocess_image(path)
                with Image.open(path) as image:
                    expected = independent(image.convert("RGB")).numpy()[None]
                np.testing.assert_array_equal(actual, expected)
                self.assertEqual(actual.shape, (1, 3, 224, 224))
                self.assertEqual(actual.dtype, np.float32)
                self.assertTrue(actual.flags.c_contiguous)

    def test_grayscale_and_transparency(self):
        with tempfile.TemporaryDirectory(prefix="h576-eval-modes-") as folder:
            for mode, color in (("L", 64), ("RGBA", (10, 20, 30, 0))):
                path = Path(folder) / (mode + ".png")
                Image.new(mode, (250, 251), color).save(path)
                value = preprocess_image(path)
                self.assertEqual(value.shape, (1, 3, 224, 224))
                self.assertTrue(np.isfinite(value).all())

    def test_ranking_ties_and_invalid_logits(self):
        np.testing.assert_array_equal(rank_logits(np.zeros((1, 6), np.float32)), [0, 1, 2, 3, 4])
        for values in (np.full((1, 6), np.nan, np.float32), np.ones((1, 4), np.float32),
                       np.ones((2, 6), np.float32), np.ones((1, 6), np.float64)):
            with self.assertRaises(ValueError):
                rank_logits(values)

    def test_paired_metrics_new_errors_and_identical_tensor(self):
        with tempfile.TemporaryDirectory(prefix="h576-eval-paired-") as folder:
            root = Path(folder)
            for i in range(4):
                make_image(root / f"{i}.png", i)
            write_labels(root, [(f"{i}.png", i) for i in range(4)])
            samples = load_samples(root, expected_count=4)
            dense_rows = [[9, 8, 7, 6, 5, 0], [0, 7, 9, 6, 5, 4],
                          [0, 9, 8, 7, 6, 5], [0, 8, 7, 9, 6, 5]]
            pruned_rows = [[0, 9, 8, 7, 6, 5], [0, 9, 8, 7, 6, 5],
                           [0, 8, 7, 9, 6, 5], [0, 8, 7, 9, 6, 5]]
            calls = {"index": 0, "tensor": None}

            def dense(tensor):
                calls["tensor"] = tensor
                return np.array([dense_rows[calls["index"]]], np.float32)

            def pruned(tensor):
                self.assertIs(tensor, calls["tensor"])
                result = np.array([pruned_rows[calls["index"]]], np.float32)
                calls["index"] += 1
                return result

            result = evaluate(samples, dense, pruned, progress_every=0)
            self.assertEqual(result["dense_top1_pct"], 50)
            self.assertEqual(result["pruned_top1_pct"], 50)
            self.assertEqual(result["dense_top5_pct"], 100)
            self.assertEqual(result["pruned_top5_pct"], 75)
            self.assertEqual(result["top1_drop_pp"], 0)
            self.assertEqual(result["recovered_errors"], 1)
            self.assertEqual([row["image"] for row in result["new_errors"]], ["0.png"])

    def test_labels_count_duplicates_and_path_escape(self):
        with tempfile.TemporaryDirectory(prefix="h576-eval-labels-") as folder:
            root = Path(folder) / "data"
            root.mkdir()
            make_image(root / "x.png")
            make_image(Path(folder) / "outside.png")
            write_labels(root, [("x.png", 0)])
            with self.assertRaisesRegex(ValueError, "exactly 100"):
                load_samples(root)
            write_labels(root, [("x.png", 0), ("x.png", 1)])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_samples(root, expected_count=2)
            write_labels(root, [("../outside.png", 0)])
            with self.assertRaisesRegex(ValueError, "inside"):
                load_samples(root, expected_count=1)
            write_labels(root, [("x.png", "dog")])
            with self.assertRaisesRegex(ValueError, "integer"):
                load_samples(root, expected_count=1)

    def test_csv_header_with_zero_new_errors_and_overwrite_guard(self):
        with tempfile.TemporaryDirectory(prefix="h576-eval-csv-") as folder:
            path = Path(folder) / "errors.csv"
            save_errors(path, [])
            before = path.read_bytes()
            self.assertTrue(before.startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, CSV_FIELDS)
                self.assertEqual(list(reader), [])
            with self.assertRaises(FileExistsError):
                save_errors(path, [])
            self.assertEqual(path.read_bytes(), before)

    def test_end_to_end_100_images_real_ort_and_csv(self):
        with tempfile.TemporaryDirectory(prefix="h576-eval-100-") as folder:
            root = Path(folder)
            dense_path, pruned_path = root / "dense.onnx", root / "pruned.onnx"
            constant_classifier(dense_path, [9, 8, 7, 6, 5, 0])
            constant_classifier(pruned_path, [0, 9, 8, 7, 6, 5])
            for i in range(100):
                make_image(root / f"image_{i:03d}.png", i)
            write_labels(root, [(f"image_{i:03d}.png", 0 if i < 60 else 1) for i in range(100)])
            output = root / "errors.csv"
            argv = ["--dense-model", str(dense_path), "--pruned-model", str(pruned_path),
                    "--data-dir", str(root), "--output-csv", str(output)]
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                self.assertEqual(main(argv), 0)
            self.assertIn("60/100 (60.00%)", log.getvalue())
            self.assertIn("40/100 (40.00%)", log.getvalue())
            self.assertIn("100/100 (100.00%)", log.getvalue())
            self.assertIn("Top1 +20.00 pp", log.getvalue())
            with output.open(encoding="utf-8-sig", newline="") as stream:
                errors = list(csv.DictReader(stream))
            self.assertEqual(len(errors), 60)
            self.assertEqual(errors[0]["image"], "image_000.png")
            self.assertTrue(all(row["dense_top1"] == "0" and row["pruned_top1"] == "1" for row in errors))
            before = output.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 1)
            self.assertEqual(before, output.read_bytes())

    def test_corrupt_image_fails_without_csv(self):
        with tempfile.TemporaryDirectory(prefix="h576-eval-corrupt-") as folder:
            root = Path(folder)
            model = root / "fixture.onnx"
            constant_classifier(model, [9, 8, 7, 6, 5, 0])
            # This test fixture is deliberately not a valid PNG.
            (root / "bad.png").write_bytes(b"not an image")
            write_labels(root, [("bad.png", 0)])
            output = root / "errors.csv"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = main(["--dense-model", str(model), "--pruned-model", str(model),
                               "--data-dir", str(root), "--expected-count", "1", "--output-csv", str(output)])
            self.assertEqual(status, 1)
            self.assertFalse(output.exists())

    def test_model_and_label_class_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="h576-eval-shapes-") as folder:
            root = Path(folder)
            model6, model7 = root / "six.onnx", root / "seven.onnx"
            constant_classifier(model6, [9, 8, 7, 6, 5, 0])
            constant_classifier(model7, [9, 8, 7, 6, 5, 0, -1])
            make_image(root / "x.png")
            write_labels(root, [("x.png", 0)])
            output = root / "errors.csv"
            argv = ["--dense-model", str(model6), "--pruned-model", str(model7),
                    "--data-dir", str(root), "--expected-count", "1", "--output-csv", str(output)]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 1)
            self.assertFalse(output.exists())
            write_labels(root, [("x.png", 6)])
            argv[3] = str(model6)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
