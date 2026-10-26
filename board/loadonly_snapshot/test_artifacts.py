"""SYNTHETIC fixtures only; no Vivado, SSH, XRT, firmware load or devices."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import uuid
import xml.etree.ElementTree as ET

import artifacts as audit

HERE = Path(__file__).resolve().parent


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def synthetic_xml():
    project = ET.Element("project", name="SYNTHETIC_TEST_ONLY")
    core = ET.SubElement(ET.SubElement(ET.SubElement(project, "platform"), "device"), "core",
                         clockFreq="125MHz", numComputeUnits="2")
    for name, instance, base in (("model_gemm_top", "gemm_1", 0xa0000000), ("axi_observer", "status_1", 0xa0010000)):
        kernel = ET.SubElement(core, "kernel", name=name, hwControlProtocol="ap_ctrl_none")
        ET.SubElement(kernel, "port", name="S_AXI_CONTROL", mode="slave", dataWidth="32")
        if name == "model_gemm_top":
            for i in range(3):
                ET.SubElement(kernel, "port", name="M_AXI_GMEM" + str(i), mode="master", dataWidth="128")
            masters = ("M_AXI_GMEM0", "M_AXI_GMEM1", "M_AXI_GMEM1", "M_AXI_GMEM2")
            for i, (arg, offset) in enumerate(audit.OFFSETS.items()):
                size = 8 if i < 4 else 4
                ET.SubElement(kernel, "arg", name=arg, id=str(i), offset=hex(offset), size=hex(size),
                              hostSize=hex(size), hostOffset="0x0", addressQualifier="1" if i < 4 else "0",
                              port=masters[i] if i < 4 else "S_AXI_CONTROL")
        ET.SubElement(ET.SubElement(kernel, "instance", name=instance), "addrRemap", base=hex(base), range="0x10000")
    return ET.tostring(project, encoding="utf-8", xml_declaration=True)


def synthetic_xclbin(xml_raw):
    ips = struct.pack("<I4x", 2)
    for name, base in (("model_gemm_top:gemm_1", 0xa0000000), ("axi_observer:status_1", 0xa0010000)):
        ips += struct.pack("<IIQ64s", 1, 512, base, name.encode())
    memory = struct.pack("<I4x", 2)
    for name, base in (("HP0_LOW", 0), ("HP0_HIGH", 0x800000000)):
        memory += struct.pack("<BB6xQQ16s", 1, 1, 0x200000, base, name.encode())
    connections = [struct.pack("<iii", i, 0, bank) for i in range(4) for bank in range(2)]
    sections = [(2, xml_raw), (8, ips), (6, memory), (7, struct.pack("<I", 8) + b"".join(connections)),
                (27, struct.pack("<I", 16) + b"".join(connections * 2)), (26, memory)]
    raw = bytearray(696)
    raw[:8] = b"xclbin2\0"
    struct.pack_into("<i", raw, 8, -1)
    struct.pack_into("<HBBHH", raw, 328, 0, 2, 18, 0, 0)
    raw[416:432] = uuid.UUID(audit.UUID).bytes
    struct.pack_into("<I", raw, 448, 6)
    for i, (kind, data) in enumerate(sections):
        raw += bytes((-len(raw)) % 8)
        offset = len(raw)
        raw += data
        struct.pack_into("<I16s4xQQ", raw, 456 + i * 40, kind, b"SYNTHETIC_ONLY", offset, len(data))
    raw += bytes(max(0, 8560 - len(raw)))
    struct.pack_into("<Q", raw, 304, len(raw))
    return bytes(raw)


def section(raw, kind):
    for i in range(6):
        k, _, start, size = struct.unpack_from("<I16s4xQQ", raw, 456 + i * 40)
        if k == kind:
            return start, size
    raise AssertionError("fixture missing section")


def synthetic_fdt(properties=None, extra_node=False):
    properties = audit.expected_fdt_properties() if properties is None else properties
    names = sorted({name for _, name in properties})
    offsets, strings = {}, bytearray()
    for name in names:
        offsets[name] = len(strings)
        strings += name.encode() + b"\0"
    tree = {"properties": {}, "children": {}}
    for (path, name), value in properties.items():
        node = tree
        for part in path.strip("/").split("/"):
            if part:
                node = node["children"].setdefault(part, {"properties": {}, "children": {}})
        node["properties"][name] = value
    if extra_node:
        tree["children"]["unreviewed_empty_node"] = {"properties": {}, "children": {}}
    stream = bytearray()
    def emit(name, node):
        stream.extend(struct.pack(">I", 1) + name.encode() + b"\0")
        stream.extend(bytes((-len(stream)) % 4))
        for key, value in node["properties"].items():
            stream.extend(struct.pack(">III", 3, len(value), offsets[key]) + value)
            stream.extend(bytes((-len(stream)) % 4))
        for key, value in node["children"].items():
            emit(key, value)
        stream.extend(struct.pack(">I", 2))
    emit("", tree)
    stream += struct.pack(">I", 9)
    header = struct.pack(">10I", 0xd00dfeed, 56 + len(stream) + len(strings), 56, 56 + len(stream), 40,
                         17, 16, 0, len(strings), len(stream))
    return header + bytes(16) + stream + strings


def offline(runtime_sha):
    results = [{"status": "CPU_GOLDEN_MATCH", "case": f"layer_{layer}_{name}", "mode": mode,
                "elements": m * (576 if layer % 4 == 2 else 192), "physical_PL_starts": 0}
               for layer in audit.LAYERS for name, m in (("public", 197), ("edge", 6)) for mode in (0, 1)]
    return {"schema": "opt50_offline_regression_v1", "status": "PASS", "jobs": 48,
            "compared_INT32_outputs": 1870848, "physical_PL_starts": 0, "negative_cases": 4,
            "scope": "Host integer/reference and file checks only; NOT ARM/XRT compilation or board/RTL proof",
            "executable_sha256": runtime_sha, "package_unchanged": True, "results": results}


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        tmp = HERE / ".test_tmp"
        tmp.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="SYNTHETIC_ONLY_", dir=tmp)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.xml = synthetic_xml()
        p = mock.patch.object(audit, "XML_SHA", audit.sha(self.xml))
        p.start()
        self.addCleanup(p.stop)

    def write(self, name, raw):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def fixture(self):
        # All payloads and provenance are explicitly synthetic; pin overrides
        # are unittest-only and never exposed by the production audit API.
        raw = b"SYNTHETIC_ONLY".ljust(80, b"\0") + bytes.fromhex("aa995566")
        swapped = b"".join(raw[i:i+4][::-1] for i in range(0, len(raw), 4))
        artifacts = {"artifacts/kv260_opt50" + suffix: audit.record(raw) for suffix in (".bit", ".bit.bin", ".hwh", ".xsa")}
        receipt = {"schema": "opt50_system_verified_v1", "status": "PASS", "verifier_version": "2", "board": "NOT_RUN",
                   "run_id": audit.RUN, "metadata_uuid": audit.UUID, "observer_tags": audit.TAGS,
                   "register_header_sha256": audit.HEADER_SHA, "register_count": 17, "axi_bits": 128,
                   "clock_hz": 125000000, "control_base": "0xA0000000", "observer_base": "0xA0010000", "artifacts": artifacts}
        for name in artifacts:
            self.write("hardware/" + name, raw)
        self.write("hardware/system_verified.json", encoded(receipt))
        self.write("firmware/" + audit.APP + ".bit.bin", swapped)
        self.write("firmware/shell.json", encoded({"shell_type": "PL_FLAT", "num_slots": "1"}))
        self.write("conversion_evidence/conversion.bif", b"SYNTHETIC_ONLY_BIF")
        conversion = {"schema": "opt50_bootgen_input_v1", "run_id": audit.RUN, "metadata_uuid": audit.UUID,
                      "source_receipt": audit.record(encoded(receipt)), "files": {"system.bit": audit.record(raw),
                      "conversion.bif": audit.record(b"SYNTHETIC_ONLY_BIF")}, "expected_output": "system.bit.bin", "board": "NOT_RUN"}
        self.write("conversion_evidence/conversion_plan.json", encoded(conversion))
        sources = {"embedded_metadata.xml": self.xml, "ip_layout.json": b"{}\n", "mem_topology.json": b"{}\n",
                   "connectivity.json": b"{}\n", "xrt.ini": b"SYNTHETIC_ONLY_INI"}
        for name, data in sources.items():
            self.write("metadata_sources/" + name, data)
        metadata = {"schema": "opt50_metadata_sources_v1", "abi": audit.ABI, "format": audit.FORMAT,
                    "run_id": audit.RUN, "metadata_uuid": audit.UUID, "observer_tags": audit.TAGS,
                    "verified_receipt_sha256": audit.sha(encoded(receipt)), "verified_artifacts": artifacts,
                    "register_header_sha256": audit.HEADER_SHA, "clock_hz": 125000000, "axi_bits": 128,
                    "BITSTREAM_section": "FORBIDDEN", "board": "NOT_RUN", "files": {n: audit.record(b) for n, b in sources.items()}}
        self.write("metadata_sources/metadata_manifest.json", encoded(metadata))
        # Bring immutable manifest inventory to 28, matching the real bundle.
        for i in range(28 - len(list(self.root.rglob("*.*")))):
            self.write(f"synthetic/padding_{i}.txt", b"SYNTHETIC_ONLY")
        paths = [p for p in self.root.rglob("*") if p.is_file()]
        self.assertEqual(len(paths), 28)
        self.manifest = {"schema": "opt50_deployment_preparation_v1", "status": "PREPARED_SOURCES_NOT_LOAD_READY",
                         "run_id": audit.RUN, "metadata_uuid": audit.UUID, "observer_tags": audit.TAGS,
                         "firmware_app": audit.APP, "firmware_file": audit.APP + ".bit.bin",
                         "derived_firmware_pairing": "BIT_PAYLOAD_32BIT_BYTE_SWAP", "hardware_authorized": False,
                         "active_loader_included": False, "physical_PL_starts": 0, "board": "NOT_RUN",
                         "system_receipt": audit.record(encoded(receipt)),
                         "files": {p.relative_to(self.root).as_posix(): audit.record(p.read_bytes()) for p in paths}}
        self.repin_manifest()
        generated = {audit.DTBO_REL: synthetic_fdt(), audit.XCLBIN_REL: synthetic_xclbin(self.xml),
                     audit.RUNTIME_REL: b"SYNTHETIC_ONLY_NOT_EXECUTABLE"}
        for name, data in generated.items():
            self.write(name, data)
        self.pins = {name: audit.sha(data) for name, data in generated.items()}
        p = mock.patch.object(audit, "GENERATED", self.pins)
        p.start()
        self.addCleanup(p.stop)
        self.report = offline(self.pins[audit.RUNTIME_REL])
        self.write(audit.REPORT_REL, encoded(self.report))

    def repin_manifest(self):
        data = encoded(self.manifest)
        self.write("deployment_manifest.json", data)
        p = mock.patch.object(audit, "BUNDLE_MANIFEST_SHA", audit.sha(data))
        p.start()
        self.addCleanup(p.stop)

    def test_synthetic_full_audit_json_and_readonly(self):
        self.fixture()
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = audit.audit(self.root)
        self.assertEqual(result["status"], "PASS_FILE_AUDIT_ONLY_NOT_LOAD_AUTHORIZATION")
        self.assertEqual(result["physical_PL_starts"], 0)
        self.assertEqual(len(result["files"]), 33)
        self.assertEqual(result["source_paths"]["firmware"], str(self.root / "firmware" / (audit.APP + ".bit.bin")))
        self.assertIn("firmware/shell.json", result["files"])
        json.dumps(result)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_no_import_time_file_io(self):
        spec = importlib.util.spec_from_file_location("synthetic_readonly_import", HERE / "artifacts.py")
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("import I/O")), \
             mock.patch.object(Path, "write_bytes", side_effect=AssertionError("import write")):
            spec.loader.exec_module(module)

    def test_manifest_pin_not_self_authorized(self):
        self.fixture()
        self.write("deployment_manifest.json", b"{}")
        with self.assertRaisesRegex(ValueError, "pinned deployment manifest"):
            audit.audit(self.root)

    def test_bundle_payload_change_rejected(self):
        self.fixture()
        self.write("metadata_sources/xrt.ini", b"changed")
        with self.assertRaisesRegex(ValueError, "bundle payload"):
            audit.audit(self.root)

    def test_generated_runtime_change_rejected(self):
        self.fixture()
        self.write(audit.RUNTIME_REL, b"changed")
        with self.assertRaisesRegex(ValueError, "pinned generated"):
            audit.audit(self.root)

    def test_receipt_run_mismatch_rejected_even_with_test_pin(self):
        self.fixture()
        path = self.root / "hardware/system_verified.json"
        value = json.loads(path.read_bytes())
        value["run_id"] = "SYNTHETIC_OLD_RUN"
        path.write_bytes(encoded(value))
        self.manifest["files"]["hardware/system_verified.json"] = audit.record(path.read_bytes())
        self.manifest["system_receipt"] = audit.record(path.read_bytes())
        self.repin_manifest()
        with self.assertRaisesRegex(ValueError, "system receipt"):
            audit.audit(self.root)

    def test_offline_report_runtime_mismatch(self):
        self.fixture()
        self.report["executable_sha256"] = "0" * 64
        self.write(audit.REPORT_REL, encoded(self.report))
        with self.assertRaisesRegex(ValueError, "offline regression/runtime"):
            audit.audit(self.root)

    def test_offline_missing_or_reordered_actual_results(self):
        report = offline("0" * 64)
        report["results"].reverse()
        with self.assertRaisesRegex(ValueError, "result/order"):
            audit.check_offline(report, "0" * 64)
        report = offline("0" * 64)
        report["results"] = []
        with self.assertRaisesRegex(ValueError, "48 results"):
            audit.check_offline(report, "0" * 64)

    def test_boolean_is_not_an_integer_result_receipt(self):
        report = offline("0" * 64)
        report["results"][0]["mode"] = False
        with self.assertRaisesRegex(ValueError, "result/order"):
            audit.check_offline(report, "0" * 64)

    def test_xclbin_positive_json(self):
        result = audit.check_xclbin(synthetic_xclbin(self.xml))
        self.assertEqual(result["section_count"], 6)
        json.dumps(result)

    def test_xclbin_uuid_version_mode_signature_and_count(self):
        base = synthetic_xclbin(self.xml)
        mutations = [(416, bytes(16)), (330, bytes([2, 19])), (332, struct.pack("<H", 1)),
                     (8, struct.pack("<i", 0)), (448, struct.pack("<I", 7))]
        for offset, data in mutations:
            with self.subTest(offset=offset):
                raw = bytearray(base)
                raw[offset:offset + len(data)] = data
                with self.assertRaises(ValueError):
                    audit.check_xclbin(raw)

    def test_xclbin_sections_bounds_duplicate_overlap_and_programming(self):
        for offset, data in ((456, struct.pack("<I", 0)), (496, struct.pack("<I", 2)),
                             (480, struct.pack("<Q", 0x100000)), (520, struct.pack("<Q", 696))):
            with self.subTest(offset=offset):
                raw = bytearray(synthetic_xclbin(self.xml))
                raw[offset:offset + len(data)] = data
                with self.assertRaises(ValueError):
                    audit.check_xclbin(raw)

    def test_xclbin_arg_width_and_cu_address(self):
        for changed in (self.xml.replace(b'port="M_AXI_GMEM1"', b'port="M_AXI_GMEM2"', 1),
                        self.xml.replace(b'dataWidth="128"', b'dataWidth="64"', 1),
                        self.xml.replace(b'base="0xa0000000"', b'base="0xa0020000"', 1)):
            with self.assertRaises(ValueError):
                audit.check_xclbin(synthetic_xclbin(changed))

    def test_xclbin_ip_memory_and_group_connectivity(self):
        for kind, relative, data in ((8, 16, struct.pack("<Q", 0xa0020000)),
                                     (6, 16, struct.pack("<Q", 0x400000)),
                                     (27, 4, struct.pack("<iii", 4, 0, 1))):
            with self.subTest(kind=kind):
                raw = bytearray(synthetic_xclbin(self.xml))
                start, _ = section(raw, kind)
                raw[start + relative:start + relative + len(data)] = data
                with self.assertRaises(ValueError):
                    audit.check_xclbin(raw)

    def test_xml_entities_and_unreviewed_xml_rejected(self):
        with self.assertRaisesRegex(ValueError, "XML declarations"):
            audit.check_xclbin(synthetic_xclbin(b'<!DOCTYPE project><project/>'))
        with self.assertRaisesRegex(ValueError, "pinned reviewed XML"):
            audit.check_xclbin(synthetic_xclbin(self.xml + b"\n"))

    def test_fdt_positive_json(self):
        result = audit.check_fdt(synthetic_fdt())
        self.assertEqual(result["properties"], 16)
        self.assertEqual(result["firmware_file"], audit.APP + ".bit.bin")
        json.dumps(result)

    def test_fdt_firmware_clock_reset_and_fixup_mutations(self):
        for key, value in ((("/fragment@0/__overlay__", "firmware-name"), b"old.bit.bin\0"),
                           (("/fragment@1/__overlay__/opt50_pl0_clock", "assigned-clock-rates"), struct.pack(">I", 100000000)),
                           (("/fragment@0/__overlay__", "resets"), bytes(32)),
                           (("/__fixups__", "amba"), b"/fragment@0:target:0\0")):
            with self.subTest(key=key):
                properties = audit.expected_fdt_properties()
                properties[key] = value
                with self.assertRaisesRegex(ValueError, "properties differ"):
                    audit.check_fdt(synthetic_fdt(properties))

    def test_fdt_extra_empty_node_and_bad_header(self):
        with self.assertRaisesRegex(ValueError, "extra/trailing"):
            audit.check_fdt(synthetic_fdt(extra_node=True))
        raw = bytearray(synthetic_fdt())
        struct.pack_into(">I", raw, 4, len(raw) + 1)
        with self.assertRaisesRegex(ValueError, "FDT layout"):
            audit.check_fdt(raw)

    def test_duplicate_json_and_unsafe_paths(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            audit.json_object(b'{"key":0,"key":1}', "synthetic")
        for name in ("../outside", "/absolute", "a\\b", "a//b", "a/./b", "D:bad", "a\0b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                audit.safe_relative(name)

    def test_symlink_and_junction_refused(self):
        self.fixture()
        path = self.root / audit.RUNTIME_REL
        actual = Path.is_symlink
        with mock.patch.object(Path, "is_symlink", lambda p: p == path or actual(p)):
            with self.assertRaisesRegex(ValueError, "linked path"):
                audit.audit(self.root)

    def test_case_package_payload_hashes_all129(self):
        case_root = self.root / "cases"
        case_root.mkdir()
        manifest = {"schema": "h576_sparse24_axi_golden_package_v1", "format": audit.FORMAT, "files": {},
                    "cases": [{"layer_index": layer} for layer in audit.LAYERS]}
        for i in range(129):
            name = f"SYNTHETIC_{i}.bin"
            raw = b"SYNTHETIC_ONLY"
            (case_root / name).write_bytes(raw)
            manifest["files"][name] = audit.record(raw)
        raw = encoded(manifest)
        (case_root / "manifest.json").write_bytes(raw)
        with mock.patch.object(audit, "PACKAGE_SHA", audit.sha(raw)):
            self.assertEqual(audit.check_cases(case_root)["payloads_checked"], 129)
            (case_root / "SYNTHETIC_128.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "test case payload"):
                audit.check_cases(case_root)


if __name__ == "__main__":
    unittest.main()
