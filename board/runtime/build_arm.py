"""Compile NEW H576-aligned OPT50 PS software; never execute its owner.

This performs compilation only. It does not connect a socket, load a driver,
open an XRT device or issue a START. Output paths must be new.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
WEIGHTS_SHA = '88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6'
REFERENCE_SHA = '50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49'
MANIFEST_SHA = 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb'
COMPILER_SHA = 'd7417f64cab520d49347646f817afad682edf169b1ef281e4e3ca55d7a93b070'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        require(not item.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'linked/reparse build input refused')
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size < 64*1024*1024, 'bounded regular build input required')
    raw = path.read_bytes()
    after = path.stat()
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(key(before) == key(after) and len(raw) == before.st_size, 'build input changed while reading')
    return raw


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def verify_model(root):
    root = Path(root).absolute()
    manifest_raw = read(root / 'manifest.json')
    require(identity(manifest_raw)['sha256'] == MANIFEST_SHA, 'fixed source-derived model manifest changed')
    value = json.loads(manifest_raw)
    require(value.get('schema') == 'opt50_full_model_mixed_bundle_v1' and
            value.get('logical_weights_sha256') == WEIGHTS_SHA and
            value.get('reference_sha256') == REFERENCE_SHA and value.get('hardware_executed') is False and
            value.get('routes') == {'PL': 36, 'PS': 13, 'PL_dense': 24, 'PL_sparse24': 12},
            'unexpected fixed mixed model identity')
    require(type(value.get('files')) is dict and len(value['files']) == 57, 'fixed mixed model inventory differs')
    for name, pin in value['files'].items():
        p = PurePosixPath(name)
        require(type(name) is str and not p.is_absolute() and str(p) == name and
                not any(c in name for c in '\\:\0\r\n') and
                all(v not in ('', '.', '..') for v in name.split('/')), 'unsafe model path')
        require(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and type(pin['bytes']) is int and
                identity(read(root / name)) == pin, 'model payload changed: ' + name)
    return value


def sources(root=ROOT):
    root = Path(root)
    paths = sorted((root / 'source').rglob('*'))
    result = {p.relative_to(root).as_posix(): identity(read(p)) for p in paths if p.is_file()}
    result['test_model_core.cpp'] = identity(read(root / 'test_model_core.cpp'))
    result['test_performance_core.cpp'] = identity(read(root / 'test_performance_core.cpp'))
    require({'source/kernel.cpp', 'source/native_gate.hpp', 'source/model_core.hpp',
             'source/owner.cpp', 'source/b_plugin.cpp', 'source/client.cpp'} <= set(result), 'adapter sources incomplete')
    return result


def elf(path):
    raw = read(path)
    require(len(raw) >= 64 and raw[:7] == b'\x7fELF\x02\x01\x01' and
            struct.unpack_from('<H', raw, 18)[0] == 183 and struct.unpack_from('<H', raw, 16)[0] in (2, 3),
            'AArch64 ELF64 little-endian binary required')
    return identity(raw)


def build(model, output, root=ROOT):
    root, model, output = Path(root).absolute(), Path(model).absolute(), Path(output).absolute()
    require(os.name == 'posix' and os.uname().machine == 'aarch64', 'compile only on ARM Linux with installed SDKs')
    before_model, before_source = verify_model(model), sources(root)
    release = root / 'source_manifest.json'
    release_raw = read(release)
    pins = json.loads(release_raw)
    require(pins.get('schema') == 'opt50_fair_software_release_v1' and pins.get('hardware_executed') is False and
            pins.get('measurement_profile') == 'same-weights-fair-single-shot-v1', 'new H576-aligned source manifest required')
    require(all(before_source[name] == pins['files'].get(name) for name in before_source),
            'adapter compile sources differ from uploaded performance release')
    for name, expected in pins['files'].items():
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and str(relative) == name and
                all(part not in ('', '.', '..') for part in name.split('/')) and
                not any(ch in name for ch in '\\:\0\r\n') and identity(read(root/name)) == expected,
                'performance release file changed: '+name)
    require(not output.exists() and not output.is_symlink() and output.parent.is_dir(), 'NEW build directory required')
    compiler = Path('/usr/bin/g++-13').resolve(strict=True)
    require(compiler.stat().st_uid == 0 and not compiler.stat().st_mode & 0o022 and
            identity(read(compiler))['sha256'] == COMPILER_SHA, 'same pinned installed ARM compiler required')
    include = Path('/usr/include/xrt')
    uuid = Path('/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include')
    require(include.is_dir() and uuid.is_dir(), 'existing XRT/UUID SDK headers required; nothing installed automatically')
    output.mkdir()
    common = [str(compiler), '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
              '-DKV5_DIAGNOSTIC', '-DOPT50_PERFORMANCE_LIGHT=1', '-DOPT50_H576_ALIGNED=1', '-DOPT50_FAIR_COMPARE=1', '-fno-fast-math', '-ffp-contract=off',
              '-I'+str(include), '-I'+str(uuid), '-I'+str(model),
              '-I'+str(root / 'source'), '-I'+str(root / 'source/headers')]
    libraries = ['-lxrt_coreutil', '-l:libuuid.so.1', '-l:libcrypto.so.3']
    commands = [common + [str(root / 'test_model_core.cpp'), '-o', str(output / 'model_core_selftest')],
                common + [str(root / 'test_performance_core.cpp'), '-o', str(output / 'performance_core_selftest')],
                common + ['-fPIC', '-shared', '-fvisibility=hidden', str(root / 'source/b_plugin.cpp'),
                          str(root / 'source/client.cpp'), *libraries, '-o', str(output / 'libopt50_model.so')],
                common + [str(root / 'source/owner.cpp'), str(root / 'source/kernel.cpp'),
                          *libraries, '-o', str(output / 'opt50_model_owner')]]
    version = subprocess.run([str(compiler), '--version'], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=20, check=True)
    self_test = None
    performance_test = None
    for index, argv in enumerate(commands):
        print(json.dumps({'argv': argv, 'hardware_access': False, 'writes': 'New compiler output only'}), flush=True)
        subprocess.run(argv, stdin=subprocess.DEVNULL, timeout=240, check=True)
        if index == 0:
            checked = subprocess.run([str(output / 'model_core_selftest'), str(model / 'weights.bin'),
                                      str(model / 'pl_w_data.bin'), str(model / 'pl_w_meta.bin')],
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40, check=True)
            require('MODEL_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS' in checked.stdout and
                    'PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS' in checked.stdout and checked.stderr == '',
                    'CPU-only core/pack self-test failed; no success receipt')
            self_test = {'returncode': checked.returncode, 'stdout': checked.stdout, 'stderr': checked.stderr}
        if index == 1:
            checked = subprocess.run([str(output/'performance_core_selftest')], stdin=subprocess.DEVNULL,
                                     capture_output=True, text=True, timeout=40, check=True)
            require(checked.stdout == 'PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n' and checked.stderr == '',
                    'performance memory-trace/poll safety self-test failed')
            performance_test = {'returncode': checked.returncode, 'stdout': checked.stdout, 'stderr': checked.stderr}
    require(sources(root) == before_source and verify_model(model) == before_model and read(release) == release_raw and
            all(identity(read(root/name)) == expected for name, expected in pins['files'].items()),
            'source/model/release changed while compiling')
    binaries = {name: elf(output / name) for name in ('model_core_selftest', 'performance_core_selftest', 'libopt50_model.so', 'opt50_model_owner')}
    result = {'schema': 'opt50_fair_arm_build_v1', 'status': 'ARM_PERFORMANCE_BUILD_PASS_NOT_BOARD_RUN',
              'measurement_profile': 'same-weights-fair-single-shot-v1', 'supported_execution_modes': ['all_dense', 'mixed'],
              'active_data_capacity_bytes': 3981312, 'source_manifest': identity(read(release)),
              'model_manifest': identity(read(model / 'manifest.json')), 'sources': before_source,
              'compiler': {'path': str(compiler), **identity(read(compiler)), 'version': version.stdout},
              'commands': commands, 'binaries': binaries, 'diagnostic': True, 'CPU_core_self_test': self_test,
              'performance_core_self_test': performance_test, 'hot_path_disk_logging': False,
              'poll_delay_us': 50, 'per_layer_deadline_seconds': 120,
              'graph_owner_sync_calls': 0, 'graph_owner_payload_access': False,
              'full_validation_scope': 'Initialization and post-timer INFO after client close; not per-layer or END',
              'native_owner_executed': False, 'hardware_executed': False,
              'accuracy_verified': False, 'performance_verified': False,
              'next': 'Native owner is gated; a separately reviewed one-model window is still required'}
    with (output / 'build_receipt.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=Path('/home/ubuntu/fpga_project/opt50_integration/full_model_fd96539d_v1_fix1/model'))
    parser.add_argument('--out', type=Path, default=ROOT / 'build_arm_fair_v1')
    args = parser.parse_args()
    result = build(args.model, args.out)
    print(json.dumps({'status': result['status'], 'hardware_executed': False,
                      'receipt': str(args.out / 'build_receipt.json')}, indent=2))


if __name__ == '__main__':
    main()
