"""Synthetic source-generation tests; no Vivado, xclbinutil, XRT or board access."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


metadata = module("opt50_make_metadata", ROOT / "scripts/make_metadata.py")
AUDITOR = ROOT / "scripts/audit_runtime_metadata.py"
if not AUDITOR.is_file():
    AUDITOR = INTEGRATION / "scripts/prepare_cases.py"
cases = module("opt50_prepare_cases_for_metadata", AUDITOR)
ABI_PATH = ROOT / "input/opt50_abi.json"
if not ABI_PATH.is_file():
    ABI_PATH = INTEGRATION / "opt50_abi.json"


class MetadataTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.abi = json.loads(ABI_PATH.read_bytes())
        raw = b"synthetic verified system artifact; never a real firmware file"
        (self.root / "artifacts").mkdir()
        for name in metadata.SYSTEM_ARTIFACTS:
            (self.root / name).write_bytes(raw)
        self.receipt = {
            "schema": "opt50_system_verified_v1", "status": "PASS", "tool_version": "2026.1",
            "clock_hz": 125000000, "axi_bits": 128, "register_header_sha256": metadata.HEADER_SHA,
            "metadata_uuid": "1d5b6a73-2e0f-45a1-93c8-2a0597c03216", "observer_tags": [0xA1234567, 0xB7654321],
            "control_base": "0xA0000000", "observer_base": "0xA0010000", "run_id": "synthetic_test_only",
            "artifacts": {name: {"bytes": len(raw), "sha256": metadata.sha256(raw)}
                          for name in metadata.SYSTEM_ARTIFACTS},
        }

    def generate(self, abi=None, receipt=None):
        return metadata.generate(self.root / "new_metadata", abi or self.abi, receipt or self.receipt,
                                 receipt_root=self.root)

    def test_new_xml_passes_existing_runtime_audit(self):
        result = self.generate()
        self.assertEqual(result["arguments"], 8)
        self.assertEqual(result["board"], "NOT_RUN")
        raw = (self.root / "new_metadata/embedded_metadata.xml").read_bytes()
        self.assertEqual(cases.inspect_metadata_xml(raw)["status"], "XML_ABI_MATCH_NOT_HARDWARE_PROOF")
        project = ET.fromstring(raw)
        core = next(project.iter("core"))
        self.assertEqual(core.get("clockFreq"), "125MHz")
        self.assertEqual([k.get("name") for k in project.iter("kernel")], ["model_gemm_top", "axi_observer"])
        self.assertEqual([n.get("base") for n in project.iter("addrRemap")], ["0xA0000000", "0xA0010000"])

    def test_four_pointer_eight_bank_connections(self):
        self.generate()
        folder = self.root / "new_metadata"
        links = json.loads((folder / "connectivity.json").read_bytes())["connectivity"]
        self.assertEqual(links["m_count"], "8")
        self.assertEqual({(int(v["arg_index"]), int(v["mem_data_index"])) for v in links["m_connection"]},
                         {(arg, bank) for arg in range(4) for bank in range(2)})
        banks = json.loads((folder / "mem_topology.json").read_bytes())["mem_topology"]["m_mem_data"]
        self.assertEqual([(int(b["m_base_address"], 0), int(b["m_sizeKB"], 0) * 1024) for b in banks],
                         [(0, 2**31), (0x800000000, 2**31)])
        ips = json.loads((folder / "ip_layout.json").read_bytes())["ip_layout"]["m_ip_data"]
        self.assertEqual([i["m_name"] for i in ips], ["model_gemm_top:gemm_1", "axi_observer:status_1"])
        self.assertTrue(all(i["m_ip_control"] == "AP_CTRL_NONE" for i in ips))

    def test_manifest_is_not_hardware_authorization(self):
        self.generate()
        folder = self.root / "new_metadata"
        value = json.loads((folder / "metadata_manifest.json").read_bytes())
        self.assertEqual(value["BITSTREAM_section"], "FORBIDDEN")
        self.assertEqual(value["xclbin_packing"], "NOT_RUN")
        self.assertEqual(value["board"], "NOT_RUN")
        self.assertIn("NOT_BOARD_TEST", value["verification_scope"])
        for name, record in value["files"].items():
            raw = (folder / name).read_bytes()
            self.assertEqual(record, {"bytes": len(raw), "sha256": metadata.sha256(raw)})
        self.assertEqual(set(p.suffix for p in folder.iterdir()), {".json", ".xml", ".ini"})

    def test_refuses_existing_directory_without_writing(self):
        destination = self.root / "new_metadata"
        destination.mkdir()
        (destination / "keep.txt").write_bytes(b"preserve")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.generate()
        self.assertEqual(list(destination.iterdir()), [destination / "keep.txt"])
        self.assertEqual((destination / "keep.txt").read_bytes(), b"preserve")

    def test_requires_actual_unchanged_artifacts(self):
        (self.root / "artifacts/kv260_opt50.hwh").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.generate()
        self.assertFalse((self.root / "new_metadata").exists())

    def test_rejects_failed_or_unrun_receipt(self):
        for status in ("NOT_RUN", "PREPARED_NOT_RUN", "FAIL"):
            receipt = copy.deepcopy(self.receipt)
            receipt["status"] = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "PASS receipt"):
                self.generate(receipt=receipt)

    def test_rejects_old_platform_identity_and_zero_identity(self):
        for key, value in (("metadata_uuid", metadata.OLD_UUID),
                           ("metadata_uuid", "00000000-0000-0000-0000-000000000000"),
                           ("observer_tags", list(metadata.OLD_TAGS)), ("observer_tags", [0, 0]),
                           ("observer_tags", [True, 1]), ("observer_tags", [0, 2**32])):
            receipt = copy.deepcopy(self.receipt)
            receipt[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.generate(receipt=receipt)

    def test_rejects_register_clock_width_and_base_mismatch(self):
        for key, value in (("clock_hz", 100000000), ("axi_bits", 32), ("tool_version", "2020.2.2"),
                           ("control_base", "0xA0020000"), ("observer_base", "0xA0000000"),
                           ("register_header_sha256", "0" * 64)):
            receipt = copy.deepcopy(self.receipt)
            receipt[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.generate(receipt=receipt)
        abi = copy.deepcopy(self.abi)
        abi["registers"]["Y"]["offset"] = "0x30"
        with self.assertRaisesRegex(ValueError, "offset/width"):
            self.generate(abi=abi)
        abi = copy.deepcopy(self.abi)
        abi["registers"]["W_meta"]["port"] = "gmem2"
        with self.assertRaisesRegex(ValueError, "pointer port"):
            self.generate(abi=abi)

    def test_unsafe_or_empty_artifact_inventory_rejected(self):
        for name in ("../fake_system.hwh", "/fake_system.hwh", "C:/fake_system.hwh", "sub\\fake_system.hwh"):
            receipt = copy.deepcopy(self.receipt)
            record = receipt["artifacts"].pop("artifacts/kv260_opt50.hwh")
            receipt["artifacts"][name] = record
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.generate(receipt=receipt)
        receipt = copy.deepcopy(self.receipt)
        receipt["artifacts"] = {}
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.generate(receipt=receipt)


if __name__ == "__main__":
    unittest.main()
