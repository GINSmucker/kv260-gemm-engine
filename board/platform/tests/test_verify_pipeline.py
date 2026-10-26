"""End-to-end SYNTHETIC build receipts; never Vivado, XRT or board evidence.

Every generated artifact stays in a temporary child of system/build and is
removed by unittest cleanup. The small .bit payload is not FPGA firmware.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import make_metadata as metadata
import verify_system as verify
import prepare_system as prep
from test_board_logic import add_logic_fixture


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_raw(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def synthetic_bit(payload):
    """Standard .bit container framing around deliberately non-firmware bytes."""
    magic = bytes.fromhex("0ff00ff00ff00ff000")
    data = struct.pack(">H", len(magic)) + magic + struct.pack(">H", 1)
    for tag, value in ((b"a", b"SYNTHETIC_TEST_ONLY;not_firmware\0"),
                       (b"b", b"xck26\0"), (b"c", b"2026/10/23\0"),
                       (b"d", b"00:00:00\0")):
        data += tag + struct.pack(">H", len(value)) + value
    return data + b"e" + struct.pack(">I", len(payload)) + payload


def swapped_words(payload):
    return b"".join(payload[i:i+4][::-1] for i in range(0, len(payload), 4))


class VerifyPipelineTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic_verify_", dir=ROOT / "build")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.run = self.project / "build/build_synthetic_test_only"
        self.artifacts = self.run / "artifacts"
        self.artifacts.mkdir(parents=True)
        abi_source = ROOT / "input/opt50_abi.json"
        if not abi_source.is_file():
            abi_source = ROOT.parent / "opt50_abi.json"
        self.abi = json.loads(abi_source.read_bytes())
        inputs = self.project / "input"
        inputs.mkdir()
        abi_file = inputs / "opt50_abi.json"
        abi_file.write_bytes(json_raw(self.abi))

        # Exercise CONFIG.-prefixed user_parameter entries and a property value
        # containing spaces, not just a synthetic whitespace-free last token.
        preset = ET.Element("ip_presets")
        params = ET.SubElement(ET.SubElement(preset, "ip_preset"), "user_parameters")
        property_rows = ["Property Type Read-only Value",
                         "CONFIG.PSU__MAXIGP0__DATA_WIDTH string false 128",
                         "CONFIG.PSU__SAXIGP2__DATA_WIDTH string false 128",
                         "CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ string false 125"]
        for index in range(39):
            name = f"CONFIG.PSU__DDRC__SYNTHETIC_{index:02d}"
            value = "synthetic value with spaces" if index == 0 else str(index)
            ET.SubElement(params, "user_parameter", name=name, value=value)
            property_rows.append(f"{name} string false {value}")
        preset_file = inputs / "board_files/kv260/1.1/preset_s4.xml"
        preset_file.parent.mkdir(parents=True)
        preset_file.write_bytes(ET.tostring(preset, encoding="utf-8", xml_declaration=True))
        recipe_files = (abi_file, preset_file)
        recipe = dict(schema="opt50_system_recipe_v1", status="PREPARED_NOT_RUN",
                      tool_version="2026.1", clock_hz=125000000, abi=self.abi["abi"],
                      board="NOT_RUN", synthetic_test_only=True,
                      files={p.relative_to(self.project).as_posix():
                             dict(bytes=p.stat().st_size, sha256=sha(p.read_bytes()))
                             for p in recipe_files})
        self.recipe_raw = json_raw(recipe)
        (self.project / "project_manifest.json").write_bytes(self.recipe_raw)
        recipe_sha = sha(self.recipe_raw)
        fingerprint = sha((recipe_sha + "|" + self.run.name + "|2026.1").encode("utf-8"))
        self.tags = [int(fingerprint[:8], 16), int(fingerprint[8:16], 16)]
        metadata_uuid = (fingerprint[:8] + "-" + fingerprint[8:12] + "-" + fingerprint[12:16] +
                         "-" + fingerprint[16:20] + "-" + fingerprint[20:32])
        self.identity = dict(schema="opt50_system_run_v1", status="VIVADO_REQUESTED_NOT_VERIFIED",
                             run_id=self.run.name, stage="build", recipe_sha256=recipe_sha,
                             tool_version="2026.1", clock_hz=125000000,
                             build_fingerprint=fingerprint, observer_tags=self.tags,
                             metadata_uuid=metadata_uuid, board="NOT_RUN")
        self.write_identity()
        (self.run / "observer_pass.txt").write_text("PASS\n", encoding="utf-8")
        (self.run / "bd_pass.txt").write_text("PASS\n", encoding="utf-8")
        (self.run / "board_logic_pass.txt").write_text("PASS\n", encoding="utf-8")
        (self.run / "board_logic_simulate.log").write_text(self.logic_log(), encoding="utf-8")
        original_elaborate = (
            "REM SYNTHETIC TEST FIXTURE ONLY; NO XELAB EXECUTED\n"
            "call xelab  -L xil_defaultlib -L zynq_ultra_ps_e_vip_v1_0_23 xil_defaultlib.tb_opt50_board_logic\n")
        (self.run / "board_logic_elaborate_original.bat").write_text(original_elaborate, encoding="utf-8")
        (self.run / "board_logic_elaborate.bat").write_text(
            original_elaborate.replace("call xelab  ", "call xelab -L opt50_ps_vip_fixed "), encoding="utf-8")
        for step in ("compile", "local_model", "elaborate", "simulate"):
            (self.run / f"board_logic_step_{step}.log").write_text(
                f"SYNTHETIC TEST FIXTURE ONLY; NO {step} EXECUTED\n", encoding="utf-8")
        (self.run / "simulation_model_scope.txt").write_text(
            "LOCAL_ARB_FIXED_V1\n36_REQUEST_PUBLICATIONS_MOVED_AFTER_PAYLOAD\n"
            "THREE_INSTANCES_DDR_OCM_REG\nOCM_REG_BUSINESS_TRAFFIC_NOT_QUALIFIED\n"
            "PROJECT_LOCAL_SIMULATION_LIBRARY_ONLY\nORIGINAL_VENDOR_MODEL_FAIL_PRESERVED\n"
            "NO_HARDWARE_RTL_CHANGE\nFULL48_PLUS_TWO_TINY_CASES\nNOT_BOARD_ACCEPTANCE\n", encoding="utf-8")
        (self.run / "reset_properties.txt").write_text("SYNTHETIC TEST FIXTURE ONLY; AUX_HIGH=1\n", encoding="utf-8")
        (self.run / "reset_contract.txt").write_text("SYNTHETIC TEST FIXTURE ONLY\n", encoding="utf-8")
        (self.run / "build_pass.txt").write_text("PASS WNS=0.704 WHS=0.055\n", encoding="utf-8")
        reports = {
            "timing_summary.rpt": "SYNTHETIC TEST ONLY\nDesign Timing Summary\n"
                                  "WNS(ns) TNS(ns) TNS Failing Endpoints TNS Total Endpoints "
                                  "WHS(ns) THS(ns) THS Failing Endpoints THS Total Endpoints\n"
                                  "------- ------- ------- ------- ------- ------- ------- -------\n"
                                  "0.704 0.000 0 2 0.055 0.000 0 2\n",
            "drc.rpt": "SYNTHETIC TEST ONLY\n0 Violations found.\n",
            "route_status.rpt": "SYNTHETIC TEST ONLY\n"
                                "# of routable nets............... : 2 :\n"
                                "# of fully routed nets........... : 2 :\n"
                                "# of nets with routing errors.... : 0 :\n",
            "clocks.rpt": "SYNTHETIC TEST ONLY\nClock Period(ns) Waveform(ns)\n"
                          "pl_clk0 8.000 {0.000 4.000}\n",
            "ps_properties.txt": "\n".join(property_rows) + "\n",
            "address_map.txt": "SYNTHETIC TEST ONLY; gemm=0xA0000000; status=0xA0010000\n",
            "check_timing.rpt": "SYNTHETIC TEST ONLY\nno_clock: 0\n",
            "utilization.rpt": "SYNTHETIC TEST ONLY; not an implementation resource result\n",
            "vivado_console.log": "SYNTHETIC TEST ONLY; Vivado never started\n",
            "stage_scope.txt": "STAGE=build\nVIVADO=2026.1\nSYNTHETIC_TEST_ONLY\nBOARD_NOT_RUN\n",
        }
        for name, text in reports.items():
            (self.run / name).write_text(text, encoding="utf-8")
        self.payload = b"\xff\xff\xff\xff\xaa\x99\x55\x66" + b"SYNTHETIC_ONLY!!"
        self.assertEqual(len(self.payload) % 4, 0)
        self.bit = synthetic_bit(self.payload)
        (self.artifacts / "kv260_opt50.bit").write_bytes(self.bit)
        (self.artifacts / "kv260_opt50.bit.bin").write_bytes(self.payload)
        (self.artifacts / "kv260_opt50.hwh").write_bytes(self.hwh())
        self.write_xsa()

    def write_identity(self):
        (self.run / "run_manifest.json").write_bytes(json_raw(self.identity))

    def logic_log(self):
        lines = ["SYNTHETIC TEST FIXTURE ONLY; NO XSIM EXECUTED"]
        top = f"tb_opt50_board_logic(TAG0=32'b{self.tags[0]:032b},TAG1=32'b{self.tags[1]:032b})"
        for suffix in ("ddrc.ddr_write_ports", "ocm_interconnect.write_ports", "reg_interconnect.write_ports"):
            lines.append("OPT50_PSVIP_ARB_FIXED_V1 changes=36 instance="+top+".dut.kv260_opt50_i.ps.inst."+suffix)
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
        return "\n".join(lines)+"\n"

    def write_xsa(self):
        project = ET.Element("Project")
        ET.SubElement(project, "File", Type="HW_HANDOFF", Name="kv260_opt50.hwh",
                      DESIGN_HIERARCHY="kv260_opt50_i", BD_TYPE="DEFAULT_BD")
        for instance in ("ctrl", "data"):
            ET.SubElement(project, "File", Type="HW_HANDOFF", Name=f"kv260_opt50_{instance}_0.hwh",
                          DESIGN_HIERARCHY=f"kv260_opt50_i/{instance}/inst", BD_TYPE="SCOPED_BD")
        ET.SubElement(project, "File", Type="BIT", Name="kv260_opt50.bit")
        with zipfile.ZipFile(self.artifacts / "kv260_opt50.xsa", "w") as archive:
            for name in ("kv260_opt50.bit", "kv260_opt50.hwh"):
                archive.writestr(name, (self.artifacts / name).read_bytes())
            archive.writestr("sysdef.xml", ET.tostring(project))
            for instance in ("ctrl", "data"):
                child = ET.Element("EDKSYSTEM", NAME="SYNTHETIC_TEST_ONLY")
                ET.SubElement(ET.SubElement(child, "MODULES"), "MODULE", INSTANCE="sc_ul",
                              MODTYPE="sc_ultralite", VLNV="xilinx.com:ip:sc_ultralite:1.0")
                archive.writestr(f"kv260_opt50_{instance}_0.hwh", ET.tostring(child))

    def hwh(self):
        root = ET.Element("SYSTEM", NAME="SYNTHETIC_TEST_ONLY")
        modules = ET.SubElement(root, "MODULES")
        gemm = ET.SubElement(modules, "MODULE", INSTANCE="gemm", VLNV="xilinx.com:hls:model_gemm_top:1.0")
        params = ET.SubElement(gemm, "PARAMETERS")
        for index in range(3):
            for suffix, value in (("ADDR_WIDTH", "64"), ("DATA_WIDTH", "128")):
                ET.SubElement(params, "PARAMETER", NAME=f"C_M_AXI_GMEM{index}_{suffix}", VALUE=value)
        registers = ET.SubElement(gemm, "REGISTERS")
        for name, offset in verify.expected_registers(self.abi).items():
            register = ET.SubElement(registers, "REGISTER", NAME=name)
            ET.SubElement(register, "PROPERTY", NAME="SIZE", VALUE="32")
            ET.SubElement(register, "PROPERTY", NAME="ADDRESS_OFFSET", VALUE=hex(offset))
        status = ET.SubElement(modules, "MODULE", INSTANCE="status", MODTYPE="f8_status")
        params = ET.SubElement(status, "PARAMETERS")
        for index, value in enumerate(self.tags):
            ET.SubElement(params, "PARAMETER", NAME=f"BUILD_TAG{index}", VALUE=str(value))
        ET.SubElement(modules, "MODULE", INSTANCE="ps")
        ranges = ET.SubElement(root, "MEMRANGES")
        for index in range(3):
            for name, low, high in (("HP0_DDR_LOW", 0, 0x7fffffff),
                                    ("HP0_DDR_HIGH", 0x800000000, 0x87fffffff)):
                ET.SubElement(ranges, "MEMRANGE", MASTERBUSINTERFACE=f"m_axi_gmem{index}",
                              ADDRESSBLOCK=name, BASEVALUE=hex(low), HIGHVALUE=hex(high))
        for name, base in (("gemm", 0xa0000000), ("status", 0xa0010000)):
            ET.SubElement(ranges, "MEMRANGE", INSTANCE=name, MASTERBUSINTERFACE="M_AXI_HPM0_FPD",
                          BASEVALUE=hex(base), HIGHVALUE=hex(base + 65535))
        add_logic_fixture(root)
        for instance in ("ctrl", "data"):
            entry = next(m for m in modules if m.get("INSTANCE") == instance)
            entry.set("BDTYPE", "SBD")
            entry.set("BD", f"kv260_opt50_{instance}_0")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def verify_then_generate(self):
        receipt = verify.verify(self.project, self.run)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["board"], "NOT_RUN")
        self.assertEqual(receipt["register_count"], 17)
        self.assertEqual(receipt["DDR_parameters_checked"], 39)
        self.assertEqual(receipt["xsa_hierarchy"]["status"], "PASS")
        self.assertEqual(len(receipt["xsa_hierarchy"]["scoped_hwh"]), 2)
        self.assertEqual(receipt["board_logic_simulation"]["PL_starts"], 50)
        self.assertEqual(receipt["board_logic_simulation"]["real_jobs"], 48)
        self.assertEqual(receipt["board_logic_simulation"]["INT32_outputs"], 1870848)
        self.assertFalse(receipt["simulation_model"]["is_unmodified_AMD_model"])
        self.assertFalse(receipt["simulation_model"]["is_board_acceptance"])
        self.assertEqual(receipt["simulation_model"]["instance_count"], 3)
        self.assertTrue(receipt["simulation_model"]["top_parameters_decorated"])
        self.assertEqual(len(receipt["simulation_model"]["runtime_instances"]), 3)
        self.assertFalse(receipt["simulation_model"]["ocm_reg_business_traffic_qualified"])
        self.assertIn("board_logic_elaborate.bat", receipt["evidence"])
        self.assertIn("simulation_model_scope.txt", receipt["evidence"])
        self.assertIn("board_logic_elaborate_original.bat", receipt["evidence"])
        self.assertEqual(receipt["simulation_steps"]["steps"], ["compile", "local_model", "elaborate", "simulate"])
        generated = metadata.generate(self.run / "metadata_sources", self.abi, receipt, receipt_root=self.run)
        self.assertEqual(generated["board"], "NOT_RUN")
        self.assertEqual(generated["arguments"], 8)
        self.assertEqual(generated["connections"], 8)
        self.assertEqual(generated["xclbin_packing"], "NOT_RUN")
        manifest = json.loads((self.run / "metadata_sources/metadata_manifest.json").read_bytes())
        self.assertEqual(manifest["board"], "NOT_RUN")
        self.assertEqual(manifest["BITSTREAM_section"], "FORBIDDEN")
        self.assertEqual(manifest["metadata_uuid"], self.identity["metadata_uuid"])
        self.assertEqual(manifest["observer_tags"], self.tags)
        return receipt

    def test_synthetic_full_pipeline_raw_bin(self):
        self.verify_then_generate()

    def test_synthetic_full_pipeline_word_swapped_bin(self):
        (self.artifacts / "kv260_opt50.bit.bin").write_bytes(swapped_words(self.payload))
        self.verify_then_generate()

    def test_stale_or_different_bin_is_rejected(self):
        (self.artifacts / "kv260_opt50.bit.bin").write_bytes(bytes(len(self.payload)))
        with self.assertRaises(ValueError):
            verify.verify(self.project, self.run)
        self.assertFalse((self.run / "system_verified.json").exists())

    def test_hwh_tags_are_checked_even_when_xsa_pair_matches(self):
        self.tags = [self.tags[0] ^ 1, self.tags[1]]
        (self.artifacts / "kv260_opt50.hwh").write_bytes(self.hwh())
        self.write_xsa()
        with self.assertRaises(ValueError):
            verify.verify(self.project, self.run)
        self.assertFalse((self.run / "system_verified.json").exists())

    def test_run_manifest_tags_cannot_be_detached_from_fingerprint(self):
        self.identity["observer_tags"] = [self.tags[0] ^ 1, self.tags[1]]
        self.write_identity()
        with self.assertRaises(ValueError):
            verify.verify(self.project, self.run)

    def test_run_manifest_uuid_cannot_be_detached_from_fingerprint(self):
        self.identity["metadata_uuid"] = "f84d0001-0002-4003-8004-000000000005"
        self.write_identity()
        with self.assertRaises(ValueError):
            verify.verify(self.project, self.run)
        self.assertFalse((self.run / "system_verified.json").exists())

    def test_receipt_and_metadata_never_claim_board_execution(self):
        receipt = self.verify_then_generate()
        self.assertIn("NOT board", receipt["scope"])
        self.assertEqual(self.identity["board"], "NOT_RUN")
        self.assertEqual(json.loads((self.project / "project_manifest.json").read_bytes())["board"], "NOT_RUN")

    def test_missing_logic_marker_is_rejected(self):
        (self.run / "board_logic_pass.txt").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            verify.verify(self.project, self.run)
        self.assertFalse((self.run / "system_verified.json").exists())

    def test_logic_result_requires_current_tags_full50_and_no_fatal(self):
        path = self.run / "board_logic_simulate.log"
        valid = path.read_text(encoding="utf-8")
        changes = (
            valid.replace(f"TAG0={self.tags[0]}", f"TAG0={self.tags[0] ^ 1}"),
            valid.replace("PL_STARTS=50", "PL_STARTS=2"),
            valid + "Fatal: synthetic timeout\n",
            valid + "$error synthetic failure\n",
            valid + valid,
            "SYNTHETIC TEST ONLY; simulation never reached its terminal PASS\n",
        )
        for changed in changes:
            with self.subTest(log=changed):
                path.write_text(changed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify.verify(self.project, self.run)
                self.assertFalse((self.run / "system_verified.json").exists())

    def test_local_model_banner_and_completion_must_be_unique_and_correct(self):
        path = self.run/"board_logic_simulate.log"
        valid = path.read_text(encoding="utf-8")
        banner = next(line for line in valid.splitlines() if line.startswith("OPT50_PSVIP_ARB_FIXED_V1"))
        completion = next(line for line in valid.splitlines() if line.startswith("OPT50_REAL_CASES_COMPLETED"))
        changes = (valid.replace(banner+"\n", ""), valid+banner+"\n",
                   valid.replace("changes=36", "changes=35"), valid.replace(completion+"\n", ""),
                   valid+completion+"\n", valid.replace("MODEL=LOCAL_ARB_FIXED_V1", "MODEL=AMD_ORIGINAL"),
                   valid.replace("checks=3000000", "checks=1"), valid.replace("jobs=48 PL_STARTS=48", "jobs=4 PL_STARTS=4"),
                   valid.replace("PACKAGE_SHA256=6d34397", "PACKAGE_SHA256=0d34397"))
        for changed in changes:
            with self.subTest(log=changed):
                path.write_text(changed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify.verify(self.project, self.run)
                self.assertFalse((self.run/"system_verified.json").exists())

    def test_all48_individual_jobs_required_not_only_aggregate_counts(self):
        path = self.run/"board_logic_simulate.log"
        valid = path.read_text(encoding="utf-8")
        first = next(line for line in valid.splitlines() if line.startswith("OPT50_REAL_CASE_OUTPUT_MATCH"))
        lines = valid.splitlines()
        first_at = lines.index(first)
        swapped = list(lines)
        swapped[first_at], swapped[first_at+1] = swapped[first_at+1], swapped[first_at]
        changes = (valid.replace(first+"\n", ""), valid+first+"\n", "\n".join(swapped)+"\n",
                   valid.replace("case=layer_26_public mode=0", "case=layer_30_public mode=0", 1),
                   valid.replace("M=197 K=192 N=576 elements=113472 jobs=1", "M=6 K=192 N=576 elements=3456 jobs=1", 1))
        for changed in changes:
            with self.subTest(log=changed):
                path.write_text(changed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify.verify(self.project, self.run)
                self.assertFalse((self.run/"system_verified.json").exists())

    def test_elaboration_local_library_and_model_scope_are_required(self):
        elaborate = self.run/"board_logic_elaborate.bat"
        scope = self.run/"simulation_model_scope.txt"
        original_elaborate, original_scope = elaborate.read_bytes(), scope.read_bytes()
        for path, wrong in ((elaborate, b"xelab -L zynq_ultra_ps_e_vip_v1_0_23\n"),
                            (scope, original_scope.replace(b"LOCAL_ARB_FIXED_V1", b"AMD_ORIGINAL"))):
            with self.subTest(path=path.name):
                path.write_bytes(wrong)
                with self.assertRaises(ValueError):
                    verify.verify(self.project, self.run)
                self.assertFalse((self.run/"system_verified.json").exists())
                elaborate.write_bytes(original_elaborate); scope.write_bytes(original_scope)

    def test_exact_three_binding_instances_not_any_three_banners(self):
        path = self.run/"board_logic_simulate.log"
        valid = path.read_text(encoding="utf-8")
        changes = (valid.replace(".inst.reg_interconnect.write_ports", ".inst.unreviewed.write_ports"),
                   valid.replace(".inst.reg_interconnect.write_ports", ".inst.ocm_interconnect.write_ports"),
                   valid.replace(".inst.ddrc.ddr_write_ports", ".inst.ddrc.unreviewed"))
        for changed in changes:
            with self.subTest(log=changed):
                path.write_text(changed, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "instance set differs"):
                    verify.verify(self.project, self.run)
                self.assertFalse((self.run/"system_verified.json").exists())

    def test_local_library_must_be_first_on_actual_call_not_echo(self):
        path = self.run/"board_logic_elaborate.bat"
        original = path.read_text(encoding="utf-8")
        changes = (original.replace("call xelab -L opt50_ps_vip_fixed -L xil_defaultlib",
                                    "call xelab -L xil_defaultlib -L opt50_ps_vip_fixed"),
                   original.replace("call xelab -L opt50_ps_vip_fixed", "echo xelab -L opt50_ps_vip_fixed")+
                   "call xelab -L zynq_ultra_ps_e_vip_v1_0_23\n")
        for changed in changes:
            with self.subTest(script=changed):
                path.write_text(changed, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "FIRST"):
                    verify.verify(self.project, self.run)
                self.assertFalse((self.run/"system_verified.json").exists())

    def test_step_errors_or_other_elaboration_edits_cannot_create_receipt(self):
        elaborate = self.run/"board_logic_elaborate.bat"
        saved = elaborate.read_bytes()
        elaborate.write_bytes(saved.replace(b"xil_defaultlib.tb_opt50_board_logic", b"xil_defaultlib.unreviewed_top"))
        with self.assertRaisesRegex(ValueError, "beyond reviewed"):
            verify.verify(self.project, self.run)
        self.assertFalse((self.run/"system_verified.json").exists())
        elaborate.write_bytes(saved)
        for step in ("compile", "local_model", "elaborate", "simulate"):
            path = self.run/f"board_logic_step_{step}.log"
            original = path.read_bytes()
            path.write_bytes(b"ERROR: SYNTHETIC intentional compiler failure\n")
            with self.subTest(step=step), self.assertRaisesRegex(ValueError, "step reported failure"):
                verify.verify(self.project, self.run)
            self.assertFalse((self.run/"system_verified.json").exists())
            path.write_bytes(original)

    def test_decorated_model_tags_must_match_exact_32_bit_same_run_values(self):
        path = self.run/"board_logic_simulate.log"
        valid = path.read_text(encoding="utf-8")
        tag0 = f"TAG0=32'b{self.tags[0]:032b}"
        tag1 = f"TAG1=32'b{self.tags[1]:032b}"
        changes = (valid.replace(tag0, f"TAG0=32'b{self.tags[0]^1:032b}", 1),
                   valid.replace(tag1, f"TAG1=32'b{self.tags[1]^1:032b}"),
                   valid.replace(tag0, f"TAG0=32'b{self.tags[0]:032b}"[:-1]),
                   valid.replace(tag0, tag0[:-1]+"x"),
                   valid.replace(tag0+","+tag1, tag0+","+tag1+",UNREVIEWED=1"),
                   valid.replace("tb_opt50_board_logic("+tag0+","+tag1+")", "tb_opt50_board_logic", 1))
        for changed in changes:
            with self.subTest(log=changed):
                path.write_text(changed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify.verify(self.project, self.run)
                self.assertFalse((self.run/"system_verified.json").exists())

    def test_exact_plain_model_top_is_supported_without_arbitrary_normalization(self):
        top = f"tb_opt50_board_logic(TAG0=32'b{self.tags[0]:032b},TAG1=32'b{self.tags[1]:032b})"
        log = self.logic_log().replace(top, "tb_opt50_board_logic")
        result = verify.check_logic_evidence(log, self.tags,
                    (self.run/"board_logic_elaborate.bat").read_text(),
                    (self.run/"simulation_model_scope.txt").read_text())
        self.assertFalse(result["simulation_model"]["top_parameters_decorated"])
        self.assertEqual(len(result["simulation_model"]["instances"]), 3)

    def test_extra_hwh_rejected_without_receipt(self):
        with zipfile.ZipFile(self.artifacts / "kv260_opt50.xsa", "a") as archive:
            archive.writestr("another_platform.hwh", b"<EDKSYSTEM/>")
        with self.assertRaisesRegex(ValueError, "undeclared/missing HWH"):
            verify.verify(self.project, self.run)
        self.assertFalse((self.run / "system_verified.json").exists())

    def test_missing_scoped_hwh_rejected_without_receipt(self):
        path = self.artifacts / "kv260_opt50.xsa"
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()
                       if name != "kv260_opt50_data_0.hwh"}
        with zipfile.ZipFile(path, "w") as archive:
            for name, value in members.items():
                archive.writestr(name, value)
        with self.assertRaisesRegex(ValueError, "undeclared/missing HWH"):
            verify.verify(self.project, self.run)
        self.assertFalse((self.run / "system_verified.json").exists())

    def test_source_checker_is_reviewed_single_implementation(self):
        path = prep.xsa_checker_source()
        self.assertEqual(path.name, "xsa_hierarchy.py")
        self.assertEqual(prep.digest(path), prep.XSA_CHECKER_SHA)

    def test_prepared_checker_uses_pinned_local_copy_without_external_workspace(self):
        source = prep.xsa_checker_source().read_bytes()
        local = self.project / "scripts/xsa_hierarchy.py"
        local.parent.mkdir()
        local.write_bytes(source)
        with mock.patch.object(prep, "ROOT", self.project):
            self.assertEqual(prep.xsa_checker_source(), local)
            local.write_bytes(source + b"\n# unexpected change\n")
            with self.assertRaisesRegex(ValueError, "checker changed"):
                prep.xsa_checker_source()


if __name__ == "__main__":
    unittest.main()
