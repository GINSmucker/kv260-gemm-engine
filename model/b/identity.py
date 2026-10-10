"""B0 and final protection audit; never opens C private vectors."""
import argparse
import importlib.metadata as metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from .common import ROOT, read_json, sha, utc, write_json

BINDINGS = {
    "results/C/A2/c_a2_20260930_01/A2_USABLE.json": "a2c1e1f93c81d275488ebf87ae813a12cbd7931674a0f6f21578301ba81ba80d",
    "results/A/A1/model_gemm_dense_v1_single_buffer_20260929_142053_203/delivery/artifact_manifest.json": "546f4d2acfac3bd5edca6411cf529bfe059ac04db3cd3908b89bd7317f124e5d",
    "results/model_integration/model_bundle_20260930_153320_781/window_manifest.json": "326c68b0041140afb7a04d707a6b8114ed866be2a959bb538e139a4aad9888b9",
    "build/model_assets/deit_tiny/model.safetensors": "21d4764d94f6c3ffdb6da3581115a0a1ee2d505537d96883b540e54766407c9e",
}

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, encoding="utf-8").strip()

def guard():
    for name, expected in BINDINGS.items():
        if sha(ROOT / name) != expected:
            raise ValueError("Frozen binding mismatch: " + name)
    gate = read_json(ROOT / next(iter(BINDINGS)))
    if gate["status"] != "PASS" or not gate["reviewed_by_A"] or not gate["reviewed_by_C"]:
        raise ValueError("A2 joint gate is not PASS")
    counts = {}
    manifests = list(BINDINGS)[1:3] + ["releases/F8_XW_E03B_ZOCL_V2_20260929/candidate_manifest.json"]
    for name in manifests:
        root = (ROOT / name).parent
        files = read_json(root / Path(name).name)["files"]
        for relative, value in files.items():
            expected = value if isinstance(value, str) else value["sha256"]
            if sha(root / relative) != expected:
                raise ValueError("Frozen payload mismatch: " + relative)
        counts[name] = len(files)
    if git("rev-parse", "P1_NAIVE_CORRECT_V1^{}") != "ce17306e9726c3cd6d89db02f62c611de686b14f":
        raise ValueError("P1 commit moved")
    sources = ["hls/gemm_naive.cpp", "hls/gemm.h", "hls/tb_gemm.cpp"]
    if git("diff", "P1_NAIVE_CORRECT_V1", "--", *sources):
        raise ValueError("P1 sources changed")
    # Only tool source identities from the public freeze, no seed/catalog/tensors.
    freeze = read_json(ROOT / "results/C/A2/c_a2_20260930_01/C_CANDIDATE_FREEZE.json")
    for name, expected in freeze["C_tools"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Frozen C tool mismatch: " + name)
    return {"status": "PASS", "host_utc": utc(), "bindings": BINDINGS,
            "payload_counts": counts, "p1_commit": git("rev-parse", "P1_NAIVE_CORRECT_V1^{}"),
            "p1_sources": {n: sha(ROOT / n) for n in sources},
            "C_candidate_freeze_sha256": sha(ROOT / "results/C/A2/c_a2_20260930_01/C_CANDIDATE_FREEZE.json"),
            "C_tools": len(freeze["C_tools"]), "C_freeze_keys": list(freeze), "private_holdout": "NOT_ACCESSED"}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    protected = guard()
    write_json(a.output / "baseline_guard.json", protected)
    packages = {d.metadata["Name"]: d.version for d in metadata.distributions()}
    record = {"stage": "B0", "run_id": a.output.name, "host_utc": utc(), "experiment_date": "2026-09-30 Asia/Shanghai",
              "role": "B_MODEL_NUMERIC_IMPLEMENTATION", "independent_of_A_DUT_development_in_this_session": True,
              "thread_id": os.environ.get("CODEX_THREAD_ID"), "executable": sys.executable,
              "python": sys.version, "platform": platform.platform(), "packages": packages,
              "disk": shutil.disk_usage(ROOT)._asdict(), "git_head": git("rev-parse", "HEAD"),
              "git_status": git("status", "--short"), "A_C_attestations": "NOT_MODIFIED",
              "B0_READY": "PENDING_COMPATIBILITY", "ARM": "NOT_RUN"}
    write_json(a.output / "environment_windows.json", record)
    (a.output / "requirements_before.txt").write_text(subprocess.check_output([sys.executable, "-m", "pip", "freeze"], encoding="utf-8"), encoding="utf-8")
    print("B0 identity/protection PASS; compatibility pending", flush=True)

if __name__ == "__main__":
    main()
