"""Prepare a NEW standalone system source snapshot; never starts Vivado or a device."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE_INDEX_SHA = "0cc0bfc66ed63697743a61449357c95825c4aff727e340f3f1cc9c8f06408a88"
HEADER_SHA = "fc243d24a6e87de438fa0db99e9ade55a3c1467b9ed9346a58eb34c95cf91c73"
COMPONENT_SHA = "e36268ecfe2f248aaba838c0862c24ba256e5ab9e4924b6d8a0ecf5f005d3fd0"
XSA_CHECKER_SHA = "4ae3dde437978a8b8d2e1735f07a7e51ea757ab9c468e2d3b2c6986bb3266d3b"
NS = {"s": "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_file(root, name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and ":" not in name and "\\" not in name,
            "unsafe relative file")
    result = root.joinpath(*path.parts)
    require(result.resolve().is_relative_to(root.resolve()) and result.is_file() and not result.is_symlink(),
            "missing/unsafe regular file: " + name)
    return result


def xsa_checker_source():
    """Return the reviewed shared checker, local in a prepared snapshot.

    The source workspace keeps its single implementation in verification/;
    prepare() copies that exact file into the standalone scripts directory.
    A present but changed local copy is never replaced by a fallback.
    """
    local = ROOT / "scripts/xsa_hierarchy.py"
    if local.exists():
        source = relative_file(ROOT, "scripts/xsa_hierarchy.py")
    else:
        source = relative_file(ROOT.parent, "verification/xsa_hierarchy.py")
    require(digest(source) == XSA_CHECKER_SHA, "reviewed XSA hierarchy checker changed")
    return source


def patch_burst_xml(raw):
    """Only correct the three exported interface declarations, not RTL or other fields."""
    root = ET.fromstring(raw)
    names = {"m_axi_gmem0", "m_axi_gmem1", "m_axi_gmem2"}
    before = {}
    for bus in root.findall("s:busInterfaces/s:busInterface", NS):
        name = bus.findtext("s:name", namespaces=NS)
        if name not in names:
            continue
        parameters = {p.findtext("s:name", namespaces=NS): p.findtext("s:value", namespaces=NS)
                      for p in bus.findall("s:parameters/s:parameter", NS)}
        require(parameters.get("HAS_BURST") in ("0", "1"), "unreviewed Burst declaration")
        require(parameters.get("ADDR_WIDTH") == "64", "expected 64-bit AXI address")
        before[name] = parameters["HAS_BURST"]
    require(set(before) == names, "three AXI master declarations required")
    text = raw.decode("utf-8")
    for index in range(3):
        pattern = (r'(<spirit:value\b[^>]*spirit:id="BUSIFPARAM_VALUE\.M_AXI_GMEM' + str(index) +
                   r'\.HAS_BURST"[^>]*>)[01](</spirit:value>)')
        text, count = re.subn(pattern, lambda m: m[1] + "1" + m[2], text)
        require(count == 1, "Burst XML identity/count mismatch")
    return text.encode("utf-8"), before


def verify_reference(root):
    require(digest(root / "asset_manifest.json") == BASE_INDEX_SHA, "unreviewed reference source index")
    index = json.loads((root / "asset_manifest.json").read_bytes())
    for name, item in index["files"].items():
        file = relative_file(root, name)
        require(file.stat().st_size == item["bytes"] and digest(file) == item["sha256"],
                "reference source changed: " + name)
    return len(index["files"])


def verify_ip(ip, abi):
    require(digest(ip / "component.xml") == COMPONENT_SHA == abi["component_xml_sha256"], "OPT50 IP export changed")
    header = ip / "drivers/model_gemm_top_v1_0/src/xmodel_gemm_top_hw.h"
    require(digest(header) == HEADER_SHA == abi["register_header_sha256"], "OPT50 register header changed")
    top = relative_file(ip, "hdl/verilog/model_gemm_top.v").read_text(encoding="utf-8")
    for index in range(3):
        require(re.search(r"C_M_AXI_GMEM" + str(index) + r"_DATA_WIDTH\s*=\s*128\b", top), "RTL master width is not 128")
        adapter = relative_file(ip, f"hdl/verilog/model_gemm_top_gmem{index}_m_axi.v").read_text(encoding="utf-8")
        signal = "AR" if index < 2 else "AW"
        require(re.search(r"assign\s+out_BUS_" + signal + r"BURST\s*=\s*2'b01\s*;", adapter),
                "actual RTL does not match INCR Burst correction")
    return patch_burst_xml((ip / "component.xml").read_bytes())


def copy_regular(source, destination):
    require(source.is_file() and not source.is_symlink(), "not a regular source file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def prepare(ip, reference, abi_path, out, cases=None):
    ip, reference, abi_path = ip.resolve(strict=True), reference.resolve(strict=True), abi_path.resolve(strict=True)
    out = out.resolve()
    require(not out.exists(), "destination already exists; choose a NEW directory")
    for source_root in (ip, reference, ROOT):
        require(not out.is_relative_to(source_root) and not source_root.is_relative_to(out), "overlapping source/destination")
    abi = json.loads(abi_path.read_bytes())
    require(abi["abi"] == "OPT50_SPLIT128_AP_CTRL_HS_V1" and abi["clock_hz"] == 125000000, "ABI/clock mismatch")
    count = verify_reference(reference)
    patched, burst_before = verify_ip(ip, abi)
    files = []
    for file in sorted(ip.rglob("*")):
        if file.is_dir():
            require(not file.is_symlink(), "linked IP directory refused")
            continue
        require(not file.is_symlink(), "linked IP file refused")
        if file.suffix.lower() in (".zip", ".log", ".jou") or ".Xil" in file.parts:
            continue
        files.append((file, Path("input/ip") / file.relative_to(ip)))
    for name in ("f8_status.v", "tb_f8_status.sv"):
        files.append((relative_file(reference, "hardware/kv260_f8/" + name), Path("input/observer") / name))
    board_root = reference / "board/hw/board_files"
    for file in sorted(board_root.rglob("*")):
        if file.is_file():
            require(not file.is_symlink(), "linked board file refused")
            files.append((file, Path("input/board_files") / file.relative_to(board_root)))
    for name in ("README.md", "BOARD_READINESS.md", ".gitignore"):
        files.append((relative_file(ROOT, name), Path(name)))
    for directory in ("scripts", "tests", "config"):
        for file in sorted((ROOT / directory).rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                files.append((file, file.relative_to(ROOT)))
    # Keep one implementation: a fresh standalone snapshot needs no external
    # workspace imports, and its recipe inventories this exact reviewed copy.
    xsa_checker = xsa_checker_source()
    if xsa_checker != ROOT / "scripts/xsa_hierarchy.py":
        files.append((xsa_checker, Path("scripts/xsa_hierarchy.py")))
    # Preserve the exact software ABI auditor used by the standalone tests.
    auditor = ROOT / "scripts/audit_runtime_metadata.py"
    if not auditor.exists():
        files.append((relative_file(ROOT.parent, "scripts/prepare_cases.py"), Path("scripts/audit_runtime_metadata.py")))
    require(len(files) == len({rel.as_posix().casefold() for _, rel in files}), "duplicate output paths")
    out.mkdir(parents=True, exist_ok=False)
    for source, relative in files:
        copy_regular(source, out / relative)
    copy_regular(abi_path, out / "input/opt50_abi.json")
    # This is a generated IP snapshot, not an edit to the frozen HLS export.
    (out / "input/ip/component.xml").write_bytes(patched)
    xml = ET.fromstring(patched)
    for entry in xml.findall("s:fileSets/s:fileSet/s:file/s:name", NS):
        relative_file(out / "input/ip", entry.text)
    from model_patch import prepare_patch
    local_model = prepare_patch(out / "input/sim_model")
    real_cases = None
    if cases is not None:
        spec = importlib.util.spec_from_file_location("_opt50_frozen_real_case_generator", ROOT / "tests/real_case_tb_generator.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        real_cases = generator.generate(cases, out / "input/sim_cases")
    inventory = {p.relative_to(out).as_posix(): {"bytes": p.stat().st_size, "sha256": digest(p)}
                 for p in sorted(out.rglob("*")) if p.is_file()}
    identity = dict(schema="opt50_system_recipe_v1", status="PREPARED_NOT_RUN", tool_version="2026.1",
                    part="xck26-sfvc784-2LV-c", board_part="xilinx.com:kv260:part0:1.1", clock_hz=125000000,
                    abi=abi["abi"], register_header_sha256=HEADER_SHA, control_base="0xA0000000",
                    observer_base="0xA0010000", axi_bits=128, reference_index_sha256=BASE_INDEX_SHA,
                    reference_members_verified=count, source_ip=str(ip), source_reference=str(reference),
                    ip_component_before_sha256=COMPONENT_SHA,
                    ip_component_after_sha256=digest(out / "input/ip/component.xml"), burst_before=burst_before,
                    ip_correction="HAS_BURST declarations only; RTL bytes unchanged", files=inventory,
                    simulation_model="LOCAL_ARB_FIXED_V1", local_model_manifest=local_model,
                    real_case_profile=real_cases["profile"] if real_cases else "NOT_PREPARED",
                    vivado="NOT_RUN", board="NOT_RUN")
    (out / "project_manifest.json").write_text(json.dumps(identity, indent=2)+"\n", encoding="utf-8")
    return dict(status="SYSTEM_SOURCE_PREPARED_NO_VIVADO_OR_BOARD", output=str(out), files=len(inventory),
                reference_members_verified=count, rtl_changed=False, vivado="NOT_RUN", board="NOT_RUN")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--abi", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="pinned real layer package; required for Stage logic/build")
    args = parser.parse_args()
    print(json.dumps(prepare(args.ip, args.reference, args.abi, args.out, args.cases), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, ET.ParseError) as error:
        raise SystemExit("REFUSED: " + str(error))
