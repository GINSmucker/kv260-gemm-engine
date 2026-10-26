"""One immutable-mode same-weight sample, or its CPU-only zero reference.

This child never starts/stops an owner, changes a driver/overlay, retries RUN,
or measures a performance benchmark. Its parent owns the hardware lifecycle.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import gc
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import time
import types

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RECORDS = Path('/var/lib/opt50-fair-fd96539d-v1')
BOOT = 'f0f2eed8-5820-4df5-ba19-d05527720f97'
HELPER_SHA = '4bb598ec0fb77cf42e6f304967afe8dffc59e98907a5263977ad43f17917fda6'
MANIFEST_SHA = 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb'
WEIGHTS_SHA = '88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6'
REFERENCE_SHA = '50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49'
GRAPH_SHA = '8a462d73b5e8bb6544f65c6187dc621b560ae1329520f5556430ff42c97f0dfa'
DATA_SHA = '4dee6bf0ea5a3b9cffca9254ba206c31d30553079d448c7c028373fb7ffaaf94'
META_SHA = '93e24c85920cc50453e19c829127d7c9afed9b5de20e2961c207172cb6f7870b'
METADATA_SHA = '715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551'
PYTHON = '/home/ubuntu/fpga_project/model_b/.venv/bin/python'
AUTH = '--authorized-one-model-36-starts'
PROFILE = 'same-weights-fair-single-shot-v1'
MODES = ('all_dense', 'mixed')
INPUT_SHA = '2449003209f501e928cd67ed0335614accf844bdf3432a3e6146b1f836249dc9'
WARNING_NAMES = ('/blocks/blocks.0/mlp/act/Constant_2_output_0',
                 '/blocks/blocks.0/mlp/act/Constant_1_output_0',
                 '/blocks/blocks.0/mlp/act/Constant_output_0')
CPU_SCOPE = 'Synthetic zero input CPU reference only; not accuracy or performance'
RUN_SCOPE = 'Synthetic zero input numerical comparison only; not accuracy or performance'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def execution_mode(plan):
    """The coordinator freezes the routing arm before any owner is created."""
    value = plan.get('execution_mode')
    need(type(value) is str and value in MODES, 'explicit immutable execution_mode required')
    return value


def validate_warnings(raw):
    """Recognize only this graph's three warnings, never strip arbitrary ANSI."""
    need(type(raw) is bytes, 'diagnostics must be raw bytes')
    if raw == b'':
        return {'classification': 'EMPTY', 'raw': identity(raw), 'initializer_names': [], 'timestamps': []}
    for colored in (False, True):
        prefix, suffix = (b'\x1b[0;93m', b'\x1b[m') if colored else (b'', b'')
        parts = []
        for name in WARNING_NAMES:
            message = (" [W:onnxruntime:, graph.cc:4454 CleanUnusedInitializersAndNodeArgs] Removing initializer '"+
                       name+"'. It is not used by any node and should be removed from the model.").encode('ascii')
            parts.append(re.escape(prefix)+rb'([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9})'+
                         re.escape(message)+re.escape(suffix)+b'\n')
        match = re.fullmatch(b''.join(parts), raw)
        if match:
            timestamps = [part.decode('ascii') for part in match.groups()]
            for value in timestamps:
                datetime.datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S')
            need(timestamps == sorted(timestamps), 'warning timestamp order differs')
            return {'classification': 'EXACT_THREE_UNUSED_INITIALIZER_WARNINGS_'+('ANSI' if colored else 'PLAIN'),
                    'raw': identity(raw), 'initializer_names': list(WARNING_NAMES), 'timestamps': timestamps}
    raise ValueError('unexpected diagnostics; only empty or the exact three known graph warnings are allowed')


def elapsed(start, end):
    need(type(start) is int and type(end) is int and 0 <= start <= end, 'invalid perf-counter sample')
    return end-start


def relative(name):
    need(type(name) is str, 'string relative path required')
    path = PurePosixPath(name)
    need(str(path) == name and not path.is_absolute() and
         all(item not in ('', '.', '..') for item in name.split('/')) and
         not any(char in name for char in '\\:\0\r\n'), 'unsafe relative path')
    return path


def pin(value):
    need(type(value) is dict and set(value) == {'bytes', 'sha256'} and
         type(value['bytes']) is int and 0 <= value['bytes'] <= 64*1024*1024 and
         type(value['sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['sha256']),
         'invalid artifact pin')
    return value


def bootstrap():
    """The only executed helper is a fixed, private, exact-hash source file."""
    path = HERE/'support/companion.py'
    for part in (path, *path.parents):
        need(not part.is_symlink(), 'linked worker/helper path refused')
    before = path.stat()
    need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1 and
         not before.st_mode & 0o077 and before.st_size < 2*1024*1024,
         'private fixed support helper required')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    with os.fdopen(descriptor, 'rb') as stream:
        need(key(os.fstat(stream.fileno())) == key(before), 'helper replaced before read')
        raw = stream.read(2*1024*1024)
        need(key(os.fstat(stream.fileno())) == key(before) == key(path.stat()), 'helper changed')
    need(identity(raw)['sha256'] == HELPER_SHA, 'support helper identity differs')
    module = types.ModuleType('_opt50_worker_support')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    module.root_linux()
    return module


def verify_tools(c, plan, root):
    folder = root/'tools'
    raw = c.read_file(folder/'tools_manifest.json', private=True)
    manifest = c.decode_json(raw)
    expected = plan.get('tool_files')
    need(type(expected) is dict and set(expected) == {'manifest', 'files'} and
         identity(raw) == pin(expected['manifest']) and
         manifest.get('schema') == 'opt50_fair_model_tools_v1' and
         manifest.get('hardware_executed') is False and manifest.get('measurement_profile') == PROFILE and
         manifest.get('files') == expected['files'] and type(expected['files']) is dict and
         {'model_worker.py', 'model_runtime.py', 'model_inputs.py', 'run_model.py',
          'support/companion.py', 'support/durable.py'} <= set(expected['files']) and
         len(expected['files']) <= 256, 'root tool inventory differs')
    for name, expected_pin in expected['files'].items():
        path = relative(name)
        need(identity(c.read_file(folder.joinpath(*path.parts), private=True)) == pin(expected_pin),
             'root tool changed: '+name)
    need(expected['files']['support/companion.py']['sha256'] == HELPER_SHA,
         'fixed support helper pin differs')
    need(HERE == folder, 'worker must execute its root-copied source')
    return expected['files']


def job_fields(raw):
    need(b'\0' not in raw and len(raw) <= 65536, 'invalid native job bytes')
    result = {}
    for line in raw.decode('utf-8').splitlines():
        need(line.count('=') == 1, 'exact key=value job fields required')
        key, value = line.split('=')
        need(key and value and key not in result, 'duplicate/empty native job field')
        result[key] = value
    return result


def validate_parent(c, plan, root):
    job = job_fields(c.read_file(root/'job.kv', private=True))
    expected = {'schema': 'opt50_fair_native_job_v1', 'boot_id': BOOT,
                'execution_mode': execution_mode(plan),
                'plan_token': plan['token'], 'companion_lock': str(c.LOCK),
                'model_root': str(root/'model'), 'weights': str(root/'model/weights.bin'),
                'weights_sha256': WEIGHTS_SHA, 'data': str(root/'model/pl_w_data.bin'),
                'data_sha256': DATA_SHA, 'meta': str(root/'model/pl_w_meta.bin'),
                'meta_sha256': META_SHA, 'reference': str(root/'model/reference.onnx'),
                'reference_sha256': REFERENCE_SHA, 'metadata': str(root/'metadata.xclbin'),
                'metadata_sha256': METADATA_SHA, 'run_id': '1', 'expected_PL_starts': '36'}
    need(set(job) == set(expected) | {'expected_parent_pid'} and
         all(job[name] == value for name, value in expected.items()) and
         re.fullmatch('[1-9][0-9]*', job['expected_parent_pid']), 'native job identity/scope differs')
    parent = int(job['expected_parent_pid'])
    need(parent > 1 and os.getppid() == parent, 'worker must be a direct coordinator child')
    proc = Path('/proc/%d' % parent)
    expected_argv = ['/usr/bin/python3', '-B', str(root/'tools/run_model.py'),
                     'run', '--plan', str(root/'plan.json'), AUTH]
    need((proc/'cmdline').read_bytes() == b''.join(arg.encode()+b'\0' for arg in expected_argv),
         'coordinator exact argv differs')
    need(re.search(r'(?m)^Uid:\s+0\s+0\s+0\s+0$', (proc/'status').read_text()),
         'root coordinator required')
    group = '0::/system.slice/'+plan['unit']+'.service\n'
    need((proc/'cgroup').read_text() == group == Path('/proc/self/cgroup').read_text(),
         'worker/coordinator service cgroup differs')
    same_inode = lambda a, b: (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    need(same_inode((proc/'exe').stat(), Path('/usr/bin/python3').stat()),
         'coordinator executable differs')
    interpreter = plan.get('interpreter')
    need(type(interpreter) is dict and set(interpreter) == {'entry', 'target', 'identity'} and
         interpreter['entry'] == plan['python'] == PYTHON, 'fixed worker interpreter entry required')
    target = Path(interpreter['target'])
    need(target.is_absolute() and Path(PYTHON).resolve(strict=True) == target,
         'worker interpreter resolved target changed')
    target_info = c.no_links(target).stat()
    need(target_info.st_uid == 0 and not target_info.st_mode & 0o022 and
         same_inode(target_info, Path('/proc/self/exe').stat()) and
         identity(c.read_file(target)) == pin(interpreter['identity']), 'worker executable identity differs')
    lock = c._lock_info()
    matches = []
    expression = re.compile(r'^\s*\d+:\s+FLOCK\s+ADVISORY\s+WRITE\s+(\d+)\s+([a-fA-F0-9]+):([a-fA-F0-9]+):(\d+)\s+0\s+EOF\s*$')
    for line in Path('/proc/locks').read_text().splitlines():
        match = expression.fullmatch(line)
        if match and (int(match[1]), int(match[2], 16), int(match[3], 16), int(match[4])) == (
                parent, os.major(lock.st_dev), os.minor(lock.st_dev), lock.st_ino):
            matches.append(line)
    need(len(matches) == 1, 'coordinator no longer owns the shared companion flock')
    return parent


def context(plan_path, action, authorized):
    need(action in ('prepare-cpu', 'run') and authorized == (action == 'run'),
         'explicit run authority required; CPU preparation has no hardware authority')
    c = bootstrap()
    need(os.uname().release == '6.8.0-1015-xilinx' and sys.byteorder == 'little' and
         Path('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT, 'fixed boot/kernel required')
    path = Path(plan_path).absolute(); root = path.parent
    need(path.name == 'plan.json' and root.parent == RECORDS and
         re.fullmatch('plan_[a-f0-9]{12}', root.name), 'fixed root model plan path required')
    for folder in (RECORDS, root, root/'tools', root/'execution'):
        c.private_dir(folder)
    plan = c.read_json(path, private=True)
    need(plan.get('schema') == 'opt50_fair_model_plan_v1' and plan.get('boot_id') == BOOT and
         plan.get('plan_path') == str(path) and plan.get('token') == root.name[5:] and
         plan.get('unit') == 'opt50-fair-'+root.name[5:] and plan.get('python') == PYTHON and
         plan.get('scope') == 'ONE_ZERO_INPUT_GRAPH_36_STARTS_WITH_HELD_GUARDIAN' and
         type(plan.get('run_id')) is int and plan['run_id'] == 1 and
         type(plan.get('expected_PL_starts')) is int and plan['expected_PL_starts'] == 36 and
         plan.get('uart_logging_operator_confirmed') is True and
         plan.get('old_guardian_delegation') is False and type(plan.get('guardian_pid')) is int and
         plan['guardian_pid'] > 1 and type(plan.get('guardian_start_ticks')) is int and
         plan['guardian_start_ticks'] >= 0,
         'model window plan identity differs')
    execution_mode(plan)
    need(plan.get('measurement_profile') == PROFILE, 'separate same-weights fair measurement plan required')
    files = verify_tools(c, plan, root)
    module = lambda name: c.compiled_module(root/'tools'/name, files[name]['sha256'])
    module('model_inputs.py').validate_copied(c, plan)
    fixed = {'model_root': str(root/'model'), 'plugin': str(root/'plugin.so'),
             'input': str(root/'input_zero.npy'), 'execution': str(root/'execution'),
             'socket': str(root/'execution/runtime.sock'), 'model_runtime': str(root/'tools/model_runtime.py')}
    need(all(plan.get(name) == value for name, value in fixed.items()) and
         plan.get('input_pin') == plan['inputs']['files']['input_zero.npy'] and
         plan.get('plugin_pin') == plan['inputs']['files']['plugin.so'] and
         plan.get('model_manifest_pin') == plan['inputs']['files']['model/manifest.json'] and
         plan['model_manifest_pin']['sha256'] == MANIFEST_SHA and
         plan['input_pin']['sha256'] == INPUT_SHA and
         plan.get('reference_sha256') == REFERENCE_SHA and plan.get('logical_weights_sha256') == WEIGHTS_SHA,
         'worker input aliases or numeric identity differ')
    validate_parent(c, plan, root)
    execution = root/'execution'
    if action == 'prepare-cpu':
        need(all(not (execution/name).exists() and not (execution/name).is_symlink()
                 for name in ('runtime.sock', 'runtime.sock.ctl', 'native_events')),
             'CPU reference must be completed before starting the native owner')
    else:
        socket_info = (execution/'runtime.sock').lstat()
        need(stat.S_ISSOCK(socket_info.st_mode) and socket_info.st_uid == 0,
             'existing root owner socket required; worker never starts an owner')
    runtime = module('model_runtime.py')
    bundle = runtime.verify_bundle(plan['model_root'])
    need(bundle['manifest']['custom_graph_sha256'] == GRAPH_SHA and
         bundle['manifest']['logical_weights_sha256'] == WEIGHTS_SHA and
         bundle['manifest']['reference_sha256'] == REFERENCE_SHA, 'fixed model numeric identity differs')
    return types.SimpleNamespace(c=c, durable=c.durable_api(), plan=plan,
                                 runtime=runtime, execution=execution, bundle=bundle)


def image_input(ctx, np):
    raw = ctx.c.read_file(ctx.plan['input'], private=True)
    need(identity(raw) == ctx.plan['input_pin'], 'zero input file identity differs')
    image = np.load(io.BytesIO(raw), allow_pickle=False)
    need(isinstance(image, np.ndarray) and image.dtype == np.dtype('float32') and
         image.shape == (1, 3, 224, 224) and bool(np.isfinite(image).all()) and
         image.tobytes(order='C') == bytes(1*3*224*224*4), 'exact positive-zero float32 image required')
    return image


def logits_checked(np, value):
    need(isinstance(value, np.ndarray) and value.dtype == np.dtype('float32') and
         value.shape == (1, 1000) and bool(np.isfinite(value).all()), 'finite float32 [1,1000] logits required')
    return np.ascontiguousarray(value)


def write_npy(ctx, np, filename, value):
    buffer = io.BytesIO(); np.save(buffer, value, allow_pickle=False); raw = buffer.getvalue()
    target = ctx.execution/filename
    ctx.c.private_dir(target.parent)
    ctx.durable.fsync_dir(target.parent)  # Refuse unsupported durability before creation.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0) |
                         getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        need(stream.write(raw) == len(raw), 'short logits write')
        stream.flush(); os.fsync(stream.fileno())
    ctx.durable.fsync_dir(target.parent)
    need(ctx.c.read_file(target, private=True) == raw, 'published logits changed')
    return identity(raw)


def base_record(ctx):
    return {'boot_id': BOOT, 'plan_token': ctx.plan['token'],
            'execution_mode': execution_mode(ctx.plan),
            'input_sha256': ctx.plan['input_pin']['sha256'], 'reference_sha256': REFERENCE_SHA,
            'model_manifest_sha256': MANIFEST_SHA, 'logical_weights_sha256': WEIGHTS_SHA}


def prepare_cpu(ctx):
    np, ort = ctx.runtime.dependencies()
    image = image_input(ctx, np)
    reference = ctx.c.read_file(Path(ctx.plan['model_root'])/'reference.onnx', private=True)
    need(identity(reference)['sha256'] == REFERENCE_SHA, 'CPU reference graph changed')
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = 4, 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    os.sched_setaffinity(0, {0})
    options.add_session_config_entry('session.intra_op_thread_affinities', '2;3;4')
    for name in ('session.intra_op.allow_spinning', 'session.inter_op.allow_spinning'):
        options.add_session_config_entry(name, '0')
    session = None
    try:
        setup_start = time.perf_counter_ns()
        session = ort.InferenceSession(reference, options, providers=['CPUExecutionProvider'])
        setup_end = time.perf_counter_ns()
        run_start = time.perf_counter_ns()
        outputs = session.run(None, {'image': image})
        run_end = time.perf_counter_ns()
        logits = logits_checked(np, outputs[0]).copy()  # Validation/copy excluded from reference_ns.
    finally:
        session = None
        gc.collect()  # CPU session must be destroyed before the parent may create an owner.
    file_pin = write_npy(ctx, np, 'cpu_reference.npy', logits)
    record = dict(base_record(ctx), schema='opt50_model_cpu_reference_v1',
                  status='CPU_REFERENCE_READY_NO_PL_ACCESS', numpy_version=np.__version__, ort_version=ort.__version__,
                  shape=[1, 1000], dtype='float32', logits_file=file_pin,
                  logits_raw_sha256=identity(logits.tobytes(order='C'))['sha256'],
                  CPU_session_closed=True, PL_START_commands_issued_by_worker=0,
                  hardware_accessed=False, scope=CPU_SCOPE, measurement_profile=PROFILE,
                  reference_setup_ns=elapsed(setup_start, setup_end),
                  reference_setup_ms=elapsed(setup_start, setup_end)/1_000_000,
                  reference_ns=elapsed(run_start, run_end), reference_ms=elapsed(run_start, run_end)/1_000_000,
                  timing_scope='reference_setup: ORT Session constructor only; reference: exactly one Session.run; excludes output validation/copy/serialization/close',
                  single_sample_only=True, performance_verified=False)
    ctx.durable.new_json(ctx.execution/'cpu_reference.json', record)
    return record, 0


def read_cpu(ctx, np):
    raw_record = ctx.c.read_file(ctx.execution/'cpu_reference.json', private=True)
    record = ctx.c.decode_json(raw_record)
    keys = set(base_record(ctx)) | {'schema', 'status', 'numpy_version', 'ort_version', 'shape', 'dtype',
           'logits_file', 'logits_raw_sha256', 'CPU_session_closed', 'PL_START_commands_issued_by_worker',
           'hardware_accessed', 'scope', 'measurement_profile', 'reference_setup_ns', 'reference_setup_ms',
           'reference_ns', 'reference_ms', 'timing_scope', 'single_sample_only', 'performance_verified'}
    need(type(record) is dict and set(record) == keys and
         all(record.get(k) == v for k, v in base_record(ctx).items()) and
         record['schema'] == 'opt50_model_cpu_reference_v1' and
         record['status'] == 'CPU_REFERENCE_READY_NO_PL_ACCESS' and
         record['numpy_version'] == '1.26.4' and record['ort_version'] == '1.22.1' and
         record['shape'] == [1, 1000] and all(type(n) is int for n in record['shape']) and
         record['dtype'] == 'float32' and type(record['logits_raw_sha256']) is str and
         re.fullmatch('[a-f0-9]{64}', record['logits_raw_sha256']) and
         record['CPU_session_closed'] is True and record['hardware_accessed'] is False and
         type(record['PL_START_commands_issued_by_worker']) is int and
         record['PL_START_commands_issued_by_worker'] == 0 and record['scope'] == CPU_SCOPE,
         'complete CPU-only reference receipt required; no runtime CPU fallback')
    need(record['measurement_profile'] == PROFILE and record['single_sample_only'] is True and
         record['performance_verified'] is False and
         all(type(record[key]) is int and record[key] >= 0 for key in ('reference_setup_ns', 'reference_ns')) and
         record['reference_setup_ms'] == record['reference_setup_ns']/1_000_000 and
         record['reference_ms'] == record['reference_ns']/1_000_000 and
         record['timing_scope'] == 'reference_setup: ORT Session constructor only; reference: exactly one Session.run; excludes output validation/copy/serialization/close',
         'single-shot CPU timing identity/scope differs')
    raw = ctx.c.read_file(ctx.execution/'cpu_reference.npy', private=True)
    need(identity(raw) == pin(record['logits_file']), 'CPU logits NPY changed')
    logits = logits_checked(np, np.load(io.BytesIO(raw), allow_pickle=False))
    need(identity(logits.tobytes(order='C'))['sha256'] == record['logits_raw_sha256'],
         'CPU logits raw identity differs')
    return logits, identity(raw_record)['sha256']


def retained(ctx, runner, error):
    """Keep the client/imported mappings alive; only an external review may act.

    The local ``runner`` reference deliberately remains on this never-returning
    stack. No close, retry, STOP, reset, unload or child signal is performed.
    Failure to publish the diagnostic receipt must not release the references.
    """
    reason = type(error).__name__+': '+str(error)
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, lambda *_: None)
    value = dict(base_record(ctx), schema='opt50_model_worker_retained_v1',
                 stage='WORKER_RETAINED_UNKNOWN_MODEL_STATE', reason=reason,
                 worker_pid=os.getpid(), Runner_closed=False,
                 actual_PL_starts='UNKNOWN_PRESERVE_NATIVE_OWNER_AND_CLIENT',
                 retry=False, STOP_issued=False, accuracy_verified=False, performance_verified=False,
                 scope='Uncertain model invocation; preserve owner, client mappings and both locks')
    try:
        ctx.durable.new_json(ctx.execution/'worker_retained.json', value)
    except BaseException:
        pass
    try:
        print('RETAINED MODEL WORKER: '+reason+'; no retry/close/STOP/kill/recovery', file=sys.stderr, flush=True)
    except BaseException:
        pass
    while True:
        try:
            time.sleep(30)
        except BaseException:
            pass


def plugin_timing(runner):
    """Read plugin memory only, after END and before close; never owner INFO."""
    scope = ('49 custom Linear operators; GEMM field mixes 36 PL client sync/IPC/wait calls and '
             '13 PS MatMul operations. Overlaps Runner.run; NOT pure PL latency and not additive to Runner.run.')
    try:
        aggregate_fn = getattr(runner.lib, 'kv5_b_timing')
        layers_fn = getattr(runner.lib, 'boundary_layers')
    except AttributeError:
        return {'schema': 'opt50_perf_plugin_timing_v1', 'status': 'UNKNOWN_NOT_EXPORTED',
                'aggregate_ns': None, 'layers': None, 'scope': scope}
    try:
        for function in (aggregate_fn, layers_fn):
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
            function.restype = ctypes.c_int
        aggregate, layers = (ctypes.c_uint64*4)(), (ctypes.c_uint64*(49*4))()
        need(aggregate_fn(runner.handle, aggregate) == 0, 'plugin aggregate snapshot rejected')
        need(layers_fn(runner.handle, layers) == 0, 'plugin per-layer snapshot rejected')
        aggregate = list(aggregate); rows = [list(layers[4*i:4*i+4]) for i in range(49)]
        need(all(sum(row[slot] for row in rows) == aggregate[slot] for slot in range(4)) and
             all(sum(row[:3]) <= row[3] for row in rows), 'plugin timing accumulation/scope differs')
        return {'schema': 'opt50_perf_plugin_timing_v1', 'status': 'AVAILABLE_PLUGIN_MEMORY_ONLY',
                'aggregate_ns': dict(zip(('quantization', 'client_gemm_or_PS_matmul', 'dequantization', 'custom_op_total'), aggregate)),
                'raw_aggregate_u64': aggregate, 'raw_layers_u64': [value for row in rows for value in row],
                'layers': [{'layer_index': i, 'route': 'PL' if i < 48 and i % 4 != 1 else 'PS',
                            'quantization_ns': row[0], 'client_gemm_or_PS_matmul_ns': row[1],
                            'dequantization_ns': row[2], 'custom_op_total_ns': row[3]} for i, row in enumerate(rows)],
                'scope': scope, 'captured_after_RUN_before_close': True, 'hardware_commands_issued': 0}
    except BaseException as error:
        return {'schema': 'opt50_perf_plugin_timing_v1', 'status': 'UNKNOWN_CAPTURE_FAILED',
                'aggregate_ns': None, 'layers': None, 'reason': type(error).__name__+': '+str(error), 'scope': scope}


def run_model(ctx):
    np, _ = ctx.runtime.dependencies()  # Version/provider checks only, no CPU reference session here.
    image = image_input(ctx, np)
    cpu, cpu_record_sha = read_cpu(ctx, np)
    setup_start = time.perf_counter_ns()
    runner = ctx.runtime.Runner(ctx.plan['model_root'], ctx.plan['plugin'], ctx.plan['socket'])
    setup_end = time.perf_counter_ns()
    try:
        run_start = time.perf_counter_ns()
        outputs = runner.run(image, run_id=1)
        run_end = time.perf_counter_ns()
        logits = logits_checked(np, outputs).copy()  # Explicitly OUTSIDE the measured Runner.run.
    except BaseException as error:
        retained(ctx, runner, error)
        raise AssertionError('retained worker unexpectedly returned')
    stats = plugin_timing(runner)  # Memory-only exports; outside the measured range and before close.
    try:
        runner.close()  # Successful RUN includes END; only then release imported mappings.
    except BaseException as error:
        retained(ctx, runner, error)
        raise AssertionError('retained worker unexpectedly returned')
    # Runner session and plugin/client handle are closed before any success receipt.
    mismatch = int(np.count_nonzero(logits.view(np.uint32) != cpu.view(np.uint32)))
    difference = float(np.max(np.abs(logits.astype(np.float64)-cpu.astype(np.float64))))
    file_pin = write_npy(ctx, np, 'model_logits.npy', logits)
    record = dict(base_record(ctx), schema='opt50_model_worker_result_v1',
                  status='MODEL_LOGITS_MATCH_BITWISE' if mismatch == 0 else 'MODEL_LOGITS_MISMATCH',
                  run_id=1, custom_graph_sha256=GRAPH_SHA, cpu_reference_record_sha256=cpu_record_sha,
                  logits_file=file_pin, logits_raw_sha256=identity(logits.tobytes(order='C'))['sha256'],
                  compared_logits=1000, bitwise_mismatches=mismatch, max_abs_difference=difference,
                  Runner_closed=True, runner_calls=1, expected_PL_starts=36,
                  actual_PL_starts='PENDING_NATIVE_END_AND_OWNER_EXIT_PROOF',
                  accuracy_verified=False, performance_verified=False, scope=RUN_SCOPE,
                  measurement_profile=PROFILE, single_sample_only=True,
                  setup_ns=elapsed(setup_start, setup_end), setup_ms=elapsed(setup_start, setup_end)/1_000_000,
                  runner_run_ns=elapsed(run_start, run_end), runner_run_ms=elapsed(run_start, run_end)/1_000_000,
                  plugin_timing=stats,
                  timing_scope='setup: Runner constructor including verification/handshake/session; runner_run: exactly one Runner.run including BEGIN/END state/drain proof and client cache sync; excludes output validation/copy, plugin snapshot, comparison, serialization, close and owner INFO full-buffer/guard/static-weight checks')
    ctx.durable.new_json(ctx.execution/'worker_result.json', record)
    return record, 0 if mismatch == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('prepare-cpu', 'run'), required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument(AUTH, action='store_true', dest='authorized')
    args = parser.parse_args(argv)
    try:
        ctx = context(args.plan, args.action, args.authorized)
        record, code = prepare_cpu(ctx) if args.action == 'prepare-cpu' else run_model(ctx)
        print(json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False), flush=True)
        return code
    except BaseException as error:
        print(type(error).__name__+': '+str(error), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())


