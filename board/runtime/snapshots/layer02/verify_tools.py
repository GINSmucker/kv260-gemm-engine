"""Verify the isolated B24 layer02 release. File reads only; no device access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
SCHEMA = "opt50_b24_layer02_tools_v1"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_relative(name):
    require(isinstance(name, str) and bool(name), "empty release path")
    part = PurePosixPath(name)
    require(str(part) == name and not part.is_absolute() and
            ".." not in part.parts and "\\" not in name and ":" not in name,
            "unsafe release path: " + name)
    return part


def identity(path):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink(),
            "ordinary file required: " + str(path))
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "file changed while hashing: " + str(path))
    return {"bytes": before.st_size, "sha256": sha.hexdigest()}


def verify(root=ROOT):
    root = Path(root)
    manifest = json.loads((root / "tools_manifest.json").read_bytes())
    require(manifest.get("schema") == SCHEMA and
            manifest.get("hardware_executed") is False,
            "unexpected B24 release manifest")
    files = manifest["files"]
    require(isinstance(files, dict) and files, "empty release inventory")
    for name, pin in files.items():
        rel = safe_relative(name)
        path = root.joinpath(*rel.parts)
        require(not any(root.joinpath(*rel.parts[:i]).is_symlink()
                        for i in range(1, len(rel.parts))), "symlink release parent")
        require(identity(path) == pin, "release identity differs: " + name)
    required = {"build_runtime.py", "offline_check.py", "run_layer02.py",
                "prepare_inputs.py", "verify_tools.py", "data/manifest.json",
                "runtime/opt50_b24_layer02.cpp", "runtime/opt50_b24_layer02_pure.hpp",
                "runtime/support/opt50_common.hpp", "runtime/support/picosha2.h"}
    require(required <= files.keys(), "incomplete B24 release")
    return manifest


def main():
    try:
        manifest = verify()
        print(json.dumps({"status": "B24_LAYER02_TOOL_FILES_PASS_NO_HARDWARE_ACCESS",
                          "files_checked": len(manifest["files"]),
                          "hardware_executed": False, "START_issued": False,
                          "scope": "File identity only; not an ARM compile or board test"}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("VERIFY_REFUSED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
