"""Exercise the native two-mode sequence against B's layer02, using fake devices."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

sys.dont_write_bytecode = True
from verify_tools import ROOT, identity, require, verify

CASE = "B24_dev0000_layer02"
MODEL_SHA = "ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43"
MANIFEST_SHA = "c0f35179ea2edd7fe848cc5643232da86b795b92a18fa27624f5770cad02f769"
METADATA_SHA = "715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551"
NAMES = ("X", "W_dense_data", "W_dense_meta", "W_sparse_data", "W_sparse_meta", "Y_golden")


def job_fields(data, token):
    """An offline job has no valid live boot/parent authority for hardware mode."""
    data = Path(data).resolve(strict=True)
    inventory = json.loads((data / "manifest.json").read_bytes())
    fields = {"schema": "opt50_b24_layer02_job_v1", "case_id": CASE,
              "M": "197", "K": "192", "N": "576", "run_id": token,
              "source_model_sha256": MODEL_SHA,
              "package_manifest": str(data / "source/B_MANIFEST.json"),
              "package_manifest_sha256": MANIFEST_SHA,
              "boot_id": "00000000-0000-0000-0000-000000000000",
              "expected_parent_pid": str(os.getpid()),
              "companion_lock": "/var/lib/opt50-first-access-fd96539d-v1/companion.lock",
              "metadata": "OFFLINE_NOT_OPENED.xclbin", "metadata_sha256": METADATA_SHA}
    for key in NAMES:
        pin = inventory["payloads"][key]
        path = data / pin["file"]
        require(identity(path) == {"bytes": pin["bytes"], "sha256": pin["sha256"]},
                "offline payload changed: " + key)
        fields[key] = str(path)
        fields[key + "_sha256"] = pin["sha256"]
    require(len(fields) == 26 and all("\n" not in value and "\r" not in value
                                     for value in fields.values()), "invalid offline KV job")
    return fields


def run(executable, out):
    verify()
    executable = Path(executable).resolve(strict=True)
    pin = identity(executable)
    out = Path(out).absolute()
    require(not out.exists() and not out.is_symlink(), "offline output exists; do not overwrite it")
    fields = job_fields(ROOT / "data", secrets.token_hex(6))
    out.mkdir(parents=True)
    job = out / "offline_job.kv"
    job.write_text("".join(key + "=" + value + "\n" for key, value in fields.items()), encoding="utf-8")
    environment = os.environ.copy()
    # Device-free executable only. --offline never selects an XRT or MMIO path.
    # ASCII argv avoids Windows' legacy narrow-argv conversion of Chinese paths.
    # Paths inside the KV file are UTF-8 and the native file reader uses u8path.
    result = subprocess.run([str(executable), "--offline", "--job", job.name], cwd=out,
                            capture_output=True, text=True, timeout=120,
                            stdin=subprocess.DEVNULL, env=environment)
    (out / "native.stdout").write_text(result.stdout, encoding="utf-8")
    (out / "native.stderr").write_text(result.stderr, encoding="utf-8")
    require(result.returncode == 0 and not result.stderr, "native offline check failed; inspect saved logs")
    native = json.loads(result.stdout)
    expected = {"status": "OFFLINE_B24_LAYER02_DENSE_SPARSE_PASS_NO_HARDWARE_ACCESS",
                "case_id": CASE, "source_model_sha256": MODEL_SHA,
                "shape_MKN": [197, 192, 576], "compared_INT32_outputs": 226944,
                "physical_PL_starts": 0, "hardware_executed": False,
                "dense_reference_uses_same_pruned_weights": True}
    require(type(native) is dict and native.keys() == expected.keys() and
            all(type(native[key]) is type(value) and native[key] == value
                for key, value in expected.items()), "unexpected native offline result")
    require(identity(executable) == pin, "executable changed during offline run")
    verify()
    value = {"schema": "opt50_b24_layer02_offline_check_v1", "status": "PASS",
             "case_id": CASE, "modes": [0, 1], "compared_INT32_outputs": 226944,
             "executable": {"path": str(executable), **pin},
             "hardware_executed": False, "physical_PL_starts": 0,
             "scope": "Native CPU/fake-backend integer, encoding, guard and control-sequence checks only",
             "native_stdout": result.stdout}
    (out / "result.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(value, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.executable, args.out)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("OFFLINE_CHECK_REFUSED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
