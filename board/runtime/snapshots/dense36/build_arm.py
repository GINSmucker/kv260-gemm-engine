"""Compile the original Dense36 ARM software and CPU self-tests. No owner is launched."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
from verify_tools import ROOT, decode, identity, need, read, verify

PROFILE = "dense500-same-pl-cpu4-pl4-v1"
LIFECYCLE = "resident-bounded-batch-v4"
BINARY_NAMES = ("model_core_selftest", "performance_core_selftest", "libopt50_model.so", "opt50_model_owner")


def source_pins(root):
    result = {path.relative_to(root).as_posix(): identity(read(path))
              for path in sorted((root/"source").rglob("*")) if path.is_file()}
    for name in ("test_model_core.cpp", "test_performance_core.cpp"):
        result[name] = identity(read(root/name))
    need({"source/kernel.cpp", "source/native_gate.hpp", "source/model_core.hpp", "source/owner.cpp",
          "source/b_plugin.cpp", "source/client.cpp"} <= result.keys(), "missing compiled sources")
    return result


def elf(raw):
    need(len(raw) >= 64 and raw[:7] == b"\x7fELF\x02\x01\x01" and
         struct.unpack_from("<H", raw, 18)[0] == 183 and struct.unpack_from("<H", raw, 16)[0] in (2, 3),
         "AArch64 ELF64 little-endian executable/library required")
    return identity(raw)


def compile_commands(compiler, model, output, *, root=ROOT):
    root, model, output = Path(root).absolute(), Path(model).absolute(), Path(output).absolute()
    include = Path('/usr/include/xrt')
    uuid = Path('/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include')
    flags = [str(compiler), "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
             "-DKV5_DIAGNOSTIC", "-DOPT50_PERFORMANCE_LIGHT=1", "-DOPT50_H576_ALIGNED=1",
             "-DOPT50_RESIDENT_BATCH=1",
             "-fno-fast-math", "-ffp-contract=off", "-I"+str(include), "-I"+str(uuid),
             "-I"+str(model), "-I"+str(root/"source"), "-I"+str(root/"source/headers")]
    libraries = ["-lxrt_coreutil", "-l:libuuid.so.1", "-l:libcrypto.so.3"]
    return [flags+[str(root/"test_model_core.cpp"), "-o", str(output/"model_core_selftest")],
                flags+[str(root/"test_performance_core.cpp"), "-o", str(output/"performance_core_selftest")],
                flags+["-fPIC", "-shared", "-fvisibility=hidden", str(root/"source/b_plugin.cpp"),
                       str(root/"source/client.cpp"), *libraries, "-o", str(output/"libopt50_model.so")],
                flags+[str(root/"source/owner.cpp"), str(root/"source/kernel.cpp"), *libraries,
                       "-o", str(output/"opt50_model_owner")]]


def verify_build(output, root=ROOT):
    root, output = Path(root).absolute(), Path(output).absolute()
    release = verify(root)
    value = decode(read(output/"build_receipt.json"))
    need(value.get("schema") == "opt50_dense_resident_arm_build_v4" and
         value.get("status") == "ARM_BUILD_PASS_NOT_BOARD_RUN" and value.get("hardware_executed") is False and
         value.get("native_owner_executed") is False and value.get("measurement_profile") == PROFILE and
         value.get("lifecycle_profile") == LIFECYCLE and value.get("build_method") == "native_compile" and
         value.get("source_release_manifest") == identity(read(root/"tools_manifest.json")) and
         value.get("model_manifest_sha256") == identity(read(root/"model/manifest.json"))["sha256"] and
         value.get("sources") == source_pins(root) and set(value.get("binaries", {})) == set(BINARY_NAMES),
         "build receipt does not bind this complete-model release")
    need(type(value.get('compiler')) is dict and type(value['compiler'].get('path')) is str and
         value.get('commands') == compile_commands(value['compiler']['path'], root/'model', output, root=root),
         'resident compiler flags/inputs/outputs differ from the sealed build contract')
    for name in BINARY_NAMES:
        need(elf(read(output/name)) == value["binaries"][name], "binary identity differs: " + name)
    for field, marker in (("CPU_core_self_test", "PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS"),
                          ("performance_core_self_test", "PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS")):
        result = value.get(field, {})
        need(result.get("returncode") == 0 and result.get("stderr") == "" and
             marker in result.get("stdout", ""), "CPU-only build self-test missing")
    need("RESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS" in
         value["CPU_core_self_test"]["stdout"], "resident state-machine CPU self-test marker missing")
    need(all(value["sources"][name] == release["files"][name] for name in value["sources"]),
         "compiled source/release mismatch")
    return value


def build(model, output):
    model, output = Path(model).absolute(), Path(output).absolute()
    need(os.name == "posix" and os.uname().machine == "aarch64", "build on ARM Linux with existing SDK only")
    release = verify()
    import model_runtime
    checked = model_runtime.verify_bundle(model)
    before_model = identity(read(model/"manifest.json"))
    need(model == ROOT/"model", "use the bundled original Dense model; no model override")
    need(checked is not None, "new model verification failed")
    sources = source_pins(ROOT)
    need(all(sources[name] == release["files"][name] for name in sources), "source inventory mismatch")
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir(),
         "new build directory required; preserve existing outputs")
    compiler_entry = shutil.which("g++-13")
    need(compiler_entry is not None, "installed g++-13 missing; no packages installed automatically")
    compiler = Path(compiler_entry).resolve(strict=True)
    info = compiler.stat()
    need(info.st_uid == 0 and not info.st_mode & 0o022, "root-owned non-writable compiler required")
    compiler_pin = identity(read(compiler))
    include = Path("/usr/include/xrt")
    uuid = Path("/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include")
    need(include.is_dir() and uuid.is_dir(), "existing XRT/UUID SDK headers required")
    version = subprocess.run([str(compiler), "--version"], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=20, check=True)
    need(len(version.stdout) < 16384 and len(version.stderr) < 8192, "compiler identity output too large")
    output.mkdir()
    commands = compile_commands(compiler, model, output)
    results = {}
    for index, argv in enumerate(commands):
        print(json.dumps({"argv": argv, "hardware_access": False,
                          "writes": "New compiler output and build receipt only"}), flush=True)
        subprocess.run(argv, stdin=subprocess.DEVNULL, timeout=300, check=True)
        if index < 2:
            test_argv = [str(output/BINARY_NAMES[index])]
            if index == 0:
                test_argv += [str(model/name) for name in ("weights.bin", "pl_w_data.bin", "pl_w_meta.bin")]
            run = subprocess.run(test_argv, stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=60, check=True)
            marker = "PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS" if index == 0 else \
                     "PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS"
            need(run.stderr == "" and marker in run.stdout, "CPU-only core self-test failed")
            if index == 0:
                need("RESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS" in run.stdout,
                     "resident state-machine CPU self-test failed")
            results["CPU_core_self_test" if index == 0 else "performance_core_self_test"] = {
                "returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    verify()
    need(source_pins(ROOT) == sources and identity(read(model/"manifest.json")) == before_model and
         identity(read(compiler)) == compiler_pin, "source, model or compiler changed during build")
    model_runtime.verify_bundle(model)
    value = {"schema": "opt50_dense_resident_arm_build_v4", "status": "ARM_BUILD_PASS_NOT_BOARD_RUN",
             "measurement_profile": PROFILE, "lifecycle_profile": LIFECYCLE, "build_method": "native_compile",
             "source_release_manifest": identity(read(ROOT/"tools_manifest.json")),
             "model_manifest_sha256": before_model["sha256"], "sources": sources,
             "binaries": {name: elf(read(output/name)) for name in BINARY_NAMES},
             "compiler": {"path": str(compiler), **compiler_pin, "version": version.stdout}, "commands": commands,
             **results, "hot_path_disk_logging": False, "poll_delay_us": 50, "graph_owner_sync_calls": 0,
             "native_owner_executed": False, "hardware_executed": False,
             "accuracy_verified": False, "performance_verified": False}
    with (output/"build_receipt.json").open("xb") as stream:
        stream.write((json.dumps(value, sort_keys=True, indent=2)+"\n").encode())
        stream.flush(); os.fsync(stream.fileno())
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    verify_build(output)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT/"model")
    parser.add_argument("--out", type=Path, default=ROOT/"build_arm")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        value = verify_build(args.out) if args.verify_only else build(args.model, args.out)
        print(json.dumps({"status": value["status"], "receipt": str(args.out/"build_receipt.json"), "lifecycle_profile": LIFECYCLE,
                          "hardware_executed": False}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("DENSE_ARM_BUILD_NOT_VERIFIED: "+str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
