"""Generate metadata SOURCE sections after system verification; never access a device.

This does not create or load an xclbin and never invokes xclbinutil. The caller
must supply a PASS receipt from verify_system.py, plus its unchanged artifacts.
The receipt is an audit chain, not a cryptographic signature or board approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import uuid
import xml.etree.ElementTree as ET

ABI = "OPT50_SPLIT128_AP_CTRL_HS_V1"
FORMAT = "H576_SPARSE24_SPLIT128_V1"
HEADER_SHA = "fc243d24a6e87de438fa0db99e9ade55a3c1467b9ed9346a58eb34c95cf91c73"
OLD_UUID = "0245b248-109b-6bf7-73c7-16b629abe9a1"
OLD_TAGS = (3652287023, 1996644805)
OFFSETS = {"X": 0x18, "W_data": 0x24, "W_meta": 0x30, "Y": 0x3C,
           "M": 0x48, "K": 0x50, "N": 0x58, "mode": 0x60}
MASTERS = ("M_AXI_GMEM0", "M_AXI_GMEM1", "M_AXI_GMEM1", "M_AXI_GMEM2")
CU_BASES = (("model_gemm_top", "gemm_1", "0xA0000000"),
            ("axi_observer", "status_1", "0xA0010000"))
SYSTEM_ARTIFACTS = {"artifacts/kv260_opt50" + suffix for suffix in (".bit", ".bit.bin", ".hwh", ".xsa")}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def integer(value):
    require(type(value) is int or isinstance(value, str), "expected integer or integer string")
    return int(value, 0) if isinstance(value, str) else value


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def validate_abi(abi):
    require(abi["schema"] == "opt50_board_abi_snapshot_v1" and abi["abi"] == ABI and
            abi["format"] == FORMAT, "unreviewed ABI schema/name/format")
    require(abi["control_protocol_in_rtl"] == "ap_ctrl_hs", "physical HLS control must remain ap_ctrl_hs")
    require(abi["register_header_sha256"] == HEADER_SHA, "unreviewed register header identity")
    require(integer(abi["clock_hz"]) == 125000000 and integer(abi["axi_data_bits"]) == 128,
            "OPT50 clock/AXI capability changed")
    require(set(abi["registers"]) == set(OFFSETS), "OPT50 requires exactly eight registers")
    for index, (name, offset) in enumerate(OFFSETS.items()):
        item = abi["registers"][name]
        require(integer(item["offset"]) == offset and integer(item["bits"]) == (64 if index < 4 else 32),
                "ABI argument offset/width: " + name)
        if index < 4:
            require(item["port"] == MASTERS[index].lower().replace("m_axi_", ""),
                    "ABI pointer port: " + name)
    require(integer(abi["control_offset"]) == 0 and integer(abi["return_offset"]) == 0x10,
            "ABI control/return offset")


def artifact_path(root, name):
    require(isinstance(name, str) and bool(name) and
            not any(c in name for c in "\\:\r\n\0"), "unsafe artifact path")
    parts = PurePosixPath(name)
    require(not parts.is_absolute() and ".." not in parts.parts, "artifact path escapes receipt root")
    path = root.joinpath(*parts.parts)
    require(path.resolve().is_relative_to(root.resolve()) and path.is_file() and not path.is_symlink(),
            "missing/unsafe artifact: " + name)
    # A linked parent could otherwise resolve to an unrelated audit tree.
    require(not any(p.is_symlink() for p in (path, *path.parents) if p != root.parent),
            "linked artifact path refused")
    return path


def validate_receipt(receipt, abi, receipt_root):
    require(receipt["schema"] == "opt50_system_verified_v1" and receipt["status"] == "PASS",
            "a PASS receipt from actual system verification is required")
    require(receipt["tool_version"] == "2026.1" and integer(receipt["clock_hz"]) == 125000000 and
            integer(receipt["axi_bits"]) == 128, "verified tool/clock/AXI differs from OPT50 recipe")
    require(receipt["register_header_sha256"] == abi["register_header_sha256"],
            "receipt/ABI register-header identity differs")
    require(integer(receipt["control_base"]) == 0xA0000000 and
            integer(receipt["observer_base"]) == 0xA0010000, "verified control address map differs")
    require(isinstance(receipt["run_id"], str) and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", receipt["run_id"]), "invalid verified run_id")
    require(isinstance(receipt["metadata_uuid"], str), "metadata UUID must be a string")
    identity = str(uuid.UUID(receipt["metadata_uuid"]))
    require(identity == receipt["metadata_uuid"].lower() and identity not in (OLD_UUID, str(uuid.UUID(int=0))),
            "new nonzero OPT50 metadata UUID required; frozen H576 UUID rejected")
    tags = receipt["observer_tags"]
    require(isinstance(tags, list) and len(tags) == 2 and
            all(type(tag) is int and 0 <= tag <= 0xFFFFFFFF for tag in tags) and
            tuple(tags) not in (OLD_TAGS, (0, 0)), "new uint32 observer fingerprint pair required")
    records = receipt["artifacts"]
    require(isinstance(records, dict) and set(records) == SYSTEM_ARTIFACTS,
            "verified artifact inventory must contain the four OPT50 system exports")
    require(receipt_root is not None, "receipt_root is required to recheck actual verified artifacts")
    root = Path(receipt_root).resolve(strict=True)
    for name, record in records.items():
        require(isinstance(record, dict) and type(record["bytes"]) is int and record["bytes"] > 0 and
                isinstance(record["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]),
                "invalid verified artifact record")
        path = artifact_path(root, name)
        require(path.stat().st_size == record["bytes"] and sha256(path.read_bytes()) == record["sha256"],
                "verified artifact changed: " + name)
    return identity


def embedded_xml():
    project = ET.Element("project", name="kv260_opt50")
    platform = ET.SubElement(project, "platform", vendor="xilinx", boardid="kv260",
                             name="kv260_opt50", featureRomTime="0")
    ET.SubElement(platform, "version", major="1", minor="0")
    device = ET.SubElement(platform, "device", name="fpga0", fpgaDevice="xck26-sfvc784-2LV-c", addrWidth="64")
    core = ET.SubElement(device, "core", name="OCL_REGION_0", target="hw", type="clc_region",
                         clockFreq="125MHz", numComputeUnits="2")
    for name, instance, base in CU_BASES:
        # This declares XRT's user-managed xrt::ip access. RTL remains ap_ctrl_hs.
        kernel = ET.SubElement(core, "kernel", name=name, language="ip_c", vlnv="local:user:" + name + ":1.0",
                               attributes="", preferredWorkGroupSizeMultiple="0", workGroupSize="1",
                               debug="false", interrupt="false", hwControlProtocol="ap_ctrl_none")
        ET.SubElement(kernel, "port", name="S_AXI_CONTROL", mode="slave", range="0x10000", dataWidth="32",
                      portType="addressable", base="0x0")
        if name == "model_gemm_top":
            for master in dict.fromkeys(MASTERS):
                ET.SubElement(kernel, "port", name=master, mode="master", range="0xFFFFFFFFFFFFFFFF",
                              dataWidth="128", portType="addressable", base="0x0")
            for index, (arg, offset) in enumerate(OFFSETS.items()):
                pointer = index < 4
                ET.SubElement(kernel, "arg", name=arg, addressQualifier="1" if pointer else "0",
                              id=str(index), port=MASTERS[index] if pointer else "S_AXI_CONTROL",
                              size="0x8" if pointer else "0x4", offset=hex(offset), hostOffset="0x0",
                              hostSize="0x8" if pointer else "0x4", type="ap_uint<128>*" if pointer else "unsigned int")
        ET.SubElement(kernel, "compileWorkGroupSize", x="1", y="1", z="1")
        ET.SubElement(kernel, "maxWorkGroupSize", x="1", y="1", z="1")
        ET.SubElement(ET.SubElement(kernel, "instance", name=instance), "addrRemap", base=base,
                      range="0x10000", port="S_AXI_CONTROL")
    ET.indent(project)
    return ET.tostring(project, encoding="utf-8", xml_declaration=True) + b"\n"


def generate(out, abi, receipt, *, receipt_root=None):
    """Write a fresh section-source directory; actual verified artifacts are rehashed."""
    validate_abi(abi)
    identity = validate_receipt(receipt, abi, receipt_root)
    out = Path(out).resolve()
    require(not out.exists(), "metadata output already exists; choose a NEW directory")
    root = Path(receipt_root).resolve(strict=True)
    require(not root.is_relative_to(out), "metadata output must not contain its verified input tree")
    ips = [{"m_type": "IP_KERNEL", "m_int_enable": "0", "m_interrupt_id": "0",
            "m_ip_control": "AP_CTRL_NONE", "m_base_address": base, "m_name": name + ":" + instance}
           for name, instance, base in CU_BASES]
    memory = [{"m_type": "MEM_DDR4", "m_used": "1", "m_sizeKB": "0x200000", "m_tag": tag,
               "m_base_address": base} for tag, base in (("HP0_LOW", "0x0"), ("HP0_HIGH", "0x800000000"))]
    connections = [{"arg_index": str(arg), "m_ip_layout_index": "0", "mem_data_index": str(bank)}
                   for arg in range(4) for bank in range(2)]
    outputs = {
        "ip_layout.json": json_bytes({"ip_layout": {"m_count": "2", "m_ip_data": ips}}),
        "mem_topology.json": json_bytes({"mem_topology": {"m_count": "2", "m_mem_data": memory}}),
        "connectivity.json": json_bytes({"connectivity": {"m_count": "8", "m_connection": connections}}),
        "embedded_metadata.xml": embedded_xml(),
        "xrt.ini": b"[Runtime]\nenable_flat=false\nenable_pr=false\nert_polling=true\nrw_shared=false\n",
    }
    manifest = {
        "schema": "opt50_metadata_sources_v1", "status": "METADATA_SOURCES_READY_NOT_PACKED",
        "abi": ABI, "format": FORMAT, "run_id": receipt["run_id"], "metadata_uuid": identity,
        "observer_tags": receipt["observer_tags"], "register_header_sha256": abi["register_header_sha256"],
        "verified_receipt_sha256": sha256(json_bytes(receipt)),
        "verified_artifacts": receipt["artifacts"], "clock_hz": 125000000, "axi_bits": 128,
        "memory_note": "Two physical-policy DDR windows of 2 GiB each; NOT the wider HWH address aperture",
        "section_sources": {"EMBEDDED_METADATA": "embedded_metadata.xml", "IP_LAYOUT": "ip_layout.json",
                            "MEM_TOPOLOGY": "mem_topology.json", "CONNECTIVITY": "connectivity.json"},
        "allowed_packed_sections": ["EMBEDDED_METADATA", "IP_LAYOUT", "MEM_TOPOLOGY", "CONNECTIVITY",
                                    "ASK_GROUP_TOPOLOGY", "ASK_GROUP_CONNECTIVITY"],
        "BITSTREAM_section": "FORBIDDEN", "xclbin_packing": "NOT_RUN", "board": "NOT_RUN",
        "verification_scope": "NOT_BOARD_TEST; verified system artifacts and metadata source ABI only",
        "files": {name: {"bytes": len(raw), "sha256": sha256(raw)} for name, raw in outputs.items()},
    }
    outputs["metadata_manifest.json"] = json_bytes(manifest)
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        with (out / name).open("xb") as stream:
            stream.write(raw)
    return {"status": manifest["status"], "output": str(out), "metadata_uuid": identity,
            "files": len(outputs), "arguments": 8, "connections": 8,
            "xclbin_packing": "NOT_RUN", "board": "NOT_RUN"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abi", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="new output directory; never overwritten")
    args = parser.parse_args()
    abi = json.loads(args.abi.read_bytes())
    receipt = json.loads(args.receipt.read_bytes())
    print(json.dumps(generate(args.out, abi, receipt, receipt_root=args.receipt.parent), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError) as error:
        raise SystemExit("REFUSED: " + str(error))
