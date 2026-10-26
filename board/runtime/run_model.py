"""One same-weight all-Dense OR mixed-Sparse sample on the new held guardian.

check creates new private files only. run may open the resident CU and issue at
most 36 ordered START commands. A timeout/signal/error never kills a child,
releases uncertain DMA resources, retries, reloads, resets or restores firmware.
The native owner is a DIRECT child: its frozen parent/lock gate stays enforced.
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
HERE = Path(__file__).resolve().parent
RECORDS = Path('/var/lib/opt50-fair-fd96539d-v1')
BOOT = 'f0f2eed8-5820-4df5-ba19-d05527720f97'
RESIDENT_SHA = 'aa0d886f0b2b07a1fb7cfbef5a2e2f3f8b5837646dbd74e7a69b66049b030a1c'
PYTHON = '/home/ubuntu/fpga_project/model_b/.venv/bin/python'
WEIGHTS = '88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6'
REFERENCE = '50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49'
MANIFEST = 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb'
DATA = '4dee6bf0ea5a3b9cffca9254ba206c31d30553079d448c7c028373fb7ffaaf94'
META = '93e24c85920cc50453e19c829127d7c9afed9b5de20e2961c207172cb6f7870b'
METADATA = '715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551'
PASS = 'PASS_FAIR_ARM_36_STARTS_SYNTHETIC_BITWISE_SINGLE_SAMPLE'
REFUSED = 'REFUSED_BEFORE_OWNER_NO_MMIO'
SCOPE = 'ONE_ZERO_INPUT_GRAPH_36_STARTS_WITH_HELD_GUARDIAN'
PROFILE = 'same-weights-fair-single-shot-v1'
PL_LAYERS = [i for i in range(48) if i % 4 != 1]
SPARSE = {26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47}
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
STOP_FLAG = '--authorized-one-model-36-starts'
stop_requested = False


def need(ok, message):
    if not ok:
        raise ValueError(message)


def api():
    path = HERE/'resident.py'
    need(not any(p.is_symlink() for p in (path, *path.parents)), 'linked resident helper')
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == RESIDENT_SHA, 'frozen resident helper changed')
    module = types.ModuleType('_opt50_fair_resident')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module

def relative(name):
    need(type(name) is str, 'nonstring release path')
    path = PurePosixPath(name)
    need(str(path) == name and not path.is_absolute() and
         all(v not in ('', '.', '..') for v in name.split('/')) and
         not any(v in name for v in '\\:\0\r\n'), 'unsafe release path')
    return path


def verify_tools(c, folder=None):
    folder = HERE if folder is None else Path(folder)
    raw = c.read_file(folder / 'tools_manifest.json')
    value = c.decode_json(raw)
    required = {'run_model.py', 'model_worker.py', 'model_inputs.py', 'model_runtime.py', 'pins.json',
                'expected_source_release.json', 'expected_arm_build_receipt.json', 'support/companion.py',
                'support/durable.py', 'resident.py', 'compare.py'}
    need(value.get('schema') == 'opt50_fair_model_tools_v1' and value.get('hardware_executed') is False and
         type(value.get('files')) is dict and required <= set(value['files']) and
         len(value['files']) <= 256 and value.get('measurement_profile') == PROFILE and
         {'perf_proof.py', 'expected_performance_release.json'} <= set(value['files']),
         'unreviewed performance-window inventory/profile')
    for name, pin in value['files'].items():
        path = relative(name)
        need(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and type(pin['bytes']) is int and
             pin['bytes'] >= 0 and type(pin['sha256']) is str and re.fullmatch('[0-9a-f]{64}', pin['sha256']),
             'invalid release identity')
        need(c.identity(c.read_file(folder.joinpath(*path.parts))) == pin, 'window tool changed: ' + name)
    need(value['files']['resident.py']['sha256'] == RESIDENT_SHA, 'resident identity differs')
    return {'manifest': c.identity(raw), 'files': value['files']}


def inputs_api(c, tools, folder=None):
    folder = HERE if folder is None else Path(folder)
    return c.compiled_module(folder / 'model_inputs.py', tools['files']['model_inputs.py']['sha256'])


def proof_api(c):
    tools = verify_tools(c)
    return c.compiled_module(HERE/'perf_proof.py', tools['files']['perf_proof.py']['sha256'])


def interpreter(c, expected=None):
    # The established venv entry is a symlink; pin its root-owned real ELF too.
    entry = Path(PYTHON)
    c.no_links(entry.parent)
    target = entry.resolve(strict=True)
    c.no_links(target)
    info = target.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
         'trusted resolved model Python required')
    value = {'entry': str(entry), 'target': str(target), 'identity': c.identity(c.read_file(target))}
    need(expected is None or expected == value, 'model Python entry/target changed')
    return value


def system_properties(c, plan, unit, fields):
    # Only this bounded read-only systemctl query uses subprocess.run.
    result = subprocess.run([plan['command_tools']['systemctl']['entry'], 'show', unit,
                             '--property=' + ','.join(fields)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=20, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 32768 and len(result.stderr) <= 8192,
         'service observation failed')
    rows = [v.split('=', 1) for v in result.stdout.splitlines() if '=' in v]
    need(len({k for k, _ in rows}) == len(rows), 'duplicate service property')
    return dict(rows)


def validate_parent(c, plan, pid):
    fields = (*c.SERVICE, 'MainPID', 'ActiveState', 'SubState')
    actual = system_properties(c, plan, plan['unit'] + '.service', fields)
    need(all(actual.get(k) == v for k, v in c.SERVICE.items()) and actual.get('MainPID') == str(pid) and
         actual.get('ActiveState') == 'active' and actual.get('SubState') in ('start', 'running'),
         'model coordinator service guard differs')
    root = Path(plan['plan_path']).parent
    argv = ['/usr/bin/python3', '-B', str(root/'tools/run_model.py'), 'run', '--plan', plan['plan_path'], STOP_FLAG]
    need(Path('/proc/%d/cmdline' % pid).read_bytes() == b''.join(s.encode()+b'\0' for s in argv),
         'coordinator exact seven-argument identity differs')
    need(Path('/proc/%d/cgroup' % pid).read_text() == '0::/system.slice/' + plan['unit'] + '.service\n',
         'coordinator cgroup differs')
    need(Path('/proc/%d/exe' % pid).resolve(strict=True) == Path('/usr/bin/python3').resolve(strict=True),
         'coordinator interpreter differs')
    return actual


def validate_lock(c, plan, fd):
    validate_parent(c, plan, os.getpid())
    need(type(fd) is int and 3 <= fd <= 4096, 'explicit inherited new companion FD required')
    actual, named = os.fstat(fd), c._lock_info()
    need((actual.st_dev, actual.st_ino) == (named.st_dev, named.st_ino) and
         os.readlink('/proc/self/fd/%d' % fd) == str(c.LOCK), 'new companion lock inode differs')
    pattern = re.compile(r'^\s*\d+:\s+FLOCK\s+ADVISORY\s+WRITE\s+(\d+)\s+([a-fA-F0-9]+):([a-fA-F0-9]+):(\d+)\s+0\s+EOF\s*$')
    matches = [m for line in Path('/proc/locks').read_text().splitlines()
               if (m := pattern.fullmatch(line)) and
               tuple(int(m[i], 16 if i in (2, 3) else 10) for i in range(1, 5)) ==
               (os.getpid(), os.major(actual.st_dev), os.minor(actual.st_dev), actual.st_ino)]
    need(len(matches) == 1, 'coordinator must own the new flock; old guardian is not delegation')


def guardian_monitor(c, plan):
    # Our own XRT FDs are legitimate while the worker runs.
    return c.monitor_guardian(plan)

def validate_plan(plan, path):
    root = path.parent
    token = root.name[5:]
    need(re.fullmatch('[0-9a-f]{12}', token) and plan.get('schema') == 'opt50_fair_model_plan_v1' and
         plan.get('token') == token and plan.get('unit') == 'opt50-fair-'+token and
         plan.get('plan_path') == str(path) and plan.get('boot_id') == BOOT and plan.get('scope') == SCOPE and
         plan.get('expires_seconds') == 900 and plan.get('expected_PL_starts') == 36 and
         plan.get('run_id') == 1 and type(plan.get('guardian_pid')) is int and plan['guardian_pid'] > 1 and
         type(plan.get('guardian_start_ticks')) is int and plan['guardian_start_ticks'] > 0 and plan.get('old_guardian_delegation') is False and
         plan.get('uart_logging_operator_confirmed') is True and plan.get('python') == PYTHON and
         plan.get('execution') == str(root/'execution') and plan.get('socket') == str(root/'execution/runtime.sock') and
         plan.get('model_runtime') == str(root/'tools/model_runtime.py') and
         plan.get('measurement_profile') == PROFILE and plan.get('execution_mode') in ('all_dense', 'mixed') and
         plan.get('load_guardian_plan') == str(api().OLD_PLAN), 'fair model plan identity/scope differs')
    need(type(plan.get('created_monotonic')) in (int, float) and
         math.isfinite(plan['created_monotonic']) and plan['created_monotonic'] >= 0, 'invalid plan creation time')
    for key in ('model_root', 'plugin', 'input'):
        need(plan.get(key) == plan['inputs'][key], 'plan input aliases differ')
    need(plan.get('input_pin') == plan['inputs']['files']['input_zero.npy'] and
         plan.get('plugin_pin') == plan['inputs']['files']['plugin.so'] and
         plan.get('model_manifest_pin') == plan['inputs']['files']['model/manifest.json'] and
         plan.get('reference_sha256') == REFERENCE and plan.get('logical_weights_sha256') == WEIGHTS,
         'plan model identity aliases differ')


def load_plan(c, path, fresh):
    path = Path(path).absolute()
    need(path.name == 'plan.json' and path.parent.parent == RECORDS and
         re.fullmatch('plan_[0-9a-f]{12}', path.parent.name), 'unexpected model plan path')
    for folder in (RECORDS, path.parent, path.parent/'tools'):
        c.private_dir(folder)
    plan = c.read_json(path, private=True)
    validate_plan(plan, path)
    need(verify_tools(c, path.parent/'tools') == plan['tool_files'], 'root copied tools changed')
    for name in (*plan['tool_files']['files'], 'tools_manifest.json'):
        c.read_file(path.parent/'tools'/name, private=True)
    helper = inputs_api(c, plan['tool_files'], path.parent/'tools')
    helper.validate_copied(c, plan)
    old = c.read_json(c.OLD_PLAN, private=True)
    need(plan['command_tools'] == old['command_tools'], 'trusted OS command identities differ')
    interpreter(c, plan['interpreter'])
    if fresh:
        c.root_linux()
        need(HERE == path.parent/'tools', 'execute only root-owned copied tools')
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT, 'boot changed')
        need(0 <= time.monotonic()-plan['created_monotonic'] <= 900, 'model plan expired; never reuse')
    return plan


def job_bytes(c, plan, parent_pid):
    root = Path(plan['plan_path']).parent
    fields = {'schema': 'opt50_fair_native_job_v1', 'boot_id': BOOT, 'plan_token': plan['token'],
              'expected_parent_pid': str(parent_pid), 'companion_lock': str(c.LOCK),
              'model_root': str(root/'model'), 'weights': str(root/'model/weights.bin'), 'weights_sha256': WEIGHTS,
              'data': str(root/'model/pl_w_data.bin'), 'data_sha256': DATA,
              'meta': str(root/'model/pl_w_meta.bin'), 'meta_sha256': META,
              'reference': str(root/'model/reference.onnx'), 'reference_sha256': REFERENCE,
              'metadata': str(root/'metadata.xclbin'), 'metadata_sha256': METADATA,
              'run_id': '1', 'expected_PL_starts': '36', 'execution_mode': plan['execution_mode']}
    need(type(parent_pid) is int and parent_pid > 1 and len(fields) == 19 and
         all(not any(ch in value for ch in '\r\n=\0') for value in fields.values()), 'invalid frozen native job')
    return ''.join(key+'='+value+'\n' for key, value in fields.items()).encode()


def new_bytes(c, durable, path, raw):
    c.private_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'wb') as stream:
        need(stream.write(raw) == len(raw), 'short exclusive write')
        stream.flush(); os.fsync(stream.fileno())
    durable.fsync_dir(path.parent)
    need(c.read_file(path, private=True) == raw, 'exclusive output readback differs')


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


def native_completion(c, execution, closed=False):
    execution = Path(execution)
    plan = load_plan(c, execution.parent/'plan.json', fresh=False)
    return proof_api(c).native_completion(c, execution, read_events, closed=closed,
                                          execution_mode=plan['execution_mode'])

def model_identity(value, plan):
    need(value.get('execution_mode') == plan['execution_mode'] and
         value.get('boot_id') == BOOT and value.get('plan_token') == plan['token'] and
         value.get('input_sha256') == plan['input_pin']['sha256'] and value.get('reference_sha256') == REFERENCE and
         value.get('model_manifest_sha256') == MANIFEST and value.get('logical_weights_sha256') == WEIGHTS,
         'worker model/input/boot identity differs')


def validate_cpu(c, plan, execution):
    result = c.read_json(execution/'cpu_reference.json', private=True)
    model_identity(result, plan)
    keys = {'schema', 'status', 'boot_id', 'plan_token', 'execution_mode', 'input_sha256', 'reference_sha256',
            'model_manifest_sha256', 'logical_weights_sha256', 'numpy_version', 'ort_version', 'shape', 'dtype',
            'logits_file', 'logits_raw_sha256', 'CPU_session_closed', 'PL_START_commands_issued_by_worker',
            'hardware_accessed', 'scope', 'measurement_profile', 'reference_setup_ns', 'reference_setup_ms',
            'reference_ns', 'reference_ms', 'timing_scope', 'single_sample_only', 'performance_verified'}
    need(set(result) == keys and result.get('schema') == 'opt50_model_cpu_reference_v1' and result.get('status') == 'CPU_REFERENCE_READY_NO_PL_ACCESS' and
         result.get('numpy_version') == '1.26.4' and result.get('ort_version') == '1.22.1' and
         result.get('shape') == [1, 1000] and all(type(n) is int for n in result['shape']) and result.get('dtype') == 'float32' and
         result.get('CPU_session_closed') is True and type(result.get('PL_START_commands_issued_by_worker')) is int and
         result.get('PL_START_commands_issued_by_worker') == 0 and
         result.get('hardware_accessed') is False and
         result.get('scope') == 'Synthetic zero input CPU reference only; not accuracy or performance' and
         type(result.get('logits_raw_sha256')) is str and re.fullmatch('[0-9a-f]{64}', result['logits_raw_sha256']) and
         result.get('logits_file') == c.identity(c.read_file(execution/'cpu_reference.npy', private=True)),
         'closed CPU reference evidence differs')
    validate_timing(result, ('reference_setup', 'reference'))
    need(result['performance_verified'] is False, 'one CPU sample is not a stable benchmark')
    return result


def validate_timing(result, prefixes):
    need(result.get('measurement_profile') == PROFILE and result.get('single_sample_only') is True and
         type(result.get('timing_scope')) is str and bool(result['timing_scope']), 'single-shot timing scope missing')
    for name in prefixes:
        need(type(result.get(name+'_ns')) is int and result[name+'_ns'] >= 0 and
             type(result.get(name+'_ms')) in (int, float) and
             result[name+'_ms'] == result[name+'_ns']/1_000_000,
             'recorded monotonic duration differs: '+name)


def validate_worker(c, plan, execution):
    result = c.read_json(execution/'worker_result.json', private=True)
    model_identity(result, plan)
    reference = validate_cpu(c, plan, execution)
    keys = {'schema', 'status', 'boot_id', 'plan_token', 'execution_mode', 'run_id', 'input_sha256', 'reference_sha256',
            'model_manifest_sha256', 'logical_weights_sha256', 'custom_graph_sha256', 'cpu_reference_record_sha256',
            'logits_file', 'logits_raw_sha256', 'compared_logits', 'bitwise_mismatches', 'max_abs_difference',
            'Runner_closed', 'runner_calls', 'expected_PL_starts', 'actual_PL_starts', 'accuracy_verified',
            'performance_verified', 'scope', 'measurement_profile', 'single_sample_only',
            'setup_ns', 'setup_ms', 'runner_run_ns', 'runner_run_ms', 'plugin_timing', 'timing_scope'}
    need(set(result) == keys and result.get('schema') == 'opt50_model_worker_result_v1' and
         result.get('status') == 'MODEL_LOGITS_MATCH_BITWISE' and type(result.get('run_id')) is int and result['run_id'] == 1 and
         result.get('Runner_closed') is True and type(result.get('runner_calls')) is int and result['runner_calls'] == 1 and
         type(result.get('compared_logits')) is int and result['compared_logits'] == 1000 and type(result.get('bitwise_mismatches')) is int and
         result['bitwise_mismatches'] == 0 and type(result.get('max_abs_difference')) in (int, float) and
         result['max_abs_difference'] == 0 and type(result.get('expected_PL_starts')) is int and result['expected_PL_starts'] == 36 and
         result.get('actual_PL_starts') == 'PENDING_NATIVE_END_AND_OWNER_EXIT_PROOF' and
         result.get('accuracy_verified') is False and result.get('performance_verified') is False and
         result.get('scope') == 'Synthetic zero input numerical comparison only; not accuracy or performance' and
         result.get('cpu_reference_record_sha256') == c.identity(c.read_file(execution/'cpu_reference.json', private=True))['sha256'] and
         result.get('logits_file') == c.identity(c.read_file(execution/'model_logits.npy', private=True)) and
         result.get('logits_raw_sha256') == reference['logits_raw_sha256'], 'single closed Runner/logit comparison differs')
    manifest = c.read_json(Path(plan['inputs']['model_root'])/'manifest.json', private=True)
    need(result.get('custom_graph_sha256') == manifest['custom_graph_sha256'], 'worker custom graph identity differs')
    validate_timing(result, ('setup', 'runner_run'))
    need(type(result.get('plugin_timing')) is dict and
         result['plugin_timing'].get('schema') == 'opt50_perf_plugin_timing_v1', 'plugin timing snapshot differs')
    return result


def receive_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.recv(size-len(result))
        need(bool(part), 'truncated authenticated owner reply')
        result.extend(part)
    return bytes(result)


class Admin:
    """Only HELLO/INFO/STOP wire50; never BEGIN/RUN/ABORT/MAP or CSR."""
    def __init__(self, path, owner_pid, execution_mode):
        need(execution_mode in ('all_dense', 'mixed'), 'explicit admin execution mode required')
        self.execution_mode = execution_mode
        self.stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.stream.settimeout(5)
            self.stream.connect(str(path))
            credentials = self.stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            pid, uid, _ = struct.unpack('3i', credentials)
            need(pid == owner_pid and uid == 0, 'admin socket belongs to another process')
            self.call(1, bytes.fromhex(WEIGHTS))
        except BaseException:
            self.stream.close(); raise

    def call(self, operation, body=b''):
        need(operation in (1, 7, 8), 'management operation forbidden')
        self.stream.sendall(struct.pack('<IHHQIIII', 0x3035504f, 50, operation, 0, 0, 0, len(body), 0)+body)
        magic, version, op, run, sequence, layer, size, status = struct.unpack('<IHHQIIII', receive_exact(self.stream, 32))
        need((magic, version, op, run, sequence, layer, status) == (0x3035504f, 50, operation, 0, 0, 0, 0) and
             size == (3200 if operation == 8 else 0), 'invalid wire50 management reply')
        return receive_exact(self.stream, size)

    def metrics(self):
        values = struct.unpack('<400Q', self.call(8))
        need(values[7] == 36 and [values[8+8*i+7] for i in PL_LAYERS] == list(range(1, 37)) and
             all(values[8+8*i+7] == 0 for i in range(49) if i not in PL_LAYERS), 'owner diagnostics not exactly 36 routed calls')
        return {'completed_PL_calls': 36, 'wire_version': 50, 'layer_call_sequence': list(PL_LAYERS),
                'owner_Run_dispatch_ms': values[0]/1_000_000,
                'layers': [{'layer_index': layer, 'mode': int(self.execution_mode == 'mixed' and layer in SPARSE),
                            'owner_Run_dispatch_ms': values[8+8*layer]/1_000_000}
                           for layer in PL_LAYERS],
                'scope': 'Owner dispatch includes ordered register programming/completion checks; client cache sync remains outside this interval; overlaps Runner.run and is not pure PL latency'}

    def stop(self):
        self.call(7)

    def close(self):
        self.stream.close()


def terminal_service(c, plan, success):
    fields = ('MainPID', 'ActiveState', 'SubState', 'ExecMainStatus', 'Result')
    actual = system_properties(c, plan, plan['unit']+'.service', fields)
    need(actual.get('MainPID') == '0' and actual.get('SubState') in ('exited', 'dead', 'failed') and
         (not success or (actual.get('ExecMainStatus') == '0' and actual.get('Result') == 'success')),
         'prior coordinator has not actually exited; child_pid=null is insufficient')
    return actual


def previous_models_clear(c, exclude=None):
    reports = []
    paths = sorted(RECORDS.glob('plan_*'))
    need(len(paths) <= 1000, 'too many prior model plans')
    for folder in paths:
        need(re.fullmatch('plan_[0-9a-f]{12}', folder.name), 'unrecognized prior model directory')
        c.private_dir(folder)
        if exclude is not None and folder == Path(exclude).parent:
            continue
        execution = folder/'execution'
        if not execution.exists():
            need(not execution.is_symlink(), 'linked prior execution')
            continue
        c.private_dir(execution)
        plan = load_plan(c, folder/'plan.json', fresh=False)
        state = c.read_json(execution/'status.json', private=True)
        need(state.get('schema') == 'opt50_fair_model_status_v1' and state.get('boot_id') == BOOT and
             state.get('unit') == plan['unit'] and state.get('stage') in (PASS, REFUSED),
             'prior model execution unresolved; preserve it for review')
        events = sorted((execution/'events').glob('event_*.json'))
        values = []
        for index, path in enumerate(events, 1):
            item = c.read_json(path, private=True)
            need(path.name == 'event_%06d.json' % index and item.get('schema') == 'opt50_durable_event_v1' and
                 item.get('sequence') == index, 'prior parent journal gap')
            values.append(item)
        need(values and values[-1].get('action') == 'state' and values[-1].get('state') == state,
             'prior final journal/status differ')
        if state['stage'] == REFUSED:
            need(not any(v.get('action') == 'owner' for v in values) and
                 state.get('owner_pid') is None and state.get('child_pid') is None and
                 not (execution/'native_events').exists(), 'prior refusal may have started Native')
        else:
            report = c.read_json(execution/'completion.json', private=True)
            need(report.get('native') == native_completion(c, execution, closed=True) and
                 report.get('worker') == validate_worker(c, plan, execution) and
                 report.get('owner_exit_code') == 0 and report.get('owner_metrics', {}).get('completed_PL_calls') == 36 and
                 state.get('completion') == report, 'prior model completion evidence differs')
            owner_stdout = c.read_file(execution/'owner.stdout', private=True).decode()
            need(owner_stdout == 'KV5_READY %d\nKV5_STOP calls=36\n' % report['owner_pid'] and
                 c.read_file(execution/'owner.stderr', private=True) == b'', 'prior owner close log differs')
            need(report.get('held_after', {}).get('guardian', {}).get('start_ticks') == plan['guardian_start_ticks'] and
                 report['held_after'].get('status') == 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START', 'prior guardian after-proof missing')
        service = terminal_service(c, plan, state['stage'] == PASS)
        reports.append({'plan': str(folder/'plan.json'), 'stage': state['stage'], 'service': service})
    return reports


def copy_tools(c, durable, folder, tools):
    durable.mkdir_new(folder/'tools')
    for name, pin in sorted(tools['files'].items(), key=lambda v: (v[0].count('/'), v[0])):
        target = folder/'tools'/relative(name)
        for parent in reversed(target.parents):
            if parent.is_relative_to(folder/'tools') and not parent.exists():
                durable.mkdir_new(parent)
        durable.copy_new(HERE/name, target, pin)
    durable.copy_new(HERE/'tools_manifest.json', folder/'tools/tools_manifest.json', tools['manifest'])


def check(uart_confirmed, execution_mode):
    need(execution_mode in ('all_dense', 'mixed'), 'choose exactly all_dense or mixed')
    c = api(); c.root_linux()
    need(uart_confirmed, 'independent UART logging confirmation required')
    tools = verify_tools(c)
    helper = inputs_api(c, tools)
    summary = helper.validate_source(c, tools)
    python = interpreter(c)
    durable = c.durable_api()
    if not RECORDS.exists():
        durable.mkdir_new(RECORDS)
    c.private_dir(RECORDS)
    with c.companion_locked():
        previous_models_clear(c)
        held = c.held_review()
        old = c.read_json(c.OLD_PLAN, private=True)
        token = secrets.token_hex(6)
        root = RECORDS/('plan_'+token)
        durable.mkdir_new(root)
        copy_tools(c, durable, root, tools)
        copied = helper.copy_inputs(c, durable, summary, root)
        plan = {'schema': 'opt50_fair_model_plan_v1', 'token': token, 'unit': 'opt50-fair-'+token,
                'boot_id': BOOT, 'plan_path': str(root/'plan.json'), 'scope': SCOPE,
                'created_monotonic': time.monotonic(), 'expires_seconds': 900,
                'uart_logging_operator_confirmed': True, 'guardian_pid': held['guardian']['pid'],
                'guardian_start_ticks': held['guardian']['start_ticks'], 'load_guardian_plan': str(c.OLD_PLAN),
                'old_guardian_delegation': False, 'expected_PL_starts': 36, 'run_id': 1,
                'tool_files': tools, 'inputs': copied, 'source_summary': summary,
                'held_review': held, 'command_tools': old['command_tools'], 'python': PYTHON, 'interpreter': python,
                'execution': str(root/'execution'), 'socket': str(root/'execution/runtime.sock'),
                'model_runtime': str(root/'tools/model_runtime.py'),
                'model_root': copied['model_root'], 'plugin': copied['plugin'], 'input': copied['input'],
                'input_pin': copied['files']['input_zero.npy'], 'plugin_pin': copied['files']['plugin.so'],
                'model_manifest_pin': copied['files']['model/manifest.json'],
                'reference_sha256': REFERENCE, 'logical_weights_sha256': WEIGHTS,
                'measurement_profile': PROFILE, 'execution_mode': execution_mode}
        validate_plan(plan, root/'plan.json')
        durable.new_json(root/'plan.json', plan)
    properties = ('Type=exec', 'ExitType=cgroup', 'KillMode=process', 'SendSIGKILL=no', 'TimeoutStopSec=infinity',
                  'RuntimeMaxSec=infinity', 'Restart=no', 'RemainAfterExit=yes', 'OOMPolicy=continue')
    command = ['sudo', 'systemd-run', '--unit='+plan['unit'], *['--property='+v for v in properties],
               '/usr/bin/python3', '-B', str(root/'tools/run_model.py'), 'run', '--plan', plan['plan_path'], STOP_FLAG]
    return {'status': 'MODEL_PLAN_CREATED_NO_HARDWARE_CHANGE', 'plan': plan['plan_path'], 'boot_id': BOOT,
            'expires_seconds': 900, 'expected_PL_starts': 36,
            'scope': 'Exactly one low-I/O Runner.run latency sample, same zero input/ORT threads; not steady-state benchmark',
            'measurement_profile': PROFILE, 'execution_mode': execution_mode,
            'expected_Dense_calls': 36 if execution_mode == 'all_dense' else 24,
            'expected_Sparse_calls': 0 if execution_mode == 'all_dense' else 12,
            'writes': 'New root-private plan, copied tools/model/binaries/input only; no owner or device access',
            'next_requires_separate_hardware_permission': shlex.join(command),
            'status_command': shlex.join(['sudo', '/usr/bin/python3', '-B', str(root/'tools/run_model.py'),
                                          'status', '--plan', plan['plan_path']]),
            'native_owner_executed': False, 'hardware_executed': False}


class Coordinator:
    def __init__(self, c, plan, lock, durable):
        self.c, self.plan, self.lock, self.durable = c, plan, lock, durable
        self.root = Path(plan['plan_path']).parent
        self.folder = self.root/'execution'
        self.journal = durable.Journal(self.folder/'events')
        self.owner = self.worker = None
        self.possibly_accessed = False
        self.logs = []
        self.state = {'schema': 'opt50_fair_model_status_v1', 'boot_id': BOOT, 'unit': plan['unit'],
                      'coordinator_pid': os.getpid(), 'guardian_pid': plan['guardian_pid'], 'execution_mode': plan['execution_mode'], 'old_board_lock_transferred': False,
                      'physical_PL_starts': 'NOT_INDEPENDENTLY_MEASURED', 'scope': SCOPE,
                      'owner_pid': None, 'child_pid': None, 'saved_status_only': True}

    def update(self, stage, **details):
        self.state.update(stage=stage, heartbeat_monotonic=time.monotonic(), **details)
        self.journal.append('state', stage, state=self.state.copy())
        self.durable.replace_json(self.folder/'status.json', self.state)

    def hold(self, reason):
        while True:
            try:
                self.update('RETAINED_MODEL_STATE_UNCERTAIN', reason=reason,
                            owner_pid=None if self.owner is None else self.owner.pid,
                            child_pid=None if self.worker is None else self.worker.pid,
                            warning='No retry/kill/reset/unload/reboot; keep both guardian and new companion lock')
            except BaseException:
                try:
                    os.write(2, b'RETAINED OPT50 MODEL: preserve every process/resource/lock; no automatic recovery\n')
                except BaseException:
                    pass
            try:
                time.sleep(30)
            except BaseException:
                pass

    def log_pair(self, name):
        handles = []
        for suffix in ('stdout', 'stderr'):
            path = self.folder/(name+'.'+suffix)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            stream = os.fdopen(os.open(path, flags, 0o600), 'wb', buffering=0)
            self.logs.append(stream); handles.append(stream); os.fsync(stream.fileno())
        self.durable.fsync_dir(self.folder)
        return handles

    def tick(self, stage):
        guardian = guardian_monitor(self.c, self.plan)
        for stream in self.logs:
            os.fsync(stream.fileno())
        self.update(stage, owner_pid=None if self.owner is None else self.owner.pid,
                    child_pid=None if self.worker is None else self.worker.pid, guardian_observation=guardian)

    def wait_child(self, process, budget, stage, *, watch_owner):
        started, last = time.monotonic(), -float('inf')
        while process.poll() is None:
            now = time.monotonic()
            need(not stop_requested, 'stop requested; do not interrupt/kill a live child')
            need(now-started <= budget, 'observation budget exceeded; child is not killed or retried')
            if watch_owner:
                need(self.owner is not None and self.owner.poll() is None, 'native owner exited during inference')
            if now-last >= 5 and not watch_owner:
                self.tick(stage); last = now
            time.sleep(0.2)
        process.wait()  # poll proved exit; reap only, never a kill/timeout.
        for stream in self.logs:
            os.fsync(stream.fileno())
        return process.returncode

    def worker_call(self, action):
        interpreter(self.c, self.plan['interpreter'])
        argv = [self.plan['python'], '-B', str(self.root/'tools/model_worker.py'), '--action', action,
                '--plan', self.plan['plan_path']]
        if action == 'run':
            argv.append(STOP_FLAG)
        name = 'cpu_worker' if action == 'prepare-cpu' else 'model_worker'
        out, err = self.log_pair(name)
        self.journal.append(name, 'INTENT', argv=argv)
        need(not stop_requested, 'stop requested before child spawn')
        self.worker = subprocess.Popen(argv, cwd=self.folder, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/root'})
        pid = self.worker.pid
        self.journal.append(name, 'SPAWNED', pid=pid, argv=argv)
        code = self.wait_child(self.worker, 300, 'CPU_REFERENCE_RUNNING' if action == 'prepare-cpu' else 'MODEL_WORKER_RUNNING',
                               watch_owner=action == 'run')
        self.journal.append(name, 'RETURNED', pid=pid, returncode=code,
                            stdout=self.c.identity(self.c.read_file(self.folder/(name+'.stdout'), private=True)),
                            stderr=self.c.identity(self.c.read_file(self.folder/(name+'.stderr'), private=True)))
        self.worker = None
        need(code == 0, 'model worker failed; no retry or automatic STOP')
        diagnostics = self.c.read_file(self.folder/(name+'.stderr'), private=True)
        worker_module = self.c.compiled_module(self.root/'tools/model_worker.py',
                        self.plan['tool_files']['files']['model_worker.py']['sha256'])
        worker_module.validate_warnings(diagnostics)
        value = validate_cpu(self.c, self.plan, self.folder) if action == 'prepare-cpu' else validate_worker(self.c, self.plan, self.folder)
        need(self.c.decode_json(self.c.read_file(self.folder/(name+'.stdout'), private=True)) == value,
             'worker stdout and saved result differ')
        return value

    def ready(self):
        started, last = time.monotonic(), -float('inf')
        while True:
            need(self.owner.poll() is None, 'owner exited before READY')
            need(not stop_requested and time.monotonic()-started <= 180, 'owner READY observation interrupted/overdue')
            raw = self.c.read_file(self.folder/'owner.stdout', private=True)
            need(len(raw) <= 4096, 'unexpected owner startup output')
            if raw == ('KV5_READY %d\n' % self.owner.pid).encode():
                for name in ('runtime.sock', 'runtime.sock.ctl'):
                    info = (self.folder/name).lstat()
                    need(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0, 'root owner socket identity differs')
                return
            if time.monotonic()-last >= 5:
                self.tick('OWNER_INITIALIZING'); last = time.monotonic()
            time.sleep(0.2)

    def execute(self):
        try:
            self.update('PREPARING_CPU_REFERENCE_NO_OWNER_OR_MMIO')
            new_bytes(self.c, self.durable, self.root/'job.kv', job_bytes(self.c, self.plan, os.getpid()))
            cpu = self.worker_call('prepare-cpu')
            self.durable.new_json(self.folder/'held_before.json', self.c.held_review())
            validate_lock(self.c, self.plan, self.lock)
            need(not stop_requested and 0 <= time.monotonic()-self.plan['created_monotonic'] <= 900,
                 'plan expired/interrupted before Native; no new plan reuse')
            inputs_api(self.c, self.plan['tool_files']).validate_copied(self.c, self.plan)
            argv = [self.plan['inputs']['owner'], str(self.root/'model/weights.bin'), self.plan['socket'],
                    self.plan['inputs']['metadata'], str(self.folder), str(self.root/'job.kv'), str(self.lock), STOP_FLAG]
            out, err = self.log_pair('owner')
            self.update('OWNER_INTENT_DURABLE')
            self.journal.append('owner', 'INTENT', argv=argv, expected_START_commands=36)
            # Set before Popen: even spawn exceptions retain this exclusive attempt.
            self.possibly_accessed = True
            self.owner = subprocess.Popen(argv, cwd=self.folder, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                          pass_fds=(self.lock,), env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                                                    'LANG': 'C.UTF-8', 'HOME': '/root'})
            owner_pid = self.owner.pid
            self.journal.append('owner', 'SPAWNED', pid=owner_pid, argv=argv)
            self.ready()
            result = self.worker_call('run')
            need(self.owner.poll() is None, 'owner exited before authenticated management')
            admin = Admin(self.folder/'runtime.sock.ctl', owner_pid, self.plan['execution_mode'])
            try:
                # INFO performs fresh postrun buffer checks, then publishes ONE
                # compact RAM receipt. Both happen after timed Runner.run.
                metrics = admin.metrics()
                self.durable.new_json(self.folder/'owner_metrics.json', metrics)
                proof_before_stop = native_completion(self.c, self.folder, closed=False)
                self.durable.new_json(self.folder/'before_stop_native.json', proof_before_stop)
                self.journal.append('management', 'STOP_INTENT', owner_pid=owner_pid,
                                    native_END_completed=True, postrun_buffer_checks_verified=True,
                                    Runner_closed=True, metrics=metrics)
                admin.stop()
                self.journal.append('management', 'STOP_RETURNED', owner_pid=owner_pid)
            finally:
                admin.close()
            code = self.wait_child(self.owner, 120, 'OWNER_SAFE_CLOSING', watch_owner=False)
            self.journal.append('owner', 'RETURNED', pid=owner_pid, returncode=code)
            need(code == 0, 'owner did not exit zero after STOP')
            need(self.c.read_file(self.folder/'owner.stdout', private=True) ==
                 ('KV5_READY %d\nKV5_STOP calls=36\n' % owner_pid).encode() and
                 self.c.read_file(self.folder/'owner.stderr', private=True) == b'', 'owner exit/call proof differs')
            final_native = native_completion(self.c, self.folder, closed=True)
            self.owner = None
            held_after = self.c.held_review()
            self.durable.new_json(self.folder/'held_after.json', held_after)
            inputs_api(self.c, self.plan['tool_files']).validate_copied(self.c, self.plan)
            report = {'schema': 'opt50_fair_model_completion_v1', 'boot_id': BOOT, 'plan_token': self.plan['token'],
                      'native': final_native, 'worker': result, 'CPU_reference': cpu, 'owner_metrics': metrics,
                      'owner_pid': owner_pid, 'owner_exit_code': code, 'held_after': held_after,
                      'accuracy_verified': False, 'performance_verified': False,
                      'scope': 'One H576-aligned low-overhead sample; postrun buffer checks outside timer; 1000 bitwise-equal logits; not accuracy or stable benchmark',
                      'measurement_profile': PROFILE, 'execution_mode': self.plan['execution_mode'], 'single_sample_only': True,
                      'latency_ms': result['runner_run_ms'], 'CPU_reference_ms': cpu['reference_ms'],
                      'Runner_setup_ms': result['setup_ms'], 'CPU_reference_setup_ms': cpu['reference_setup_ms'],
                      'latency_scope': result['timing_scope'], 'plugin_timing': result['plugin_timing']}
            self.durable.new_json(self.folder/'completion.json', report)
            self.update(PASS, owner_pid=None, child_pid=None, completion=report)
            for stream in self.logs:
                stream.close()
            return 0
        except BaseException as error:
            reason = type(error).__name__+': '+str(error)
            if self.possibly_accessed or self.worker is not None:
                self.hold(reason)
            self.update(REFUSED, owner_pid=None, child_pid=None, reason=reason, hardware_executed=False)
            for stream in self.logs:
                stream.close()
            return 1


def request_stop(*_):
    global stop_requested
    stop_requested = True  # No asynchronous unwind, signal forwarding or DMA cancellation.


def run(path, authorized):
    need(authorized, 'separate explicit one-model 36-START permission required')
    c = api(); c.root_linux()
    plan = load_plan(c, path, fresh=True)
    validate_parent(c, plan, os.getpid())
    with c.companion_locked() as fd:
        previous_models_clear(c, exclude=path)
        c.held_review()
        validate_lock(c, plan, fd)
        durable = c.durable_api()
        folder = Path(path).parent/'execution'
        durable.mkdir_new(folder); durable.mkdir_new(folder/'events')
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, request_stop)
        return Coordinator(c, plan, fd, durable).execute()


def status(path, summary=False):
    c = api()
    plan = load_plan(c, path, fresh=False)
    file = Path(path).parent/'execution/status.json'
    value = c.read_json(file, private=True) if file.exists() else {'stage': 'NOT_STARTED'}
    value.update(saved_status_only=True, plan_boot_id=plan['boot_id'],
                 warning='Saved receipt only; not fresh device inspection. Do not retry, kill, unload or reboot from it.')
    if summary:
        result = value.get('completion', {})
        stats = result.get('plugin_timing', {}).get('aggregate_ns') or {}
        return {'stage': value.get('stage'), 'saved_status_only': True, 'plan': str(path),
                'reason': value.get('reason'), 'single_sample_only': True, 'measurement_profile': PROFILE,
                'execution_mode': plan['execution_mode'],
                'inference_ms': result.get('latency_ms'), 'CPU_reference_ms': result.get('CPU_reference_ms'),
                'Runner_setup_ms_excluded': result.get('Runner_setup_ms'),
                'CPU_reference_setup_ms_excluded': result.get('CPU_reference_setup_ms'),
                'bitwise_mismatches': result.get('worker', {}).get('bitwise_mismatches'),
                'completed_PL_calls': result.get('native', {}).get('completed_PL_calls'),
                'Dense_calls': result.get('native', {}).get('PL_dense'),
                'Sparse_calls': result.get('native', {}).get('PL_sparse'),
                'owner_exit_code': result.get('owner_exit_code'),
                'device_closed': result.get('native', {}).get('device_closed'),
                'hot_path_disk_events': result.get('native', {}).get('hot_path_disk_events'),
                'postrun_buffer_checks_verified': result.get('native', {}).get('postrun_buffer_checks_verified'),
                'graph_owner_sync_calls': result.get('native', {}).get('graph_owner_sync_calls'),
                'plugin_subintervals_ms_overlapping': {key: ns/1_000_000 for key, ns in stats.items()},
                'scope': 'One Runner.run includes BEGIN/END state/drain proof and client cache sync; full buffer/guard/static-weight checks occur in INFO outside timer; not pure PL or steady-state benchmark',
                'subinterval_warning': 'Plugin GEMM includes 36 PL sync/IPC/wait calls and 13 PS MatMul; do not add these to inference_ms',
                'warning': value['warning']}
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    check_parser = actions.add_parser('check')
    check_parser.add_argument('--uart-log-confirmed', action='store_true')
    check_parser.add_argument('--execution-mode', choices=('all_dense', 'mixed'), required=True)
    for name in ('run', 'status'):
        item = actions.add_parser(name); item.add_argument('--plan', type=Path, required=True)
        if name == 'run':
            item.add_argument(STOP_FLAG, action='store_true')
        else:
            item.add_argument('--summary', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'run':
            return run(args.plan, args.authorized_one_model_36_starts)
        value = check(args.uart_log_confirmed, args.execution_mode) if args.action == 'check' else status(args.plan, args.summary)
        print(json.dumps(value, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'MODEL_WINDOW_REFUSED_NO_AUTOMATIC_RECOVERY',
                          'error': type(error).__name__+': '+str(error)}, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
