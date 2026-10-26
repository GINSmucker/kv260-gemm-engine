"""Freeze NEW H576-aligned PS software; preserve the measured v1 baseline.

Model, HLS, bitstream, client synchronization and numeric kernels are unchanged.
This publisher reads sources and writes new release records only; no devices.
"""
import hashlib
import json
from pathlib import Path
import stat

HERE = Path(__file__).resolve().parent
BASE = HERE.parent/'performance_v2'
BASE_MANIFEST = 'c174aac00699855f6a43330219db07adcafd1ff36441f7323b7e4f007026bf25'
PROFILE = 'same-weights-fair-single-shot-v1'
ALLOWED = {'source/kernel.cpp', 'source/kernel.h', 'source/model_core.hpp',
           'source/performance_trace.hpp', 'source/native_gate.hpp'}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    path = Path(path)
    for item in (path, *path.parents):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
             'linked source input refused')
    before = path.stat()
    need(stat.S_ISREG(before.st_mode) and before.st_size <= 64*1024*1024, 'bounded ordinary source required')
    raw = path.read_bytes()
    after = path.stat()
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    need(key(before) == key(after) and len(raw) == before.st_size, 'source changed while reading')
    return raw


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def publish(path, value, *, replace_parent_copy=False):
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    with path.open('wb' if replace_parent_copy else 'xb') as stream:
        need(stream.write(raw) == len(raw), 'short NEW source release write')
    need(read(path) == raw, 'source release readback differs')
    return identity(raw)


def prepare():
    provenance_path, manifest_path = HERE/'source_provenance.json', HERE/'source_manifest.json'
    old_raw = read(BASE/'source_manifest.json')
    need(identity(old_raw)['sha256'] == BASE_MANIFEST, 'measured v1 source release changed')
    replace_parent = provenance_path.exists() or manifest_path.exists()
    if replace_parent:
        need(read(manifest_path) == old_raw and
             read(provenance_path) == read(BASE/'source_provenance.json'),
             'only the untouched v2 parent metadata copies may be replaced in this NEW draft')
        for path in (provenance_path, manifest_path):
            backup = path.with_name(path.stem+'_parent_v2.json')
            with backup.open('xb') as stream:
                stream.write(read(path))
    old = json.loads(old_raw)
    need(old['schema'] == 'opt50_performance_software_release_v2' and
         old['measurement_profile'] == 'h576-aligned-low-overhead-v2', 'unexpected v1 parent release')
    for name, pin in old['files'].items():
        need(identity(read(BASE/name)) == pin, 'v1 frozen source changed: '+name)
    source_names = {name for name in old['files'] if name.startswith('source/')}
    found = {p.relative_to(HERE).as_posix() for p in (HERE/'source').rglob('*') if p.is_file()}
    need(found == source_names and len(found) == 23, 'same exact 23-source/header inventory required')
    current, changed, unchanged = {}, {}, {}
    for name in sorted(source_names):
        before, after = read(BASE/name), read(HERE/name)
        current[name] = identity(after)
        if before != after:
            need(name in ALLOWED, 'unexpected numeric/client/owner/gate change: '+name)
            changed[name] = {'before': identity(before), 'after': identity(after)}
        else:
            unchanged[name] = identity(before)
    need({'source/kernel.cpp', 'source/performance_trace.hpp', 'source/native_gate.hpp'} <= set(changed),
         'H576-aligned hot path and truthful v2 proof must both change')
    provenance = {
        'schema': 'opt50_fair_source_provenance_v1', 'measurement_profile': PROFILE,
        'base_release': identity(old_raw), 'changed_sources': changed, 'unchanged_sources': unchanged,
        'model_HLS_RTL_bitstream_ABI_numeric_semantics': 'UNCHANGED',
        'execution_selection': 'Same binary: all_dense36 or mixed24Dense12Sparse; same canonical pruned Wq; equal BO capacity',
        'model_manifest_sha256': 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb',
        'synchronization_contract': 'Client retains bounded X TO_DEVICE/Y FROM_DEVICE; owner never accesses shared payload during graph',
        'full_validation': 'Initialization and INFO after timed Runner/client close; not per-layer or at END',
        'not_claimed': ['per-layer input immutability', 'per-layer guard verification', 'measured speedup before board run'],
        'hardware_executed': False, 'ARM_compiled': False}
    publish(provenance_path, provenance, replace_parent_copy=replace_parent)
    for name in ('test_model_core.cpp', 'test_performance_core.cpp', 'build_arm.py', 'prepare_source.py', 'source_provenance.json'):
        current[name] = identity(read(HERE/name))
    manifest = {
        'schema': 'opt50_fair_software_release_v1', 'measurement_profile': PROFILE,
        'files': current, 'hardware_executed': False, 'ARM_compiled': False,
        'model_included': False, 'expected_PL_starts_per_owner': 36,
        'model_source': '/home/ubuntu/fpga_project/opt50_integration/full_model_fd96539d_v1_fix1/model',
        'compiler_flags': ['-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                           '-DKV5_DIAGNOSTIC', '-DOPT50_PERFORMANCE_LIGHT=1', '-DOPT50_H576_ALIGNED=1', '-DOPT50_FAIR_COMPARE=1',
                           '-fno-fast-math', '-ffp-contract=off'],
        'timing_scope': 'One Runner.run including BEGIN/END and completion/drain/error checks; full buffer checks after timer/client close'}
    result = publish(manifest_path, manifest, replace_parent_copy=replace_parent)
    need(read(BASE/'source_manifest.json') == old_raw, 'measured v1 release was modified')
    return {'status': 'H576_ALIGNED_SOURCE_READY_NOT_ARM_BUILT_OR_BOARD_RUN', 'manifest': str(manifest_path),
            **result, 'files': len(current), 'unchanged_frozen_source_files': len(unchanged),
            'changed_source_files': sorted(changed), 'hardware_executed': False}


if __name__ == '__main__':
    print(json.dumps(prepare(), indent=2))
