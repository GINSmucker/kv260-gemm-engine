"""Reuse the fixed fix1 ARM build, retaining its original native compile proof.

Staging copies four source-identical ELFs and reruns only two CPU self-tests.
It never launches an owner or labels those copies as a new native compilation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import types

sys.dont_write_bytecode = True
from verify_tools import ROOT, decode, identity, need, read, verify

PROFILE = "b24-batch-acceptance-cpu4-pl4-v1"
LIFECYCLE = "resident-bounded-batch-v4"
BINARY_NAMES = ("model_core_selftest", "performance_core_selftest", "libopt50_model.so", "opt50_model_owner")
ORIGINAL_ROOT = Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_resident_20261029_v4_fix1/software')
ORIGINAL_MANIFEST = {'bytes': 23152, 'sha256': '03d0b13bb25bc04dbffd561e08267812db8bdd6697ad474ee0b2f40294773fb1'}
REUSE_SCHEMA = 'opt50_b24_resident_arm_reuse_fix2'
REUSE_STATUS = 'ARM_RESIDENT_SOURCE_IDENTICAL_REUSE_PASS_NOT_BOARD_RUN'


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


def verify_compiler(value):
    compiler = value.get('compiler', {})
    need(type(compiler) is dict and type(compiler.get('path')) is str and
         type(compiler.get('version')) is str, 'compiler identity missing')
    path = Path(compiler['path']).resolve(strict=True)
    info = path.stat()
    need(str(path) == compiler['path'] and info.st_uid == 0 and not info.st_mode & 0o022 and
         identity(read(path)) == {key: compiler.get(key) for key in ('bytes', 'sha256')},
         'resident compiler identity changed')


def verify_self_tests(value):
    for field, marker in (("CPU_core_self_test", "PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS"),
                          ("performance_core_self_test", "PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS")):
        result = value.get(field, {})
        need(type(result.get('returncode')) is int and result['returncode'] == 0 and
             result.get('stderr') == '' and type(result.get('stdout')) is str and marker in result['stdout'],
             'CPU-only build self-test missing')
    need('RESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS' in value['CPU_core_self_test']['stdout'],
         'resident state-machine CPU self-test marker missing')


def verify_native_build(output, root=ROOT, release=None):
    root, output = Path(root).absolute(), Path(output).absolute()
    release = verify(root) if release is None else release
    value = decode(read(output/"build_receipt.json"))
    need(value.get("schema") == "opt50_b24_resident_arm_build_v4" and
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
    verify_self_tests(value)
    verify_compiler(value)
    need(all(value["sources"][name] == release["files"][name] for name in value["sources"]),
         "compiled source/release mismatch")
    return value


@contextmanager
def predecessor_builder(source, listing):
    """Load the literal-pinned fix1 verifier/builder without ambient imports."""
    source = Path(source).absolute()
    raws = {name: read(source/name) for name in ('verify_tools.py', 'build_arm.py')}
    need(all(identity(raws[name]) == listing['files'][name] for name in raws),
         'fixed fix1 verifier/builder changed before isolated import')
    previous = sys.modules.get('verify_tools')
    try:
        verifier = types.ModuleType('_fix2_pinned_fix1_verify_tools')
        verifier.__file__ = str(source/'verify_tools.py')
        sys.modules['verify_tools'] = verifier
        exec(compile(raws['verify_tools.py'], verifier.__file__, 'exec'), verifier.__dict__)
        builder = types.ModuleType('_fix2_pinned_fix1_build_arm')
        builder.__file__ = str(source/'build_arm.py')
        exec(compile(raws['build_arm.py'], builder.__file__, 'exec'), builder.__dict__)
        yield builder
    finally:
        if previous is None:
            sys.modules.pop('verify_tools', None)
        else:
            sys.modules['verify_tools'] = previous


def protected(name):
    return name.startswith(('model/', 'data/', 'source/')) or Path(name).suffix in ('.cpp', '.h', '.hpp')


def original_build(root):
    """Re-audit fixed fix1, whose unchanged builder proves the real v4 build."""
    root = Path(root).absolute()
    raw = read(root/'predecessor_v4_fix1_tools_manifest.json')
    need(identity(raw) == ORIGINAL_MANIFEST, 'fixed predecessor fix1 inventory differs')
    listing = decode(raw)
    need(listing.get('schema') == 'opt50_b24_batch_tools_v1' and listing.get('hardware_executed') is False and
         type(listing.get('files')) is dict and len(listing['files']) == 147,
         'fixed 147-file original fix1 release required')
    source = Path(ORIGINAL_ROOT).absolute()
    need(identity(read(source/'tools_manifest.json')) == ORIGINAL_MANIFEST,
         'original public source manifest differs')
    for name, expected in listing['files'].items():
        path = Path(name)
        need(not path.is_absolute() and '..' not in path.parts and name.replace('\\', '/') == name and
             ':' not in name and identity(read(source/path)) == expected,
             'original public fix1 file changed: '+name)
    old_raw = read(source/'build_arm/build_receipt.json')
    with predecessor_builder(source, listing) as builder:
        old = builder.verify_build(source/'build_arm', root=source)
    need(old.get('schema') == 'opt50_b24_resident_arm_reuse_fix1' and
         old.get('status') == REUSE_STATUS and old.get('build_method') == 'reuse_verified_source_identical' and
         old.get('source_release_manifest') == ORIGINAL_MANIFEST and 'commands' not in old,
         'fixed fix1 source-identical reuse receipt and native provenance required')
    need(decode(old_raw) == old, 'fix1 receipt changed during predecessor verification')
    old_sources = source_pins(source)
    need(source_pins(root) == old_sources == old['sources'], 'compiled source/header/test bytes differ; recompile required')
    numerical = {name: expected for name, expected in listing['files'].items() if protected(name)}
    current = verify(root)
    need(numerical and {name: expected for name, expected in current['files'].items() if protected(name)} == numerical,
         'model/data/native inventory differs; recompile required')
    for name, expected in numerical.items():
        need(identity(read(root/name)) == expected, 'numerical model bytes differ: '+name)
    need(read(source/'build_arm/build_receipt.json') == old_raw and
         identity(read(source/'tools_manifest.json')) == ORIGINAL_MANIFEST and
         all(identity(read(source/name)) == expected for name, expected in listing['files'].items()),
         'original fix1 receipt or source changed during review')
    return {'source_root': str(source), 'oldsource_manifest': ORIGINAL_MANIFEST,
            'receipt_rawpin': identity(old_raw), 'predecessor_receipt_schema': old['schema'],
            'original_native_reuse_proof': old['reuse_proof']}, old


def verify_build(output, root=ROOT):
    root, output = Path(root).absolute(), Path(output).absolute()
    release = verify(root)
    value = decode(read(output/'build_receipt.json'))
    if value.get('schema') == 'opt50_b24_resident_arm_build_v4':
        return verify_native_build(output, root, release)
    need(value.get('schema') == REUSE_SCHEMA and value.get('status') == REUSE_STATUS and
         value.get('build_method') == 'reuse_verified_source_identical' and
         value.get('measurement_profile') == PROFILE and value.get('lifecycle_profile') == LIFECYCLE and
         value.get('hardware_executed') is False and value.get('native_owner_executed') is False and
         value.get('source_release_manifest') == identity(read(root/'tools_manifest.json')) and
         value.get('model_manifest_sha256') == identity(read(root/'model/manifest.json'))['sha256'] and
         value.get('sources') == source_pins(root) and set(value.get('binaries', {})) == set(BINARY_NAMES) and
         'commands' not in value, 'controlled source-identical reuse receipt required')
    proof, old = original_build(root)
    need(value.get('reuse_proof') == proof and value.get('compiler') == old['compiler'] and
         value['binaries'] == old['binaries'], 'reuse provenance differs from the revalidated original native build')
    for name in BINARY_NAMES:
        need(elf(read(output/name)) == value['binaries'][name], 'reused binary changed: '+name)
    verify_self_tests(value)
    need(all(value['sources'][name] == release['files'][name] for name in value['sources']),
         'reused source/release mismatch')
    return value


def native_arm():
    need(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0 and os.uname().machine == 'aarch64',
         'source-identical reuse and CPU self-tests require root ARM Linux')


def durable_api(root, release):
    raw = read(root/'support_py/durable.py')
    need(identity(raw) == release['files']['support_py/durable.py'], 'durable copy helper changed')
    import types
    durable = types.ModuleType('_fix2_reuse_durable')
    durable.__file__ = str(root/'support_py/durable.py')
    exec(compile(raw, durable.__file__, 'exec'), durable.__dict__)
    return durable


def reuse_build(output=ROOT/'build_arm', root=ROOT):
    """Copy four verified ELFs and rerun only their two CPU-only self-tests."""
    native_arm()
    root, output = Path(root).absolute(), Path(output).absolute()
    need(output == root/'build_arm', 'source-identical reuse writes only this release build_arm directory')
    release = verify(root)
    proof, old = original_build(root)
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir(),
         'new reuse build directory required; never overwrite existing output')
    durable = durable_api(root, release)
    durable.mkdir_new(output)
    for name in BINARY_NAMES:
        durable.copy_new(Path(proof['source_root'])/'build_arm'/name, output/name, old['binaries'][name],
                         mode=0o600 if name.endswith('.so') else 0o700)
    results = {}
    for name, field in ((BINARY_NAMES[0], 'CPU_core_self_test'), (BINARY_NAMES[1], 'performance_core_self_test')):
        argv = [str(output/name)]
        if name == BINARY_NAMES[0]:
            argv += [str(root/'model'/part) for part in ('weights.bin', 'pl_w_data.bin', 'pl_w_meta.bin')]
        run = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=True)
        results[field] = {'returncode': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
    verify_self_tests(results)
    after, after_old = original_build(root)
    need(after == proof and after_old == old and verify(root) == release,
         'original or new source changed during reuse/self-tests')
    value = {'schema': REUSE_SCHEMA, 'status': REUSE_STATUS, 'build_method': 'reuse_verified_source_identical',
             'measurement_profile': PROFILE, 'lifecycle_profile': LIFECYCLE,
             'source_release_manifest': identity(read(root/'tools_manifest.json')),
             'model_manifest_sha256': identity(read(root/'model/manifest.json'))['sha256'],
             'sources': source_pins(root), 'binaries': {name: elf(read(output/name)) for name in BINARY_NAMES},
             'compiler': old['compiler'], 'reuse_proof': proof, **results,
             'native_owner_executed': False, 'hardware_executed': False,
             'accuracy_verified': False, 'performance_verified': False,
             'scope': 'Four fixed fix1 ELF copies with revalidated original v4 native compile provenance; fresh two CPU self-tests only; no native owner/device access'}
    durable.new_json(output/'build_receipt.json', value)
    verify_build(output, root)
    return value


def build(model, output):
    model, output = Path(model).absolute(), Path(output).absolute()
    need(os.name == "posix" and os.uname().machine == "aarch64", "build on ARM Linux with existing SDK only")
    release = verify()
    import model_runtime
    checked = model_runtime.verify_bundle(model)
    before_model = identity(read(model/"manifest.json"))
    need(model == ROOT/"model", "use the bundled B24 model; no old-model override")
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
    value = {"schema": "opt50_b24_resident_arm_build_v4", "status": "ARM_BUILD_PASS_NOT_BOARD_RUN",
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
        print("B24_ARM_BUILD_NOT_VERIFIED: "+str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
