"""SYNTHETIC rejection-path tests only; no Vivado, implementation or board.

All fixture files are explicitly synthetic and temporary. HWH/PS parsing is
mocked here because its real checks have separate tests. These tests must never
produce a successful logic receipt or be presented as RTL execution evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_logic as verify


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class VerifyLogicRefusalTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="SYNTHETIC_verify_logic_", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "SYNTHETIC_PROJECT_ONLY"
        self.run = self.root / "build/logic_SYNTHETIC_TEST_ONLY"
        self.run.mkdir(parents=True)
        self.receipt = self.run / "logic_verified.json"
        self.frozen = self.root / "input/frozen_source.txt"
        self.put(self.frozen, b"SYNTHETIC SOURCE FIXTURE ONLY; NOT HARDWARE\n")
        self.recipe = dict(schema="opt50_system_recipe_v1", status="PREPARED_NOT_RUN",
                           simulation_model="LOCAL_ARB_FIXED_V1", real_case_profile="full48",
                           synthetic_test_only=True,
                           files={"input/frozen_source.txt": dict(bytes=self.frozen.stat().st_size,
                                                                 sha256=sha(self.frozen.read_bytes()))})
        recipe_raw = json_bytes(self.recipe)
        self.put(self.root / "project_manifest.json", recipe_raw)
        fingerprint = sha((sha(recipe_raw) + "|" + self.run.name + "|2026.1").encode())
        self.tags = [int(fingerprint[:8], 16), int(fingerprint[8:16], 16)]
        self.identity = dict(schema="opt50_system_run_v1", stage="logic", tool_version="2026.1",
                             run_id=self.run.name, recipe_sha256=sha(recipe_raw), clock_hz=125000000,
                             build_fingerprint=fingerprint, observer_tags=self.tags,
                             metadata_uuid=str(uuid.UUID(fingerprint[:32])), synthetic_test_only=True)
        self.write_identity()
        for marker in ("bd_pass.txt", "board_logic_pass.txt"):
            self.put(self.run / marker, b"PASS\n")
        self.put(self.run / "vivado_console.log", b"SYNTHETIC ONLY; NO VIVADO EXECUTED\n")
        self.log = self.synthetic_log()
        self.put(self.run / "board_logic_simulate.log", self.log.encode())
        self.put(self.run / "board_logic_elaborate.bat",
                 b"REM SYNTHETIC ONLY; NO XELAB EXECUTED\n"
                 b"call xelab -L opt50_ps_vip_fixed -L zynq_ultra_ps_e_vip_v1_0_23\n")
        self.put(self.run / "board_logic_elaborate_original.bat",
                 b"REM SYNTHETIC ONLY; NO XELAB EXECUTED\n"
                 b"call xelab -L zynq_ultra_ps_e_vip_v1_0_23\n")
        for step in ("compile", "local_model", "elaborate", "simulate"):
            self.put(self.run / f"board_logic_step_{step}.log",
                     b"SYNTHETIC TEST FIXTURE ONLY; NO TOOL EXECUTED\n")
        self.put(self.run / "simulation_model_scope.txt",
                 b"LOCAL_ARB_FIXED_V1\n36_REQUEST_PUBLICATIONS_MOVED_AFTER_PAYLOAD\n"
                 b"THREE_INSTANCES_DDR_OCM_REG\nOCM_REG_BUSINESS_TRAFFIC_NOT_QUALIFIED\n"
                 b"PROJECT_LOCAL_SIMULATION_LIBRARY_ONLY\nORIGINAL_VENDOR_MODEL_FAIL_PRESERVED\n"
                 b"NO_HARDWARE_RTL_CHANGE\nFULL48_PLUS_TWO_TINY_CASES\nNOT_BOARD_ACCEPTANCE\n")
        for name in ("stage_scope.txt", "reset_contract.txt", "reset_properties.txt", "ps_properties.txt"):
            self.put(self.run / name, b"SYNTHETIC TEST FIXTURE ONLY\n")
        self.hwh = self.run / "project/opt50_kv260.gen/sources_1/bd/kv260_opt50/hw_handoff/kv260_opt50.hwh"
        self.put(self.hwh, b"<SYNTHETIC_TEST_FIXTURE_ONLY/>\n")
        self.put(self.root / "input/opt50_abi.json", b'{"synthetic_test_only": true}\n')
        self.put(self.root / "input/board_files/kv260/1.1/preset_s4.xml", b"<SYNTHETIC_ONLY/>\n")
        self.vendor = self.root / "SYNTHETIC_VENDOR_FIXTURE_ONLY/vendor.sv"
        self.params = self.vendor.parent / verify.PARAMS
        self.put(self.vendor, b"SYNTHETIC ONLY; NOT AMD SOURCE\n")
        self.put(self.params, b"SYNTHETIC ONLY; NOT AMD PARAMETERS\n")
        self.put(self.root / "input/sim_model/patch_manifest.json",
                 json_bytes(dict(original_vendor_source=str(self.vendor),
                                 original_source_sha256=verify.SOURCE_SHA, synthetic_test_only=True)))
        self.cases = dict(job_count=48, INT32_outputs=1870848,
                          package_manifest_sha256="6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf",
                          synthetic_test_only=True)
        self.write_cases()
        # Deliberate parser boundaries: NO generated HWH or actual PS report is
        # claimed by this unit suite. The refusal checks remain unmocked.
        self.hwh_mock = mock.patch.object(verify, "check_hwh", return_value={"SYNTHETIC_ONLY": True})
        self.ps_mock = mock.patch.object(verify, "check_ps", return_value=39)
        self.hwh_mock.start(); self.ps_mock.start()
        self.addCleanup(self.hwh_mock.stop); self.addCleanup(self.ps_mock.stop)

    @staticmethod
    def put(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def write_identity(self):
        self.put(self.run / "run_manifest.json", json_bytes(self.identity))

    def write_cases(self):
        self.put(self.root / "input/sim_cases/real_cases_manifest.json", json_bytes(self.cases))

    def synthetic_log(self):
        lines = ["SYNTHETIC TEST FIXTURE ONLY; NO XSIM EXECUTED"]
        for suffix in ("ddrc.ddr_write_ports", "ocm_interconnect.write_ports",
                       "reg_interconnect.write_ports"):
            lines.append("OPT50_PSVIP_ARB_FIXED_V1 changes=36 instance="
                         "tb_opt50_board_logic.dut.kv260_opt50_i.ps.inst." + suffix)
        jobs = 0
        for layer in (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47):
            k, n = (192, 576) if layer % 4 == 2 else (576, 192)
            for kind, m in (("public", 197), ("edge", 6)):
                for mode in (0, 1):
                    jobs += 1
                    lines.append(f"OPT50_REAL_CASE_OUTPUT_MATCH case=layer_{layer}_{kind} mode={mode} "
                                 f"M={m} K={k} N={n} elements={m*n} jobs={jobs}")
        lines += ["OPT50_REAL_CASES_COMPLETED profile=full48 jobs=48 PL_STARTS=48 INT32=1870848 "
                  "PACKAGE_SHA256=6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf BOARD_NOT_RUN",
                  f"OPT50_BOARD_LOGIC_RTL_PASS checks=3000000 TAG0={self.tags[0]} TAG1={self.tags[1]} "
                  "PL_STARTS=50 REAL_JOBS=48 INT32=1870848 MODEL=LOCAL_ARB_FIXED_V1"]
        return "\n".join(lines) + "\n"

    def refused(self, pattern=None):
        with self.assertRaisesRegex((ValueError, KeyError, OSError), pattern or ".+"):
            verify.verify(self.root, self.run)
        self.assertFalse(self.receipt.exists(), "a rejected synthetic run must not create a receipt")

    def mock_only_vendor_identity(self):
        actual_digest = verify.digest

        def synthetic_digest(path):
            if path == self.vendor:
                return verify.SOURCE_SHA
            if path == self.params:
                return verify.PARAMS_SHA
            return actual_digest(path)
        return mock.patch.object(verify, "digest", side_effect=synthetic_digest)

    def test_run_must_be_immediate_child(self):
        original = self.run
        self.run = self.run / "nested"
        self.run.mkdir()
        self.refused("direct child")
        self.assertFalse((original / "logic_verified.json").exists())

    def test_existing_receipt_preserved(self):
        original = b"SYNTHETIC PREVIOUS RECEIPT; PRESERVE\n"
        self.put(self.receipt, original)
        with self.assertRaisesRegex(ValueError, "preserve"):
            verify.verify(self.root, self.run)
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_wrong_recipe_model_or_profile(self):
        for field, value in (("schema", "wrong"), ("status", "PASS"),
                             ("simulation_model", "ORIGINAL_AMD_MODEL"),
                             ("real_case_profile", "tiny_only")):
            altered = dict(self.recipe); altered[field] = value
            self.put(self.root / "project_manifest.json", json_bytes(altered))
            with self.subTest(field=field):
                self.refused("recipe")

    def test_changed_frozen_source(self):
        self.put(self.frozen, b"changed synthetic source\n")
        self.refused("frozen source changed")

    def test_wrong_stage_version_clock_or_run(self):
        original = copy.deepcopy(self.identity)
        for field, value in (("stage", "build"), ("tool_version", "2025.2"),
                             ("clock_hz", 100000000), ("run_id", "other_run")):
            self.identity = copy.deepcopy(original); self.identity[field] = value
            self.write_identity()
            with self.subTest(field=field):
                self.refused("Stage logic")

    def test_wrong_identity_chain(self):
        self.identity["observer_tags"][0] ^= 1
        self.write_identity()
        self.refused("identity chain")

    def test_stage_marker_not_pass(self):
        self.put(self.run / "board_logic_pass.txt", b"FAILED\n")
        self.refused("stage marker")

    def test_failure_marker_refused(self):
        self.put(self.run / "failure.txt", b"SYNTHETIC FAILURE\n")
        self.refused("failed run")

    def test_implementation_artifacts_refused(self):
        (self.run / "artifacts").mkdir()
        self.refused("implementation artifacts")

    def test_build_marker_refused(self):
        self.put(self.run / "build_pass.txt", b"PASS WNS=1 WHS=1\n")
        self.refused("implementation artifacts")

    def test_console_implementation_or_failure_refused(self):
        for text in ("Launched synth_1", "Launched impl_1", "OPT50_SYSTEM_STAGE_FAIL: synthetic"):
            self.put(self.run / "vivado_console.log", text.encode())
            with self.subTest(text=text):
                self.refused("launched implementation or failed")

    def test_rtl_failure_even_with_pass_marker_refused(self):
        self.put(self.run / "board_logic_simulate.log", (self.log + "Fatal: synthetic\n").encode())
        self.refused("reported failure")

    def test_duplicate_model_banner_refused(self):
        banner = "OPT50_PSVIP_ARB_FIXED_V1 changes=36 instance=SYNTHETIC_DUPLICATE\n"
        self.put(self.run / "board_logic_simulate.log", (banner + self.log).encode())
        self.refused("exactly three DDR/OCM/REG")

    def test_missing_one_of_three_model_instances_refused(self):
        altered = "\n".join(line for line in self.log.splitlines()
                            if not line.endswith("ocm_interconnect.write_ports")) + "\n"
        self.put(self.run / "board_logic_simulate.log", altered.encode())
        self.refused("exactly three DDR/OCM/REG")

    def test_wrong_model_hierarchy_refused(self):
        altered = self.log.replace("ps.inst.ddrc.ddr_write_ports", "ps.inst.SYNTHETIC_WRONG.ddr_write_ports")
        self.put(self.run / "board_logic_simulate.log", altered.encode())
        self.refused("instance set differs")

    def test_wrong_terminal_tags_refused(self):
        altered = self.log.replace(f"TAG0={self.tags[0]}", f"TAG0={self.tags[0] ^ 1}")
        self.put(self.run / "board_logic_simulate.log", altered.encode())
        self.refused("same-build")

    def test_missing_or_reordered_real_case_refused(self):
        lines = self.log.splitlines()
        first = next(index for index, line in enumerate(lines)
                     if line.startswith("OPT50_REAL_CASE_OUTPUT_MATCH"))
        for variant in (lines[:first] + lines[first+1:],
                        lines[:first] + [lines[first+1], lines[first]] + lines[first+2:]):
            self.put(self.run / "board_logic_simulate.log", ("\n".join(variant) + "\n").encode())
            with self.subTest(first=variant[first]):
                self.refused("48 individual|identity/order")

    def test_missing_local_model_library_refused(self):
        self.put(self.run / "board_logic_elaborate.bat", b"REM SYNTHETIC; xelab -L original_only\n")
        self.refused("library FIRST")

    def test_local_library_must_be_first_refused(self):
        self.put(self.run / "board_logic_elaborate.bat",
                 b"REM SYNTHETIC ONLY\ncall xelab -L zynq_ultra_ps_e_vip_v1_0_23 -L opt50_ps_vip_fixed\n")
        self.refused("library FIRST")

    def test_elaboration_cannot_change_other_arguments(self):
        path = self.run / "board_logic_elaborate.bat"
        self.put(path, path.read_bytes().replace(b"-L zynq_ultra_ps_e_vip_v1_0_23",
                                                b"-L zynq_ultra_ps_e_vip_v1_0_23 -generic_top TAG0=0"))
        self.refused("differs beyond reviewed")

    def test_missing_simulation_step_stdout_refused(self):
        (self.run / "board_logic_step_local_model.log").unlink()
        self.refused()

    def test_step_failure_even_with_terminal_pass_refused(self):
        self.put(self.run / "board_logic_step_elaborate.log", b"ERROR: SYNTHETIC ELABORATION FAILURE\n")
        self.refused("step reported failure")

    def test_scope_cannot_be_board_acceptance(self):
        scope = self.run / "simulation_model_scope.txt"
        self.put(scope, scope.read_bytes().replace(b"NOT_BOARD_ACCEPTANCE", b"BOARD_ACCEPTED"))
        self.refused("scope evidence")

    def test_ambiguous_current_handoff_refused(self):
        other = self.run / "project/opt50_kv260.srcs/sources_1/bd/kv260_opt50/hw_handoff/kv260_opt50.hwh"
        self.put(other, b"<SECOND_SYNTHETIC_FIXTURE/>\n")
        self.refused("unique current generated")

    def test_changed_vendor_source_refused(self):
        self.refused("AMD installed source identity changed")

    def test_wrong_package_count_refused(self):
        self.cases["job_count"] = 47
        self.write_cases()
        with self.mock_only_vendor_identity():
            self.refused("wrong real-case package")

    def test_missing_evidence_does_not_create_partial_receipt(self):
        (self.run / "reset_contract.txt").unlink()
        with self.mock_only_vendor_identity():
            self.refused()


if __name__ == "__main__":
    unittest.main()
