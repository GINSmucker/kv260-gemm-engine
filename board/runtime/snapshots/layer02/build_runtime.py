"""Build a new layer02 executable and run CPU-only self-tests. Never opens PL."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
from verify_tools import ROOT, identity, require, verify

SOURCES = ("runtime/opt50_b24_layer02.cpp", "runtime/opt50_b24_layer02_pure.hpp",
           "runtime/support/opt50_common.hpp", "runtime/support/picosha2.h")


def elf_identity(path):
    with Path(path).open("rb") as stream:
        header = stream.read(64)
    require(len(header) == 64 and header[:6] == b"\x7fELF\x02\x01" and
            int.from_bytes(header[18:20], "little") == 183 and
            int.from_bytes(header[16:18], "little") in (2, 3),
            "XRT output must be a little-endian AArch64 ELF64 executable")
    return {"class": "ELF64", "endianness": "little", "machine": 183,
            "type": int.from_bytes(header[16:18], "little")}


def build(with_xrt=False, compiler="g++-13", out=None):
    verify()
    found = shutil.which(compiler)
    require(found is not None, "existing compiler missing; no software is installed automatically")
    compiler_path = Path(found).resolve(strict=True)
    if with_xrt:
        require(os.name == "posix" and os.uname().machine == "aarch64",
                "--with-xrt requires ARM Linux on the board")
    output = (ROOT / "bin" / ("opt50_b24_layer02_xrt" if with_xrt else
                              "opt50_b24_layer02_offline")) if out is None else Path(out).absolute()
    receipt = ROOT / "build_receipt.json" if with_xrt else output.with_name(output.name + ".build.json")
    require(not output.exists() and not output.is_symlink() and
            not receipt.exists() and not receipt.is_symlink(),
            "output or receipt exists; preserve it and choose a new destination")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_pins = {name: identity(ROOT / name) for name in SOURCES}
    environment = os.environ.copy()
    environment["PATH"] = str(compiler_path.parent) + os.pathsep + environment.get("PATH", "")
    version = subprocess.run([str(compiler_path), "--version"], env=environment,
                             capture_output=True, text=True, check=True, timeout=20,
                             stdin=subprocess.DEVNULL)
    require(len(version.stdout) <= 16384 and len(version.stderr) <= 8192,
            "oversized compiler identity output")
    argv = [str(compiler_path), "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
            "-Wno-unknown-pragmas", str(ROOT / SOURCES[0]), "-o", str(output)]
    if with_xrt:
        xrt = Path("/usr/include/xrt")
        uuid = Path("/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include")
        require(xrt.is_dir() and uuid.is_dir(), "existing XRT/UUID include directories missing")
        argv += ["-DOPT50_WITH_XRT", "-I" + str(xrt), "-I" + str(uuid),
                 "-lxrt_coreutil", "-l:libuuid.so.1", "-pthread"]
    print(json.dumps({"argv": argv, "hardware_access": False,
                      "writes": "New executable, receipt and compiler temporary files only"}), flush=True)
    subprocess.run(argv, env=environment, stdin=subprocess.DEVNULL, check=True, timeout=300)
    tested = subprocess.run([str(output), "--self-test"], env=environment,
                            capture_output=True, text=True, check=True, timeout=60,
                            stdin=subprocess.DEVNULL)
    require(len(tested.stdout) <= 65536 and len(tested.stderr) <= 8192 and
            "SELF_TEST_PASS_NO_HARDWARE_ACCESS" in tested.stdout and not tested.stderr,
            "CPU-only self-test did not pass cleanly")
    verify()
    require(source_pins == {name: identity(ROOT / name) for name in SOURCES},
            "source changed during build")
    value = {"schema": "opt50_b24_layer02_build_v1", "with_xrt": with_xrt,
             "status": "BUILD_PASS_WITH_XRT_NOT_BOARD_TEST" if with_xrt else "BUILD_PASS_OFFLINE_ONLY",
             "hardware_executed": False, "sources": source_pins,
             "release_manifest": identity(ROOT / "tools_manifest.json"),
             "compiler": {"path": str(compiler_path), **identity(compiler_path), "version": version.stdout.strip()},
             "executable": {"path": str(output), **identity(output)},
             "ELF": elf_identity(output) if with_xrt else None,
             "self_test": {"returncode": tested.returncode, "stdout": tested.stdout, "stderr": tested.stderr}}
    with receipt.open("xb") as stream:
        stream.write((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        fd = os.open(receipt.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    print(json.dumps({"status": value["status"], "receipt": str(receipt), "hardware_executed": False}, indent=2))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-xrt", action="store_true")
    parser.add_argument("--compiler", default="g++-13")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        build(args.with_xrt, args.compiler, args.out)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("BUILD_REFUSED_OR_FAILED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
