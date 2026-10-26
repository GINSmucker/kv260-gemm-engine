"""Verify real system build artifacts before metadata generation; never touches a device."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

from prepare_system import digest, relative_file, require, HEADER_SHA
from prepare_system import xsa_checker_source
from board_logic import check_board_logic

ROOT = Path(__file__).resolve().parents[1]
# Load the same SHA-pinned implementation from this prepared snapshot (or
# its source-workspace location), without depending on ambient sys.path.
_xsa_spec = importlib.util.spec_from_file_location("_opt50_reviewed_xsa_hierarchy", xsa_checker_source())
_xsa_module = importlib.util.module_from_spec(_xsa_spec)
_xsa_spec.loader.exec_module(_xsa_module)
check_xsa = _xsa_module.check_xsa
ALIASES = {"AP_CTRL": "CTRL", "GIER": "GIE", "IP_IER": "IER", "IP_ISR": "ISR", "ap_return": "RETURN"}
for _name in ("X", "W_data", "W_meta", "Y"):
    ALIASES[_name + "_1"] = _name + "_LOW"
    ALIASES[_name + "_2"] = _name + "_HIGH"


def integer(text):
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


def expected_registers(abi):
    result = dict(CTRL=0, GIE=4, IER=8, ISR=12, RETURN=16)
    for name, item in abi["registers"].items():
        offset = int(item["offset"], 0)
        if item["bits"] == 64:
            result[name + "_LOW"], result[name + "_HIGH"] = offset, offset+4
        else:
            result[name] = offset
    return result


def properties(module):
    return {p.attrib["NAME"]: p.attrib["VALUE"] for p in module.findall("./PARAMETERS/PARAMETER")}


def check_hwh(raw, abi, tags):
    hwh = ET.fromstring(raw)
    modules = {m.attrib["INSTANCE"]: m for m in hwh.findall(".//MODULE")}
    require(all(name in modules for name in ("gemm", "status", "ps")), "missing system modules")
    gemm, status = modules["gemm"], modules["status"]
    require(gemm.attrib.get("VLNV") == "xilinx.com:hls:model_gemm_top:1.0" and
            status.attrib.get("MODTYPE") == "f8_status", "unexpected IP/observer")
    actual = {}
    for register in gemm.findall(".//REGISTER"):
        props = {p.attrib["NAME"]: p.attrib["VALUE"] for p in register.findall("PROPERTY")}
        name = ALIASES.get(register.attrib["NAME"], register.attrib["NAME"])
        require(integer(props["SIZE"]) == 32 and name not in actual, "bad/duplicate register")
        actual[name] = integer(props["ADDRESS_OFFSET"])
    require(actual == expected_registers(abi) and len(actual) == 17, "actual HWH differs from OPT50 eight-argument ABI")
    params = properties(gemm)
    for index in range(3):
        require(params.get(f"C_M_AXI_GMEM{index}_ADDR_WIDTH") == "64" and
                params.get(f"C_M_AXI_GMEM{index}_DATA_WIDTH") == "128", "actual AXI width mismatch")
    observer = properties(status)
    require([integer(observer[f"BUILD_TAG{i}"]) for i in range(2)] == tags, "actual observer tags mismatch")
    ranges = [entry.attrib for entry in hwh.findall(".//MEMRANGE")]
    for index in range(3):
        entries = {e["ADDRESSBLOCK"]: e for e in ranges if e.get("MASTERBUSINTERFACE") == f"m_axi_gmem{index}"}
        require(integer(entries["HP0_DDR_LOW"]["BASEVALUE"]) == 0 and
                integer(entries["HP0_DDR_LOW"]["HIGHVALUE"]) == 0x7fffffff, "DDR low range mismatch")
        require(integer(entries["HP0_DDR_HIGH"]["BASEVALUE"]) == 0x800000000 and
                integer(entries["HP0_DDR_HIGH"]["HIGHVALUE"]) >= 0x87fffffff, "DDR high range too small")
    for name, base in (("gemm", 0xa0000000), ("status", 0xa0010000)):
        entries = [e for e in ranges if e.get("INSTANCE") == name and e.get("MASTERBUSINTERFACE") == "M_AXI_HPM0_FPD"]
        require(len(entries) == 1 and integer(entries[0]["BASEVALUE"]) == base and
                integer(entries[0]["HIGHVALUE"]) == base+65535, "control/observer address mismatch")
    logic = check_board_logic(raw)
    return dict(register_count=len(actual), axi_bits=128, address_ranges=ranges, board_logic=logic)


def check_ps(text, preset):
    def value(name):
        lines = [line for line in text.splitlines() if re.match(r"\s*CONFIG\." + re.escape(name) + r"\s", line)]
        require(len(lines) == 1, "PS property missing/duplicated: " + name)
        match = re.fullmatch(r"\s*CONFIG\." + re.escape(name) + r"\s+\S+\s+(?:true|false|0|1)\s+(.*?)\s*", lines[0], re.IGNORECASE)
        require(match is not None, "unrecognized PS report format: " + name)
        return match[1]
    for name in ("PSU__MAXIGP0__DATA_WIDTH", "PSU__SAXIGP2__DATA_WIDTH"):
        require(value(name) == "128", "PS AXI width mismatch: " + name)
    require(float(value("PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ")) == 125, "PS PL0 target is not 125MHz")
    expected = {p.attrib["name"].removeprefix("CONFIG."): p.attrib["value"] for p in ET.fromstring(preset).iter("user_parameter")
                if "DDRC" in p.attrib.get("name", "")}
    require(len(expected) >= 30, "reviewed DDR preset is incomplete")
    for name, target in expected.items():
        actual = value(name)
        if actual != target:
            try:
                equivalent = float(actual) == float(target)
            except ValueError:
                equivalent = False
            require(equivalent, "PS DDR preset differs: " + name)
    return len(expected)


def bit_payload(raw):
    """Read the length-tagged Xilinx .bit configuration payload, not its text header."""
    def length(at, width):
        require(at+width <= len(raw), "truncated bitstream header")
        return int.from_bytes(raw[at:at+width], "big")
    first = length(0, 2)
    require(first == 9 and raw[2:11] == bytes.fromhex("0ff00ff00ff00ff000"), "unreviewed .bit header")
    at = 2+first
    require(length(at, 2) == 1, "unreviewed bitstream tag header")
    at += 2
    for tag in b"abcd":
        require(at < len(raw) and raw[at] == tag, "bitstream header field order")
        size = length(at+1, 2)
        at += 3+size
        require(at <= len(raw), "bitstream header outside file")
    require(at < len(raw) and raw[at] == ord("e"), "missing bitstream payload")
    size = length(at+1, 4)
    at += 5
    require(size > 0 and size % 4 == 0 and at+size == len(raw), "bitstream payload length/trailer")
    return raw[at:]


def check_bit_bin(bit, binary):
    payload = bit_payload(bit)
    if binary == payload:
        return "BIT_PAYLOAD_IDENTICAL_BYTES"
    swapped = b"".join(payload[i:i+4][::-1] for i in range(0, len(payload), 4))
    require(binary == swapped, "binary is not paired with the exported bitstream payload")
    return "BIT_PAYLOAD_32BIT_BYTE_SWAP"


def route_counts(text):
    counts = []
    for label in ("routable nets", "fully routed nets", "nets with routing errors"):
        match = re.search(r"# of " + label + r"[. ]*:\s*([0-9]+)\s*:", text, re.IGNORECASE)
        require(match is not None, "unrecognized route report: " + label)
        counts.append(int(match[1]))
    require(counts[0] > 0 and counts[0] == counts[1] and counts[2] == 0, "routing incomplete")
    return counts


def check_logic_evidence(log_text, tags, elaborate_text, scope_text):
    """Validate full50 evidence, explicitly qualified by its local VIP model.

    Read-only and reusable by the logic-only verifier. This does not equate a
    project-local simulation-model result to an unmodified AMD or board result.
    """
    package_sha = "6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf"
    require(not re.search(r"fatal:|error:|\$fatal|\$error|OPT50_BOARD_LOGIC_RTL_FAIL", log_text, re.IGNORECASE),
            "board logic RTL simulation reported failure")
    def unique_line(prefix):
        lines = re.findall(r"^" + re.escape(prefix) + r"[^\r\n]*", log_text, re.MULTILINE)
        require(len(lines) == 1, "missing/ambiguous RTL marker: " + prefix)
        return lines[0].rstrip(" \t")
    expected_instances = {
        "tb_opt50_board_logic.dut.kv260_opt50_i.ps.inst.ddrc.ddr_write_ports",
        "tb_opt50_board_logic.dut.kv260_opt50_i.ps.inst.ocm_interconnect.write_ports",
        "tb_opt50_board_logic.dut.kv260_opt50_i.ps.inst.reg_interconnect.write_ports"}
    banners = re.findall(r"^OPT50_PSVIP_ARB_FIXED_V1[^\r\n]*", log_text, re.MULTILINE)
    require(len(banners) == 3, "local model must bind exactly three DDR/OCM/REG instances")
    bound, runtime_instances, decoration = [], [], []
    require(len(tags) == 2 and all(type(tag) is int and 0 <= tag <= 0xffffffff for tag in tags),
            "invalid expected run tags for local-model binding")
    for banner in banners:
        model_match = re.fullmatch(r"OPT50_PSVIP_ARB_FIXED_V1 changes=36 instance=([^ \t\r]+)", banner)
        require(model_match is not None, "unexpected local simulation model identity line")
        instance = model_match[1]
        runtime_instances.append(instance)
        hierarchy = re.fullmatch(r"tb_opt50_board_logic(?:\(TAG0=32'b([01]{32}),TAG1=32'b([01]{32})\))?"
                                 r"(\.dut\.kv260_opt50_i\.ps\.inst\.[A-Za-z0-9_.]+)", instance)
        require(hierarchy is not None, "unexpected local-model top parameter/hierarchy syntax")
        decorated = hierarchy[1] is not None
        decoration.append(decorated)
        if decorated:
            require([int(hierarchy[1], 2), int(hierarchy[2], 2)] == tags,
                    "local-model decorated top parameters differ from this run's tags")
        bound.append("tb_opt50_board_logic"+hierarchy[3])
    require(len(set(decoration)) == 1, "mixed plain/decorated local-model top identities")
    require(len(set(bound)) == 3 and set(bound) == expected_instances,
            "local model instance set differs from reviewed DDR/OCM/REG hierarchy")
    complete = unique_line("OPT50_REAL_CASES_COMPLETED")
    require(complete == "OPT50_REAL_CASES_COMPLETED profile=full48 jobs=48 PL_STARTS=48 INT32=1870848 "
            "PACKAGE_SHA256=" + package_sha + " BOARD_NOT_RUN", "frozen full48 regression did not finish")
    terminal = unique_line("OPT50_BOARD_LOGIC_RTL_PASS")
    match = re.fullmatch(r"OPT50_BOARD_LOGIC_RTL_PASS checks=([1-9][0-9]*) TAG0=([0-9]+) TAG1=([0-9]+) "
                         r"PL_STARTS=50 REAL_JOBS=48 INT32=1870848 MODEL=LOCAL_ARB_FIXED_V1", terminal)
    require(match is not None and [int(match[2]), int(match[3])] == tags,
            "missing/ambiguous same-build full50 RTL proof")
    require(int(match[1]) >= 1870848, "RTL assertion count cannot cover every real INT32 output")
    lines = re.findall(r"^OPT50_REAL_CASE_OUTPUT_MATCH[^\r\n]*", log_text, re.MULTILINE)
    require(len(lines) == 48, "exactly 48 individual real-layer output matches required")
    expected, total = [], 0
    for layer in (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47):
        k, n = (192, 576) if layer % 4 == 2 else (576, 192)
        for kind, m in (("public", 197), ("edge", 6)):
            for mode in (0, 1):
                sequence = len(expected)+1
                expected.append(f"OPT50_REAL_CASE_OUTPUT_MATCH case=layer_{layer}_{kind} mode={mode} "
                                f"M={m} K={k} N={n} elements={m*n} jobs={sequence}")
                total += m*n
    require([line.rstrip(" \t") for line in lines] == expected and total == 1870848,
            "real-layer job identity/order/shape/output count mismatch")
    calls = re.findall(r"^[ \t]*call[ \t]+xelab[^\r\n]*", elaborate_text, re.MULTILINE | re.IGNORECASE)
    require(len(calls) == 1 and
            re.match(r"^[ \t]*call[ \t]+xelab[ \t]+-L[ \t]+opt50_ps_vip_fixed(?=\s|$)", calls[0], re.IGNORECASE) is not None,
            "actual call xelab must search project-local simulation library FIRST")
    expected_scope = ["LOCAL_ARB_FIXED_V1", "36_REQUEST_PUBLICATIONS_MOVED_AFTER_PAYLOAD",
                      "THREE_INSTANCES_DDR_OCM_REG", "OCM_REG_BUSINESS_TRAFFIC_NOT_QUALIFIED",
                      "PROJECT_LOCAL_SIMULATION_LIBRARY_ONLY", "ORIGINAL_VENDOR_MODEL_FAIL_PRESERVED",
                      "NO_HARDWARE_RTL_CHANGE", "FULL48_PLUS_TWO_TINY_CASES", "NOT_BOARD_ACCEPTANCE"]
    require(scope_text.splitlines() == expected_scope, "local simulation model scope evidence differs")
    return dict(schema="opt50_local_model_full50_rtl_evidence_v1", status="PASS_LOCAL_MODEL_RTL_EVIDENCE_ONLY",
                checks=int(match[1]), observer_tags=tags, PL_starts=50, tiny_jobs=2,
                real_jobs=48, INT32_outputs=total, package_manifest_sha256=package_sha,
                simulation_model=dict(id="LOCAL_ARB_FIXED_V1", request_moves_per_module=36,
                                      instance_count=3, instances=sorted(bound), binding_scope="DDR_OCM_REG",
                                      runtime_instances=sorted(runtime_instances), top_parameters_decorated=decoration[0],
                                      ocm_reg_business_traffic_qualified=False,
                                      is_unmodified_AMD_model=False, is_board_acceptance=False,
                                      scope="Project-local simulation-only arbiter derivative"),
                scope="Full50 integrated RTL with explicit local PS VIP model; NOT board acceptance", board="NOT_RUN")


def check_simulation_steps(run):
    """Check actual step stdout and the single reviewed elaboration rewrite."""
    run = Path(run)
    names = ["board_logic_elaborate_original.bat", "board_logic_elaborate.bat"]
    names += [f"board_logic_step_{step}.log" for step in ("compile", "local_model", "elaborate", "simulate")]
    texts, evidence = {}, {}
    for name in names:
        raw = (run/name).read_bytes()
        require(bool(raw), "empty actual simulation step evidence: " + name)
        texts[name] = raw.decode("utf-8", errors="replace")
        evidence[name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    original = texts["board_logic_elaborate_original.bat"]
    ordered = texts["board_logic_elaborate.bat"]
    # Normalize only newline encoding and the extra final newline added by
    # Tcl puts; command spelling, arguments, generics and top are preserved.
    original = original.replace("\r\n", "\n").rstrip("\n")
    ordered = ordered.replace("\r\n", "\n").rstrip("\n")
    pattern = r"^call xelab[ \t]+"
    require(len(re.findall(pattern, original, re.MULTILINE)) == 1,
            "unexpected original actual elaboration command")
    require(re.search(r"(?:^|\s)-L[ \t]+opt50_ps_vip_fixed(?=\s|$)", original) is None,
            "original generated elaboration already contains local model override")
    expected = re.sub(pattern, "call xelab -L opt50_ps_vip_fixed ", original, count=1, flags=re.MULTILINE)
    require(ordered == expected, "actual elaboration differs beyond reviewed first-library insertion")
    for step in ("compile", "local_model", "elaborate", "simulate"):
        text = texts[f"board_logic_step_{step}.log"]
        require(not re.search(r"(?:^|\W)(?:ERROR|FATAL)\s*:|\$fatal|\$error|OPT50_BOARD_LOGIC_RTL_FAIL", text, re.IGNORECASE),
                "actual simulation step reported failure: " + step)
    return dict(schema="opt50_actual_simulation_steps_v1", status="FOUR_STEP_OUTPUTS_AND_ELAB_ORDER_CHECKED",
                steps=["compile", "local_model", "elaborate", "simulate"], evidence=evidence,
                scope="File evidence of actual simulation commands only; NOT hardware execution", board="NOT_RUN")


def verify(root, run):
    root, run = root.resolve(strict=True), run.resolve(strict=True)
    require(run.is_relative_to(root / "build"), "run must be within this project's build directory")
    receipt_path = run / "system_verified.json"
    require(not receipt_path.exists(), "verification receipt already exists; preserve it")
    recipe_raw = (root / "project_manifest.json").read_bytes()
    recipe = json.loads(recipe_raw)
    require(recipe["schema"] == "opt50_system_recipe_v1" and recipe["status"] == "PREPARED_NOT_RUN", "wrong source recipe")
    for name, item in recipe["files"].items():
        file = relative_file(root, name)
        require(file.stat().st_size == item["bytes"] and digest(file) == item["sha256"], "prepared source changed: " + name)
    identity = json.loads((run / "run_manifest.json").read_text(encoding="utf-8-sig"))
    require(identity["schema"] == "opt50_system_run_v1" and identity["stage"] == "build" and
            identity["tool_version"] == "2026.1" and identity["recipe_sha256"] == hashlib.sha256(recipe_raw).hexdigest(),
            "not this recipe's complete system build")
    require(identity["run_id"] == run.name and identity["clock_hz"] == 125000000, "run/clock mismatch")
    fingerprint = hashlib.sha256((identity["recipe_sha256"]+"|"+run.name+"|2026.1").encode()).hexdigest()
    require(identity["build_fingerprint"] == fingerprint and
            identity["observer_tags"] == [int(fingerprint[:8], 16), int(fingerprint[8:16], 16)] and
            identity["metadata_uuid"] == str(uuid.UUID(fingerprint[:32])), "run fingerprint/tag/UUID chain mismatch")
    for marker in ("observer_pass.txt", "bd_pass.txt", "board_logic_pass.txt"):
        require((run / marker).read_text().strip() == "PASS", "missing real stage PASS: " + marker)
    logic = check_logic_evidence((run / "board_logic_simulate.log").read_text(errors="replace"),
                                 identity["observer_tags"],
                                 (run / "board_logic_elaborate.bat").read_text(errors="replace"),
                                 (run / "simulation_model_scope.txt").read_text(errors="replace"))
    simulation_steps = check_simulation_steps(run)
    marker = (run / "build_pass.txt").read_text().strip()
    match = re.fullmatch(r"PASS WNS=([-+\d.eE]+) WHS=([-+\d.eE]+)", marker)
    require(match is not None and float(match[1]) >= 0 and float(match[2]) >= 0, "system timing failed/missing")
    for name in ("timing_summary.rpt", "drc.rpt", "route_status.rpt", "clocks.rpt", "ps_properties.txt", "address_map.txt"):
        require((run / name).is_file() and (run / name).stat().st_size > 0, "required report missing: " + name)
    require(not re.search(r"\|\s*Error\s*\|", (run / "drc.rpt").read_text(errors="replace")), "system DRC error")
    routed = route_counts((run / "route_status.rpt").read_text(errors="replace"))
    artifacts = {}
    for name in ("kv260_opt50.bit", "kv260_opt50.bit.bin", "kv260_opt50.hwh", "kv260_opt50.xsa"):
        path = relative_file(run, "artifacts/" + name)
        require(path.stat().st_size > 0, "empty system artifact")
        artifacts["artifacts/" + name] = dict(bytes=path.stat().st_size, sha256=digest(path))
    binary_pairing = check_bit_bin((run / "artifacts/kv260_opt50.bit").read_bytes(),
                                  (run / "artifacts/kv260_opt50.bit.bin").read_bytes())
    # Pair the unique DEFAULT_BD with its two declared SmartConnect scoped HWHs.
    with zipfile.ZipFile(run / "artifacts/kv260_opt50.xsa") as archive:
        xsa_hierarchy = check_xsa(archive, artifacts)
    abi = json.loads((root / "input/opt50_abi.json").read_bytes())
    hwh = check_hwh((run / "artifacts/kv260_opt50.hwh").read_bytes(), abi, identity["observer_tags"])
    ddr_count = check_ps((run / "ps_properties.txt").read_text(errors="replace"),
                         (root / "input/board_files/kv260/1.1/preset_s4.xml").read_bytes())
    receipt = dict(schema="opt50_system_verified_v1", status="PASS", run_id=run.name, tool_version="2026.1",
                   clock_hz=125000000, register_header_sha256=HEADER_SHA, metadata_uuid=identity["metadata_uuid"],
                   observer_tags=identity["observer_tags"], control_base="0xA0000000", observer_base="0xA0010000",
                   axi_bits=128, artifacts=artifacts, xsa_hierarchy=xsa_hierarchy,
                   WNS_ns=float(match[1]), WHS_ns=float(match[2]),
                   register_count=hwh["register_count"], DDR_parameters_checked=ddr_count,
                   board_logic=hwh["board_logic"], board_logic_rtl_checks=logic["checks"],
                   board_logic_simulation=logic, simulation_model=logic["simulation_model"],
                   simulation_steps=simulation_steps,
                   binary_pairing=binary_pairing, routed_nets=routed[1],
                   evidence={name: dict(bytes=(run/name).stat().st_size, sha256=digest(run/name))
                             for name in ("run_manifest.json", "stage_scope.txt", "observer_pass.txt", "bd_pass.txt", "board_logic_pass.txt", "board_logic_simulate.log", "board_logic_elaborate.bat", "board_logic_elaborate_original.bat", "board_logic_step_compile.log", "board_logic_step_local_model.log", "board_logic_step_elaborate.log", "board_logic_step_simulate.log", "simulation_model_scope.txt", "reset_properties.txt", "reset_contract.txt",
                                          "build_pass.txt", "timing_summary.rpt", "drc.rpt", "route_status.rpt",
                                          "clocks.rpt", "ps_properties.txt", "address_map.txt")},
                   scope="System build file cross-check with LOCAL_ARB_FIXED_V1 simulation; NOT unmodified AMD model proof; NOT board execution or quality acceptance", board="NOT_RUN")
    with receipt_path.open("x", encoding="utf-8") as file:
        file.write(json.dumps(receipt, indent=2)+"\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(ROOT, args.run), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile) as error:
        raise SystemExit("REFUSED: " + str(error))
