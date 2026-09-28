"""Small C-side entry point; hardware selection fails explicitly until board validation."""

import argparse
import json
from pathlib import Path
import platform
import subprocess
from time import perf_counter_ns

from .backend import select_backend
from .cases import load_case, postprocess_toy, quantize_toy_inputs, verify_output


def _git_state() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                    text=True, check=True).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def execute(command: str, backend_name: str, case_dir: Path, output_dir: Path,
            manifest_path=None, socket_path=None) -> dict:
    app_start = perf_counter_ns()
    case = load_case(case_dir)
    backend = select_backend(backend_name, manifest_path=manifest_path, socket_path=socket_path)
    if command == "toy-model":
        X, W = quantize_toy_inputs(case)
    else:
        X, W = case.Xq, case.Wq
    handle = backend.prepare(case.spec, W)
    try:
        run = backend.run(handle, X)
        verify_output(case, run.Y)
        toy_output = postprocess_toy(case, run.Y) if command == "toy-model" else None
        app_ms = (perf_counter_ns() - app_start) / 1e6
        record = {
            "status": "PASS", "evidence_kind": "MEASURED", "run_id": run.run_id,
            "case_id": case.manifest["case_id"], "manifest_sha256": case.manifest_sha256,
            "payload_sha256": case.manifest["payload"]["sha256"],
            "schema_version": case.manifest["schema_version"],
            "mode": case.spec.mode, "layout_id": case.spec.layout_id,
            "M": case.spec.M, "K": case.spec.K, "N": case.spec.N,
            "backend": run.backend, "execution_host": platform.node(),
            "build_id": run.build_id, "software": _git_state(),
            "Y_int32": run.Y.tolist(), "timings": {**run.timings, "app_ms": app_ms},
            "hardware_status": "PASS" if run.backend == "fpga_f8" else "NOT_RUN",
            "actual_pl_clock_hz": None,  # Report clock evidence from the load record.
        }
        if toy_output is not None:
            record["Y_model"] = toy_output.tolist()
        result_dir = output_dir / run.run_id
        result_dir.mkdir(parents=True, exist_ok=False)
        (result_dir / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        record["result_path"] = str(result_dir / "result.json")
        return record
    finally:
        session = getattr(backend, "session", None)
        if session is None or session.state == "IDLE":
            backend.release(handle)
            if hasattr(backend, "close"):
                backend.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run-case", "toy-model"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--backend", default="cpu_reference")
        sub.add_argument("--f8-manifest", type=Path)
        sub.add_argument("--f8-socket", type=Path)
        sub.add_argument("--case", type=Path, required=True)
        sub.add_argument("--output-dir", type=Path, default=Path("results/c_integration"))
    args = parser.parse_args()
    try:
        record = execute(args.command, args.backend, args.case, args.output_dir,
                         args.f8_manifest, args.f8_socket)
        print(json.dumps(record, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__,
                          "reason": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
