"""Read-only provenance checks for the completed OPT50 report/export window."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def require(ok, message):
    if not ok:
        raise ValueError(message)


def summary(path):
    raw = path.read_bytes()
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def safe_file(root, name):
    path = PurePosixPath(name)
    require(bool(name) and not path.is_absolute() and "\\" not in name and ":" not in name and
            all(p not in ("", ".", "..") for p in name.split("/")), "unsafe evidence path")
    result = (root / name).resolve(strict=True)
    require(result.is_relative_to(root.resolve()) and result.is_file(), "evidence outside root")
    return result


def check_table(root, table):
    require(isinstance(table, dict) and bool(table), "empty evidence table")
    for name, expected in table.items():
        require(summary(safe_file(root, name)) == expected, "evidence changed: " + name)


def check_timing(text):
    labels = {"no_clock", "constant_clock", "pulse_width_clock", "unconstrained_internal_endpoints",
              "no_input_delay", "no_output_delay", "multiple_clock", "generated_clocks", "loops",
              "partial_input_delay", "partial_output_delay", "latch_loops"}
    found = re.findall(r"^\d+\. checking (\w+) \((\d+)\)\s*$", text, re.MULTILINE)
    require({name for name, _ in found} == labels and all(int(n) == 0 for _, n in found),
            "missing/nonzero final timing completeness category")
    return dict.fromkeys(sorted(labels), 0)


def drc_summary(text):
    require(re.search(r"\|\s*(?:Error|Critical Warning)\s*\|", text, re.IGNORECASE) is None,
            "final DRC contains error/critical warning")
    found = re.findall(r"^\|\s*(\S+)\s*\|\s*Warning\s*\|[^\n]*\|\s*(\d+)\s*\|\s*$", text, re.MULTILINE)
    warnings = {name: int(n) for name, n in found}
    count = re.search(r"Checks found:\s*(\d+)", text)
    require(count is not None and sum(warnings.values()) == int(count[1]), "unrecognized final DRC table")
    return dict(errors=0, critical_warnings=0, warning_checks=sum(warnings.values()), warning_rules=warnings)


def timing_summary(text, wns, whs):
    header = re.search(r"^\s*WNS\(ns\)\s+TNS\(ns\)[^\n]*\n[^\n]*\n([^\n]+)", text, re.MULTILINE)
    require(header is not None, "missing design timing summary row")
    words = header[1].split()
    require(len(words) == 12 and all(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", n) for n in words),
            "unrecognized design timing summary values")
    values = [float(n) for n in words]
    require(values[0] == wns and values[4] == whs and min(values[0], values[4], values[8]) >= 0 and
            all(values[i] == 0 for i in (1, 2, 5, 6, 9, 10)) and
            all(values[i] > 0 for i in (3, 7, 11)) and
            "All user specified timing constraints are met." in text,
            "report timing differs from PASS marker or has failing endpoints")
    return dict(WNS_ns=values[0], WHS_ns=values[4], WPWS_ns=values[8],
                TNS_ns=values[1], THS_ns=values[5], TPWS_ns=values[9])


def clocks(text):
    found = re.findall(r"^clk_pl_([01])\s+([0-9.]+)\s+\{[0-9.]+\s+[0-9.]+\}", text, re.MULTILINE)
    require(len(found) == 2 and {n for n, _ in found} == {"0", "1"}, "missing/duplicate PL clock")
    result = {"clk_pl_" + n: float(value) for n, value in found}
    require(result == dict(clk_pl_0=8.0, clk_pl_1=10.0), "implemented clock periods differ")
    return result


def completion_provenance(root, run, verifier):
    """Validate frozen inputs and finished outputs; returns hashes, never writes."""
    manifest_path = verifier / "verification_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    require(manifest["schema"] == "opt50_verifier_deployment_v2" and manifest["board"] == "NOT_RUN" and
            Path(manifest["system_root"]).resolve() == root and manifest["run_id"] == run.name,
            "verifier deployment identity mismatch")
    check_table(verifier, manifest["files"])
    require(summary(root / "scripts/verify_system.py") == manifest["base_checker"], "frozen base checker changed")
    require(summary(root / "project_manifest.json")["sha256"] == manifest["recipe_sha256"] and
            summary(run / "run_manifest.json")["sha256"] == manifest["run_manifest_sha256"],
            "recipe/run manifest changed")
    name = manifest["finish_folder"]
    require(re.fullmatch(r"finish_[A-Za-z0-9_]+", name) is not None, "unsafe finish folder")
    finish = (root / name).resolve(strict=True)
    require(finish.parent == root and not (finish / "execution/failure.txt").exists(), "invalid/failed finish")
    check_table(finish, manifest["finish_evidence"])
    plan = json.loads((finish / "finish_plan.json").read_bytes())
    require(plan["schema"] == "opt50_route_finish_v1" and Path(plan["run"]).resolve() == run and
            Path(plan["system_root"]).resolve() == root and
            plan["recipe_sha256"] == manifest["recipe_sha256"] and
            plan["run_manifest_sha256"] == manifest["run_manifest_sha256"], "wrong finish identity")
    check_table(run, plan["source_files"])
    check_table(finish, plan["files"])
    marker = (finish / "execution/finish_pass.txt").read_text().splitlines()
    require(len(marker) == 5 and marker[0] == "FINISH_EXPORT_PASS_REQUIRES_VERIFY_SYSTEM" and
            marker[3:] == ["NO_IMPLEMENTATION_RERUN", "BOARD_NOT_RUN"], "wrong finish marker")
    original = (run / "build_pass.txt").read_text().strip()
    require(original == "PASS " + marker[1] + " " + marker[2], "finish/build timing marker mismatch")
    require(re.fullmatch(r"WNS=\d+(?:\.\d+)?", marker[1]) is not None and
            re.fullmatch(r"WHS=\d+(?:\.\d+)?", marker[2]) is not None, "unrecognized finish timing marker")
    wns, whs = float(marker[1].split("=")[1]), float(marker[2].split("=")[1])
    staged_timing = timing_summary((finish / "execution/reports/timing_summary.rpt").read_text(), wns, whs)
    require(timing_summary((run / "timing_summary.rpt").read_text(), wns, whs) == staged_timing,
            "original/final timing results differ")
    staged_clocks = clocks((finish / "execution/reports/clocks.rpt").read_text())
    require(clocks((run / "clocks.rpt").read_text()) == staged_clocks, "original/final clock periods differ")
    for name in ("kv260_opt50.bit", "kv260_opt50.bit.bin", "kv260_opt50.hwh", "kv260_opt50.xsa"):
        require(summary(run / "artifacts" / name) == summary(finish / "execution/artifacts" / name),
                "published/staged artifact mismatch: " + name)
    for name in ("check_timing.rpt", "drc.rpt", "route_status.rpt", "utilization.rpt"):
        require(summary(run / name) == summary(finish / "execution/reports" / name),
                "published/staged report mismatch: " + name)
    timing = check_timing((finish / "execution/reports/check_timing.rpt").read_text())
    drc = drc_summary((finish / "execution/reports/drc.rpt").read_text())
    return dict(schema="opt50_verification_provenance_v2", status="PASS", board="NOT_RUN",
                deployment_manifest=summary(manifest_path), checker_files=manifest["files"],
                frozen_base_checker=manifest["base_checker"], finish_folder=finish.name,
                finish_evidence=manifest["finish_evidence"],
                frozen_source_files_checked=len(plan["source_files"]),
                frozen_finish_files_checked=len(plan["files"]), timing_completeness=timing, drc=drc,
                final_timing=staged_timing, implemented_clock_periods_ns=staged_clocks,
                scope="File identity and finished-report checks; NO new implementation or board execution")
