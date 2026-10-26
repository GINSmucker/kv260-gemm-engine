"""Python-only generation checks; no XSim, Vivado, vendor patch or device I/O."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("_real_case_generator_tests", HERE / "real_case_tb_generator.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def decode_hex(text):
    return b"".join(bytes.fromhex(line)[::-1] for line in text.splitlines())


class EncodingTests(unittest.TestCase):
    def test_byte_zero_is_low_lane_without_byte_or_word_swaps(self):
        raw = bytes(range(32))
        encoded = gen.encode_hex(raw)
        self.assertEqual(encoded.splitlines(), ["0f0e0d0c0b0a09080706050403020100",
                                               "1f1e1d1c1b1a19181716151413121110"])
        self.assertEqual(decode_hex(encoded), raw)

    def test_negative_int32_and_int8_bits_are_not_requantized(self):
        raw = bytes.fromhex("fdffffff0000008080ff017f88776655")
        self.assertEqual(decode_hex(gen.encode_hex(raw)), raw)
        word = int(gen.encode_hex(raw).strip(), 16)
        self.assertEqual(word & 0xffffffff, 0xfffffffd)
        self.assertEqual((word >> 32) & 0xffffffff, 0x80000000)

    def test_empty_partial_and_oversized_words_refused(self):
        for raw in (b"", b"a", bytes(17), bytes((gen.MAX_WORDS+1)*16)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                gen.encode_hex(raw)

    def test_largest_actual_y_size_allowed(self):
        raw = bytes(gen.MAX_WORDS*16)
        self.assertEqual(len(gen.encode_hex(raw).splitlines()), gen.MAX_WORDS)

    def test_sv_string_preserves_windows_drive_and_escapes_quote(self):
        self.assertEqual(gen.sv_string('D:/new folder/a"b.hex'), '"D:/new folder/a\\"b.hex"')
        for text in ("bad\nname", "bad\0name", "bad\rname"):
            with self.assertRaises(ValueError):
                gen.sv_string(text)

    def test_parent_tasks_and_no_completion_spoofing(self):
        sv = gen.SV_TASKS
        self.assertIn("`PSVIP.write_mem", sv)
        self.assertIn("`PSVIP.read_mem", sv)
        self.assertIn("check_idle();", sv)
        self.assertIn("csr_write(GEMM, 1); pl_starts++;", sv)
        self.assertNotRegex(sv, r"(?m)^\s*\$finish\b")
        self.assertNotRegex(sv, r"(?m)^\s*(?:force|release)\b")
        self.assertNotIn("OPT50_BOARD_LOGIC_RTL_PASS", sv)

    def test_deadline_done_return_and_monitor_guards_present(self):
        sv = gen.SV_TASKS
        self.assertIn("OPT50_REAL_CASE_LIMIT_CYCLES = 1000000", sv)
        self.assertIn("while (opt50_real_cycles < deadline)", sv)
        self.assertIn("seen_done && ((control & 32'h85) == 32'h04)", sv)
        self.assertIn("read_equals(GEMM+'h10, 0)", sv)
        self.assertIn("read_equals(STATUS+'h34, y_bytes/16)", sv)
        self.assertIn("ticks(256)", sv)

    def test_chunk_limit_and_all_four_buffers_checked(self):
        sv = gen.SV_TASKS
        self.assertIn("OPT50_REAL_API_CHUNK_WORDS = 128", sv)
        self.assertEqual(sv.count("offset += OPT50_REAL_API_CHUNK_WORDS"), 2)
        self.assertEqual(sv.count("if (count > OPT50_REAL_API_CHUNK_WORDS) count = OPT50_REAL_API_CHUNK_WORDS"), 2)
        self.assertIn("offset += OPT50_REAL_API_CHUNK_BYTES", sv)
        self.assertIn("if (count > OPT50_REAL_API_CHUNK_BYTES) count = OPT50_REAL_API_CHUNK_BYTES", sv)
        self.assertNotIn("offset += 4096", sv)
        self.assertNotIn("offset += 256", sv)
        self.assertIn("logic [32767:0] chunk", sv)
        self.assertIn("opt50_real_guards(OPT50_REAL_Y, y_bytes, 0)", sv)
        for name, address, length in (("x", "X", "x"), ("w", "W", "w"), ("meta", "META", "meta")):
            self.assertIn(f"opt50_real_input({name}_path, OPT50_REAL_{address}, {length}_bytes, 1)", sv)
            self.assertIn(f"opt50_real_input({name}_path, OPT50_REAL_{address}, {length}_bytes, 0)", sv)
        self.assertIn("real input changed", sv)
        self.assertIn("opt50_real_compared++", sv)

    def test_2048_strobe_model_covers_all_chunk_boundaries_and_full_y(self):
        # Software model of the PINNED API's zero-extended 2048-bit mask;
        # it is not a vendor simulation or an alternative arithmetic golden.
        self.assertEqual((gen.API_CHUNK_WORDS, gen.API_CHUNK_BYTES), (128, 2048))
        for size in (16, 64, 1152, 2048, 2064, 4096, 4112, 110592, 453888):
            source = bytes((i % 251 for i in range(size)))
            actual = bytearray([0xcd])*size
            chunks = []
            for offset in range(0, size, gen.API_CHUNK_BYTES):
                count = min(gen.API_CHUNK_BYTES, size-offset)
                chunks.append((offset, count))
                actual[offset:offset+min(count, 2048)] = source[offset:offset+min(count, 2048)]
            self.assertEqual(bytes(actual), source)
            self.assertTrue(all(0 < count <= 2048 and count % 16 == 0 for _, count in chunks))
            self.assertEqual(sum(count for _, count in chunks), size)
        # Establish why simply using the 32768-bit vector limit was wrong.
        bad = bytearray([0xcd])*4096
        bad[:2048] = bytes(2048)
        self.assertNotEqual(bytes(bad), bytes(4096))


class FullFrozenPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = HERE.parents[2] / "h576_sparse24/model_gemm_opt50/tb/axi_cases"
        # This read-only fixture exists in the development workspace. A copied
        # standalone project's tests may not contain the original binary pack.
        if not cls.package.is_dir():
            raise unittest.SkipTest("read-only original full48 package fixture is not present")
        temporary_parent = HERE / ".test_tmp"
        temporary_parent.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="full48_", dir=temporary_parent)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.parent = Path(cls.temporary.name)
        cls.out = cls.parent / "sim_cases"
        cls.before = gen.digest((cls.package / "manifest.json").read_bytes())
        cls.result = gen.generate(cls.package, cls.out)
        cls.sv = (cls.out / gen.INCLUDE_NAME).read_text(encoding="utf-8")

    def test_frozen_identity_inventory_and_no_simulation_claim(self):
        result = self.result
        self.assertEqual(self.before, gen.PACKAGE_SHA)
        self.assertEqual(result["status"], "GENERATED_NOT_SIMULATED")
        self.assertEqual(result["profile"], "full48")
        self.assertEqual(result["job_count"], 48)
        self.assertEqual(result["INT32_outputs"], 1870848)
        self.assertEqual(result["physical_PL_starts"], 0)
        self.assertEqual(result["simulator"], "NOT_RUN")
        self.assertEqual(result["board"], "NOT_RUN")
        self.assertEqual(result["api_chunk_words"], 128)
        self.assertEqual(result["api_chunk_bytes"], 2048)
        self.assertEqual(result["vip_api_contract"]["valid_write_strobe_bits"], 2048)
        self.assertEqual(result["vip_api_contract"]["params_sha256"], gen.VIP_PARAMS_SHA)
        self.assertEqual(result["vip_api_contract"]["apis_sha256"], gen.VIP_APIS_SHA)
        self.assertEqual(len(result["source_buffers"]), 96)
        self.assertEqual(sum(x["bytes"] for x in result["source_buffers"].values()), 6833856)
        self.assertEqual(gen.digest((self.package / "manifest.json").read_bytes()), self.before)

    def test_all_generated_files_have_exact_frozen_hash_and_length(self):
        inventory = self.result["files"]
        self.assertEqual(len(inventory), 97)
        for name, expected in inventory.items():
            self.assertEqual(gen.identity((self.out/name).read_bytes()), expected, name)

    def test_every_hex_payload_roundtrips_to_original_bin(self):
        for name, expected in self.result["source_buffers"].items():
            raw = (self.package/name).read_bytes()
            hex_text = (self.out/expected["hex"]).read_text(encoding="ascii")
            self.assertEqual(decode_hex(hex_text), raw, name)
            self.assertEqual(len(hex_text.splitlines()), expected["words"])
            self.assertEqual(gen.identity(raw), {key: expected[key] for key in ("bytes", "sha256")})

    def test_exact_12_layers_two_kinds_two_modes_with_identical_goldens(self):
        jobs = self.result["jobs"]
        self.assertEqual(len(jobs), 48)
        expected_order = [(layer, kind, mode) for layer in gen.LAYERS
                          for kind in ("public", "edge") for mode in (0, 1)]
        self.assertEqual([(j["layer_index"], j["case_id"].split("_")[-1], j["mode"]) for j in jobs], expected_order)
        for dense, sparse in zip(jobs[::2], jobs[1::2]):
            self.assertEqual(dense["buffers"]["Y"], sparse["buffers"]["Y"])
            self.assertEqual(dense["buffers"]["X"], sparse["buffers"]["X"])
            self.assertEqual(dense["buffers"]["W_data"]["bytes"], 110592)
            self.assertEqual(dense["buffers"]["W_meta"]["bytes"], 16)
            self.assertEqual(sparse["buffers"]["W_data"]["bytes"], 55296)
            self.assertEqual(sparse["buffers"]["W_meta"]["bytes"], 13824)

    def test_full_shapes_and_output_sum(self):
        jobs = self.result["jobs"]
        self.assertEqual(sum(j["M"]*j["N"] for j in jobs), gen.INT32_COUNT)
        self.assertEqual({(j["M"], j["K"], j["N"]) for j in jobs},
                         {(197, 192, 576), (6, 192, 576), (197, 576, 192), (6, 576, 192)})

    def test_include_calls_all48_and_counts_completion_not_pass(self):
        calls = re.findall(r'^\s*opt50_run_frozen_case\("(layer_[0-9]+_(?:public|edge))", (\d+), (\d+), (\d+), ([01]),', self.sv, re.M)
        self.assertEqual(len(calls), 48)
        self.assertIn("task automatic run_real_cases", self.sv)
        self.assertIn("pl_starts-starts_before == 48", self.sv)
        self.assertIn("`PSVIP.max_burst_bits == 32768", self.sv)
        self.assertIn("`PSVIP.max_burst_bytes == 4096", self.sv)
        self.assertIn("$bits(`PSVIP.all_strb_valid) == 2048", self.sv)
        self.assertIn("OPT50_REAL_API_CHUNK_BYTES <= $bits(`PSVIP.all_strb_valid)", self.sv)
        self.assertIn("opt50_real_compared == 1870848", self.sv)
        self.assertIn("OPT50_REAL_CASES_COMPLETED profile=full48", self.sv)
        self.assertNotIn("OPT50_BOARD_LOGIC_RTL_PASS", self.sv)
        self.assertNotRegex(self.sv, r"(?m)^\s*\$finish\b")
        for item in self.result["files"]:
            if item.endswith(".hex"):
                self.assertIn((self.out/item).as_posix(), self.sv)

    def test_existing_output_refused_without_change(self):
        before = (self.out/"real_cases_manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "never overwrite"):
            gen.generate(self.package, self.out)
        self.assertEqual((self.out/"real_cases_manifest.json").read_bytes(), before)

    def test_changed_manifest_rejected_before_publishing(self):
        broken = self.parent/"wrong_package"
        broken.mkdir()
        (broken/"manifest.json").write_bytes((self.package/"manifest.json").read_bytes()+b"\n")
        out = self.parent/"must_not_exist"
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            gen.generate(broken, out)
        self.assertFalse(out.exists())

    def test_output_inside_source_package_refused_before_reading(self):
        out = self.package/"not_created_by_this_test"
        self.assertFalse(out.exists())
        with mock.patch.object(gen, "package_checker", side_effect=AssertionError("must reject before auditor")):
            with self.assertRaisesRegex(ValueError, "overlaps"):
                gen.generate(self.package, out)
        self.assertFalse(out.exists())

    def test_cli_has_no_partial_profile_and_emits_no_acceptance(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["generator", "--package", str(self.package), "--out", "not_used"]), \
             mock.patch.object(gen, "generate", return_value=self.result), contextlib.redirect_stdout(output):
            gen.main()
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["profile"], "full48")
        self.assertEqual(summary["job_count"], 48)
        self.assertEqual(summary["simulator"], "NOT_RUN")

    def test_missing_case_cannot_be_relabelled_full48(self):
        auditor = gen.package_checker()
        manifest = json.loads((self.package/"manifest.json").read_bytes())
        manifest["cases"] = [c for c in manifest["cases"] if c["case_id"] != "layer_47_edge"]
        out = self.parent/"partial_must_not_exist"
        # Deliberately bypass the earlier auditor to test the generator's own
        # independent full-inventory invariant. This is only a negative test.
        with mock.patch.object(auditor, "verify_package", return_value=manifest), \
             mock.patch.object(gen, "package_checker", return_value=auditor):
            with self.assertRaisesRegex(ValueError, "exact ordered"):
                gen.generate(self.package, out)
        self.assertFalse(out.exists())

    def test_one_mode_cannot_be_relabelled_full48(self):
        auditor = gen.package_checker()
        manifest = json.loads((self.package/"manifest.json").read_bytes())
        case = next(c for c in manifest["cases"] if c["case_id"] == "layer_26_edge")
        case["modes"] = case["modes"][:1]
        out = self.parent/"one_mode_must_not_exist"
        with mock.patch.object(auditor, "verify_package", return_value=manifest), \
             mock.patch.object(gen, "package_checker", return_value=auditor):
            with self.assertRaisesRegex(ValueError, "output count"):
                gen.generate(self.package, out)
        self.assertFalse(out.exists())

    def test_standalone_auditor_import_does_not_need_workspace_search_path(self):
        auditor_raw = Path(gen.package_checker().__file__).read_bytes()
        standalone = self.parent/"standalone_snapshot"
        scripts = standalone/"scripts"
        scripts.mkdir(parents=True)
        helper = scripts/"audit_runtime_metadata.py"
        helper.write_bytes(auditor_raw)
        with mock.patch.object(gen, "SYSTEM", standalone):
            loaded = gen.package_checker()
        self.assertEqual(Path(loaded.__file__), helper)
        self.assertEqual(loaded.PACKAGE_SHA, gen.PACKAGE_SHA)
        helper.write_bytes(auditor_raw.replace(gen.PACKAGE_SHA.encode(), b"0"*64))
        with mock.patch.object(gen, "SYSTEM", standalone):
            with self.assertRaisesRegex(ValueError, "auditor identity"):
                gen.package_checker()

    def test_missing_standalone_auditor_is_explicitly_refused(self):
        with mock.patch.object(gen, "SYSTEM", self.parent/"missing_standalone"):
            with self.assertRaisesRegex(ValueError, "missing regular"):
                gen.package_checker()


if __name__ == "__main__":
    unittest.main()
