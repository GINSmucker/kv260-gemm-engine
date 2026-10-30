"""Device-free B24 input integrity/format tests; temporary fixtures only."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b24_prepare_inputs", ROOT/"prepare_inputs.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def temporary_directory():
    # Keep test fixtures inside the writable workspace, not a sandbox-owned
    # Windows TEMP alias. TemporaryDirectory removes only its own new fixture.
    return tempfile.TemporaryDirectory(prefix=".b24-input-test-", dir=ROOT.parent)


def tiny_sparse():
    pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    values = [(0, -128), (127, -1), (-2, 3), (4, -5), (6, 7), (-8, 9)]
    dense = bytearray(4*6)
    codes = [a | (b << 2) for a, b in pairs]
    data = bytes(v & 255 for pair in values for v in pair) + bytes(4)
    meta = bytes(codes[i] | (codes[i+1] << 4) for i in (0, 2, 4)) + bytes(13)
    for col, ((a, b), (x, y)) in enumerate(zip(pairs, values)):
        dense[a*6+col] = x & 255
        dense[b*6+col] = y & 255
    return data, meta, bytes(dense)


class CodecTests(unittest.TestCase):
    def test_six_codes_signed_bytes_and_retained_zero(self):
        data, meta, expected = tiny_sparse()
        actual, report = p.decode_sparse(data, meta, 4, 6)
        self.assertEqual(actual, expected)
        self.assertEqual(set(report["position_codes"]), {"4", "8", "9", "12", "13", "14"})
        self.assertEqual(report["records"], 6)
        self.assertEqual(report["retained_zero_values"], 1)

    def test_document_low_nibble_and_low_byte_order(self):
        data = bytes.fromhex("fb 07 03 fe") + bytes(12)
        meta = bytes.fromhex("8d") + bytes(15)
        rebuilt, _ = p.decode_sparse(data, meta, 4, 2)
        self.assertEqual(rebuilt, bytes.fromhex("00 03 fb 00 00 fe 07 00"))

    def test_duplicate_or_descending_positions_refused(self):
        data, meta, _ = tiny_sparse()
        for invalid in (0, 1, 2, 3, 5, 6, 7, 10, 11, 15):
            with self.subTest(code=invalid), self.assertRaisesRegex(ValueError, "position code"):
                p.decode_sparse(data, bytes([(meta[0]&240)|invalid])+meta[1:], 4, 6)

    def test_sparse_truncated_data_refused(self):
        data, meta, _ = tiny_sparse()
        with self.assertRaisesRegex(ValueError, "physical length"):
            p.decode_sparse(data[:-1], meta, 4, 6)

    def test_sparse_truncated_meta_refused(self):
        data, meta, _ = tiny_sparse()
        with self.assertRaisesRegex(ValueError, "physical length"):
            p.decode_sparse(data, meta[:-1], 4, 6)

    def test_sparse_nonzero_data_tail_refused(self):
        data, meta, _ = tiny_sparse()
        with self.assertRaisesRegex(ValueError, "tail padding"):
            p.decode_sparse(data[:-1]+b"\x01", meta, 4, 6)

    def test_sparse_nonzero_meta_tail_refused(self):
        data, meta, _ = tiny_sparse()
        with self.assertRaisesRegex(ValueError, "tail padding"):
            p.decode_sparse(data, meta[:-1]+b"\x01", 4, 6)

    def test_odd_record_unused_high_nibble_refused(self):
        data, meta = bytes([1, 2])+bytes(14), bytes([4])+bytes(15)
        p.decode_sparse(data, meta, 4, 1)
        with self.assertRaisesRegex(ValueError, "unused high nibble"):
            p.decode_sparse(data, bytes([0x44])+bytes(15), 4, 1)

    def test_invalid_sparse_shape_refused(self):
        for k, n in ((0, 6), (5, 6), (4, 0), (True, 6), (4, 1.0)):
            with self.subTest(k=k, n=n), self.assertRaisesRegex(ValueError, "shape"):
                p.decode_sparse(b"", b"", k, n)


class ArchiveSafetyTests(unittest.TestCase):
    def archive(self, names):
        buffer = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w") as archive:
            warnings.simplefilter("ignore", UserWarning)
            for name in names:
                archive.writestr(name, b"x")
        buffer.seek(0)
        return zipfile.ZipFile(buffer)

    def test_regular_member_allowed(self):
        with self.archive([p.ARCHIVE_ROOT+"/README.md"]) as archive:
            self.assertEqual(len(p.inspect_entries(archive)), 1)

    def test_traversal_absolute_separator_and_device_paths_refused(self):
        invalid = ["../evil", "/abs", "C:/evil", p.ARCHIVE_ROOT+"/a/../evil",
                   p.ARCHIVE_ROOT+"//evil", p.ARCHIVE_ROOT+"/a\\evil", p.ARCHIVE_ROOT+"/CON.txt",
                   p.ARCHIVE_ROOT+"/bad.", "WRONG_ROOT/member"]
        for name in invalid:
            with self.subTest(name=name), self.archive([p.ARCHIVE_ROOT+"/placeholder"]) as archive:
                # Windows ZipInfo normalizes backslashes while creating ZIPs;
                # emulate an externally created raw central-directory name.
                archive.infolist()[0].filename = name
                with self.assertRaises(ValueError):
                    p.inspect_entries(archive)

    def test_duplicate_and_case_collision_refused(self):
        for second in ("same", "SAME"):
            with self.subTest(second=second), self.archive([p.ARCHIVE_ROOT+"/same", p.ARCHIVE_ROOT+"/"+second]) as archive:
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    p.inspect_entries(archive)

    def test_symlink_and_special_member_refused(self):
        for kind in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO):
            with self.subTest(kind=kind), self.archive([p.ARCHIVE_ROOT+"/member"]) as archive:
                archive.infolist()[0].external_attr = (kind | 0o600) << 16
                with self.assertRaisesRegex(ValueError, "linked/special"):
                    p.inspect_entries(archive)

    def test_encrypted_member_refused(self):
        with self.archive([p.ARCHIVE_ROOT+"/member"]) as archive:
            archive.infolist()[0].flag_bits |= 1
            with self.assertRaisesRegex(ValueError, "encrypted"):
                p.inspect_entries(archive)

    def test_oversized_member_refused(self):
        with self.archive([p.ARCHIVE_ROOT+"/member"]) as archive:
            archive.infolist()[0].file_size = 129*1024*1024
            with self.assertRaisesRegex(ValueError, "oversized ZIP member"):
                p.inspect_entries(archive)

    def test_duplicate_json_and_nonfinite_refused(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                p.json_object(raw, "test")

    def test_wrong_zip_is_rejected_before_creating_output(self):
        with temporary_directory() as directory:
            root = Path(directory)
            source = root/"not-authorized.zip"
            source.write_bytes(b"not the pinned ZIP")
            out = root/"new-data"
            with self.assertRaisesRegex(ValueError, "ZIP SHA256"):
                p.prepare(source, out)
            self.assertFalse(out.exists())

    def test_existing_output_preserved_without_reading_archive(self):
        with temporary_directory() as directory:
            out = Path(directory)/"existing"
            out.mkdir()
            (out/"user.txt").write_text("preserve", encoding="utf-8")
            with mock.patch.object(p, "audit_zip", side_effect=AssertionError("must not read ZIP")):
                with self.assertRaisesRegex(ValueError, "output exists"):
                    p.prepare("missing.zip", out)
            self.assertEqual((out/"user.txt").read_text(), "preserve")


@unittest.skipUnless((ROOT/"data/manifest.json").is_file(), "prepare fixed data before integration tests")
class FixedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixed = ROOT/"data"
        cls.raw = {name: (cls.fixed/name).read_bytes() for name in p.SELECTED}

    def copied(self, temp):
        return Path(shutil.copytree(self.fixed, Path(temp)/"data"))

    def test_fixed_copied_files_and_format_verify_without_numpy(self):
        with mock.patch.dict(sys.modules, {"numpy": None}), mock.patch.object(p, "golden_check", side_effect=AssertionError("stdlib verify")):
            result = p.verify_data(self.fixed)
        self.assertEqual(result["physical_PL_starts"], 0)
        self.assertEqual(result["payloads"], 6)
        self.assertEqual(result["arithmetic"]["status"], "NOT_RERUN_STDLIB_VERIFY")

    def test_selected_inventory_contains_no_pt_npz_or_executable(self):
        self.assertEqual(len(p.SELECTED), 11)
        self.assertTrue(all(Path(name).suffix in (".bin", ".json", ".sha256") for name in p.SELECTED))

    def test_independent_full_k_golden_match(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("NumPy only required for optional arithmetic verification")
        result = p.golden_check(self.raw)
        self.assertEqual(result["elements"], 113472)
        self.assertEqual(result["mismatches"], 0)

    def test_changed_golden_rejected_by_independent_dot(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("NumPy only required for optional arithmetic verification")
        raw = dict(self.raw)
        name = p.PAYLOAD_FILES["Y_golden"]
        raw[name] = bytes([raw[name][0] ^ 1])+raw[name][1:]
        with self.assertRaisesRegex(ValueError, "INT64 full-K dot"):
            p.golden_check(raw)

    def test_changed_payload_refused_even_with_updated_local_manifest_hash(self):
        with temporary_directory() as temporary:
            data = self.copied(temporary)
            target = data/"X.bin"
            changed = target.read_bytes()
            changed = bytes([changed[0] ^ 1])+changed[1:]
            target.write_bytes(changed)
            manifest = json.loads((data/"manifest.json").read_bytes())
            manifest["payloads"]["X"].update(p.identity(changed))
            (data/"manifest.json").write_bytes(p.json_bytes(manifest))
            with self.assertRaisesRegex(ValueError, "copied source/payload changed"):
                p.verify_data(data)

    def test_changed_b_manifest_and_sidecar_refused(self):
        with temporary_directory() as temporary:
            data = self.copied(temporary)
            target = data/"source/B_MANIFEST.json"
            changed = target.read_bytes()+b" "
            target.write_bytes(changed)
            (data/"source/B_MANIFEST.sha256").write_text(p.sha(changed)+"  MANIFEST.json\n")
            with self.assertRaisesRegex(ValueError, "B original manifest identity"):
                p.verify_data(data)

    def test_changed_source_case_refused(self):
        raw = dict(self.raw)
        case = json.loads(raw["source/B_CASE.json"])
        case["M"] = 198
        raw["source/B_CASE.json"] = p.json_bytes(case)
        with self.assertRaisesRegex(ValueError, "copied source/payload changed"):
            p.check_sources(raw)

    def test_changed_local_mode_contract_refused(self):
        with temporary_directory() as temporary:
            data = self.copied(temporary)
            manifest = json.loads((data/"manifest.json").read_bytes())
            manifest["modes"] = [1]
            (data/"manifest.json").write_bytes(p.json_bytes(manifest))
            with self.assertRaisesRegex(ValueError, "manifest contract"):
                p.verify_data(data)

    def test_unexpected_extra_file_or_directory_refused(self):
        for directory in (False, True):
            with self.subTest(directory=directory), temporary_directory() as temporary:
                data = self.copied(temporary)
                extra = data/"unexpected"
                extra.mkdir() if directory else extra.write_bytes(b"not allowed")
                with self.assertRaises(ValueError):
                    p.verify_data(data)

    def test_missing_payload_refused(self):
        with temporary_directory() as temporary:
            data = self.copied(temporary)
            (data/"W_sparse_meta.bin").unlink()
            with self.assertRaisesRegex(ValueError, "missing data member"):
                p.verify_data(data)

    def test_dense_meta_must_not_contain_data(self):
        raw = dict(self.raw)
        raw["W_dense_meta.bin"] = b"\x01"+bytes(15)
        with self.assertRaisesRegex(ValueError, "Dense metadata dummy"):
            p.check_payloads(raw)

    def test_sparse_reconstruction_must_use_same_dense_w(self):
        raw = dict(self.raw)
        raw["W_dense_data.bin"] = bytes([raw["W_dense_data.bin"][0] ^ 1])+raw["W_dense_data.bin"][1:]
        with self.assertRaisesRegex(ValueError, "SAME pruned Dense"):
            p.check_payloads(raw)

    def test_truncated_payload_refused(self):
        raw = dict(self.raw)
        raw["W_sparse_data.bin"] = raw["W_sparse_data.bin"][:-1]
        with self.assertRaisesRegex(ValueError, "payload length"):
            p.check_payloads(raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
