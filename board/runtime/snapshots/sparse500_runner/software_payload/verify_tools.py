"""Read-only file identity audit for the isolated, four-thread fused B24 release."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def need(ok, message):
    if not ok:
        raise ValueError(message)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def read(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
             "linked/reparse release path")
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= 64*1024*1024,
         "bounded single-link regular file required")
    raw = path.read_bytes()
    after = path.lstat()
    key = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    need(key(before) == key(after) and len(raw) == before.st_size, "file changed while reading")
    return raw


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def verify(root=ROOT):
    root = Path(root).absolute()
    value = decode(read(root/"tools_manifest.json"))
    required = {"build_arm.py", "stage_arm.py", "run_once.py", "run_model.py", "model_worker.py", "model_runtime.py", "verify_tools.py",
                "source/kernel.cpp", "source/model_core.hpp", "source/native_gate.hpp", "source/owner.cpp",
                "source/b_plugin.cpp", "test_model_core.cpp", "test_performance_core.cpp", "model/manifest.json",
                "model/reference.onnx", "model/graphs/model_w8a8_pl.onnx", "model/inputs/dev0000_input.npy",
                "model/inputs/zero_input.npy", "dataset.py", "accuracy_metrics.py",
                "release_compat.py", "status_receipt.py", "predecessor_tools_manifest.json",
                "predecessor_v2_tools_manifest.json", "predecessor_v3_tools_manifest.json",
                "legacy_model.py", "resident_proof.py", "before_owner.py", "prepare_fix1.py", "predecessor_v4_tools_manifest.json",
                "predecessor_v4_fix1_tools_manifest.json", "support_py/publisher_snapshot.py", "zero_graph_review.py",
                "history_index.py", "guardian_checkpoint.py", "run_first50.py", "reboot_review.py", "predecessor_h1_tools_manifest.json"}
    need(value.get("schema") == "opt50_b24_batch_tools_v1" and value.get("hardware_executed") is False and
         value.get("software_revision") == "resident-bounded-batch-v4-reboot-r1" and
         value.get("lifecycle_profile") == "resident-bounded-batch-v4" and
         value.get("source_model_sha256") == "ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43" and
         isinstance(value.get("files"), dict) and required <= value["files"].keys() and len(value["files"]) <= 256,
         "unexpected B24 full-model inventory/identity")
    for name, pin in value["files"].items():
        part = PurePosixPath(name)
        need(isinstance(name, str) and str(part) == name and not part.is_absolute() and
             ".." not in part.parts and not any(ch in name for ch in "\\:\0\r\n"), "unsafe release filename")
        need(isinstance(pin, dict) and set(pin) == {"bytes", "sha256"} and
             identity(read(root.joinpath(*part.parts))) == pin, "release identity differs: " + name)
    return value


if __name__ == "__main__":
    try:
        result = verify()
        print(json.dumps({"status": "B24_RESIDENT_FILES_PASS_NO_HARDWARE_ACCESS",
                          "files_checked": len(result["files"]), "hardware_executed": False,
                          "scope": "Full model and software file identity; not ARM compilation or model execution"}, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("B24_VERIFY_REFUSED: " + str(error), file=sys.stderr)
        raise SystemExit(2)
