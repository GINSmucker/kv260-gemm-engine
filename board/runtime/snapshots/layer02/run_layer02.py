"""Prepare/run one B24 layer02 Dense/Sparse test with a resident OPT50 guardian.

check: OS/file reads plus NEW root-private plan/copies; no device or MMIO access.
run: explicit two-START authorization, same CU/buffers, exact B integer golden.
No firmware load, driver load, clock/reset write, retry, signal or restoration.
Unknown native state retains the coordinator and global companion lock.
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
import stat
import struct
import subprocess
import sys
import time
import types

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
RECORDS = Path('/var/lib/opt50-b24-layer02-v1')
CASE = 'B24_dev0000_layer02'
SHAPE = [197, 192, 576]
MODEL_SHA = 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
ARCHIVE_SHA = '7ada4de5cf99d09d3d6a5b9d92108bcbce85dc636e0c1669d8b14d5171139fc4'
PACKAGE_SHA = 'c0f35179ea2edd7fe848cc5643232da86b795b92a18fa27624f5770cad02f769'
METADATA = {'bytes': 8560, 'sha256': '715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551'}
METADATA_UUID = 'fd96539d-4cbc-f732-7694-0f0236ca1cf3'
PAYLOADS = {
    'X': ('X.bin', 37824, '67563398b91ee2a852c21b098bad353e5db10e2d13652574512bb88149b2dfe9'),
    'W_dense_data': ('W_dense_data.bin', 110592, '953f919af13d4b9a19ecb60e9bcaa5a59eb4e19204cd8bdbe63cce2c1a239e14'),
    'W_dense_meta': ('W_dense_meta.bin', 16, '374708fff7719dd5979ec875d56cd2286f6d3cf7ec317a3b25632aab28ec37bb'),
    'W_sparse_data': ('W_sparse_data.bin', 55296, '6a424a993a7f6b0d991663b7619f14fffd7d83d6c3f09c7702af3c6a1bf4ae9b'),
    'W_sparse_meta': ('W_sparse_meta.bin', 13824, '94a777971b6bc52b7180251d2c474c5ae4a79fba7e509917e171451ae7e06f78'),
    'Y_golden': ('Y_golden.bin', 453888, '7e5b8c893a3477702c2566097a1c12d82e5d710d5b9ede11d42ecd410803d292'),
}
SOURCES = ('runtime/opt50_b24_layer02.cpp', 'runtime/opt50_b24_layer02_pure.hpp',
           'runtime/support/opt50_common.hpp', 'runtime/support/picosha2.h')
SCOPE = 'B24_LAYER02_TWO_MODES_SAME_PRUNED_WEIGHTS_WITH_HELD_GUARDIAN'
PASS = 'PASS_B24_LAYER02_DENSE_SPARSE_EXACT_NO_MODEL_ACCEPTANCE'
REFUSED = 'REFUSED_BEFORE_NATIVE_NO_MMIO'
ZERO_REFUSAL = 'REFUSED_ZERO_START_NATIVE_CLOSED_NOT_COMPUTE_PASS'
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
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
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


def verify_tools(folder=HERE, private=False):
    folder = ordinary(folder)
    raw = read(folder/'tools_manifest.json', private=private)
    value = decode_json(raw)
    required = {'run_layer02.py', 'support_py/resident.py', 'support_py/durable.py', *SOURCES,
                'prepare_inputs.py', 'build_runtime.py', 'data/manifest.json', 'data/source/B_MANIFEST.json'}
    need(value.get('schema') == 'opt50_b24_layer02_tools_v1' and value.get('hardware_executed') is False and
         type(value.get('files')) is dict and required <= set(value['files']) and len(value['files']) <= 160,
         'B24 tool inventory differs')
    for name, expected in value['files'].items():
        parts = safe_relative(name)
        need(type(expected) is dict and set(expected) == {'bytes', 'sha256'} and
             type(expected['bytes']) is int and 0 <= expected['bytes'] <= 32*1024*1024 and
             type(expected['sha256']) is str and re.fullmatch('[a-f0-9]{64}', expected['sha256']),
             'invalid tool content pin')
        need(identity(read(folder.joinpath(*parts), private=private)) == expected, 'tool/data changed: '+name)
    return {'manifest': identity(raw), 'files': value['files']}


def helpers(folder=HERE, private=False):
    # Source bytes are verified as a complete inventory before helper code runs.
    inventory = verify_tools(folder, private)
    loaded = []
    for name in ('resident', 'durable'):
        path = Path(folder)/'support_py'/(name+'.py')
        raw = read(path, private=private)
        need(identity(raw) == inventory['files']['support_py/'+name+'.py'], 'helper changed before import')
        module = types.ModuleType('_b24_'+name)
        module.__file__ = str(path)
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        loaded.append(module)
    return (*loaded, inventory)


def verify_inputs(folder=HERE, private=False):
    folder = Path(folder)/'data'
    raw = read(folder/'manifest.json', private=private)
    manifest = decode_json(raw)
    need(manifest.get('schema') == 'opt50_b24_layer02_inputs_v1' and manifest.get('case_id') == CASE and
         [manifest.get(key) for key in ('M', 'K', 'N')] == SHAPE and
         manifest.get('source_model_w8a8_sha256') == MODEL_SHA and
         manifest.get('source', {}).get('zip_sha256') == ARCHIVE_SHA and
         manifest['source'].get('original_manifest_sha256') == PACKAGE_SHA,
         'B24 input manifest identity/shape differs')
    files = {'manifest.json': identity(raw)}
    for key, (name, count, digest) in PAYLOADS.items():
        need(identity(read(folder/name, private=private)) == {'bytes': count, 'sha256': digest},
             'B24 fixed input differs: '+name)
        item = manifest.get('payloads', {}).get(key, {})
        need(item.get('file') == name and item.get('bytes') == count and item.get('sha256') == digest,
             'B24 payload manifest differs: '+key)
        files[name] = {'bytes': count, 'sha256': digest}
    source = read(folder/'source/B_MANIFEST.json', private=private)
    need(identity(source)['sha256'] == PACKAGE_SHA, 'B original manifest differs')
    files['source/B_MANIFEST.json'] = identity(source)
    return {'case_id': CASE, 'shape_MKN': SHAPE, 'source_model_sha256': MODEL_SHA,
            'source_archive_sha256': ARCHIVE_SHA, 'files': files}


def verify_build(tools):
    raw = read(HERE/'build_receipt.json')
    receipt = decode_json(raw)
    executable = HERE/'bin/opt50_b24_layer02_xrt'
    binary = read(executable)
    need(len(binary) >= 64 and binary[:7] == b'\x7fELF\x02\x01\x01' and
         struct.unpack_from('<H', binary, 18)[0] == 183 and struct.unpack_from('<H', binary, 16)[0] in (2, 3),
         'native AArch64 little-endian ELF64 required')
    need(receipt.get('schema') == 'opt50_b24_layer02_build_v1' and
         receipt.get('status') == 'BUILD_PASS_WITH_XRT_NOT_BOARD_TEST' and receipt.get('with_xrt') is True and
         receipt.get('hardware_executed') is False and receipt.get('executable') ==
         {'path': str(executable), **identity(binary)} and receipt.get('release_manifest') == tools['manifest'] and
         receipt.get('sources') == {name: tools['files'][name] for name in SOURCES},
         'ARM/XRT build receipt/source identity differs')
    test = receipt.get('self_test', {})
    need(type(test.get('returncode')) is int and test['returncode'] == 0 and test.get('stderr') == '' and
         type(test.get('stdout')) is str and 'SELF_TEST_PASS_NO_HARDWARE_ACCESS' in test['stdout'],
         'native build CPU self-test receipt missing')
    return {'receipt': identity(raw), 'executable': identity(binary)}


def file_inode(path):
    info = ordinary(path).lstat()
    return {'device': info.st_dev, 'inode': info.st_ino}


def review_guardian(c, guardian_path, expected=None, full=True):
    c.configure(guardian_path)
    result = c.held_review() if full else c.monitor_guardian(expected)
    if full:
        guardian = result['guardian']
    else:
        guardian = result
    if expected is not None:
        need(guardian['boot_id'] == expected['boot_id'] and guardian['pid'] == expected['guardian_pid'] and
             guardian['start_ticks'] == expected['guardian_start_ticks'], 'guardian identity changed from plan')
        need(identity(read(guardian_path, private=True)) == expected['guardian_plan_pin'] and
             file_inode(guardian_path) == expected['guardian_plan_inode'], 'guardian plan changed from frozen identity')
    return result


def previous_clear(c, exclude=None):
    # Only this new namespace is classified here. Other live users serialize on
    # the shared companion flock; full held_review also checks device occupancy.
    if not RECORDS.exists():
        return []
    c.private_dir(RECORDS)
    summaries = []
    folders = sorted(RECORDS.iterdir())
    need(len(folders) <= 1000, 'too many B24 plans; review required')
    for folder in folders:
        need(re.fullmatch('plan_[a-f0-9]{12}', folder.name), 'unrecognized B24 record')
        c.private_dir(folder)
        if exclude is not None and folder == Path(exclude).parent:
            continue
        if not (folder/'execution').exists():
            continue  # A created, unlaunched plan has issued no command.
        state = c.read_json(folder/'execution/status.json', private=True)
        need(state.get('schema') == 'opt50_b24_layer02_status_v1' and
             state.get('stage') in (PASS, REFUSED, ZERO_REFUSAL) and state.get('child_pid') is None,
             'a prior B24 attempt is unresolved; preserve it and inspect')
        if state['stage'] != REFUSED:
            completion = c.read_json(folder/'execution/completion.json', private=True)
            need(completion.get('worker_reaped') is True and completion.get('device_closed') is True,
                 'prior B24 native completion not proved')
        summaries.append({'plan': str(folder/'plan.json'), 'stage': state['stage']})
    return summaries


def copy_tools(c, durable, folder, tools):
    durable.mkdir_new(folder/'tools')
    for name in sorted(tools['files'], key=lambda item: (item.count('/'), item)):
        target = folder/'tools'/name
        for parent in reversed(target.parents):
            if parent.is_relative_to(folder/'tools') and not parent.exists():
                durable.mkdir_new(parent)
        durable.copy_new(HERE/name, target, tools['files'][name])
    durable.copy_new(HERE/'tools_manifest.json', folder/'tools/tools_manifest.json', tools['manifest'])


def command_lines(plan):
    path = Path(plan['plan_path'])
    script = path.parent/'tools/run_layer02.py'
    properties = ('Type=exec', 'ExitType=cgroup', 'KillMode=process', 'SendSIGKILL=no',
                  'TimeoutStopSec=infinity', 'RuntimeMaxSec=infinity', 'Restart=no',
                  'RemainAfterExit=yes', 'OOMPolicy=continue')
    command = ['sudo', 'systemd-run', '--unit='+plan['unit'], *['--property='+x for x in properties],
               '/usr/bin/python3', '-B', str(script), 'run', '--plan', str(path),
               '--authorized-layer02-two-starts']
    status_command = ['sudo', '/usr/bin/python3', '-B', str(script), 'status', '--plan', str(path)]
    return shlex.join(command), shlex.join(status_command)


def check(guardian_plan, metadata, uart_confirmed):
    c, durable, tools = helpers()
    c.root_linux()
    need(uart_confirmed, 'independent UART logging confirmation required')
    build = verify_build(tools)
    inputs = verify_inputs()
    c.configure(guardian_plan)
    with c.companion_locked():
        previous = previous_clear(c)
        held = review_guardian(c, guardian_plan)
        old = c.read_json(guardian_plan, private=True)
        source = Path(old['artifacts']['source_paths']['metadata']) if metadata is None else Path(metadata)
        need(identity(read(source)) == METADATA, 'metadata must be the exact fd96539d 8560-byte image')
        if not RECORDS.exists():
            durable.mkdir_new(RECORDS)
        c.private_dir(RECORDS)
        token = secrets.token_hex(6)
        folder = RECORDS/('plan_'+token)
        durable.mkdir_new(folder)
        copy_tools(c, durable, folder, tools)
        durable.copy_new(HERE/'bin/opt50_b24_layer02_xrt', folder/'runtime', build['executable'], mode=0o700)
        durable.copy_new(HERE/'build_receipt.json', folder/'build_receipt.json', build['receipt'])
        durable.copy_new(source, folder/'metadata.xclbin', METADATA)
        need(verify_inputs(folder/'tools', private=True) == inputs, 'root-copied B24 inputs differ')
        guardian = held['guardian']
        path = folder/'plan.json'
        value = {'schema': 'opt50_b24_layer02_plan_v1', 'token': token, 'unit': 'opt50-b24-layer02-'+token,
                 'plan_path': str(path), 'boot_id': guardian['boot_id'], 'expires_seconds': 900,
                 'created_monotonic': time.monotonic(), 'scope': SCOPE,
                 'guardian_plan': str(c.OLD_PLAN), 'guardian_plan_pin': identity(read(c.OLD_PLAN, private=True)),
                 'guardian_plan_inode': file_inode(c.OLD_PLAN),
                 'guardian_pid': guardian['pid'], 'guardian_start_ticks': guardian['start_ticks'],
                 'old_board_lock_transferred': False, 'uart_logging_operator_confirmed': True,
                 'expected_START_commands': 2, 'expected_compared_INT32_outputs': 226944,
                 'case_id': CASE, 'shape_MKN': SHAPE, 'source_model_sha256': MODEL_SHA,
                 'source_archive_sha256': ARCHIVE_SHA, 'inputs': inputs, 'tool_files': tools,
                 'board_build': build, 'runtime': build['executable'], 'metadata': METADATA,
                 'held_review': held, 'previous_attempts': previous, 'command_tools': old['command_tools']}
        durable.new_json(path, value)
    run_command, status_command = command_lines(value)
    return {'status': 'B24_LAYER02_PLAN_CREATED_NO_HARDWARE_ACCESS', 'plan': str(path),
            'boot_id': value['boot_id'], 'expires_seconds': 900, 'case_id': CASE, 'shape_MKN': SHAPE,
            'expected_START_commands': 2, 'expected_compared_INT32_outputs': 226944,
            'writes': 'New root-private tools, B24 case, executable, metadata and plan copies only',
            'old_12_layer_release_modified': False, 'old_guardian_and_board_lock': 'PRESERVED_NOT_TRANSFERRED',
            'next_requires_explicit_hardware_permission': run_command, 'status_command': status_command,
            'hardware_executed': False, 'accuracy_verified': False, 'performance_verified': False}


def validate_plan(value, path):
    path = Path(path)
    token = path.parent.name[5:]
    need(type(value) is dict and re.fullmatch('[a-f0-9]{12}', token) and
         value.get('schema') == 'opt50_b24_layer02_plan_v1' and value.get('token') == token and
         value.get('plan_path') == str(path) and value.get('unit') == 'opt50-b24-layer02-'+token and
         value.get('scope') == SCOPE and value.get('expires_seconds') == 900 and
         value.get('uart_logging_operator_confirmed') is True and value.get('old_board_lock_transferred') is False and
         value.get('case_id') == CASE and value.get('shape_MKN') == SHAPE and
         all(type(x) is int for x in value['shape_MKN']) and value.get('expected_START_commands') == 2 and
         value.get('expected_compared_INT32_outputs') == 226944 and value.get('metadata') == METADATA and
         value.get('source_model_sha256') == MODEL_SHA and value.get('source_archive_sha256') == ARCHIVE_SHA,
         'B24 plan identity/scope differs')
    need(type(value.get('boot_id')) is str and re.fullmatch(
         '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', value['boot_id']) and
         type(value.get('guardian_pid')) is int and value['guardian_pid'] > 1 and
         type(value.get('guardian_start_ticks')) is int and value['guardian_start_ticks'] > 0 and
         type(value.get('created_monotonic')) in (int, float) and
         math.isfinite(value['created_monotonic']) and value['created_monotonic'] >= 0,
         'B24 boot/process/time identity invalid')
    return value


def load_plan(c, path, tools, fresh=False):
    path = Path(path).absolute()
    need(path.name == 'plan.json' and path.parent.parent == RECORDS and
         re.fullmatch('plan_[a-f0-9]{12}', path.parent.name), 'unexpected B24 plan path')
    for folder in (RECORDS, path.parent, path.parent/'tools'):
        c.private_dir(folder)
    value = validate_plan(c.read_json(path, private=True), path)
    need(HERE == path.parent/'tools' and tools == value['tool_files'], 'use the frozen root-owned tool copy')
    need(verify_tools(HERE, private=True) == tools and verify_inputs(HERE, private=True) == value['inputs'],
         'root copied sources/data differ')
    need(identity(read(path.parent/'runtime', private=True)) == value['runtime'] and
         identity(read(path.parent/'metadata.xclbin', private=True)) == METADATA and
         identity(read(path.parent/'build_receipt.json', private=True)) == value['board_build']['receipt'],
         'root copied runtime/metadata/build receipt differ')
    if fresh:
        c.root_linux()
        need(c._proc_read(Path('/proc/sys/kernel/random/boot_id'), 128).decode().strip() == value['boot_id'],
             'plan belongs to another boot')
        need(0 <= time.monotonic()-value['created_monotonic'] <= 900, 'plan expired; do not reuse')
    return value


def validate_parent(c, value, lock):
    old = c.read_json(value['guardian_plan'], private=True)
    tool = c._command_tool(old, 'systemctl')
    fields = (*c.SERVICE, 'ActiveState', 'SubState', 'MainPID')
    query = subprocess.run([tool['entry'], 'show', value['unit']+'.service', '--property='+','.join(fields)],
                           capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, check=False)
    need(query.returncode == 0 and len(query.stdout) <= 16384 and len(query.stderr) <= 8192,
         'coordinator unit observation failed')
    rows = [line.split('=', 1) for line in query.stdout.splitlines() if '=' in line]
    need(len({key for key, _ in rows}) == len(rows), 'duplicate service property')
    actual = dict(rows)
    need(all(actual.get(key) == expected for key, expected in c.SERVICE.items()) and
         actual.get('MainPID') == str(os.getpid()) and actual.get('ActiveState') == 'active' and
         actual.get('SubState') in ('start', 'running'), 'coordinator service guard differs')
    expected = ['/usr/bin/python3', '-B', str(HERE/'run_layer02.py'), 'run', '--plan', value['plan_path'],
                '--authorized-layer02-two-starts']
    need(c._proc_read(Path('/proc/self/cmdline')) == b''.join(x.encode()+b'\0' for x in expected),
         'coordinator exact argv differs')
    need(c._proc_read(Path('/proc/self/cgroup')) == ('0::/system.slice/'+value['unit']+'.service\n').encode(),
         'coordinator cgroup differs')
    info, expected_info = os.fstat(lock), c._lock_info()
    need((info.st_dev, info.st_ino) == (expected_info.st_dev, expected_info.st_ino), 'companion lock replaced')
    c.parse_flock(c._proc_read(Path('/proc/locks'), 4*1024*1024), os.getpid(),
                  (os.major(info.st_dev), os.minor(info.st_dev)), info.st_ino)


def write_job(c, durable, value, execution):
    data = Path(value['plan_path']).parent/'tools/data'
    fields = {'schema': 'opt50_b24_layer02_job_v1', 'run_id': value['token'], 'boot_id': value['boot_id'],
              'expected_parent_pid': str(os.getpid()), 'companion_lock': str(c.LOCK),
              'metadata': str(Path(value['plan_path']).parent/'metadata.xclbin'), 'metadata_sha256': METADATA['sha256'],
              'case_id': CASE, 'M': '197', 'K': '192', 'N': '576',
              'source_model_sha256': MODEL_SHA,
              'package_manifest': str(data/'source/B_MANIFEST.json'), 'package_manifest_sha256': PACKAGE_SHA}
    need(identity(read(data/'source/B_MANIFEST.json', private=True))['sha256'] == PACKAGE_SHA,
         'root copied B manifest differs')
    for key, (name, count, digest) in PAYLOADS.items():
        need(identity(read(data/name, private=True)) == {'bytes': count, 'sha256': digest}, 'copied payload differs')
        fields[key], fields[key+'_sha256'] = str(data/name), digest
    need(len(fields) == 26 and all(not any(ch in str(x) for ch in '\r\n=\0') for x in fields.values()),
         'invalid 26-field native job')
    raw = ''.join(key+'='+str(item)+'\n' for key, item in fields.items()).encode()
    path = execution/'layer02_job.kv'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        need(stream.write(raw) == len(raw), 'short job write')
        stream.flush(); os.fsync(stream.fileno())
    durable.fsync_dir(execution)
    need(read(path, private=True) == raw, 'published job changed')
    return path


def expected_result(value):
    digest = PAYLOADS['Y_golden'][2]
    return {'schema': 'opt50_b24_layer02_runtime_result_v1', 'status': 'REAL_LAYER_DENSE_SPARSE_EXACT_MATCH',
            'run_id': value['token'], 'boot_id': value['boot_id'], 'metadata_uuid': METADATA_UUID,
            'metadata_sha256': METADATA['sha256'], 'PL_START_commands_issued_by_worker': 2,
            'physical_PL_starts': 'NOT_MEASURED', 'compared_INT32_outputs': 226944, 'modes': [0, 1],
            'outputs_sha256': [digest, digest], 'same_CU_and_buffers': True, 'canaries_and_inputs': 'PASS',
            'hardware_executed': True, 'device_closed': True, 'accuracy_verified': False, 'performance_verified': False,
            'case_id': CASE, 'shape_MKN': SHAPE, 'package_manifest_sha256': PACKAGE_SHA,
            'source_model_sha256': MODEL_SHA, 'source_archive_sha256': ARCHIVE_SHA,
            'dense_reference_uses_same_pruned_weights': True}


def validate_result(result, value):
    expected = expected_result(value)
    need(type(result) is dict and set(result) == set(expected), 'native result inventory differs')
    for key, item in expected.items():
        need(type(result[key]) is type(item) and result[key] == item, 'native result differs: '+key)
    need(all(type(x) is int for x in result['modes']+result['shape_MKN']), 'native integer vector types differ')
    return result


def native_events(c, execution, starts):
    folder = Path(execution)/'native_events'
    c.private_dir(folder)
    events = sorted(folder.glob('event_*.json'))
    need(2 <= len(events) <= 100000, 'native event inventory incomplete')
    terminal = 'result.json' if starts == 2 else 'refusal.json'
    need({p.name for p in folder.iterdir()} == {p.name for p in events} | {terminal}, 'unexpected native file')
    phases, previous = [], -1
    for index, path in enumerate(events, 1):
        item = c.read_json(path, private=True)
        need(path.name == 'event_%06d.json' % index and type(item) is dict and
             set(item) == {'schema', 'sequence', 'phase', 'details', 'monotonic_ns'} and
             item['schema'] == 'opt50_b24_layer02_native_event_v1' and type(item['sequence']) is int and
             item['sequence'] == index and type(item['phase']) is str and type(item['details']) is str and
             type(item['monotonic_ns']) is int and item['monotonic_ns'] > previous,
             'native event sequence/schema/time differs')
        previous = item['monotonic_ns']; phases.append(item['phase'])
    need(phases[0] == 'RUNTIME_ENTERED' and phases[-2:] == ['XRT_RESOURCE_CLOSE_INTENT', 'XRT_RESOURCES_CLOSED'] and
         phases.count('XRT_RESOURCES_CLOSED') == 1 and
         'RETAINED_NATIVE_RESOURCES_NO_RETRY' not in phases, 'native resource closure not proved')
    for phase in ('DMA_LIFETIME_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED', 'DMA_COMPLETION_PROVEN'):
        need(phases.count(phase) == starts, 'native two-mode/zero-START lifecycle differs: '+phase)
    if starts == 0:
        need(not any('START' in x or 'DMA' in x or 'ACTIVE' in x for x in phases),
             'zero-START refusal has active lifecycle evidence')
    return {'native_event_count': len(events), 'START_commands_issued': starts,
            'device_closed': True, 'physical_PL_starts': 'NOT_INDEPENDENTLY_MEASURED'}


class Coordinator:
    def __init__(self, c, durable, value, lock):
        self.c, self.durable, self.value, self.lock = c, durable, value, lock
        self.folder = Path(value['plan_path']).parent/'execution'
        self.journal = durable.Journal(self.folder/'events')
        self.child = None
        self.possibly_accessed = False
        self.state = {'schema': 'opt50_b24_layer02_status_v1', 'boot_id': value['boot_id'], 'unit': value['unit'],
                      'coordinator_pid': os.getpid(), 'guardian_pid': value['guardian_pid'],
                      'old_board_lock_transferred': False, 'scope': SCOPE, 'saved_status_only': True,
                      'accuracy_verified': False, 'performance_verified': False, 'physical_PL_starts': 'NOT_MEASURED'}

    def update(self, stage, **details):
        self.state.update(stage=stage, heartbeat_monotonic=time.monotonic(), **details)
        self.journal.append('state', stage, state=self.state.copy())
        self.durable.replace_json(self.folder/'status.json', self.state)
        print(json.dumps({'stage': stage, 'saved_status_only': True}), flush=True)

    def hold(self, reason):
        while True:
            try:
                self.update('RETAINED_NATIVE_STATE_UNCERTAIN', reason=reason,
                            child_pid=None if self.child is None else self.child.pid,
                            warning='Preserve resident guardian, worker and companion lock; no automatic retry or recovery')
                time.sleep(30)
            except BaseException:
                try:
                    os.write(2, b'RETAINED: native state uncertain; preserving resources and locks\n')
                    time.sleep(5)
                except BaseException:
                    pass

    def execute(self):
        c, d, value = self.c, self.durable, self.value
        try:
            self.update('REVIEWING_HELD_GUARDIAN_BEFORE_NATIVE_ACCESS')
            d.new_json(self.folder/'held_before.json', review_guardian(c, value['guardian_plan'], value))
            validate_parent(c, value, self.lock)
            need(not stop_requested, 'stop requested before native access')
            need(0 <= time.monotonic()-value['created_monotonic'] <= 900, 'plan expired before native access')
            job = write_job(c, d, value, self.folder)
            argv = [str(Path(value['plan_path']).parent/'runtime'), '--run', '--job', str(job),
                    '--receipt-dir', str(self.folder/'native_events'), '--lock-fd', str(self.lock), '--authorized-two-starts']
            outpath, errpath = self.folder/'worker.stdout', self.folder/'worker.stderr'
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            with os.fdopen(os.open(outpath, flags, 0o600), 'wb', buffering=0) as out, \
                    os.fdopen(os.open(errpath, flags, 0o600), 'wb', buffering=0) as err:
                os.fsync(out.fileno()); os.fsync(err.fileno()); d.fsync_dir(self.folder)
                self.journal.append('worker', 'INTENT', argv=argv, expected_START_commands=2)
                self.update('NATIVE_INTENT_DURABLE')
                need(not stop_requested, 'stop requested after intent; worker not spawned')
                self.possibly_accessed = True  # Set BEFORE Popen; exceptions after spawn cannot release the lock.
                self.child = subprocess.Popen(argv, cwd=self.folder, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                              pass_fds=(self.lock,),
                                              env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/root'})
                self.journal.append('worker', 'SPAWNED', pid=self.child.pid, argv=argv)
                started = last = time.monotonic()
                while self.child.poll() is None:
                    now = time.monotonic()
                    if now-last >= 5:
                        # No subprocess timeout, signal, terminate or kill path exists.
                        review_guardian(c, value['guardian_plan'], value, full=False)
                        self.update('RETAINED_NATIVE_STILL_RUNNING' if now-started > 300 else 'NATIVE_RUNNING',
                                    child_pid=self.child.pid, elapsed_seconds=now-started)
                        last = now
                    time.sleep(1)
                code, pid = self.child.returncode, self.child.pid
                os.fsync(out.fileno()); os.fsync(err.fileno())
            self.journal.append('worker', 'RETURNED', pid=pid, returncode=code,
                                stdout=identity(read(outpath, private=True)), stderr=identity(read(errpath, private=True)))
            self.child = None
            if code == 2:
                refusal = c.read_json(self.folder/'native_events/refusal.json', private=True)
                need(refusal.get('schema') == 'opt50_b24_layer02_native_refusal_v1' and
                     refusal.get('status') == 'REFUSED_OR_FAILED_NO_AUTOMATIC_RETRY' and
                     type(refusal.get('PL_START_commands_attempted')) is int and
                     refusal['PL_START_commands_attempted'] == 0 and refusal.get('device_closed') is True and
                     type(refusal.get('reason')) is str and read(outpath, private=True) == b'',
                     'worker failure is not a proved closed zero-START refusal')
                proof = native_events(c, self.folder, 0)
                terminal, result = ZERO_REFUSAL, refusal
            else:
                need(code == 0 and read(errpath, private=True) == b'', 'native worker failed or emitted diagnostics')
                result = validate_result(c.decode_json(read(outpath, private=True)), value)
                need(result == c.read_json(self.folder/'native_events/result.json', private=True),
                     'native result and stdout differ')
                proof = native_events(c, self.folder, 2)
                terminal = PASS
            self.update('VERIFYING_GUARDIAN_AFTER_DEVICE_CLOSED', child_pid=None)
            after = review_guardian(c, value['guardian_plan'], value)
            d.new_json(self.folder/'held_after.json', after)
            d.new_json(self.folder/'completion.json', {'result': result, 'native_completion': proof,
                        'worker_reaped': True, 'worker_returncode': code, 'device_closed': True})
            self.update(terminal, child_pid=None, result=result,
                        old_guardian_and_board_lock='PRESERVED_NOT_TRANSFERRED',
                        next='Review B24 layer02 proof before all-layer/model work; no load/recovery authority')
            return 0 if terminal == PASS else 1
        except BaseException as error:
            reason = type(error).__name__+': '+str(error)
            if self.possibly_accessed:
                self.hold(reason)
            self.update(REFUSED, reason=reason, child_pid=None)
            return 1


def request_stop(*_):
    global stop_requested
    stop_requested = True


def run(path, authorized):
    need(authorized, 'explicit layer02 observer/XRT/two-START permission required')
    c, durable, tools = helpers(private=True)
    c.root_linux()
    value = load_plan(c, path, tools, fresh=True)
    c.configure(value['guardian_plan'])
    with c.companion_locked() as lock:
        previous_clear(c, exclude=path)
        folder = Path(path).parent/'execution'
        durable.mkdir_new(folder); durable.mkdir_new(folder/'events')  # Single use, never replace old execution.
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, request_stop)
        return Coordinator(c, durable, value, lock).execute()


def status(path):
    c, _, tools = helpers(private=True)
    value = load_plan(c, path, tools, fresh=False)
    file = Path(path).parent/'execution/status.json'
    state = c.read_json(file, private=True) if file.exists() else {'stage': 'NO_SAVED_STATUS_YET_SEE_SYSTEMD_JOURNAL'}
    state.update(saved_status_only=True, plan_boot_id=value['boot_id'],
                 warning='Saved receipt only; not fresh hardware inspection or retry/recovery permission')
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    check_parser = sub.add_parser('check')
    check_parser.add_argument('--guardian-plan', type=Path, required=True)
    check_parser.add_argument('--metadata', type=Path)
    check_parser.add_argument('--uart-log-confirmed', action='store_true')
    for action in ('run', 'status'):
        item = sub.add_parser(action)
        item.add_argument('--plan', type=Path, required=True)
        if action == 'run':
            item.add_argument('--authorized-layer02-two-starts', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'run':
            return run(args.plan, args.authorized_layer02_two_starts)
        value = check(args.guardian_plan, args.metadata, args.uart_log_confirmed) if args.action == 'check' else status(args.plan)
        print(json.dumps(value, ensure_ascii=True, indent=2), flush=True)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'B24_LAYER02_ENTRY_REFUSED_NO_AUTOMATIC_RECOVERY',
                          'error': type(error).__name__+': '+str(error)}, indent=2), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
