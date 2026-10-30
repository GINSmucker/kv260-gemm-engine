"""One sealed acceptance chunk, with a fresh single-graph owner for every image.

check audits files/OS and creates private copies only. Each image runs CPU4 and
one PL4 graph, exactly 36 STARTs, followed by full checks and normal STOP/close.
The unchanged 900-second gate applies before EVERY Native owner. No replay,
automatic renewal, firmware change, clock change or process killing exists.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import types
sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
RECORDS = Path('/var/lib/opt50-dense-model-v1')
PYTHON = '/home/ubuntu/fpga_project/model_b/.venv/bin/python'
AUTH = '--authorized-one-model-36-starts'
PROFILE = 'dense500-same-pl-cpu4-pl4-v1'
SOURCE_MODEL = 'f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'
METADATA = {'bytes':8560,'sha256':'715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551'}
LAYER_TOOLS = {'bytes':4636,'sha256':'4915b1354b2434e9687b49f21907b3365eaa967e26a81aa08ac55204c82d8dbd'}
PREVIOUS_MODEL_TOOLS = {'bytes':16220,'sha256':'4a40296b3abf248a7dba7acfa76b4ba14008e74437b28d13a69333424f2b12dd'}
IMAGE_PASS = 'PASS_DENSE_SINGLE_IMAGE_BITWISE_NATIVE_CLOSED'
PASS = 'PASS_DENSE_BATCH_ALL_IMAGES_BITWISE_CLOSED'
BATCH_FAIL = 'FAIL_DENSE_BATCH_NUMERICAL_NATIVE_CLOSED'
PLAN_SCHEMA = 'opt50_dense_acceptance_batch_plan_v1'
MAX_IMAGES = 1800
MAX_BATCH_IMAGES = 50
FAIL_NUMERICAL = 'FAIL_DENSE_FULL_MODEL_NUMERICAL_NATIVE_CLOSED'
REFUSED = 'REFUSED_BEFORE_OWNER_NO_MMIO'
SCOPE = 'SEALED_B24_ACCURACY_BATCH_FRESH_OWNER_ONE_GRAPH_36_STARTS_PER_IMAGE'
IMAGE_SCOPE = 'ONE_SEALED_BATCH_SAMPLE_FRESH_OWNER_36_STARTS_DENSE12_SPARSE24'
PL = [i for i in range(48) if i % 4 != 1]
SPARSE = set()
stop_requested = False


def need(ok, why):
    if not ok:
        raise ValueError(why)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def decode_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'duplicate JSON field')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def ordinary(path):
    original = Path(path)
    need('..' not in original.parts, 'parent traversal refused')
    path = original.absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
             'linked ordinary path refused')
    return path


def read(path, private=False, limit=32*1024*1024):
    path = ordinary(path)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit, 'bounded regular file required')
    if private:
        need(before.st_uid == 0 and before.st_nlink == 1 and not before.st_mode & 0o077,
             'root private single-link input required')
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_birthtime_ns if os.name == 'nt' else s.st_ctime_ns)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        need(key(os.fstat(stream.fileno())) == key(before), 'file replaced before read')
        raw = stream.read(limit+1)
        need(len(raw) == before.st_size and key(os.fstat(stream.fileno())) == key(before),
             'file changed during read')
    need(key(path.lstat()) == key(before), 'file replaced after read')
    return raw


def safe_relative(name):
    need(type(name) is str and name and not any(x in name for x in '\\:\0\r\n'), 'unsafe filename')
    value = PurePosixPath(name)
    need(str(value) == name and not value.is_absolute() and
         all(part not in ('', '.', '..') for part in name.split('/')), 'unsafe relative filename')
    return value.parts


def file_inode(path):
    info = ordinary(path).lstat()
    return {'device': info.st_dev, 'inode': info.st_ino}


def installed_model(folder=HERE):
    folder = Path(folder)
    return folder.parent/'model' if folder.name == 'tools' and folder.parent.parent == RECORDS else folder/'model'


def verify_tools(folder=HERE, private=False):
    folder = ordinary(folder)
    raw = read(folder/'tools_manifest.json', private=private)
    manifest = decode_json(raw)
    required = {'run_model.py', 'model_runtime.py', 'model_worker.py', 'support_py/resident.py',
                'support_py/durable.py', 'support_py/model_proof.py', 'model/manifest.json', 'build_arm.py', 'dataset.py', 'accuracy_metrics.py',
                'release_compat.py', 'status_receipt.py', 'history_index.py', 'guardian_checkpoint.py', 'paired_reference.py'}
    need(manifest.get('schema') == 'opt50_dense_batch_tools_v1' and
         manifest.get('hardware_executed') is False and type(manifest.get('files')) is dict and
         required <= set(manifest['files']) and len(manifest['files']) <= 256, 'B24 full-model tool inventory differs')
    for name, pin in manifest['files'].items():
        parts = safe_relative(name)
        need(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and type(pin['bytes']) is int and
             0 <= pin['bytes'] <= 64*1024*1024 and type(pin['sha256']) is str and
             re.fullmatch('[a-f0-9]{64}', pin['sha256']), 'invalid full-model file pin')
        path = installed_model(folder).joinpath(*parts[1:]) if parts[0] == 'model' else folder.joinpath(*parts)
        need(identity(read(path, private=private, limit=64*1024*1024)) == pin, 'tool/model changed: '+name)
    return {'manifest': identity(raw), 'files': manifest['files']}


def module_checked(name, tools, folder=HERE, private=False):
    name = '/'.join(safe_relative(name))
    raw = read(Path(folder)/name, private=private)
    need(identity(raw) == tools['files'][name], 'source changed before module import: '+name)
    module = types.ModuleType('_b24_full_'+Path(name).stem)
    module.__file__ = str(Path(folder)/name)
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    return module


def api(folder=HERE, private=False):
    tools = verify_tools(folder, private)
    resident = module_checked('support_py/resident.py', tools, folder, private)
    durable = module_checked('support_py/durable.py', tools, folder, private)
    runtime = module_checked('model_runtime.py', tools, folder, private)
    proof = module_checked('support_py/model_proof.py', tools, folder, private)
    dataset = module_checked('dataset.py', tools, folder, private)
    metrics = module_checked('accuracy_metrics.py', tools, folder, private)
    compat = module_checked('release_compat.py', tools, folder, private)
    history_index = module_checked('history_index.py', tools, folder, private)
    guardian_checkpoint = module_checked('guardian_checkpoint.py', tools, folder, private)
    return types.SimpleNamespace(c=resident, durable=durable, runtime=runtime, proof=proof, tools=tools,
                                 dataset=dataset, metrics=metrics, compat=compat,
                                 history_index=history_index, guardian_checkpoint=guardian_checkpoint)


def interpreter(c, expected=None):
    entry = Path(PYTHON)
    target = entry.resolve(strict=True)
    info = c.no_links(target).lstat()
    need(info.st_uid == 0 and not info.st_mode & 0o022 and stat.S_ISREG(info.st_mode),
         'root-owned non-writable worker interpreter required')
    result = {'entry': PYTHON, 'target': str(target), 'identity': identity(c.read_file(target))}
    need(expected is None or result == expected, 'worker interpreter changed')
    return result


def verify_build(tools, manifest):
    root = HERE/'build_arm'
    raw = read(root/'build_receipt.json')
    value = decode_json(raw)
    names = {'opt50_model_owner', 'libopt50_model.so', 'model_core_selftest', 'performance_core_selftest'}
    native_build = (value.get('schema') == 'opt50_dense_batch_arm_build_v1' and
                    value.get('status') == 'ARM_BUILD_PASS_NOT_BOARD_RUN' and
                    value.get('build_method') in (None, 'native_compile'))
    reused_build = (value.get('schema') == 'opt50_dense_batch_arm_reuse_v1' and
                    value.get('status') == 'ARM_BATCH_READY_NOT_BOARD_RUN' and
                    value.get('build_method') == 'reuse_verified_source_identical')
    need((native_build or reused_build) and value.get('measurement_profile') == PROFILE and
         value.get('hardware_executed') is False and value.get('model_manifest_sha256') == manifest and
         value.get('source_release_manifest') == tools['manifest'] and
         type(value.get('binaries')) is dict and set(value['binaries']) == names,
         'full-model ARM build receipt differs')
    for name in names:
        binary = read(root/name)
        need(identity(binary) == value['binaries'][name] and len(binary) >= 64 and
             binary[:7] == b'\x7fELF\x02\x01\x01' and struct.unpack_from('<H', binary, 18)[0] == 183 and
             struct.unpack_from('<H', binary, 16)[0] in (2, 3), 'fixed AArch64 build output differs: '+name)
    expected_sources = {name: pin for name, pin in tools['files'].items()
                        if name.startswith('source/') or name in ('test_model_core.cpp', 'test_performance_core.cpp')}
    need(value.get('sources') == expected_sources and expected_sources, 'build C++/header identities differ')
    if reused_build:
        proof = value.get('reuse_proof', {})
        needed = {'original_plan_path', 'original_receipt', 'original_model_manifest', 'source_file_count',
                  'source_bytes_identical', 'catalog_identical', 'aggregates_identical',
                  'original_tools_manifest', 'original_binaries'}
        need(type(proof) is dict and set(proof) == needed and
             proof.get('original_tools_manifest') == PREVIOUS_MODEL_TOOLS and
             proof.get('original_binaries') == value['binaries'] and
             type(proof.get('source_file_count')) is int and proof['source_file_count'] == len(expected_sources) and
             all(proof.get(key) is True for key in ('source_bytes_identical', 'catalog_identical', 'aggregates_identical')),
             'source-identical ARM reuse proof is incomplete')
    for key in ('CPU_core_self_test', 'performance_core_self_test'):
        test = value.get(key, {})
        need(type(test.get('returncode')) is int and test['returncode'] == 0 and test.get('stderr') == '',
             'CPU-only build self-test did not pass')
    compiler = value.get('compiler', {})
    compiler_path = Path('/usr/bin/g++-13').resolve(strict=True)
    info = c_info = compiler_path.lstat()
    need(info.st_uid == 0 and not info.st_mode & 0o022 and compiler.get('path') == str(compiler_path) and
         {key: compiler.get(key) for key in ('bytes', 'sha256')} == identity(read(compiler_path)),
         'installed compiler differs from build receipt')
    result = {'receipt': identity(raw), 'binaries': value['binaries']}
    if reused_build:
        result['reuse_proof'] = value['reuse_proof']
    return result


def validate_reuse_origin(c, build, previous_path, manifest, tools):
    """Do not trust reuse booleans without the closed original's private pins."""
    if 'reuse_proof' not in build:
        return
    proof = build['reuse_proof']; previous_path = Path(previous_path).absolute(); root = previous_path.parent
    need(proof['original_plan_path'] == str(previous_path), 'staged binaries belong to another previous model plan')
    raw_receipt = c.read_file(root/'build_receipt.json', private=True)
    raw_manifest = c.read_file(root/'model/manifest.json', private=True)
    old_receipt, old_manifest = c.decode_json(raw_receipt), c.decode_json(raw_manifest)
    sources = {name: pin for name, pin in tools['files'].items()
               if name.startswith('source/') or name in ('test_model_core.cpp', 'test_performance_core.cpp')}
    need(identity(raw_receipt) == proof['original_receipt'] and identity(raw_manifest) == proof['original_model_manifest'] and
         old_receipt.get('source_release_manifest') == PREVIOUS_MODEL_TOOLS and
         old_receipt.get('sources') == sources and old_receipt.get('binaries') == build['binaries'] == proof['original_binaries'] and
         old_receipt.get('model_manifest_sha256') == identity(raw_manifest)['sha256'],
         'original private build/source/model identities do not justify ARM reuse')
    for name in ('model_catalog.hpp', 'weights.bin', 'pl_w_data.bin', 'pl_w_meta.bin', 'reference.onnx'):
        need(manifest['files'][name] == old_manifest['files'][name] and
             identity(c.read_file(root/'model'/name, private=True, limit=64*1024*1024)) == manifest['files'][name],
             'catalog or C++ numerical aggregate changed; native recompilation is required: '+name)


def system_properties(c, guardian_path, unit, fields):
    guardian = c.read_json(guardian_path, private=True)
    tool = c._command_tool(guardian, 'systemctl')
    result = subprocess.run([tool['entry'], 'show', unit, '--property='+','.join(fields)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 32768 and len(result.stderr) <= 8192,
         'bounded service observation failed')
    rows = [line.split('=', 1) for line in result.stdout.splitlines() if '=' in line]
    need(len({key for key, _ in rows}) == len(rows), 'duplicate systemd field')
    return dict(rows)


def layer_proof(c, path, held):
    path = Path(path).absolute()
    need(path.name == 'plan.json' and path.parent.parent == Path('/var/lib/opt50-b24-layer02-v1') and
         re.fullmatch('plan_[a-f0-9]{12}', path.parent.name), 'explicit completed B24 layer02 plan required')
    folder = path.parent
    raw = c.read_file(folder/'tools/tools_manifest.json', private=True)
    need(identity(raw) == LAYER_TOOLS, 'layer02 must use the exact published B24 tools')
    listing = c.decode_json(raw)
    for name, pin in listing['files'].items():
        need(identity(c.read_file(folder/'tools'/name, private=True)) == pin, 'prior B24 layer02 source/data differs')
    old = c.compiled_module(folder/'tools/run_layer02.py', listing['files']['run_layer02.py']['sha256'])
    _, _, old_tools = old.helpers(private=True)
    plan = old.load_plan(c, path, old_tools, fresh=False)
    state = c.read_json(folder/'execution/status.json', private=True)
    complete = c.read_json(folder/'execution/completion.json', private=True)
    result = old.validate_result(complete.get('result'), plan)
    need(state.get('stage') == old.PASS and state.get('child_pid') is None and state.get('result') == result and
         plan['boot_id'] == held['boot_id'] and plan['guardian_pid'] == held['guardian']['pid'] and
         plan['guardian_start_ticks'] == held['guardian']['start_ticks'] and
         complete.get('worker_reaped') is True and complete.get('worker_returncode') == 0 and
         complete.get('device_closed') is True and complete.get('native_completion') ==
         old.native_events(c, folder/'execution', 2) and
         c.decode_json(c.read_file(folder/'execution/worker.stdout', private=True)) == result and
         c.read_file(folder/'execution/worker.stderr', private=True) == b'',
         'B24 layer02 did not complete and close on this resident guardian')
    service = system_properties(c, c.OLD_PLAN, plan['unit']+'.service',
                                ('MainPID', 'SubState', 'ExecMainStatus', 'Result'))
    need(service.get('MainPID') == '0' and service.get('SubState') == 'exited' and
         service.get('ExecMainStatus') == '0' and service.get('Result') == 'success',
         'layer02 coordinator has not exited normally')
    return {'plan': str(path), 'plan_pin': identity(c.read_file(path, private=True)),
            'completion': identity(c.read_file(folder/'execution/completion.json', private=True)), 'service': service}



def historical_parent_proof(c, root, plan, state, complete):
    """Prove the old CPU/worker/owner were reaped from its immutable journal.

    A new boot has no historical systemd process table. Stored, ordered
    RETURNED/STOP/PASS records and Native resource closure remain necessary;
    the current manager's default ExecMainStatus=0 is never used as old proof.
    """
    folder = Path(root)/'execution/events'; c.private_dir(folder)
    paths = sorted(folder.glob('event_*.json'))
    need(0 < len(paths) <= 10000 and {p.name for p in folder.iterdir()} == {p.name for p in paths},
         'historical parent journal inventory incomplete')
    records, digest = [], hashlib.sha256()
    allowed_states = {'PREPARING_CPU_REFERENCE_NO_OWNER_OR_MMIO', 'CPU_REFERENCE_RUNNING',
                      'OWNER_INTENT_DURABLE', 'OWNER_INITIALIZING', 'MODEL_WORKER_RUNNING',
                      'OWNER_SAFE_CLOSING', state['stage']}
    for index, path in enumerate(paths, 1):
        raw = c.read_file(path, private=True); value = c.decode_json(raw)
        need(path.name == 'event_%06d.json' % index and value.get('schema') == 'opt50_durable_event_v1' and
             type(value.get('sequence')) is int and value['sequence'] == index and
             value.get('action') in ('state', 'cpu_worker', 'model_worker', 'owner', 'management') and
             type(value.get('phase')) is str, 'historical parent journal gap/identity differs')
        if value['action'] == 'state':
            snapshot = value.get('state', {})
            need(value['phase'] in allowed_states and snapshot.get('stage') == value['phase'] and
                 snapshot.get('boot_id') == plan['boot_id'] and snapshot.get('unit') == plan['unit'],
                 'historical parent contains unresolved/foreign state evidence')
        records.append(value); digest.update(raw)
    need(records[-1].get('action') == 'state' and records[-1].get('phase') == state['stage'] and
         records[-1].get('state') == state, 'historical final PASS and durable journal differ')
    positions = {}
    for action in ('cpu_worker', 'model_worker', 'owner'):
        rows = [(i, value) for i, value in enumerate(records) if value['action'] == action]
        need([value['phase'] for _, value in rows] == ['INTENT', 'SPAWNED', 'RETURNED'],
             'historical child lifecycle incomplete: '+action)
        intent, spawned, returned = [value for _, value in rows]
        need(type(spawned.get('pid')) is int and spawned['pid'] > 1 and
             spawned.get('pid') == returned.get('pid') and type(returned.get('returncode')) is int and
             returned['returncode'] == 0 and type(intent.get('argv')) is list and
             intent['argv'] == spawned.get('argv'), 'historical child was not reaped at exit zero: '+action)
        if action == 'owner':
            need(returned['pid'] == complete['owner_pid'] and intent.get('expected_START_commands') == 36,
                 'historical owner PID or START budget differs')
        positions[action] = [i for i, _ in rows]
    management = [(i, value) for i, value in enumerate(records) if value['action'] == 'management']
    need([value['phase'] for _, value in management] == ['STOP_INTENT', 'STOP_RETURNED'] and
         all(value.get('owner_pid') == complete['owner_pid'] for _, value in management) and
         all(management[0][1].get(key) is True for key in
             ('Runner_closed', 'native_END_completed', 'postrun_buffer_checks_verified')),
         'historical authenticated STOP proof incomplete')
    need(positions['cpu_worker'][2] < positions['owner'][0] < positions['owner'][1] <
         positions['model_worker'][0] < positions['model_worker'][2] < management[0][0] <
         management[1][0] < positions['owner'][2] < len(records)-1,
         'historical CPU/model/STOP/reap/PASS order differs')
    need(state.get('boot_id') == plan['boot_id'] and state.get('unit') == plan['unit'],
         'historical parent state belongs to another plan/boot')
    return {'events': len(records), 'journal_sha256': digest.hexdigest(), 'worker_reaped_exit0': True,
            'owner_reaped_exit0': True, 'normal_STOP_recorded': True,
            'scope': 'Persisted historical lifecycle only; not a current process or residency observation'}


def historical_service_proof(service, *, same_boot):
    if same_boot:
        need(service.get('MainPID') == '0' and service.get('SubState') == 'exited' and
             service.get('ExecMainStatus') == '0' and service.get('Result') == 'success',
             'same-boot previous model service has not exited normally')
        return 'SAME_BOOT_TERMINAL_SERVICE_OBSERVED'
    need(service.get('MainPID') == '0' and service.get('ActiveState') == 'inactive' and
         service.get('SubState') == 'dead' and service.get('LoadState') in ('not-found', 'loaded'),
         'historical unit has a current active/ambiguous instance after boot change')
    return 'NO_CURRENT_INSTANCE_AFTER_BOOT_CHANGE_NOT_HISTORICAL_EXIT_PROOF'


def previous_model_proof(c, path, held):
    """Revalidate the exact published unfused B24 run with its own verifier.

    Its model/ORT profile and boot are historical provenance. All stored source,
    bitwise arithmetic and closed-resource proofs remain exact. A different
    current boot is reviewed separately, never impersonated by the old PID.
    """
    path = Path(path).absolute(); root = path.parent
    need(path.name == 'plan.json' and root.parent == RECORDS and re.fullmatch('plan_[a-f0-9]{12}', root.name),
         'explicit completed B24 full-model plan required')
    for folder in (RECORDS, root, root/'tools', root/'model', root/'execution'):
        c.private_dir(folder)
    raw = c.read_file(root/'tools/tools_manifest.json', private=True)
    need(identity(raw) == PREVIOUS_MODEL_TOOLS, 'previous model must use the exact published unfused B24 tools')
    listing = c.decode_json(raw)
    need(listing.get('schema') == 'opt50_dense_full_model_tools_v1' and listing.get('hardware_executed') is False,
         'previous model tool schema/scope differs')
    for name, pin in listing['files'].items():
        parts = safe_relative(name)
        source = root/'model'/Path(*parts[1:]) if parts[0] == 'model' else root/'tools'/Path(*parts)
        need(identity(c.read_file(source, private=True, limit=64*1024*1024)) == pin,
             'previous B24 model source/data changed: '+name)
    old = c.compiled_module(root/'tools/run_model.py', listing['files']['run_model.py']['sha256'])
    a = old.api(private=True)
    plan = old.load_plan(a, path, fresh=False)
    state = c.read_json(root/'execution/status.json', private=True)
    complete = c.read_json(root/'execution/completion.json', private=True)
    worker = old.validate_worker(c, plan, root/'execution')
    cpu = old.validate_cpu(c, plan, root/'execution')
    manifest = a.runtime.verify_bundle(root/'model')['manifest']
    native = a.proof.native_completion(c, root/'execution', old.read_events, plan['boot_id'], manifest, closed=True)
    need(state.get('stage') == old.PASS and state.get('owner_pid') is None and state.get('child_pid') is None and
         state.get('completion') == complete and complete.get('worker') == worker and complete.get('CPU_reference') == cpu and
         complete.get('native') == native and native.get('device_closed') is True and complete.get('owner_exit_code') == 0 and
         worker.get('bitwise_mismatches') == 0 and worker.get('max_abs_difference') == 0 and
         native.get('completed_PL_calls') == 36 and native.get('PL_dense') == 12 and native.get('PL_sparse') == 24,
         'previous full-model invocation lacks exact bitwise and closed-resource proof')
    saved_after = complete.get('held_after', {})
    need(saved_after.get('status') == 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START' and
         saved_after.get('boot_id') == plan['boot_id'] and
         saved_after.get('guardian', {}).get('pid') == plan['guardian_pid'] and
         saved_after['guardian'].get('start_ticks') == plan['guardian_start_ticks'],
         'stored historical guardian after-proof differs from the historical plan')
    same_boot = plan['boot_id'] == held['boot_id']
    if same_boot:
        need(plan['guardian_pid'] == held['guardian']['pid'] and
             plan['guardian_start_ticks'] == held['guardian']['start_ticks'],
             'same-boot historical model used a different resident guardian')
    need(type(complete.get('owner_pid')) is int and complete['owner_pid'] > 1 and
         c.read_file(root/'execution/owner.stdout', private=True) ==
         ('KV5_READY %d\nKV5_STOP calls=36\n' % complete['owner_pid']).encode() and
         c.read_file(root/'execution/owner.stderr', private=True) == b'', 'previous native owner normal STOP proof differs')
    for prefix, expected in (('cpu_worker', cpu), ('model_worker', worker)):
        need(c.decode_json(c.read_file(root/'execution'/(prefix+'.stdout'), private=True)) == expected and
             c.read_file(root/'execution'/(prefix+'.stderr'), private=True) == b'', 'previous worker diagnostics/results differ')
    journal = historical_parent_proof(c, root, plan, state, complete)
    service = system_properties(c, c.OLD_PLAN, plan['unit']+'.service',
                                ('LoadState', 'MainPID', 'ActiveState', 'SubState', 'ExecMainStatus', 'Result'))
    service_scope = historical_service_proof(service, same_boot=same_boot)
    return {'plan': str(path), 'tools_manifest': PREVIOUS_MODEL_TOOLS,
            'plan_pin': identity(c.read_file(path, private=True)),
            'completion': identity(c.read_file(root/'execution/completion.json', private=True)),
            'measurement_profile': old.PROFILE, 'inference_ms': worker['runner_run_ms'],
            'CPU_reference_ms': cpu['reference_ms'], 'service': service, 'service_scope': service_scope,
            'historical_boot_id': plan['boot_id'], 'current_boot_id': held['boot_id'], 'same_boot': same_boot,
            'historical_parent_journal': journal,
            'scope': 'Historical completed computation and binary provenance; not current PL residency or permission'}


def guardian_review(c, plan, full=True):
    c.configure(plan['guardian_plan'])
    value = c.held_review() if full else c.monitor_guardian(plan)
    guardian = value['guardian'] if full else value
    need(guardian['boot_id'] == plan['boot_id'] and guardian['pid'] == plan['guardian_pid'] and
         guardian['start_ticks'] == plan['guardian_start_ticks'] and
         identity(c.read_file(c.OLD_PLAN, private=True)) == plan['guardian_plan_pin'] and
         file_inode(c.OLD_PLAN) == plan['guardian_plan_inode'], 'resident guardian changed from frozen identity')
    return value


def previous_clear(c, exclude=None):
    if not RECORDS.exists():
        return []
    c.private_dir(RECORDS)
    reports = []
    folders = sorted(RECORDS.iterdir())
    need(len(folders) <= 1000, 'too many full-model plans')
    for folder in folders:
        need(re.fullmatch('plan_[a-f0-9]{12}', folder.name), 'unrecognized full-model record')
        c.private_dir(folder)
        if exclude is not None and folder == Path(exclude).parent:
            continue
        if (folder/'batch_status.json').exists():
            state = c.read_json(folder/'batch_status.json', private=True)
            need(state.get('stage') in (PASS, BATCH_FAIL, 'REFUSED_BATCH_BEFORE_OWNER',
                                       'PARTIAL_CLOSED_PLAN_EXPIRED', 'PARTIAL_CLOSED_OPERATOR_STOP') and
                 state.get('owner_pid') is None and state.get('child_pid') is None,
                 'previous batch has an unresolved image; no new hardware access')
            done = c.read_json(folder/'batch_completion.json', private=True)
            need(done.get('closed') is True and done.get('stage') == state['stage'], 'previous batch closure differs')
            reports.append({'plan': str(folder/'plan.json'), 'stage': state['stage']})
            continue
        if not (folder/'execution').exists():
            continue
        state = c.read_json(folder/'execution/status.json', private=True)
        need(state.get('stage') in (IMAGE_PASS, 'PASS_DENSE_ALIGNED_MODEL_36_STARTS_BITWISE_SINGLE_INPUT',
                                   'PASS_DENSE_FULL_MODEL_36_STARTS_REAL_INPUT_BITWISE_SINGLE_IMAGE',
                                   FAIL_NUMERICAL, REFUSED) and
             state.get('owner_pid') is None and state.get('child_pid') is None,
             'previous model execution unresolved; preserve for review')
        if state['stage'] != REFUSED:
            complete = c.read_json(folder/'execution/completion.json', private=True)
            need(complete.get('owner_exit_code') == 0 and complete.get('native', {}).get('device_closed') is True,
                 'previous model resource closure not proved')
        reports.append({'plan': str(folder/'plan.json'), 'stage': state['stage']})
    return reports


def copy_tree(c, durable, tools, root):
    durable.mkdir_new(root/'tools'); durable.mkdir_new(root/'model')
    for name, pin in sorted(tools['files'].items(), key=lambda item: (item[0].count('/'), item[0])):
        parts = safe_relative(name)
        target = root/'model'/Path(*parts[1:]) if parts[0] == 'model' else root/'tools'/Path(*parts)
        for parent in reversed(target.parents):
            if parent.is_relative_to(root) and not parent.exists():
                durable.mkdir_new(parent)
        durable.copy_new(HERE/name, target, pin)
    durable.copy_new(HERE/'tools_manifest.json', root/'tools/tools_manifest.json', tools['manifest'])


def new_bytes(c, durable, path, raw, mode=0o600):
    c.private_dir(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        need(stream.write(raw) == len(raw), 'short exclusive file write')
        stream.flush(); os.fsync(stream.fileno())
    durable.fsync_dir(path.parent)
    need(c.read_file(path, private=True) == raw, 'published private file changed')


def dataset_manifest(a, plan):
    folder = Path(plan['plan_path']).parent/'dataset'
    raw = a.c.read_file(folder/'manifest.json', private=True, limit=8*1024*1024)
    need(identity(raw) == plan['dataset_manifest'], 'sealed dataset manifest changed')
    value = a.dataset.validate_manifest(raw)
    need(value.get('count') == 1800 and len(value.get('samples', [])) == 1800,
         'complete pinned 1800-image corpus required even for a selected pilot')
    return value


def selected_samples(a, plan):
    manifest = dataset_manifest(a, plan)
    start, count = plan['sample_start'], plan['sample_count']
    need(type(start) is int and type(count) is int and 0 <= start < MAX_IMAGES and
         1 <= count <= min(MAX_BATCH_IMAGES, MAX_IMAGES-start) and plan['total_START_budget'] == 36*count,
         'bounded sealed sample range/START budget differs')
    result = manifest['samples'][start:start+count]
    need([x['ordinal'] for x in result] == list(range(start, start+count)), 'dataset ordinal sequence differs')
    return result


def sample_selection(plan, sample):
    return {'schema': 'opt50_dense_batch_current_sample_v1', 'plan_token': plan['token'],
            'dataset_manifest': plan['dataset_manifest'], 'sample': sample,
            'expected_PL_starts': 36, 'run_id': 1}


def sample_plan(a, plan, *, archived=None):
    root = Path(plan['plan_path']).parent
    file = (Path(archived) if archived is not None else root)/'current_sample.json'
    choice = a.c.read_json(file, private=True)
    sample = choice.get('sample', {})
    samples = selected_samples(a, plan)
    need(type(sample.get('ordinal')) is int and plan['sample_start'] <= sample['ordinal'] <
         plan['sample_start']+plan['sample_count'], 'current sample is outside the sealed range')
    expected = samples[sample['ordinal']-plan['sample_start']]
    need(choice == sample_selection(plan, expected), 'current input/label/sample selection differs from sealed corpus')
    value = dict(plan, input=str(root/'input.npy'), input_pin={key: expected['input'][key] for key in ('bytes', 'sha256')},
                 input_kind='dataset', sample=expected,
                 dataset_root=str(root/'dataset'))
    return value


def tree_usage(path):
    total, entries = 0, 0
    path = Path(path)
    for entry in (path, *path.rglob('*')):
        info = entry.lstat()
        need(not stat.S_ISLNK(info.st_mode), 'linked generated audit entry')
        total += getattr(info, 'st_blocks', (info.st_size+4095)//4096*8)*512
        entries += 1
    return {'bytes': total, 'inodes': entries}


def resource_requirement(count, static_bytes, pilot=None):
    need(type(count) is int and 1 <= count <= MAX_BATCH_IMAGES and type(static_bytes) is int and static_bytes >= 0,
         'invalid storage estimate inputs')
    if pilot is None:
        need(count <= 5, 'run and review a real five-image pilot before a larger batch')
        per_bytes, per_inodes = 16*1024*1024, 2048
    else:
        need(pilot['count'] == 5 and pilot['bytes'] > 0 and pilot['inodes'] > 0, 'complete five-image footprint required')
        per_bytes = math.ceil(pilot['bytes']/5*1.5)
        per_inodes = math.ceil(pilot['inodes']/5*1.5)
    return {'required_free_bytes': static_bytes+count*per_bytes+512*1024*1024,
            'required_free_inodes': count*per_inodes+10000, 'sample_count': count,
            'basis': 'measured-five-image-times-1.5' if pilot else 'conservative-pilot-only'}


def dataset_copy_files(verified, start, count):
    need(type(start) is int and type(count) is int and start >= 0 and 1 <= count <= MAX_BATCH_IMAGES and
         start+count <= len(verified['manifest']['samples']), 'invalid dataset copy range')
    need(type(verified.get('files')) is dict and 'manifest.json' in verified['files'], 'verified dataset file inventory missing')
    selected = {'manifest.json': verified['files']['manifest.json']}
    for sample in verified['manifest']['samples'][start:start+count]:
        name = sample['image']['file']; safe_relative(name)
        pin = {key: sample['image'][key] for key in ('bytes', 'sha256')}
        need(verified['files'].get(name) == pin, 'selected JPEG differs from verified source inventory')
        selected[name] = pin
    return selected


def copy_dataset(a, verified, root, start, count):
    a.durable.mkdir_new(root/'dataset')
    for name, pin in sorted(dataset_copy_files(verified, start, count).items(), key=lambda row: (row[0].count('/'), row[0])):
        parts = safe_relative(name); target = root/'dataset'/Path(*parts)
        for parent in reversed(target.parents):
            if parent.is_relative_to(root/'dataset') and not parent.exists():
                a.durable.mkdir_new(parent)
        a.durable.copy_new(Path(verified['root'])/name, target, {key: pin[key] for key in ('bytes', 'sha256')})


def committed_records(a, plan, root, *, complete=True):
    samples = selected_samples(a, plan)
    folder = Path(root)/'samples'; records = []
    existing = sorted(folder.iterdir()) if folder.exists() else []
    need(len(existing) <= len(samples), 'more archived samples than authorized')
    for index, child in enumerate(existing):
        expected = samples[index]
        need(child.name == '%06d' % expected['ordinal'], 'committed samples are not a contiguous sealed prefix')
        commit = a.c.read_json(child/'commit.json', private=True)
        need(commit.get('schema') == 'opt50_dense_batch_sample_commit_v1' and commit.get('sample') == expected and
             commit.get('plan_token') == plan['token'] and commit.get('closed') is True,
             'archived sample commit identity differs')
        pins = commit.get('files', {})
        need(type(pins) is dict and {'completion.json', 'accuracy_record.json', 'current_sample.json', 'input.npy'} <= pins.keys(),
             'archived sample audit closure missing')
        actual = {x.relative_to(child).as_posix() for x in child.rglob('*') if x.is_file()}
        need(actual == set(pins)|{'commit.json'}, 'unlisted or missing archived audit file')
        for name, pin in pins.items():
            need(identity(a.c.read_file(child/Path(*safe_relative(name)), private=True, limit=64*1024*1024)) == pin,
                 'archived sample file changed: '+name)
        view = sample_plan(a, plan, archived=child)
        need(pins.get('input.npy') == view['input_pin'], 'archived input differs from frozen sample')
        validate_input(a.c, dict(view, input=str(child/'input.npy')), child)
        cpu = validate_cpu(a.c, view, child); worker = validate_worker(a.c, view, child)
        native = a.proof.native_completion(a.c, child, read_events, plan['boot_id'],
                                           a.runtime.verify_bundle(Path(root)/'model')['manifest'], closed=True)
        proof = a.c.read_json(child/'completion.json', private=True)
        need(proof.get('worker') == worker and proof.get('CPU_reference') == cpu and
             proof.get('native') == native and proof.get('owner_exit_code') == 0 and
             native.get('device_closed') is True, 'archived native/worker closure differs')
        sample_parent_proof(a.c, child, view, proof)
        record = a.c.read_json(child/'accuracy_record.json', private=True)
        a.metrics.validate_record(record, expected)
        need(record == sample_accuracy(a, view, proof), 'archived accuracy differs from closed worker/native proof')
        records.append(record)
    if complete:
        need(len(records) == len(samples), 'batch is incomplete; do not claim dataset acceptance')
    return records


def historical_records(a, path, *, complete=False):
    """Audit the exact release closure, then use its read-only record verifier."""
    entry, root, source = a.compat.load_closed_entry(path, a.tools, access=a.c)
    rows = entry.read_completed_records(path, allow_partial=not complete)
    plan = a.c.read_json(root/'plan.json', private=True)
    need(plan.get('tool_files', {}).get('manifest') == source['tools_manifest'],
         'historical plan/release identity differs')
    return plan, rows, root, source


def review_pilot(a, pilot_path, held, dataset_pin, start, count):
    """An old release may prove a pilot; an old boot may not prove residency."""
    pilot_root = Path(pilot_path).absolute().parent
    need(pilot_root.parent == RECORDS and re.fullmatch('plan_[a-f0-9]{12}', pilot_root.name), 'pilot namespace differs')
    plan, rows, _, _ = historical_records(a, pilot_root/'plan.json', complete=True)
    need(plan.get('schema') == PLAN_SCHEMA and plan.get('measurement_profile') == PROFILE and
         plan.get('sample_count') == 5 and plan.get('dataset_manifest') == dataset_pin and
         plan.get('boot_id') == held['boot_id'], 'pilot identity/source/dataset/boot differs')
    need(all(x['bitwise_mismatches'] == 0 for x in rows), 'pilot was not bitwise exact')
    done = a.c.read_json(pilot_root/'batch_completion.json', private=True)
    need(done.get('stage') == PASS and done.get('closed') is True and done.get('committed_samples') == 5,
         'five-image pilot has not completed safely')
    need(not {x['ordinal'] for x in rows} & set(range(start, start+count)),
         'pilot images cannot be replayed in the next batch')
    return dict(tree_usage(pilot_root/'samples'), count=5)


def prevent_replay(a, history, dataset_pin, start, count):
    requested = set(range(start, start+count))
    for prior in history:
        previous_root = Path(prior['plan']).parent
        previous = a.c.read_json(previous_root/'plan.json', private=True)
        # The validated corpus has fixed labels/JPEG/input identities. A new
        # transport archive changes manifest provenance, not sample identity.
        if previous.get('schema') == PLAN_SCHEMA:
            _, rows, _, _ = historical_records(a, previous_root/'plan.json')
            need(not requested & {row['ordinal'] for row in rows},
                 'a selected image already has a closed Native execution; do not replay committed samples')


def check(guardian_path, layer_path, metadata, uart, *, previous_path, dataset, start=0, count=5, pilot_path=None):
    a = api(); c, durable = a.c, a.durable
    c.root_linux(); need(uart, 'independent UART logging confirmation required')
    need(dataset is not None, 'real prepared JPEG dataset is required; no synthetic READY plan')
    need(type(start) is int and type(count) is int and 0 <= start < MAX_IMAGES and
         1 <= count <= min(MAX_BATCH_IMAGES, MAX_IMAGES-start), 'select 1..50 samples within the sealed 1800-image dataset')
    verified = a.dataset.verify_dataset(Path(dataset), verify_payloads=True)
    need(verified['manifest']['count'] == 1800, 'complete acceptance dataset required')
    bundle = a.runtime.verify_bundle(HERE/'model'); manifest = bundle['manifest']
    build = verify_build(a.tools, a.runtime.MANIFEST_SHA); python = interpreter(c)
    c.configure(guardian_path)
    with c.companion_locked():
        history = previous_clear(c); held = c.held_review()
        history_snapshot = a.history_index.admit(a, history, verified['manifest_pin'], start, count)
        prior_layer = layer_proof(c, layer_path, held)
        prior_model = previous_model_proof(c, previous_path, held)
        validate_reuse_origin(c, build, previous_path, manifest, a.tools)
        old = c.read_json(c.OLD_PLAN, private=True)
        metadata = Path(metadata) if metadata is not None else Path(old['artifacts']['source_paths']['metadata'])
        need(identity(read(metadata)) == METADATA, 'pinned fd96539d metadata differs')
        pilot = None
        if count > 5:
            need(pilot_path is not None, 'larger batches require the completed five-image pilot plan')
            pilot = review_pilot(a, pilot_path, held, verified['manifest_pin'], start, count)
        static_bytes = sum(x['bytes'] for x in a.tools['files'].values())+sum(x['bytes'] for x in dataset_copy_files(verified, start, count).values())
        static_bytes += a.tools['manifest']['bytes']+build['receipt']['bytes']+METADATA['bytes']
        static_bytes += sum(build['binaries'][name]['bytes'] for name in ('opt50_model_owner', 'libopt50_model.so'))
        resources = resource_requirement(count, static_bytes, pilot)
        fs = os.statvfs(RECORDS if RECORDS.exists() else RECORDS.parent)
        need(fs.f_bavail*fs.f_frsize >= resources['required_free_bytes'] and
             fs.f_favail >= resources['required_free_inodes'], 'insufficient disk/inodes for sealed batch; select a smaller new chunk')
        if not RECORDS.exists(): durable.mkdir_new(RECORDS)
        c.private_dir(RECORDS); token = secrets.token_hex(6); root = RECORDS/('plan_'+token); durable.mkdir_new(root)
        copy_tree(c, durable, a.tools, root); copy_dataset(a, verified, root, start, count)
        for src, dst, mode in (('opt50_model_owner', 'native_owner', 0o700), ('libopt50_model.so', 'plugin.so', 0o600)):
            durable.copy_new(HERE/'build_arm'/src, root/dst, build['binaries'][src], mode=mode)
        durable.copy_new(HERE/'build_arm/build_receipt.json', root/'build_receipt.json', build['receipt'])
        durable.copy_new(metadata, root/'metadata.xclbin', METADATA); new_bytes(c, durable, root/'xrt.ini', b'')
        guardian = held['guardian']
        plan = {'schema': PLAN_SCHEMA, 'token': token, 'unit': 'opt50-dense-model-'+token,
                'plan_path': str(root/'plan.json'), 'boot_id': guardian['boot_id'], 'created_monotonic': time.monotonic(),
                'expires_seconds': 900, 'expiry_scope': 'before every Native owner; no renewal or automatic resubmission',
                'uart_logging_operator_confirmed': True, 'expected_PL_starts': 36,
                'expected_Dense_calls': 36, 'expected_Sparse_calls': 0, 'run_id': 1, 'scope': SCOPE,
                'total_START_budget': count*36, 'sample_start': start, 'sample_count': count,
                'dataset_manifest': verified['manifest_pin'], 'dataset_root': str(root/'dataset'),
                'resource_requirement': resources, 'pilot_plan': str(pilot_path) if pilot_path else None,
                'measurement_profile': PROFILE, 'old_board_lock_transferred': False,
                'guardian_plan': str(c.OLD_PLAN), 'guardian_plan_pin': identity(c.read_file(c.OLD_PLAN, private=True)),
                'guardian_plan_inode': file_inode(c.OLD_PLAN), 'guardian_pid': guardian['pid'],
                'guardian_start_ticks': guardian['start_ticks'], 'held_review': held, 'layer_proof': prior_layer,
                'previous_model_proof': prior_model,
                'previous_attempts': {'scope': 'Verified closed-history index; no embedded historical file inventory',
                                      'indexed_images': history_snapshot['images_count']},
                'history_index': history_snapshot, 'tool_files': a.tools,
                'board_build': build, 'python': PYTHON, 'interpreter': python, 'model_root': str(root/'model'),
                'plugin': str(root/'plugin.so'), 'execution': str(root/'execution'),
                'socket': str(root/'execution/runtime.sock'), 'source_model_sha256': SOURCE_MODEL,
                'model_manifest_sha256': a.runtime.MANIFEST_SHA, 'model_manifest': identity(read(HERE/'model/manifest.json')),
                'logical_weights_sha256': manifest['logical_weights_sha256'], 'reference_sha256': manifest['reference_sha256'],
                'custom_graph_sha256': manifest['custom_graph_sha256']}
        need(len(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode())+1 <= 65536, 'batch plan exceeds native 64KiB gate')
        durable.new_json(root/'plan.json', plan)
    props = ('Type=exec', 'ExitType=cgroup', 'KillMode=process', 'SendSIGKILL=no', 'TimeoutStopSec=infinity',
             'RuntimeMaxSec=infinity', 'Restart=no', 'RemainAfterExit=yes', 'OOMPolicy=continue')
    command = ['sudo', 'systemd-run', '--unit='+plan['unit'], *['--property='+x for x in props],
               '/usr/bin/python3', '-B', str(root/'tools/run_model.py'), 'run', '--plan', plan['plan_path'], AUTH]
    return {'status': 'B24_BATCH_PLAN_CREATED_NO_HARDWARE_ACCESS', 'plan': plan['plan_path'], 'boot_id': plan['boot_id'],
            'expires_seconds': 900, 'sample_start': start, 'sample_count': count, 'total_START_budget': count*36,
            'expected_PL_starts_per_owner': 36, 'expected_Dense_calls_per_owner': 12, 'expected_Sparse_calls_per_owner': 24,
            'ORT_intra_threads': 4, 'ORT_inter_threads': 1, 'dataset_manifest': verified['manifest_pin'],
            'next_requires_explicit_batch_authorization': shlex.join(command),
            'status_command': shlex.join(['sudo', '/usr/bin/python3', '-B', str(root/'tools/run_model.py'), 'status', '--plan', plan['plan_path'], '--summary']),
            'hardware_executed': False, 'accuracy_verified': False,
            'scope': 'Sealed image range; every fresh owner remains one model/36 STARTs; no replay or automatic recovery'}


def load_plan(a, path, fresh=False):
    c = a.c; path = Path(path).absolute(); root = path.parent
    need(path.name == 'plan.json' and root.parent == RECORDS and re.fullmatch('plan_[a-f0-9]{12}', root.name), 'canonical batch plan required')
    for folder in (RECORDS, root, root/'model', root/'tools', root/'dataset'): c.private_dir(folder)
    plan = c.decode_json(c.read_file(path, private=True, limit=65536)); token = root.name[5:]
    need(plan.get('schema') == PLAN_SCHEMA and plan.get('token') == token and plan.get('unit') == 'opt50-dense-model-'+token and
         plan.get('plan_path') == str(path) and plan.get('scope') == SCOPE and plan.get('measurement_profile') == PROFILE and
         plan.get('expires_seconds') == 900 and plan.get('expected_PL_starts') == 36 and plan.get('expected_Dense_calls') == 12 and
         plan.get('expected_Sparse_calls') == 24 and plan.get('source_model_sha256') == SOURCE_MODEL and
         plan.get('model_manifest_sha256') == a.runtime.MANIFEST_SHA and plan.get('old_board_lock_transferred') is False and
         plan.get('uart_logging_operator_confirmed') is True and HERE == root/'tools' and plan.get('tool_files') == a.tools,
         'sealed batch identity/source/scope differs')
    fixed = {'model_root': str(root/'model'), 'plugin': str(root/'plugin.so'), 'dataset_root': str(root/'dataset'),
             'execution': str(root/'execution'), 'socket': str(root/'execution/runtime.sock'), 'python': PYTHON}
    need(all(plan.get(k) == v for k, v in fixed.items()), 'batch path aliases differ')
    history = plan.get('history_index')
    need(type(history) is dict and set(history) == {'schema', 'path', 'revision', 'pin', 'images_count'} and
         history.get('schema') == a.history_index.SNAPSHOT and
         history.get('path') == str(a.history_index.INDEX_DIR/'index.json') and
         type(history.get('revision')) is int and history['revision'] >= 1 and
         type(history.get('images_count')) is int and 0 <= history['images_count'] <= 1800 and
         type(history.get('pin')) is dict and set(history['pin']) == {'bytes', 'sha256'} and
         type(history['pin'].get('bytes')) is int and 0 < history['pin']['bytes'] <= a.history_index.MAX_INDEX_BYTES and
         type(history['pin'].get('sha256')) is str and re.fullmatch('[a-f0-9]{64}', history['pin']['sha256']),
         'sealed batch history snapshot differs')
    bundle = a.runtime.verify_bundle(root/'model')
    for key in ('logical_weights_sha256', 'reference_sha256', 'custom_graph_sha256'):
        need(plan.get(key) == bundle['manifest'][key], 'batch numerical identity differs')
    for name, pin in (('native_owner', plan['board_build']['binaries']['opt50_model_owner']),
                      ('plugin.so', plan['board_build']['binaries']['libopt50_model.so']), ('metadata.xclbin', METADATA),
                      ('build_receipt.json', plan['board_build']['receipt'])):
        need(identity(c.read_file(root/name, private=True)) == pin, 'batch artifact changed: '+name)
    need(c.read_file(root/'xrt.ini', private=True) == b'', 'unexpected XRT configuration')
    selected_samples(a, plan)
    if fresh:
        c.root_linux()
        need(c._proc_read(Path('/proc/sys/kernel/random/boot_id'), 128).decode().strip() == plan['boot_id'] and
             type(plan.get('created_monotonic')) in (int, float) and math.isfinite(plan['created_monotonic']) and
             0 <= time.monotonic()-plan['created_monotonic'] <= 900, 'batch admission expired or belongs to another boot')
    return plan


def job_bytes(plan, manifest, pid):
    root = Path(plan['plan_path']).parent
    fields = {'schema': 'opt50_dense_model_native_job_v1', 'boot_id': plan['boot_id'], 'plan_token': plan['token'],
              'expected_parent_pid': str(pid), 'companion_lock': '/var/lib/opt50-first-access-fd96539d-v1/companion.lock',
              'model_root': str(root/'model'), 'weights': str(root/'model/weights.bin'),
              'weights_sha256': manifest['logical_weights_sha256'], 'data': str(root/'model/pl_w_data.bin'),
              'data_sha256': manifest['pl_data_sha256'], 'meta': str(root/'model/pl_w_meta.bin'),
              'meta_sha256': manifest['pl_meta_sha256'], 'reference': str(root/'model/reference.onnx'),
              'reference_sha256': manifest['reference_sha256'], 'metadata': str(root/'metadata.xclbin'),
              'metadata_sha256': METADATA['sha256'], 'run_id': '1', 'expected_PL_starts': '36',
              'source_model_sha256': SOURCE_MODEL}
    need(type(pid) is int and pid > 1 and len(fields) == 19 and
         all(not any(ch in value for ch in '\r\n=\0') for value in fields.values()), 'invalid 19-field Native job')
    return ''.join(key+'='+value+'\n' for key, value in fields.items()).encode()


NATIVE_PHASES = {
    'RAW_MMIO_READ_INTENT', 'RAW_MMIO_READ_RETURNED', 'RAW_PREFLIGHT_PASS',
    'BO_TO_DEVICE_INTENT', 'BO_TO_DEVICE_RETURNED', 'BO_FROM_DEVICE_INTENT', 'BO_FROM_DEVICE_RETURNED',
    'RESOURCE_CLOSE_INTENT', 'RESOURCES_CLOSED', 'NATIVE_ENTERED', 'XRT_DEVICE_OPEN_INTENT',
    'XRT_METADATA_REGISTRATION_INTENT', 'XRT_OBSERVER_OPEN_INTENT', 'XRT_GEMM_OPEN_INTENT',
    'BO_ALLOC_INTENT', 'BO_ALLOC_RETURNED', 'BO_MAP_INTENT', 'BO_MAP_RETURNED', 'NATIVE_READY',
    'BO_EXPORT_DUP_INTENT', 'BO_EXPORT_DUP_RETURNED', 'MODEL_BEGIN', 'MODEL_END_COMPLETED',
    'GEMM_READ_INTENT', 'GEMM_READ_RETURNED', 'OBSERVER_READ_INTENT', 'OBSERVER_READ_RETURNED',
    'START_WRITE_INTENT', 'START_WRITE_RETURNED', 'GEMM_WRITE_INTENT', 'GEMM_WRITE_RETURNED',
    'OBSERVER_CLEAR_INTENT', 'OBSERVER_CLEAR_RETURNED', 'DMA_ACTIVE_BEFORE_START',
    'DMA_COMPLETION_PROVEN', 'LAYER_COMPLETION_VERIFIED',
    'PERFORMANCE_COMPACT_PUBLISHED',
}


def read_events(c, folder):
    c.private_dir(folder)
    paths = sorted(folder.glob('event_*.json'))
    need(0 < len(paths) <= 100000 and len(list(folder.iterdir())) == len(paths), 'native event inventory differs')
    events = []
    previous = -1
    for index, path in enumerate(paths, 1):
        value = c.read_json(path, private=True)
        need(set(value) == {'schema', 'sequence', 'phase', 'details', 'monotonic_ns'} and
             path.name == 'event_%06d.json' % index and value.get('schema') == 'opt50_model_native_event_v1' and
             type(value.get('sequence')) is int and value['sequence'] == index and
             type(value.get('phase')) is str and type(value.get('details')) is str and
             type(value.get('monotonic_ns')) is int and value['monotonic_ns'] > previous, 'native event gap/identity differs')
        need(value['phase'] in NATIVE_PHASES, 'unknown/aborted/retained native phase; never interpret as PASS')
        previous = value['monotonic_ns']; events.append(value)
    return events


class Admin:
    """Only HELLO/INFO/STOP wire50; never BEGIN/RUN/ABORT/MAP or CSR."""
    def __init__(self, path, owner_pid, weights_sha):
        self.stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.stream.settimeout(5)
            self.stream.connect(str(path))
            credentials = self.stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            pid, uid, _ = struct.unpack('3i', credentials)
            need(pid == owner_pid and uid == 0, 'admin socket belongs to another process')
            self.call(1, bytes.fromhex(weights_sha))
        except BaseException:
            self.stream.close(); raise

    def call(self, operation, body=b''):
        need(operation in (1, 7, 8), 'management operation forbidden')
        # INFO includes fresh full-BO cache synchronization, guard/SHA checks
        # and durable publication. A slow SD fsync must not inherit HELLO's
        # short handshake timeout. Expiry still retains, never retries/kills.
        self.stream.settimeout(150 if operation == 8 else 10)
        self.stream.sendall(struct.pack('<IHHQIIII', 0x3035504f, 50, operation, 0, 0, 0, len(body), 0)+body)
        magic, version, op, run, sequence, layer, size, status = struct.unpack('<IHHQIIII', receive_exact(self.stream, 32))
        need((magic, version, op, run, sequence, layer, status) == (0x3035504f, 50, operation, 0, 0, 0, 0) and
             size == (3200 if operation == 8 else 0), 'invalid wire50 management reply')
        return receive_exact(self.stream, size)

    def metrics(self):
        values = struct.unpack('<400Q', self.call(8))
        need(values[7] == 36 and [values[8+8*i+7] for i in PL] == list(range(1, 37)) and
             all(values[8+8*i+7] == 0 for i in range(49) if i not in PL), 'owner diagnostics not exactly 36 routed calls')
        return {'completed_PL_calls': 36, 'wire_version': 50, 'layer_call_sequence': list(PL),
                'owner_Run_dispatch_ms': values[0]/1_000_000,
                'layers': [{'layer_index': layer, 'mode': int(layer in SPARSE),
                            'owner_Run_dispatch_ms': values[8+8*layer]/1_000_000}
                           for layer in PL],
                'scope': 'Owner dispatch includes ordered register programming/completion checks; client cache sync remains outside this interval; overlaps Runner.run and is not pure PL latency'}

    def stop(self):
        self.call(7)

    def close(self):
        self.stream.close()


def receive_exact(stream, count):
    raw = bytearray()
    while len(raw) < count:
        block = stream.recv(count-len(raw))
        need(bool(block), 'truncated authenticated owner reply')
        raw.extend(block)
    return bytes(raw)


def validate_parent(c, plan, lock):
    actual = system_properties(c, plan['guardian_plan'], plan['unit']+'.service',
                              (*c.SERVICE, 'MainPID', 'ActiveState', 'SubState'))
    need(all(actual.get(key) == value for key, value in c.SERVICE.items()) and
         actual.get('MainPID') == str(os.getpid()) and actual.get('ActiveState') == 'active' and
         actual.get('SubState') in ('start', 'running'), 'coordinator systemd process guards differ')
    command = ['/usr/bin/python3', '-B', str(HERE/'run_model.py'), 'run', '--plan', plan['plan_path'], AUTH]
    need(c._proc_read(Path('/proc/self/cmdline')) == b''.join(x.encode()+b'\0' for x in command) and
         c._proc_read(Path('/proc/self/cgroup')) == ('0::/system.slice/'+plan['unit']+'.service\n').encode(),
         'coordinator exact argv/cgroup differs')
    before, info = c._lock_info(), os.fstat(lock)
    need((before.st_dev, before.st_ino) == (info.st_dev, info.st_ino), 'companion inode replaced')
    c.parse_flock(c._proc_read(Path('/proc/locks'), 4*1024*1024), os.getpid(),
                  (os.major(info.st_dev), os.minor(info.st_dev)), info.st_ino)


def model_identity(value, plan):
    fields = {'boot_id': plan['boot_id'], 'plan_token': plan['token'],
              'input_sha256': plan['input_pin']['sha256'], 'input_kind': plan['input_kind'],
              'sample_ordinal': plan['sample']['ordinal'], 'sample_id': plan['sample']['id'],
              'label': plan['sample']['label'], 'image_sha256': plan['sample']['image']['sha256'],
              'reference_sha256': plan['reference_sha256'],
              'model_manifest_sha256': plan['model_manifest_sha256'],
              'logical_weights_sha256': plan['logical_weights_sha256'], 'source_model_sha256': SOURCE_MODEL,
              'measurement_profile': PROFILE, 'single_sample_only': True,
              'accuracy_verified': False, 'performance_verified': False,
              'ORT_options': {'intra_op_threads': 4, 'inter_op_threads': 1, 'optimization': 'OFF',
                              'sequential': True, 'spinning': 0, 'log_severity': 3, 'affinity': [0],
                              'worker_affinities': '2;3;4'}}
    need(type(value) is dict and all(type(value.get(key)) is type(expected) and value[key] == expected
                                   for key, expected in fields.items()), 'worker model/input/thread identity differs')


def timing(value, prefixes):
    for name in prefixes:
        need(type(value.get(name+'_ns')) is int and value[name+'_ns'] >= 0 and
             type(value.get(name+'_ms')) in (int, float) and value[name+'_ms'] == value[name+'_ns']/1e6,
             'invalid monotonic duration: '+name)
    need(type(value.get('timing_scope')) is str and value['timing_scope'], 'timing scope missing')


def validate_input(c, plan, execution):
    value = c.read_json(Path(execution)/'input_preparation.json', private=True)
    model_identity(value, plan)
    need(value.get('schema') == 'opt50_dense_input_preparation_v1' and
         value.get('status') == 'REAL_INPUT_MATERIALIZED_NO_HARDWARE_ACCESS' and
         value.get('hardware_accessed') is False and value.get('PL_START_commands_issued_by_worker') == 0 and
         value.get('input_file') == plan['input_pin'] and
         identity(c.read_file(plan['input'], private=True)) == plan['input_pin'], 'prepared real image identity differs')
    return value


def validate_cpu(c, plan, execution):
    value = c.read_json(Path(execution)/'cpu_reference.json', private=True)
    model_identity(value, plan)
    need(value.get('schema') == 'opt50_dense_model_cpu_reference_v1' and
         value.get('status') == 'CPU_REFERENCE_READY_NO_PL_ACCESS' and value.get('CPU_session_closed') is True and
         value.get('hardware_accessed') is False and type(value.get('PL_START_commands_issued_by_worker')) is int and
         value['PL_START_commands_issued_by_worker'] == 0 and value.get('shape') == [1, 1000] and
         value.get('dtype') == 'float32' and value.get('numpy_version') == '1.26.4' and value.get('ort_version') == '1.22.1' and
         value.get('logits_file') == identity(c.read_file(Path(execution)/'cpu_reference.npy', private=True)) and
         re.fullmatch('[a-f0-9]{64}', value.get('logits_raw_sha256', '')), 'closed fresh CPU reference proof differs')
    timing(value, ('reference_setup', 'reference'))
    return value


def validate_worker(c, plan, execution):
    execution = Path(execution)
    value = c.read_json(execution/'worker_result.json', private=True)
    model_identity(value, plan)
    reference = validate_cpu(c, plan, execution)
    mismatch = value.get('bitwise_mismatches')
    need(value.get('schema') == 'opt50_dense_model_worker_result_v1' and value.get('Runner_closed') is True and
         type(value.get('runner_calls')) is int and value['runner_calls'] == 1 and type(value.get('run_id')) is int and
         value['run_id'] == 1 and type(value.get('compared_logits')) is int and value['compared_logits'] == 1000 and
         type(mismatch) is int and 0 <= mismatch <= 1000 and
         value.get('status') == ('MODEL_LOGITS_MATCH_BITWISE' if mismatch == 0 else 'MODEL_LOGITS_MISMATCH') and
         type(value.get('expected_PL_starts')) is int and value['expected_PL_starts'] == 36 and
         value.get('actual_PL_starts') == 'PENDING_NATIVE_END_AND_OWNER_EXIT_PROOF' and
         value.get('custom_graph_sha256') == plan['custom_graph_sha256'] and
         value.get('cpu_reference_record_sha256') == identity(c.read_file(execution/'cpu_reference.json', private=True))['sha256'] and
         value.get('logits_file') == identity(c.read_file(execution/'model_logits.npy', private=True)),
         'one completed, closed full-model worker proof differs')
    for field in ('max_abs_difference', 'max_relative_difference'):
        need(type(value.get(field)) in (int, float) and math.isfinite(value[field]) and value[field] >= 0,
             'invalid numerical difference')
    need(type(value.get('first_differences')) is list and len(value['first_differences']) == min(20, mismatch) and
         type(value.get('allclose_atol_1e_5_rtol_1e_5')) is bool, 'numerical diagnostic evidence incomplete')
    if mismatch == 0:
        need(value['max_abs_difference'] == value['max_relative_difference'] == 0 and
             value.get('logits_raw_sha256') == reference['logits_raw_sha256'], 'bitwise PASS conflicts with logits identity')
    timing(value, ('setup', 'runner_run'))
    accuracy = value.get('accuracy', {})
    need(accuracy.get('label') == plan['sample']['label'] and accuracy.get('compared_logits') == 1000 and
         accuracy.get('bitwise_mismatches') == mismatch and accuracy.get('CPU_logits_sha256') == reference['logits_raw_sha256'] and
         accuracy.get('PL_logits_sha256') == value['logits_raw_sha256'] and accuracy.get('CPU_top5') == value['CPU_top5'] and
         accuracy.get('PL_top5') == value['PL_top5'], 'accuracy payload differs from validated numerical proof')
    return value


class Coordinator:
    def __init__(self, a, plan, lock, observer=None):
        self.a, self.c, self.durable, self.plan, self.lock = a, a.c, a.durable, plan, lock
        self.guard = getattr(a, 'guard', None)
        need(self.guard is not None and callable(getattr(self.guard, 'boundary', None)) and
             callable(getattr(self.guard, 'monitor', None)), 'full batch guardian checkpoint must be bound before any image')
        self.root = Path(plan['plan_path']).parent; self.folder = self.root/'execution'
        self.journal = self.durable.Journal(self.folder/'events')
        self.owner = self.worker = None
        self.possibly_accessed = self.possibly_spawned = False
        self.logs = []
        self.observer = observer
        self.state = {'schema': 'opt50_dense_model_status_v1', 'boot_id': plan['boot_id'], 'unit': plan['unit'],
                      'coordinator_pid': os.getpid(), 'guardian_pid': plan['guardian_pid'],
                      'old_board_lock_transferred': False, 'scope': IMAGE_SCOPE, 'owner_pid': None, 'child_pid': None,
                      'saved_status_only': True, 'physical_PL_starts': 'NOT_INDEPENDENTLY_MEASURED'}

    def update(self, stage, **details):
        durable = stage in (IMAGE_PASS, FAIL_NUMERICAL, REFUSED, 'OWNER_INTENT_DURABLE',
                            'RETAINED_MODEL_STATE_UNCERTAIN')
        self.state.update(stage=stage, heartbeat_monotonic=time.monotonic(),
                          durable_transition=durable, **details)
        if durable:
            self.journal.append('state', stage, state=self.state.copy())
            self.durable.replace_json(self.folder/'status.json', self.state)
        if self.observer is not None:
            self.observer(self.state.copy())
        print(json.dumps({'stage': stage, 'saved_status_only': True}), flush=True)

    def hold(self, reason):
        while True:
            try:
                self.update('RETAINED_MODEL_STATE_UNCERTAIN', reason=reason,
                            owner_pid=None if self.owner is None else self.owner.pid,
                            child_pid=None if self.worker is None else self.worker.pid,
                            warning='Preserve processes/resources/locks; no automatic retry, kill, reset or unload')
            except BaseException:
                try:
                    os.write(2, b'RETAINED B24 MODEL: unknown native lifetime; preserving all resources and locks\n')
                except BaseException:
                    pass
            try:
                time.sleep(30)
            except BaseException:
                pass

    def log_pair(self, name):
        out = []
        for suffix in ('stdout', 'stderr'):
            stream = os.fdopen(os.open(self.folder/(name+'.'+suffix),
                                      os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb', buffering=0)
            self.logs.append(stream); out.append(stream); os.fsync(stream.fileno())
        self.durable.fsync_dir(self.folder)
        return out

    def tick(self, stage):
        observation = self.guard.monitor(self.plan)
        self.update(stage, owner_pid=None if self.owner is None else self.owner.pid,
                    child_pid=None if self.worker is None else self.worker.pid, guardian_observation=observation)

    def wait_child(self, process, budget, stage, watch_owner=False):
        started, last = time.monotonic(), -float('inf')
        while process.poll() is None:
            now = time.monotonic()
            need(not stop_requested and now-started <= budget,
                 'child observation interrupted/overdue; no kill, retry or resource release')
            if watch_owner:
                need(self.owner is not None and self.owner.poll() is None, 'owner exited during model worker')
            if now-last >= 5:
                self.tick(stage); last = now
            time.sleep(0.2)
        process.wait()  # Reap only after poll proved exit; never timeout/kill.
        for stream in self.logs:
            os.fsync(stream.fileno())
        return process.returncode

    def worker_call(self, action):
        interpreter(self.c, self.plan['interpreter'])
        command = [PYTHON, '-B', str(self.root/'tools/model_worker.py'), '--action', action,
                   '--plan', self.plan['plan_path']]
        if action == 'run':
            command.append(AUTH)
        need(action in ('prepare', 'run'), 'only merged preparation or one-model worker is supported')
        name = {'prepare': 'prepare_worker', 'run': 'model_worker'}[action]
        out, err = self.log_pair(name)
        self.journal.append(name, 'INTENT', argv=command)
        need(not stop_requested, 'stop requested before worker spawn')
        self.possibly_spawned = True
        self.worker = subprocess.Popen(command, cwd=self.folder, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/root',
                                            'XRT_INI_PATH': str(self.root/'xrt.ini')})
        pid = self.worker.pid
        self.journal.append(name, 'SPAWNED', pid=pid, argv=command)
        stage = {'prepare': 'INPUT_AND_CPU_PREPARATION_RUNNING', 'run': 'MODEL_WORKER_RUNNING'}[action]
        code = self.wait_child(self.worker, 300, stage,
                               watch_owner=action == 'run')
        self.journal.append(name, 'RETURNED', pid=pid, returncode=code)
        self.worker = None; self.possibly_spawned = False
        need(code == 0 and self.c.read_file(self.folder/(name+'.stderr'), private=True) == b'',
             'worker failed or emitted unexpected diagnostics; error suppression is forbidden')
        if action == 'prepare':
            record = {'schema': 'opt50_dense_merged_preparation_v1',
                      'input': validate_input(self.c, self.plan, self.folder),
                      'CPU_reference': validate_cpu(self.c, self.plan, self.folder),
                      'hardware_accessed': False, 'PL_START_commands_issued_by_worker': 0}
        else:
            record = validate_worker(self.c, self.plan, self.folder)
        need(self.c.decode_json(self.c.read_file(self.folder/(name+'.stdout'), private=True)) == record,
             'worker saved result and stdout differ')
        return record

    def ready(self):
        started, last = time.monotonic(), -float('inf')
        while True:
            need(self.owner.poll() is None, 'native owner exited before READY')
            need(not stop_requested and time.monotonic()-started <= 180, 'owner READY interrupted/overdue')
            raw = self.c.read_file(self.folder/'owner.stdout', private=True)
            need(len(raw) <= 4096, 'unexpected owner startup diagnostics')
            if raw == ('KV5_READY %d\n' % self.owner.pid).encode():
                for name in ('runtime.sock', 'runtime.sock.ctl'):
                    info = (self.folder/name).lstat()
                    need(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0, 'root owner socket identity differs')
                return
            if time.monotonic()-last >= 5:
                self.tick('OWNER_INITIALIZING'); last = time.monotonic()
            time.sleep(0.2)

    def native_completion(self, closed=False):
        return self.a.proof.native_completion(self.c, self.folder, read_events,
                    self.plan['boot_id'], self.a.runtime.verify_bundle(self.root/'model')['manifest'], closed=closed)

    def execute(self):
        try:
            self.update('PREPARING_REAL_INPUT_NO_OWNER_OR_MMIO')
            manifest = self.a.runtime.verify_bundle(self.root/'model')['manifest']
            expected_job = job_bytes(self.plan, manifest, os.getpid())
            if (self.root/'job.kv').exists():
                need(self.c.read_file(self.root/'job.kv', private=True) == expected_job, 'same coordinator/model job changed')
            else:
                new_bytes(self.c, self.durable, self.root/'job.kv', expected_job)
            prepared = self.worker_call('prepare')
            cpu = prepared['CPU_reference']
            self.durable.new_json(self.folder/'held_before.json', self.guard.boundary(self.plan))
            validate_parent(self.c, self.plan, self.lock)
            need(not stop_requested and 0 <= time.monotonic()-self.plan['created_monotonic'] <= 900,
                 'plan expired/interrupted before native access')
            verify_tools(HERE, private=True)
            command = [str(self.root/'native_owner'), str(self.root/'model/weights.bin'), self.plan['socket'],
                       str(self.root/'metadata.xclbin'), str(self.folder), str(self.root/'job.kv'), str(self.lock), AUTH]
            out, err = self.log_pair('owner')
            self.update('OWNER_INTENT_DURABLE')
            self.journal.append('owner', 'INTENT', argv=command, expected_START_commands=36)
            self.possibly_accessed = True  # Must precede Popen, including ambiguous spawn failures.
            self.owner = subprocess.Popen(command, cwd=self.folder, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                          pass_fds=(self.lock,), env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                            'LANG': 'C.UTF-8', 'HOME': '/root', 'XRT_INI_PATH': str(self.root/'xrt.ini')})
            pid = self.owner.pid
            self.journal.append('owner', 'SPAWNED', pid=pid, argv=command)
            self.ready()
            result = self.worker_call('run')
            need(self.owner.poll() is None, 'owner exited before authenticated INFO/STOP')
            admin = Admin(self.folder/'runtime.sock.ctl', pid, self.plan['logical_weights_sha256'])
            try:
                metrics = admin.metrics()  # Fresh full guard/static hashes and four-BO sync, outside Runner timer.
                self.durable.new_json(self.folder/'owner_metrics.json', metrics)
                before_stop = self.native_completion(closed=False)
                self.durable.new_json(self.folder/'before_stop_native.json', before_stop)
                self.journal.append('management', 'STOP_INTENT', owner_pid=pid, Runner_closed=True,
                                    native_END_completed=True, postrun_buffer_checks_verified=True)
                admin.stop()
                self.journal.append('management', 'STOP_RETURNED', owner_pid=pid)
            finally:
                admin.close()
            code = self.wait_child(self.owner, 120, 'OWNER_SAFE_CLOSING')
            self.journal.append('owner', 'RETURNED', pid=pid, returncode=code)
            need(code == 0 and self.c.read_file(self.folder/'owner.stdout', private=True) ==
                 ('KV5_READY %d\nKV5_STOP calls=36\n' % pid).encode() and
                 self.c.read_file(self.folder/'owner.stderr', private=True) == b'', 'owner normal STOP/exit proof differs')
            native = self.native_completion(closed=True)
            self.owner = None
            held = self.guard.boundary(self.plan)
            self.durable.new_json(self.folder/'held_after.json', held)
            verify_tools(HERE, private=True)
            terminal = IMAGE_PASS if result['bitwise_mismatches'] == 0 else FAIL_NUMERICAL
            complete = {'schema': 'opt50_dense_model_completion_v1', 'boot_id': self.plan['boot_id'],
                        'parent_lifecycle': 'opt50_dense_merged_prepare_parent_v1',
                        'input_preparation': prepared['input'],
                        'plan_token': self.plan['token'], 'native': native, 'worker': result, 'CPU_reference': cpu,
                        'owner_metrics': metrics, 'owner_pid': pid, 'owner_exit_code': code, 'held_after': held,
                        'latency_ms': result['runner_run_ms'], 'CPU_reference_ms': cpu['reference_ms'],
                        'Runner_setup_ms': result['setup_ms'], 'CPU_reference_setup_ms': cpu['reference_setup_ms'],
                        'measurement_profile': PROFILE, 'single_sample_only': True,
                        'accuracy_verified': False, 'performance_verified': False,
                        'input_kind': self.plan['input_kind'], 'sample': self.plan['sample'],
                        'scope': 'One sealed dataset sample; fresh owner, exactly 36 PL calls; CPU4/PL4 comparison'}
            self.durable.new_json(self.folder/'completion.json', complete)
            self.update(terminal, owner_pid=None, child_pid=None, completion=complete)
            for stream in self.logs:
                stream.close()
            return 0 if terminal == IMAGE_PASS else 1
        except BaseException as error:
            reason = type(error).__name__+': '+str(error)
            if self.possibly_accessed or self.possibly_spawned or self.worker is not None:
                self.hold(reason)
            self.update(REFUSED, owner_pid=None, child_pid=None, reason=reason, hardware_executed=False)
            for stream in self.logs:
                stream.close()
            return 1


def sample_parent_proof(c, folder, plan, complete):
    """Read persisted child/STOP/reap proof; never contact a process or device."""
    folder = Path(folder); state = c.read_json(folder/'status.json', private=True)
    need(state.get('stage') in (IMAGE_PASS, FAIL_NUMERICAL) and state.get('owner_pid') is None and
         state.get('child_pid') is None and state.get('completion') == complete, 'sample final parent state differs')
    need(complete.get('parent_lifecycle') == 'opt50_dense_merged_prepare_parent_v1',
         'merged-preparation parent lifecycle marker required')
    paths = sorted((folder/'events').glob('event_*.json'))
    need(0 < len(paths) <= 10000 and {x.name for x in (folder/'events').iterdir()} == {x.name for x in paths},
         'sample parent journal inventory differs')
    records = []
    stages = {'OWNER_INTENT_DURABLE', state['stage']}
    for index, path in enumerate(paths, 1):
        value = c.read_json(path, private=True)
        need(path.name == 'event_%06d.json' % index and value.get('schema') == 'opt50_durable_event_v1' and
             value.get('sequence') == index and value.get('action') in
             ('state', 'prepare_worker', 'model_worker', 'owner', 'management'), 'sample parent journal gap/action differs')
        if value['action'] == 'state':
            snapshot = value.get('state', {})
            need(value['phase'] in stages and snapshot.get('stage') == value['phase'] and
                 snapshot.get('boot_id') == plan['boot_id'] and snapshot.get('unit') == plan['unit'],
                 'sample parent records foreign/unresolved state')
        records.append(value)
    need(records[-1].get('action') == 'state' and records[-1].get('state') == state,
         'sample final status differs from durable journal')
    order = {}
    for action in ('prepare_worker', 'model_worker', 'owner'):
        rows = [(i, row) for i, row in enumerate(records) if row['action'] == action]
        need([row['phase'] for _, row in rows] == ['INTENT', 'SPAWNED', 'RETURNED'], 'sample child lifecycle incomplete: '+action)
        intent, spawned, returned = [row for _, row in rows]
        need(type(spawned.get('pid')) is int and spawned['pid'] > 1 and spawned['pid'] == returned.get('pid') and
             type(returned.get('returncode')) is int and returned['returncode'] == 0 and
             type(intent.get('argv')) is list and intent['argv'] == spawned.get('argv'), 'sample child exit/reap differs: '+action)
        if action == 'owner':
            need(returned['pid'] == complete['owner_pid'] and intent.get('expected_START_commands') == 36, 'sample owner identity/budget differs')
        order[action] = [i for i, _ in rows]
    management = [(i, row) for i, row in enumerate(records) if row['action'] == 'management']
    need([row['phase'] for _, row in management] == ['STOP_INTENT', 'STOP_RETURNED'] and
         all(row.get('owner_pid') == complete['owner_pid'] for _, row in management) and
         all(management[0][1].get(key) is True for key in ('Runner_closed', 'native_END_completed', 'postrun_buffer_checks_verified')),
         'sample authenticated STOP proof differs')
    need(order['prepare_worker'][2] < order['owner'][0] <
         order['owner'][1] < order['model_worker'][0] < order['model_worker'][2] < management[0][0] < management[1][0] <
         order['owner'][2] < len(records)-1, 'sample input/CPU/Native/STOP/reap order differs')
    input_plan = dict(plan, input=str(folder/'input.npy')) if (folder/'input.npy').exists() else plan
    input_record = validate_input(c, input_plan, folder)
    need(input_record == complete.get('input_preparation'), 'merged input preparation proof differs')
    preparation = {'schema': 'opt50_dense_merged_preparation_v1', 'input': input_record,
                   'CPU_reference': complete['CPU_reference'], 'hardware_accessed': False,
                   'PL_START_commands_issued_by_worker': 0}
    for action, record in (('prepare_worker', preparation), ('model_worker', complete['worker'])):
        need(c.read_file(folder/(action+'.stderr'), private=True) == b'' and
             c.decode_json(c.read_file(folder/(action+'.stdout'), private=True)) == record, 'sample child stdout/stderr differs')
    need(c.read_file(folder/'owner.stderr', private=True) == b'' and
         c.read_file(folder/'owner.stdout', private=True) == ('KV5_READY %d\nKV5_STOP calls=36\n' % complete['owner_pid']).encode(),
         'sample owner normal STOP text differs')
    return {'children_reaped_exit0': True, 'STOP_verified': True, 'events': len(records)}


def sample_accuracy(a, view, complete):
    native, result, cpu = complete['native'], complete['worker'], complete['CPU_reference']
    sample = view['sample']
    comparison = result['accuracy']
    record = {'schema': 'opt50_dense_accuracy_record_v1', 'ordinal': sample['ordinal'], 'id': sample['id'],
              'label': sample['label'], 'image_sha256': sample['image']['sha256'], 'input_sha256': sample['input']['sha256'],
              **{key: comparison[key] for key in ('CPU_logits_sha256', 'PL_logits_sha256', 'CPU_top5', 'PL_top5',
                 'bitwise_mismatches', 'max_abs_difference')},
              'CPU_reference_ms': cpu['reference_ms'], 'inference_ms': result['runner_run_ms'],
              'completed_PL_calls': native['completed_PL_calls'], 'Dense_calls': native['PL_dense'],
              'Sparse_calls': native['PL_sparse'], 'owner_exit_code': complete['owner_exit_code'],
              'device_closed': native['device_closed'], 'postrun_buffer_checks_verified': native['postrun_buffer_checks_verified'],
              'native_END_verified': native['native_END_completed'], 'worker_runner_calls': result['runner_calls'],
              'compared_logits': result['compared_logits'], 'hot_path_disk_events': native['hot_path_disk_events'],
              'graph_owner_sync_calls': native['graph_owner_sync_calls']}
    a.metrics.validate_record(record, sample)
    return record


def rotate_closed_sample(a, plan, view, complete):
    """All callers must have reaped both children and independently proved STOP/closure."""
    root = Path(plan['plan_path']).parent; execution = root/'execution'
    native = complete.get('native', {})
    need(complete.get('owner_exit_code') == 0 and native.get('device_closed') is True and
         native.get('native_END_completed') is True and native.get('postrun_buffer_checks_verified') is True and
         native.get('completed_PL_calls') == 36 and native.get('PL_dense') == 12 and native.get('PL_sparse') == 24,
         'rotation forbidden before complete single-owner closure')
    state = a.c.read_json(execution/'status.json', private=True)
    need(state.get('stage') in (IMAGE_PASS, FAIL_NUMERICAL) and state.get('owner_pid') is None and
         state.get('child_pid') is None and state.get('completion') == complete, 'children/status not closed before rotation')
    cpu = validate_cpu(a.c, view, execution); worker = validate_worker(a.c, view, execution)
    verified_native = a.proof.native_completion(a.c, execution, read_events, plan['boot_id'],
                                                a.runtime.verify_bundle(root/'model')['manifest'], closed=True)
    need(complete.get('CPU_reference') == cpu and complete.get('worker') == worker and native == verified_native,
         'rotation proof differs from immutable CPU/worker/native records')
    sample_parent_proof(a.c, execution, view, complete)
    record = sample_accuracy(a, view, complete)
    a.durable.new_json(execution/'accuracy_record.json', record)
    need(identity(a.c.read_file(root/'input.npy', private=True)) == view['input_pin'] and
         a.c.read_json(root/'current_sample.json', private=True) == sample_selection(plan, view['sample']),
         'input/selection changed before archival')
    target = root/'samples'/('%06d' % view['sample']['ordinal'])
    need(not target.exists(), 'sample already archived; no overwrite/replay')
    # No live worker/owner may refer to these paths after closure. Every rename
    # is durable before the next image's input or owner can be created.
    for name in ('input.npy', 'current_sample.json'):
        os.rename(root/name, execution/name)
    a.durable.fsync_dir(root); a.durable.fsync_dir(execution)
    pins = {}
    for file in sorted(execution.rglob('*')):
        info = file.lstat()
        need(not stat.S_ISLNK(info.st_mode), 'linked audit file before archive')
        if stat.S_ISREG(info.st_mode):
            pins[file.relative_to(execution).as_posix()] = identity(a.c.read_file(file, private=True, limit=64*1024*1024))
        else:
            need(stat.S_ISDIR(info.st_mode), 'unclosed special file in audit archive')
    a.durable.new_json(execution/'commit.json', {'schema': 'opt50_dense_batch_sample_commit_v1', 'plan_token': plan['token'],
                         'sample': view['sample'], 'closed': True, 'files': pins})
    os.rename(execution, target)
    a.durable.fsync_dir(root/'samples'); a.durable.fsync_dir(root)
    return record


class BatchCoordinator:
    def __init__(self, a, plan, lock):
        self.a, self.plan, self.lock = a, plan, lock
        self.root = Path(plan['plan_path']).parent
        self.journal = a.durable.Journal(self.root/'batch_events')
        self.samples = selected_samples(a, plan)
        self.records = []
        self.last_progress_saved = -float('inf')
        self.state = {'schema': 'opt50_dense_acceptance_batch_status_v1', 'boot_id': plan['boot_id'],
                      'plan_token': plan['token'], 'unit': plan['unit'], 'coordinator_pid': os.getpid(),
                      'guardian_pid': plan['guardian_pid'], 'measurement_profile': PROFILE, 'sample_start': plan['sample_start'],
                      'sample_count': plan['sample_count'], 'total_START_budget': plan['total_START_budget'],
                      'committed_samples': 0, 'completed_PL_calls': 0, 'owner_pid': None, 'child_pid': None,
                      'single_sample_only': False, 'saved_status_only': True, 'accuracy_verified': False,
                      'physical_PL_starts': 'NOT_INDEPENDENTLY_MEASURED'}

    def update(self, stage, **fields):
        self.state.update(stage=stage, heartbeat_monotonic=time.monotonic(), **fields)
        self.journal.append('batch', stage, state=self.state.copy())
        self.a.durable.replace_json(self.root/'batch_status.json', self.state)

    def observe_image(self, state):
        now = time.monotonic()
        self.state.update(stage='IMAGE_RUNNING' if not state['stage'].startswith('RETAINED') else 'RETAINED_BATCH_STATE_UNCERTAIN',
                          heartbeat_monotonic=now, image_stage=state['stage'],
                          owner_pid=state.get('owner_pid'), child_pid=state.get('child_pid'), reason=state.get('reason'))
        if state.get('durable_transition') or now-self.last_progress_saved >= 5:
            # Progress is one saved receipt, not two extra durable journals.
            # Child intents/results, terminal state and closed commits remain durable.
            self.a.durable.replace_json(self.root/'batch_status.json', self.state)
            self.last_progress_saved = now

    def hold(self, reason):
        while True:
            try:
                self.update('RETAINED_BATCH_STATE_UNCERTAIN', reason=reason,
                            warning='No next image, retry, process termination, reset or unload')
            except BaseException:
                try: os.write(2, b'RETAINED B24 BATCH: preserve all processes/resources/locks; no replay\n')
                except BaseException: pass
            try: time.sleep(30)
            except BaseException: pass

    def finish(self, stage, reason=None):
        # Called only on a boundary with no unknown owner or worker lifetime.
        final_guardian = self.a.guard.finish(self.plan)
        self.a.durable.new_json(self.root/'batch_guardian_final.json', final_guardian)
        aggregate = batch_accuracy(self.a, self.records, self.samples)
        done = {'schema': 'opt50_dense_batch_completion_v1', 'stage': stage, 'closed': True, 'plan_token': self.plan['token'],
                'dataset_manifest': self.plan['dataset_manifest'], 'sample_start': self.plan['sample_start'],
                'sample_count': len(self.samples), 'committed_samples': len(self.records),
                'completed_PL_calls': 36*len(self.records), 'Dense_calls': 36*len(self.records), 'Sparse_calls': 0*len(self.records),
                'compared_logits': 1000*len(self.records), 'total_START_budget': self.plan['total_START_budget'],
                'accuracy': aggregate, 'owner_pid': None, 'child_pid': None, 'reason': reason,
                'scope': 'Selected chunk only; all 1800 unique bound records are required for final acceptance',
                'footprint': tree_usage(self.root/'samples')}
        self.a.durable.new_json(self.root/'batch_completion.json', done)
        committed_index = self.a.history_index.append_closed(self.a, self.plan, self.records, done)
        self.update(stage, owner_pid=None, child_pid=None, completion=done, reason=reason,
                    history_index_committed=committed_index)
        return 0 if stage == PASS else 1

    def execute(self):
        try:
            self.a.guard = self.a.guardian_checkpoint.bind_checkpoint(self.a, self.plan)
            self.a.durable.new_json(self.root/'batch_guardian_entry.json', self.a.guard.initial_review)
            self.update('BATCH_ADMITTED_NO_OWNER_YET')
            for position, sample in enumerate(self.samples):
                if stop_requested:
                    return self.finish('PARTIAL_CLOSED_OPERATOR_STOP', 'Stopped on a proven closed image boundary; no automatic replay')
                # Keep the existing 900-second hardware gate for every owner.
                if not 0 <= time.monotonic()-self.plan['created_monotonic'] <= 900:
                    return self.finish('PARTIAL_CLOSED_PLAN_EXPIRED', 'No next owner launched; submit a separate new chunk only after review')
                need(position*36 < self.plan['total_START_budget'], 'sealed total START budget exhausted')
                need(not (self.root/'execution').exists() and not (self.root/'current_sample.json').exists() and
                     not (self.root/'input.npy').exists(), 'previous sample paths were not closed and archived')
                self.a.durable.new_json(self.root/'current_sample.json', sample_selection(self.plan, sample))
                self.a.durable.mkdir_new(self.root/'execution'); self.a.durable.mkdir_new(self.root/'execution/events')
                self.journal.append('sample', 'ADMISSION_DURABLE_BEFORE_ANY_CHILD', ordinal=sample['ordinal'], id=sample['id'],
                                    input_sha256=sample['input']['sha256'], label=sample['label'], expected_START_commands=36)
                self.update('SAMPLE_ADMITTED_BEFORE_ANY_CHILD', current_ordinal=sample['ordinal'], current_id=sample['id'],
                            admitted_samples=position+1, reserved_START_budget=(position+1)*36)
                view = sample_plan(self.a, self.plan)
                image = Coordinator(self.a, view, self.lock, observer=self.observe_image)
                code = image.execute()
                if image.state.get('stage') == REFUSED and image.owner is None and image.worker is None and not image.possibly_accessed:
                    reason = image.state.get('reason', 'Input/reference refused before Native owner')
                    stage = 'PARTIAL_CLOSED_PLAN_EXPIRED' if 'plan expired' in reason else 'REFUSED_BATCH_BEFORE_OWNER'
                    return self.finish(stage, reason)
                need(image.owner is None and image.worker is None and code in (0, 1), 'image children were not normally reaped')
                complete = self.a.c.read_json(self.root/'execution/completion.json', private=True)
                record = rotate_closed_sample(self.a, self.plan, view, complete)
                self.records.append(record)
                self.journal.append('sample', 'COMMITTED_CLOSED', ordinal=sample['ordinal'], id=sample['id'],
                                    archive=str(self.root/'samples'/('%06d' % sample['ordinal'])),
                                    commit_pin=identity(self.a.c.read_file(self.root/'samples'/('%06d' % sample['ordinal'])/'commit.json', private=True)))
                self.update('SAMPLE_COMMITTED_NATIVE_CLOSED', committed_samples=len(self.records),
                            completed_PL_calls=36*len(self.records), owner_pid=None, child_pid=None)
                if record['bitwise_mismatches']:
                    return self.finish(BATCH_FAIL, 'Numerical mismatch; owner closed, sample preserved, no next image')
            return self.finish(PASS)
        except BaseException as error:
            self.hold(type(error).__name__+': '+str(error))


def read_completed_records(path, allow_partial=False):
    a = api(private=True); plan = load_plan(a, path, fresh=False); root = Path(path).parent
    state = a.c.read_json(root/'batch_status.json', private=True)
    done = a.c.read_json(root/'batch_completion.json', private=True)
    accepted = (PASS,) if not allow_partial else (PASS, BATCH_FAIL, 'PARTIAL_CLOSED_PLAN_EXPIRED', 'PARTIAL_CLOSED_OPERATOR_STOP', 'REFUSED_BATCH_BEFORE_OWNER')
    need(state.get('stage') in accepted and state.get('owner_pid') is None and state.get('child_pid') is None and
         state.get('completion') == done and done.get('closed') is True and done.get('stage') == state['stage'],
         'batch is active, incomplete, uncertain or not normally closed')
    records = committed_records(a, plan, root, complete=not allow_partial)
    need(done.get('committed_samples') == len(records) and done.get('completed_PL_calls') == 36*len(records) and
         done.get('compared_logits') == 1000*len(records) and done.get('accuracy') ==
         batch_accuracy(a, records, selected_samples(a, plan)),
         'batch completion counters/metrics differ from independent archived records')
    return records


def batch_accuracy(a, records, samples):
    if len(records) == len(samples):
        return a.metrics.aggregate(records, expected_count=len(samples), expected_samples=samples)
    need(len(records) < len(samples), 'more records than sealed samples')
    partial = a.metrics.aggregate(records, expected_count=len(records), expected_samples=samples[:len(records)]) if records else {}
    return {**partial, 'status': 'INCOMPLETE', 'full_1800_acceptance': False,
            'planned_chunk_images': len(samples), 'completed_images': len(records),
            'missing_images': len(samples)-len(records), 'scope': 'Completed prefix statistics only; no chunk/dataset acceptance'}


def request_stop(*_):
    global stop_requested
    stop_requested = True


def run(path, authorized):
    need(authorized, 'explicit sealed-batch admission; each fresh owner remains limited to one model/36 STARTs')
    a = api(private=True); a.c.root_linux()
    plan = load_plan(a, path, fresh=True)
    a.c.configure(plan['guardian_plan'])
    with a.c.companion_locked() as lock:
        history = previous_clear(a.c, exclude=path)
        snapshot = a.history_index.admit(a, history, plan['dataset_manifest'], plan['sample_start'], plan['sample_count'])
        need(snapshot == plan.get('history_index'), 'history changed after planning; no automatic renewal or replay')
        validate_parent(a.c, plan, lock)
        root = Path(path).parent
        need(not (root/'batch_started.json').exists(), 'batch plan already consumed; no replay/resume/automatic renewal')
        a.durable.new_json(root/'batch_started.json', {'schema': 'opt50_dense_batch_start_intent_v1',
                          'plan_token': plan['token'], 'pid': os.getpid(), 'total_START_budget': plan['total_START_budget'],
                          'sample_count': plan['sample_count'], 'boot_id': plan['boot_id']})
        a.durable.mkdir_new(root/'batch_events'); a.durable.mkdir_new(root/'samples')
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, request_stop)
        return BatchCoordinator(a, plan, lock).execute()


def status(path, summary=False):
    # Saved-status inspection must not repeatedly decode the entire model.
    # The reader binds its own source and the coordinator to the private plan;
    # execution admission still uses the complete api/load_plan verification.
    raw = read(HERE/'tools_manifest.json', private=True)
    listing = decode_json(raw)
    tools = {'manifest': identity(raw), 'files': listing['files']}
    reader = module_checked('status_receipt.py', tools, HERE, private=True)
    return reader.read_saved(path, summary=summary, folder=HERE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    item = sub.add_parser('check')
    item.add_argument('--guardian-plan', type=Path, required=True)
    item.add_argument('--layer-plan', type=Path, required=True)
    item.add_argument('--previous-model-plan', type=Path, required=True)
    item.add_argument('--dataset', type=Path, required=True)
    item.add_argument('--start', type=int, default=0)
    item.add_argument('--count', type=int, default=5)
    item.add_argument('--pilot-plan', type=Path)
    item.add_argument('--metadata', type=Path)
    item.add_argument('--uart-log-confirmed', action='store_true')
    for name in ('run', 'status'):
        item = sub.add_parser(name); item.add_argument('--plan', type=Path, required=True)
        if name == 'run':
            item.add_argument(AUTH, action='store_true', dest='authorized')
        else:
            item.add_argument('--summary', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'run':
            return run(args.plan, args.authorized)
        result = check(args.guardian_plan, args.layer_plan, args.metadata, args.uart_log_confirmed,
                       previous_path=args.previous_model_plan, dataset=args.dataset, start=args.start, count=args.count,
                       pilot_path=args.pilot_plan) if args.action == 'check' else \
                 status(args.plan, args.summary)
        print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False), flush=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'B24_MODEL_ENTRY_REFUSED_NO_AUTOMATIC_RECOVERY',
                          'error': type(error).__name__+': '+str(error)}), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
