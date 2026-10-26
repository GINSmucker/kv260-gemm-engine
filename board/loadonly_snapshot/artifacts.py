"""Read-only, stdlib-only audit for the fd96539d deployment preparation.

There are no subprocesses, XRT imports, locks, device files or import-time I/O.
PASS means file provenance and layouts only, never board/load authorization.
All expected hashes are review-time pins, not learned from an input manifest.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import uuid
import xml.etree.ElementTree as ET

UUID = "fd96539d-4cbc-f732-7694-0f0236ca1cf3"
TAGS = [4254487453, 1287452466]
APP = "kv260_opt50_fd96539d_4cbcf732"
RUN = "build_20261024_132157_775_c2e60ae4"
ABI = "OPT50_SPLIT128_AP_CTRL_HS_V1"
FORMAT = "H576_SPARSE24_SPLIT128_V1"
HEADER_SHA = "fc243d24a6e87de438fa0db99e9ade55a3c1467b9ed9346a58eb34c95cf91c73"
PACKAGE_SHA = "6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf"
BUNDLE_MANIFEST_SHA = "a78a2ce9235b98eff4c1843c2fd624882ff8f671935183a4ec675bf15c1c3732"
XML_SHA = "17fc0eed29036988291cab6da5a1b2724a249c77e5ec21f6299ce99782784df7"
GENERATED = {
    "generated_v1/" + APP + ".dtbo": "0cd94eaa4c2907ea42b55e4b360a920330843521d646eea227d4f6d4e11a7b0f",
    "generated_v1/opt50_layer_smoke_xrt": "0caf49f354dedd64ef73b3cec357b1b491baefbfcb762b60382aa797fb7a64ae",
    "generated_v1/model_metadata_fd96539d_v1.xclbin": "715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551",
}
DTBO_REL = "generated_v1/" + APP + ".dtbo"
RUNTIME_REL = "generated_v1/opt50_layer_smoke_xrt"
XCLBIN_REL = "generated_v1/model_metadata_fd96539d_v1.xclbin"
REPORT_REL = "generated_v1/offline_regression_v1/regression.json"
OFFSETS = {"X": 0x18, "W_data": 0x24, "W_meta": 0x30, "Y": 0x3c,
           "M": 0x48, "K": 0x50, "N": 0x58, "mode": 0x60}
LAYERS = (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def no_links(path):
    for item in (path, *path.parents):
        require(not item.is_symlink() and not (hasattr(item, "is_junction") and item.is_junction()),
                "linked path refused: " + str(item))


def safe_relative(name):
    require(isinstance(name, str) and bool(name) and not any(c in name for c in "\\:\r\n\0"),
            "unsafe relative name")
    parts = name.split("/")
    value = PurePosixPath(name)
    require(not value.is_absolute() and all(p not in ("", ".", "..") for p in parts),
            "relative path escapes root")
    return value


def regular(root, name):
    path = root.joinpath(*safe_relative(name).parts)
    no_links(path)
    require(path.resolve(strict=True).is_relative_to(root), "file escapes root: " + name)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= 32 * 1024 * 1024,
            "missing/nonregular/oversized audit file: " + name)
    return path


def read(root, name):
    path = regular(root, name)
    with path.open("rb") as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    require(len(raw) <= 32 * 1024 * 1024, "audit file grew beyond limit")
    return raw


def json_object(raw, label):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key: " + label)
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("invalid JSON: " + label) from error
    require(isinstance(value, dict), "JSON object required: " + label)
    return value


def check_record(value):
    require(isinstance(value, dict) and set(value) == {"bytes", "sha256"} and
            type(value["bytes"]) is int and value["bytes"] >= 0 and
            isinstance(value["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]),
            "invalid file record")


def check_xclbin(raw):
    """Check public XRT 2024.2 native layout; no xclbinutil/device access."""
    raw = bytes(raw)
    require(len(raw) >= 696 and raw[:8] == b"xclbin2\0", "invalid xclbin header")
    require(struct.unpack_from("<i", raw, 8)[0] == -1, "unreviewed xclbin signature/trailer")
    length = struct.unpack_from("<Q", raw, 304)[0]
    mode, action = struct.unpack_from("<HH", raw, 332)
    identity = str(uuid.UUID(bytes=raw[416:432]))
    count = struct.unpack_from("<I", raw, 448)[0]
    require(length == len(raw) and mode == 0 and action == 0 and count == 6 and identity == UUID,
            "xclbin size/mode/action/UUID/section count")
    require(tuple(raw[330:332]) == (2, 18), "unreviewed xclbin header version")
    names = {2: "EMBEDDED_METADATA", 6: "MEM_TOPOLOGY", 7: "CONNECTIVITY", 8: "IP_LAYOUT",
             26: "GROUP_TOPOLOGY", 27: "GROUP_CONNECTIVITY"}
    sections, ranges = {}, []
    for index in range(count):
        kind, _, offset, size = struct.unpack_from("<I16s4xQQ", raw, 456 + index * 40)
        require(kind in names and kind not in sections and size > 0 and offset % 8 == 0 and
                offset >= 456 + count * 40 and offset + size <= len(raw), "invalid/unsafe xclbin section")
        require(all(offset + size <= lo or hi <= offset for lo, hi in ranges), "overlapping xclbin sections")
        ranges.append((offset, offset + size))
        sections[kind] = raw[offset:offset + size]
    require(set(sections) == set(names), "programming/missing xclbin section")
    embedded = sections[2]
    require(re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", embedded, re.I) is None, "XML declarations refused")
    try:
        root = ET.fromstring(embedded)
    except ET.ParseError as error:
        raise ValueError("invalid embedded XML") from error
    core = root.find("./platform/device/core")
    require(root.tag == "project" and core is not None and core.get("clockFreq") == "125MHz" and
            core.get("numComputeUnits") == "2", "embedded core/clock contract")
    kernels = core.findall("kernel")
    expected_cus = [("model_gemm_top", "gemm_1", 0xa0000000), ("axi_observer", "status_1", 0xa0010000)]
    require(len(kernels) == 2, "unexpected embedded kernel count")
    for kernel, (name, instance, base) in zip(kernels, expected_cus):
        require(kernel.get("name") == name and kernel.get("hwControlProtocol") == "ap_ctrl_none",
                "user-managed CU identity/protocol mismatch")
        instances = kernel.findall("instance")
        require(len(instances) == 1 and instances[0].get("name") == instance, "CU instance mismatch")
        remaps = instances[0].findall("addrRemap")
        require(len(remaps) == 1 and int(remaps[0].get("base", "-1"), 0) == base and
                int(remaps[0].get("range", "-1"), 0) == 65536, "CU address remap mismatch")
    args = kernels[0].findall("arg")
    require(len(args) == 8 and not kernels[1].findall("arg"), "eight OPT50 arguments required")
    ports = kernels[0].findall("port")
    port_map = {p.get("name"): p for p in ports}
    require(len(port_map) == len(ports), "duplicate embedded port")
    masters = ("M_AXI_GMEM0", "M_AXI_GMEM1", "M_AXI_GMEM1", "M_AXI_GMEM2")
    for i, (name, offset) in enumerate(OFFSETS.items()):
        arg = args[i]
        size = 8 if i < 4 else 4
        require(arg.get("name") == name and int(arg.get("id", "-1"), 0) == i and
                int(arg.get("offset", "-1"), 0) == offset and int(arg.get("size", "-1"), 0) == size and
                arg.get("addressQualifier") == ("1" if i < 4 else "0") and
                arg.get("port") == (masters[i] if i < 4 else "S_AXI_CONTROL") and
                int(arg.get("hostSize", "-1"), 0) == size and int(arg.get("hostOffset", "-1"), 0) == 0,
                "OPT50 argument ABI mismatch")
    require(set(port_map) == {"S_AXI_CONTROL", "M_AXI_GMEM0", "M_AXI_GMEM1", "M_AXI_GMEM2"} and
            all(port_map["M_AXI_GMEM" + str(i)].get("dataWidth") == "128" and
                port_map["M_AXI_GMEM" + str(i)].get("mode") == "master" for i in range(3)) and
            port_map["S_AXI_CONTROL"].get("dataWidth") == "32", "AXI width/port mismatch")
    require(sha(embedded) == XML_SHA, "packed XML differs from pinned reviewed XML")
    ip = sections[8]
    require(len(ip) == 168 and struct.unpack_from("<i", ip)[0] == 2, "IP_LAYOUT shape")
    for i, (name, instance, base) in enumerate(expected_cus):
        typ, props, address, label = struct.unpack_from("<IIQ64s", ip, 8 + i * 80)
        expected_label = (name + ":" + instance).encode()
        require((typ, props, address, label) == (1, 512, base, expected_label.ljust(64, b"\0")),
                "IP_LAYOUT identity/control/address mismatch")
    expected_memory = [(1, 1, 0x200000, 0, b"HP0_LOW".ljust(16, b"\0")),
                       (1, 1, 0x200000, 0x800000000, b"HP0_HIGH".ljust(16, b"\0"))]
    for kind in (6, 26):
        block = sections[kind]
        require(len(block) == 88 and struct.unpack_from("<i", block)[0] == 2, "memory topology count/size")
        actual = [struct.unpack_from("<BB6xQQ16s", block, 8 + i * 40) for i in range(2)]
        require(actual == expected_memory, "DDR topology mismatch/unsafe merged window")
    require(sections[6] == sections[26], "group topology differs from two noncontiguous windows")
    connections = {(i, 0, bank) for i in range(4) for bank in range(2)}
    for kind, copies in ((7, 1), (27, 2)):
        block = sections[kind]
        require(len(block) == 4 + 8 * copies * 12 and struct.unpack_from("<i", block)[0] == 8 * copies,
                "connectivity count/size mismatch")
        actual = [struct.unpack_from("<iii", block, 4 + i * 12) for i in range(8 * copies)]
        require(collections.Counter(actual) == collections.Counter({c: copies for c in connections}),
                "connectivity tuple/multiplicity mismatch")
    return {"uuid": identity, "section_count": 6, "arguments": 8, "register_count": 17,
            "control_base": "0xA0000000", "observer_base": "0xA0010000", "axi_bits": 128,
            "section_files": {names[k]: record(b) for k, b in sections.items()}}


def fdt_properties(raw):
    raw = bytes(raw)
    require(len(raw) >= 56, "truncated FDT")
    magic, total, off_struct, off_strings, off_rsv, version, compatible, cpu, len_strings, len_struct = struct.unpack_from(
        ">10I", raw)
    require(magic == 0xd00dfeed and total == len(raw) and version == 17 and compatible == 16 and cpu == 0 and
            off_struct >= 40 and off_struct % 4 == 0 and off_strings + len_strings == total and
            off_struct + len_struct <= off_strings and off_rsv >= 40 and off_rsv + 16 <= off_struct and
            raw[off_rsv:off_rsv+16] == bytes(16), "unsupported FDT layout/reserved memory")
    strings = raw[off_strings:off_strings + len_strings]
    at, end, stack, properties, nodes = off_struct, off_struct + len_struct, [], {}, set()
    while at < end:
        require(at + 4 <= end, "truncated FDT token")
        token = struct.unpack_from(">I", raw, at)[0]
        at += 4
        if token == 1:
            stop = raw.find(b"\0", at, end)
            require(stop >= at, "unterminated FDT node")
            try:
                name = raw[at:stop].decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError("invalid FDT node encoding") from error
            require("/" not in name and (bool(stack) and bool(name) or not stack and name == "" and not nodes),
                    "invalid FDT node name/root")
            stack.append(name)
            path = "/".join(stack) or "/"
            require(path not in nodes, "duplicate FDT node")
            nodes.add(path)
            at = (stop + 4) & ~3
        elif token == 2:
            require(bool(stack), "unbalanced FDT nodes")
            stack.pop()
        elif token == 3:
            require(stack and at + 8 <= end, "orphan/truncated FDT property")
            size, offset = struct.unpack_from(">II", raw, at)
            at += 8
            require(offset < len(strings) and at + size <= end, "FDT property outside bounds")
            stop = strings.find(b"\0", offset)
            require(stop >= offset, "FDT string outside bounds")
            try:
                name = strings[offset:stop].decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError("invalid FDT property encoding") from error
            key = ("/".join(stack) or "/", name)
            require(name and key not in properties, "duplicate/empty FDT property")
            properties[key] = raw[at:at + size]
            at = (at + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            require(not stack and at == end and nodes == expected_fdt_nodes(), "incomplete/extra/trailing FDT structure")
            return properties
        else:
            raise ValueError("unknown FDT token")
    raise ValueError("FDT END token missing")


def expected_fdt_nodes():
    return {"/", "/fragment@0", "/fragment@0/__overlay__", "/fragment@1", "/fragment@1/__overlay__",
            "/fragment@1/__overlay__/opt50_pl0_clock", "/fragment@1/__overlay__/zyxclmm_drm", "/__fixups__"}


def expected_fdt_properties():
    def cells(*values):
        return struct.pack(">" + "I" * len(values), *values)
    f0, f1 = "/fragment@0", "/fragment@1"
    overlay = f0 + "/__overlay__"
    clock = f1 + "/__overlay__/opt50_pl0_clock"
    drm = f1 + "/__overlay__/zyxclmm_drm"
    return {
        (f0, "target"): cells(0xffffffff), (overlay, "#address-cells"): cells(2),
        (overlay, "#size-cells"): cells(2), (overlay, "firmware-name"): (APP + ".bit.bin\0").encode(),
        (overlay, "resets"): cells(0xffffffff, 116, 0xffffffff, 117, 0xffffffff, 118, 0xffffffff, 119),
        (f1, "target"): cells(0xffffffff), (clock, "compatible"): b"xlnx,fclk\0",
        (clock, "clocks"): cells(0xffffffff, 71), (clock, "assigned-clocks"): cells(0xffffffff, 71),
        (clock, "assigned-clock-rates"): cells(125000000),
        (drm, "compatible"): b"xlnx,zocl\0", (drm, "status"): b"okay\0",
        ("/__fixups__", "fpga_full"): b"/fragment@0:target:0\0",
        ("/__fixups__", "amba"): b"/fragment@1:target:0\0",
        ("/__fixups__", "zynqmp_reset"): b"".join((overlay + ":resets:" + str(i)).encode() + b"\0"
                                                  for i in (0, 8, 16, 24)),
        ("/__fixups__", "zynqmp_clk"): (clock + ":clocks:0\0" + clock + ":assigned-clocks:0\0").encode(),
    }


def check_fdt(raw):
    properties = fdt_properties(raw)
    require(properties == expected_fdt_properties(), "DTBO firmware/clock/reset/zocl/fixup properties differ")
    return {"properties": len(properties), "clock_hz": 125000000, "firmware_file": APP + ".bit.bin",
            "symbols": ["fpga_full", "amba", "zynqmp_clk", "zynqmp_reset"]}


def check_bundle(root):
    raw = read(root, "deployment_manifest.json")
    require(sha(raw) == BUNDLE_MANIFEST_SHA, "pinned deployment manifest differs")
    manifest = json_object(raw, "deployment manifest")
    require(manifest.get("schema") == "opt50_deployment_preparation_v1" and
            manifest.get("status") == "PREPARED_SOURCES_NOT_LOAD_READY" and manifest.get("run_id") == RUN and
            manifest.get("metadata_uuid") == UUID and manifest.get("observer_tags") == TAGS and
            manifest.get("firmware_app") == APP and manifest.get("firmware_file") == APP + ".bit.bin" and
            manifest.get("derived_firmware_pairing") == "BIT_PAYLOAD_32BIT_BYTE_SWAP" and
            manifest.get("hardware_authorized") is False and manifest.get("active_loader_included") is False and
            manifest.get("physical_PL_starts") == 0 and manifest.get("board") == "NOT_RUN" and
            isinstance(manifest.get("files"), dict) and len(manifest["files"]) == 28,
            "bundle schema/identity/preparation scope differs")
    files = {"deployment_manifest.json": record(raw)}
    for name, expected in manifest["files"].items():
        check_record(expected)
        data = read(root, name)
        require(record(data) == expected, "bundle payload differs: " + name)
        files[name] = record(data)
    receipt_raw = read(root, "hardware/system_verified.json")
    receipt = json_object(receipt_raw, "system receipt")
    require(record(receipt_raw) == manifest.get("system_receipt") and
            receipt.get("schema") == "opt50_system_verified_v1" and receipt.get("status") == "PASS" and
            receipt.get("verifier_version") == "2" and receipt.get("board") == "NOT_RUN" and
            receipt.get("run_id") == RUN and receipt.get("metadata_uuid") == UUID and receipt.get("observer_tags") == TAGS and
            receipt.get("register_header_sha256") == HEADER_SHA and receipt.get("register_count") == 17 and
            receipt.get("axi_bits") == 128 and receipt.get("clock_hz") == 125000000 and
            receipt.get("control_base") == "0xA0000000" and receipt.get("observer_base") == "0xA0010000",
            "system receipt identity/ABI mismatch")
    names = {"artifacts/kv260_opt50" + suffix for suffix in (".bit", ".bit.bin", ".hwh", ".xsa")}
    require(set(receipt.get("artifacts", {})) == names, "system artifact inventory mismatch")
    for name, expected in receipt["artifacts"].items():
        require(record(read(root, "hardware/" + name)) == expected, "receipt/raw artifact mismatch")
    source = read(root, "hardware/artifacts/kv260_opt50.bit.bin")
    firmware = read(root, "firmware/" + APP + ".bit.bin")
    require(len(source) % 4 == 0 and firmware == b"".join(source[i:i+4][::-1] for i in range(0, len(source), 4)) and
            source[80:84] == bytes.fromhex("aa995566") and firmware[80:84] == bytes.fromhex("665599aa"),
            "Bootgen image is not exact complete 32-bit byte-swap derivation")
    require(json_object(read(root, "firmware/shell.json"), "shell") == {"shell_type": "PL_FLAT", "num_slots": "1"},
            "shell contract differs")
    conversion = json_object(read(root, "conversion_evidence/conversion_plan.json"), "conversion plan")
    require(conversion.get("schema") == "opt50_bootgen_input_v1" and conversion.get("run_id") == RUN and
            conversion.get("metadata_uuid") == UUID and conversion.get("source_receipt") == record(receipt_raw) and
            conversion.get("files", {}).get("system.bit") == receipt["artifacts"]["artifacts/kv260_opt50.bit"] and
            conversion.get("files", {}).get("conversion.bif") == record(read(root, "conversion_evidence/conversion.bif")) and
            conversion.get("expected_output") == "system.bit.bin" and conversion.get("board") == "NOT_RUN",
            "Bootgen conversion provenance differs")
    metadata = json_object(read(root, "metadata_sources/metadata_manifest.json"), "metadata source manifest")
    canonical = (json.dumps(receipt, indent=2, ensure_ascii=False) + "\n").encode()
    require(metadata.get("schema") == "opt50_metadata_sources_v1" and metadata.get("abi") == ABI and
            metadata.get("format") == FORMAT and metadata.get("run_id") == RUN and
            metadata.get("metadata_uuid") == UUID and metadata.get("observer_tags") == TAGS and
            metadata.get("verified_receipt_sha256") == sha(canonical) and metadata.get("verified_artifacts") == receipt["artifacts"] and
            metadata.get("register_header_sha256") == HEADER_SHA and metadata.get("clock_hz") == 125000000 and
            metadata.get("axi_bits") == 128 and metadata.get("BITSTREAM_section") == "FORBIDDEN" and
            metadata.get("board") == "NOT_RUN", "metadata/system provenance differs")
    expected_sources = {"embedded_metadata.xml", "ip_layout.json", "mem_topology.json", "connectivity.json", "xrt.ini"}
    require(set(metadata.get("files", {})) == expected_sources, "metadata source inventory differs")
    for name, expected in metadata["files"].items():
        require(record(read(root, "metadata_sources/" + name)) == expected, "metadata source payload differs")
    return files, receipt


def check_offline(report, runtime_sha):
    expected_keys = {"schema", "status", "jobs", "compared_INT32_outputs", "physical_PL_starts", "scope",
                     "executable_sha256", "package_unchanged", "negative_cases", "results"}
    require(set(report) == expected_keys and report.get("schema") == "opt50_offline_regression_v1" and
            report.get("status") == "PASS" and report.get("jobs") == 48 and
            report.get("compared_INT32_outputs") == 1870848 and type(report.get("physical_PL_starts")) is int and
            report.get("physical_PL_starts") == 0 and report.get("negative_cases") == 4 and
            all(type(report.get(name)) is int for name in ("jobs", "compared_INT32_outputs", "negative_cases")) and
            report.get("package_unchanged") is True and report.get("executable_sha256") == runtime_sha and
            report.get("scope") == "Host integer/reference and file checks only; NOT ARM/XRT compilation or board/RTL proof",
            "offline regression/runtime identity differs")
    values = report.get("results")
    require(isinstance(values, list) and len(values) == 48, "offline regression 48 results required")
    expected = [{"status": "CPU_GOLDEN_MATCH", "case": f"layer_{layer}_{name}", "mode": mode,
                 "elements": m * (576 if layer % 4 == 2 else 192), "physical_PL_starts": 0}
                for layer in LAYERS for name, m in (("public", 197), ("edge", 6)) for mode in (0, 1)]
    for value, wanted in zip(values, expected):
        require(isinstance(value, dict) and value == wanted and
                all(type(value.get(name)) is int for name in ("mode", "elements", "physical_PL_starts")),
                "offline regression result/order differs")
    return {key: value for key, value in report.items() if key != "results"}


def check_cases(cases):
    root = Path(cases).absolute()
    no_links(root)
    root = root.resolve(strict=True)
    require(root.is_dir(), "cases root is not a directory")
    raw = read(root, "manifest.json")
    require(sha(raw) == PACKAGE_SHA, "pinned test case manifest differs")
    manifest = json_object(raw, "case manifest")
    require(manifest.get("schema") == "h576_sparse24_axi_golden_package_v1" and manifest.get("format") == FORMAT and
            isinstance(manifest.get("files"), dict) and len(manifest["files"]) == 129,
            "case manifest schema/inventory differs")
    files = {"manifest.json": record(raw)}
    for name, expected in manifest["files"].items():
        check_record(expected)
        actual = record(read(root, name))
        require(actual == expected, "test case payload differs: " + name)
        files[name] = actual
    layers = sorted({c.get("layer_index") for c in manifest.get("cases", []) if c.get("layer_index") is not None})
    require(layers == list(LAYERS), "case layer inventory differs")
    return {"root": str(root), "manifest_sha256": PACKAGE_SHA, "files": files, "payloads_checked": 129,
            "scope": "Pinned file identity only; no new arithmetic or PL test"}


def audit(package: Path, cases: Path | None = None):
    """Audit an extracted immutable bundle plus its generated_v1 board files.

    Generated files are never trusted merely because an old report says PASS.
    This function rehashes them against the exact values supplied by the user.
    The offline receipt is evidence of an earlier CPU test, not a new test.
    """
    root = Path(package).absolute()
    no_links(root)
    root = root.resolve(strict=True)
    require(root.is_dir(), "deployment package is not a directory")
    files, _ = check_bundle(root)
    for name, expected_sha in GENERATED.items():
        raw = read(root, name)
        require(sha(raw) == expected_sha, "pinned generated artifact differs: " + name)
        files[name] = record(raw)
    raw = read(root, XCLBIN_REL)
    require(len(raw) == 8560, "packed xclbin length differs")
    binary = check_xclbin(raw)
    require(binary["section_files"]["EMBEDDED_METADATA"] == record(read(root, "metadata_sources/embedded_metadata.xml")),
            "packed XML differs from source")
    overlay = check_fdt(read(root, DTBO_REL))
    report_raw = read(root, REPORT_REL)
    report = check_offline(json_object(report_raw, "offline regression"), GENERATED[RUNTIME_REL])
    files[REPORT_REL] = record(report_raw)
    source_paths = {"runtime": str(regular(root, RUNTIME_REL)), "metadata": str(regular(root, XCLBIN_REL)),
                    "dtbo": str(regular(root, DTBO_REL)), "firmware": str(regular(root, "firmware/" + APP + ".bit.bin")),
                    "shell": str(regular(root, "firmware/shell.json")),
                    "xrt_ini": str(regular(root, "metadata_sources/xrt.ini"))}
    return {"schema": "opt50_fd96539d_artifact_audit_v1", "status": "PASS_FILE_AUDIT_ONLY_NOT_LOAD_AUTHORIZATION",
            "package": str(root), "files": files, "source_paths": source_paths,
            "identity": {**binary, "metadata_uuid": UUID, "observer_tags": list(TAGS), "firmware_app": APP,
                         "abi": ABI, "format": FORMAT, "clock_hz": 125000000,
                         "runtime_accepted_linux_rates": [125000000, 124999999], "register_header_sha256": HEADER_SHA},
            "dtbo": overlay, "offline_regression": report,
            "cases": check_cases(cases) if cases is not None else {"status": "NOT_RECHECKED", "manifest_sha256": PACKAGE_SHA},
            "physical_PL_starts": 0, "board": "NOT_RUN"}
