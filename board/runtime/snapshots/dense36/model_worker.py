"""One CPU reference or one complete DENSE Runner invocation; never starts an owner."""
from __future__ import annotations
import argparse
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time
import types

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
AUTH = '--authorized-one-model-36-starts'
RESIDENT_AUTH = '--authorized-resident-batch'
OPTIONS = {'intra_op_threads': 4, 'inter_op_threads': 1, 'optimization': 'OFF',
           'sequential': True, 'spinning': 0, 'log_severity': 3, 'affinity': [0],
           'worker_affinities': '2;3;4'}


def need(ok, why):
    if not ok:
        raise ValueError(why)


def parent_identity(c, root, plan, auth=AUTH):
    """Pure /proc identity reads, called only after private bootstrap validation."""
    parent = os.getppid()
    command = ['/usr/bin/python3', '-B', str(root/'tools/run_model.py'), 'run', '--plan', plan['plan_path'], auth]
    need(c._proc_read(Path('/proc')/str(parent)/'cmdline') == b''.join(x.encode()+b'\0' for x in command),
         'worker must be direct child of the exact coordinator')
    group = ('0::/system.slice/'+plan['unit']+'.service\n').encode()
    need(c._proc_read(Path('/proc/self/cgroup')) == group == c._proc_read(Path('/proc')/str(parent)/'cgroup'),
         'worker/coordinator cgroup differs')
    need(c._proc_read(Path('/proc')/str(parent)/'status').split(b'Uid:')[1].splitlines()[0].split() ==
         [b'0', b'0', b'0', b'0'], 'root coordinator required')
    return parent


def bootstrap(plan_path, action, authorized):
    """Verify the root-copied coordinator before executing its helper source."""
    root = Path(plan_path).absolute().parent
    need(root.parent == Path('/var/lib/opt50-dense-model-v1') and HERE == root/'tools',
         'worker must execute the frozen root-private copy')
    for path in (root, HERE, HERE/'run_model.py', HERE/'tools_manifest.json'):
        for part in (path, *path.parents):
            info = part.lstat()
            need(not stat.S_ISLNK(info.st_mode), 'linked worker bootstrap refused')
        info = path.lstat()
        need(info.st_uid == 0 and not info.st_mode & 0o077 and
             (stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)),
             'root private worker/bootstrap input required')
    manifest = json.loads((HERE/'tools_manifest.json').read_bytes())
    raw = (HERE/'run_model.py').read_bytes()
    need({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} ==
         manifest['files']['run_model.py'], 'coordinator source differs from frozen tool inventory')
    entry = types.ModuleType('_b24_worker_entry')
    entry.__file__ = str(HERE/'run_model.py')
    exec(compile(raw, entry.__file__, 'exec'), entry.__dict__)
    a = entry.api(private=True); c = a.c
    c.root_linux()
    batch = entry.load_plan(a, plan_path, fresh=False)
    plan = entry.sample_plan(a, batch)
    need(os.environ.get('XRT_INI_PATH') == str(root/'xrt.ini'), 'worker must use the frozen XRT configuration')
    need(action in ('prepare', 'prepare-input', 'prepare-cpu') or authorized, 'one-model hardware permission required for the run worker')
    need(c._proc_read(Path('/proc/sys/kernel/random/boot_id'), 128).decode().strip() == plan['boot_id'],
         'worker current boot differs from plan')
    parent = parent_identity(c, root, plan)
    a_runtime = a.runtime.verify_bundle(root/'model')
    need(c.read_file(root/'job.kv', private=True) == entry.job_bytes(plan, a_runtime['manifest'], parent),
         'worker parent/job/model binding differs')
    entry.interpreter(c, plan['interpreter'])
    need(Path('/proc/self/exe').resolve(strict=True) == Path(plan['interpreter']['target']),
         'actual worker interpreter differs')
    lock = c._lock_info()
    c.parse_flock(c._proc_read(Path('/proc/locks'), 4*1024*1024), parent,
                  (os.major(lock.st_dev), os.minor(lock.st_dev)), lock.st_ino)
    execution = root/'execution'
    if action in ('prepare', 'prepare-input', 'prepare-cpu'):
        need(not any((execution/name).exists() for name in ('runtime.sock', 'runtime.sock.ctl', 'native_events')),
             'CPU reference must precede native owner/device access')
    else:
        for name in ('runtime.sock', 'runtime.sock.ctl'):
            info = (execution/name).lstat()
            need(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0, 'existing root owner sockets required')
    return types.SimpleNamespace(a=a, entry=entry, c=c, durable=a.durable, runtime=a.runtime,
                                 bundle=a_runtime, plan=plan, root=root, execution=execution)


def image_input(ctx, np):
    raw = ctx.c.read_file(ctx.plan['input'], private=True)
    need(ctx.entry.identity(raw) == ctx.plan['input_pin'], 'real image input file changed')
    image = np.load(io.BytesIO(raw), allow_pickle=False)
    need(image.shape == (1, 3, 224, 224) and image.dtype == np.dtype('float32') and
         bool(np.isfinite(image).all()), 'finite preprocessed float32 [1,3,224,224] input required')
    if ctx.plan['input_kind'] == 'zero':
        need(image.tobytes(order='C') == bytes(1*3*224*224*4), 'fixed positive-zero input required')
    return np.ascontiguousarray(image)


def prepare_input(ctx):
    raw = ctx.a.dataset.materialize_input(Path(ctx.plan['dataset_root']), ctx.plan['sample'])
    need(isinstance(raw, bytes) and ctx.entry.identity(raw) == ctx.plan['input_pin'],
         'materialized real input differs from sealed NPY identity')
    ctx.entry.new_bytes(ctx.c, ctx.durable, Path(ctx.plan['input']), raw)
    record = dict(base_record(ctx), schema='opt50_dense_input_preparation_v1',
                  status='REAL_INPUT_MATERIALIZED_NO_HARDWARE_ACCESS', input_file=ctx.plan['input_pin'],
                  hardware_accessed=False, PL_START_commands_issued_by_worker=0)
    ctx.durable.new_json(ctx.execution/'input_preparation.json', record)
    return record, 0


def checked_logits(np, value):
    need(isinstance(value, np.ndarray) and value.shape == (1, 1000) and value.dtype == np.dtype('float32') and
         bool(np.isfinite(value).all()), 'finite float32 [1,1000] logits required')
    return np.ascontiguousarray(value)


def write_npy(ctx, np, name, value):
    stream = io.BytesIO(); np.save(stream, value, allow_pickle=False); raw = stream.getvalue()
    ctx.entry.new_bytes(ctx.c, ctx.durable, ctx.execution/name, raw)
    return ctx.entry.identity(raw)


def base_record(ctx):
    p = ctx.plan
    return {'boot_id': p['boot_id'], 'plan_token': p['token'], 'input_sha256': p['input_pin']['sha256'],
            'input_kind': p['input_kind'],
            'sample_ordinal': p['sample']['ordinal'], 'sample_id': p['sample']['id'], 'label': p['sample']['label'],
            'image_sha256': p['sample']['image']['sha256'],
            'reference_sha256': p['reference_sha256'], 'model_manifest_sha256': p['model_manifest_sha256'],
            'logical_weights_sha256': p['logical_weights_sha256'], 'source_model_sha256': p['source_model_sha256'],
            'measurement_profile': ctx.entry.PROFILE, 'single_sample_only': True, 'ORT_options': OPTIONS,
            'accuracy_verified': False, 'performance_verified': False}


def cpu_reference(runtime, reference, image, *, bind_affinity=True):
    """Pure CPU operation exposed for a device-free host replay test."""
    np, ort = runtime.dependencies()
    options = runtime.session_options(ort, bind_affinity=bind_affinity)
    session = None
    try:
        setup_start = time.perf_counter_ns()
        session = ort.InferenceSession(reference, options, providers=['CPUExecutionProvider'])
        setup_end = time.perf_counter_ns()
        started = time.perf_counter_ns()
        outputs = session.run(None, {'image': image})
        ended = time.perf_counter_ns()
        logits = checked_logits(np, outputs[0]).copy()
    finally:
        session = None
        gc.collect()
    return logits, {'reference_setup_ns': setup_end-setup_start, 'reference_setup_ms': (setup_end-setup_start)/1e6,
                    'reference_ns': ended-started, 'reference_ms': (ended-started)/1e6}


def prepare_cpu(ctx):
    np, ort = ctx.runtime.dependencies()
    image = image_input(ctx, np)
    raw = ctx.c.read_file(ctx.root/'model/reference.onnx', private=True)
    need(ctx.entry.identity(raw)['sha256'] == ctx.plan['reference_sha256'], 'CPU reference graph changed')
    logits, timing = cpu_reference(ctx.runtime, raw, image)
    record = dict(base_record(ctx), schema='opt50_dense_model_cpu_reference_v1', status='CPU_REFERENCE_READY_NO_PL_ACCESS',
                  numpy_version=np.__version__, ort_version=ort.__version__, CPU_session_closed=True,
                  PL_START_commands_issued_by_worker=0, hardware_accessed=False, shape=[1, 1000], dtype='float32',
                  logits_file=write_npy(ctx, np, 'cpu_reference.npy', logits),
                  logits_raw_sha256=ctx.entry.identity(logits.tobytes(order='C'))['sha256'],
                  top5=np.argsort(-logits[0], kind='stable')[:5].astype(int).tolist(), **timing,
                  scope='Fresh ARM CPU reference for one selected real/zero input; not dataset accuracy or a performance benchmark',
                  timing_scope='ORT setup and exactly one CPU Session.run separately measured; excludes validation/serialization/close')
    ctx.durable.new_json(ctx.execution/'cpu_reference.json', record)
    return record, 0


def prepare(ctx):
    """One fresh child prepares input and a fresh, normally closed CPU session."""
    input_record, input_code = prepare_input(ctx)
    need(input_code == 0, 'input preparation did not complete')
    cpu_record, cpu_code = prepare_cpu(ctx)
    need(cpu_code == 0 and cpu_record.get('CPU_session_closed') is True,
         'fresh CPU preparation did not complete and close')
    return {'schema': 'opt50_dense_merged_preparation_v1', 'input': input_record,
            'CPU_reference': cpu_record, 'hardware_accessed': False,
            'PL_START_commands_issued_by_worker': 0}, 0


def read_cpu(ctx, np):
    record = ctx.entry.validate_cpu(ctx.c, ctx.plan, ctx.execution)
    raw = ctx.c.read_file(ctx.execution/'cpu_reference.npy', private=True)
    value = checked_logits(np, np.load(io.BytesIO(raw), allow_pickle=False))
    need(ctx.entry.identity(value.tobytes(order='C'))['sha256'] == record['logits_raw_sha256'], 'CPU raw logits changed')
    return value, ctx.entry.identity(ctx.c.read_file(ctx.execution/'cpu_reference.json', private=True))['sha256']


def retain(ctx, runner, error):
    # runner deliberately remains strongly referenced on this never-returning
    # stack. There is no automatic ABORT, close, STOP, retry or subprocess kill.
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, lambda *_: None)
    reason = type(error).__name__+': '+str(error)
    try:
        ctx.durable.new_json(ctx.execution/'worker_retained.json', dict(base_record(ctx),
                            stage='RETAINED_WORKER_MODEL_LIFETIME_UNCERTAIN', reason=reason,
                            worker_pid=os.getpid(), Runner_closed=False))
    except BaseException:
        pass
    while True:
        try:
            time.sleep(30)
        except BaseException:
            pass


def compare_logits(np, cpu, actual):
    cpu, actual = checked_logits(np, cpu), checked_logits(np, actual)
    different = np.flatnonzero((cpu.view(np.uint32) != actual.view(np.uint32)).ravel())
    absolute = np.abs(cpu.astype(np.float64)-actual.astype(np.float64))
    denominator = np.maximum(np.abs(cpu.astype(np.float64)), np.finfo(np.float32).tiny)
    return {'bitwise_mismatches': int(different.size), 'max_abs_difference': float(np.max(absolute)),
            'max_relative_difference': float(np.max(absolute/denominator)),
            'first_differences': [{'index': int(i), 'cpu': float(cpu.ravel()[i]), 'PL': float(actual.ravel()[i])}
                                  for i in different[:20]],
            'allclose_atol_1e_5_rtol_1e_5': bool(np.allclose(cpu, actual, atol=1e-5, rtol=1e-5)),
            'CPU_top5': np.argsort(-cpu[0], kind='stable')[:5].astype(int).tolist(),
            'PL_top5': np.argsort(-actual[0], kind='stable')[:5].astype(int).tolist()}


def bootstrap_resident(plan_path, authorized):
    """Load only the pinned coordinator, then request its batch-bound context."""
    need(authorized, 'explicit resident batch authorization required')
    root = Path(plan_path).absolute().parent
    need(root.parent == Path('/var/lib/opt50-dense-model-v1') and HERE == root/'tools',
         'resident worker must execute the frozen root-private copy')
    for path in (root, HERE, HERE/'run_model.py', HERE/'tools_manifest.json'):
        for part in (path, *path.parents):
            info = part.lstat()
            need(not stat.S_ISLNK(info.st_mode), 'linked resident worker bootstrap refused')
        info = path.lstat()
        need(info.st_uid == 0 and not info.st_mode & 0o077 and
             (stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)),
             'root private resident worker/bootstrap input required')
    manifest = json.loads((HERE/'tools_manifest.json').read_bytes())
    raw = (HERE/'run_model.py').read_bytes()
    need({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} == manifest['files']['run_model.py'],
         'resident coordinator source differs from frozen inventory')
    entry = types.ModuleType('_b24_resident_worker_entry')
    entry.__file__ = str(HERE/'run_model.py')
    exec(compile(raw, entry.__file__, 'exec'), entry.__dict__)
    ctx = entry.worker_context(Path(plan_path))
    ctx.entry = entry
    need(Path(ctx.root) == root and Path(ctx.execution) == root/'execution' and
         ctx.plan.get('resident_profile') == 'resident-bounded-batch-v4', 'resident worker context differs')
    c, plan = ctx.c, ctx.plan
    c.root_linux()
    need(os.environ.get('XRT_INI_PATH') == str(root/'xrt.ini'), 'resident worker XRT configuration differs')
    need(c._proc_read(Path('/proc/sys/kernel/random/boot_id'), 128).decode().strip() == plan['boot_id'],
         'resident worker current boot differs from plan')
    parent_identity(c, root, plan, RESIDENT_AUTH)
    entry.interpreter(c, plan['interpreter'])
    need(Path('/proc/self/exe').resolve(strict=True) == Path(plan['interpreter']['target']),
         'actual resident worker interpreter differs')
    lock = c._lock_info()
    c.parse_flock(c._proc_read(Path('/proc/locks'), 4*1024*1024), os.getppid(),
                  (os.major(lock.st_dev), os.minor(lock.st_dev)), lock.st_ino)
    for name in ('runtime.sock', 'runtime.sock.ctl'):
        info = (ctx.execution/name).lstat()
        need(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0, 'existing root resident owner sockets required')
    return ctx


class ResidentRunUncertain(RuntimeError):
    """The strong session/Runner references must survive for the parent review."""


class ResidentSession:
    """One CPU session and one PL Runner for a sealed, strictly ordered batch.

    Session creation is separate so the caller retains this object even when
    Runner construction fails after making owner contact. Tests may inject CPU
    and Runner factories; production always uses the checked runtime objects.
    """
    def __init__(self, ctx, *, cpu_factory=None, runner_factory=None, bind_affinity=True):
        self.ctx = ctx
        self.cpu_factory, self.runner_factory = cpu_factory, runner_factory
        self.bind_affinity = bind_affinity
        self.cpu = self.runner = None
        self.completed = 0
        self.last_end_returned = 0
        self.closed = self.failed = self.numerical_failed = False
        self.runner_possibly_contacted = False
        self.np = None
        self.samples = list(ctx.samples)
        count = ctx.plan.get('sample_count')
        need(type(count) is int and 1 <= count <= 50 and len(self.samples) == count and
             ctx.plan.get('owner_graph_count') == count and ctx.plan.get('total_START_budget') == 36*count,
             'sealed resident graph budget differs')
        need([sample['ordinal'] for sample in self.samples] ==
             list(range(ctx.plan['sample_start'], ctx.plan['sample_start']+count)),
             'sealed resident samples must be a contiguous range')
        self.max_runs = count

    def initialize(self):
        need(self.cpu is None and self.runner is None and not self.closed, 'resident session already initialized')
        ctx = self.ctx
        self.np, ort = ctx.runtime.dependencies()
        self.ort_version = ort.__version__
        bundle = getattr(ctx, 'bundle', None)
        if bundle is None:
            bundle = ctx.runtime.verify_bundle(ctx.root/'model')
        bundle.unchanged(ctx.root/'model')
        reference = bundle.reference_bytes
        need(hashlib.sha256(reference).hexdigest() == ctx.plan['reference_sha256'], 'resident CPU reference differs')
        options = ctx.runtime.session_options(ort, bind_affinity=self.bind_affinity)
        begin = time.perf_counter_ns()
        factory = self.cpu_factory or ort.InferenceSession
        self.cpu = factory(reference, options, providers=['CPUExecutionProvider'])
        end = time.perf_counter_ns()
        self.cpu_setup_ns = end-begin
        begin = time.perf_counter_ns()
        self.runner_possibly_contacted = True
        try:
            args = (ctx.plan['model_root'], ctx.plan['plugin'], ctx.plan['socket'])
            kwargs = {'max_runs': self.max_runs, 'checked_bundle': bundle, 'bind_affinity': self.bind_affinity}
            if self.runner_factory is None:
                # Assign before __init__: retain even a partially constructed
                # handle/session after an ambiguous owner handshake failure.
                self.runner = object.__new__(ctx.runtime.ResidentRunner)
                ctx.runtime.ResidentRunner.__init__(self.runner, *args, **kwargs)
            else:
                self.runner = self.runner_factory(*args, **kwargs)
        except BaseException as error:
            self.failed = True
            raise ResidentRunUncertain('resident Runner initialization failed: '+str(error)) from error
        end = time.perf_counter_ns()
        self.runner_setup_ns = end-begin
        return {'action': 'ready', 'CPU_session_closed': False, 'Runner_closed': False,
                'max_runs': self.max_runs, 'completed': 0, 'ORT_options': OPTIONS,
                'CPU_reference_setup_ns': self.cpu_setup_ns, 'CPU_reference_setup_ms': self.cpu_setup_ns/1e6,
                'Runner_setup_ns': self.runner_setup_ns, 'Runner_setup_ms': self.runner_setup_ns/1e6,
                'scope': 'Resident sessions initialized; no model BEGIN issued by worker'}

    def run(self, command):
        need(not self.closed and not self.failed and not self.numerical_failed and
             self.cpu is not None and self.runner is not None, 'resident session cannot admit another image')
        need(type(command) is dict and set(command) == {'action', 'run_id', 'sample'} and command['action'] == 'run',
             'exact resident run command required')
        run_id = command['run_id']
        need(type(run_id) is int and run_id == self.completed+1 and run_id <= self.max_runs,
             'resident run_id is not the next sealed sample')
        sample = self.samples[run_id-1]
        need(command['sample'] == sample, 'resident sample differs from frozen selection')
        self.admission_live()
        ctx, np = self.ctx, self.np
        folder = ctx.root/'samples'/('%06d' % sample['ordinal'])
        need(not folder.exists() and not folder.is_symlink(), 'resident sample output already exists; no replay')
        ctx.durable.mkdir_new(folder)
        input_start = time.perf_counter_ns()
        raw = ctx.a.dataset.materialize_input(Path(ctx.plan['dataset_root']), sample)
        need(isinstance(raw, bytes) and ctx.entry.identity(raw) ==
             {key: sample['input'][key] for key in ('bytes', 'sha256')}, 'resident input differs from sealed NPY')
        ctx.entry.new_bytes(ctx.c, ctx.durable, folder/'input.npy', raw)
        image = np.load(io.BytesIO(raw), allow_pickle=False)
        need(isinstance(image, np.ndarray) and image.dtype == np.dtype('float32') and
             image.shape == (1, 3, 224, 224) and bool(np.isfinite(image).all()), 'resident input shape/dtype differs')
        image = np.ascontiguousarray(image)
        input_end = time.perf_counter_ns()
        started = time.perf_counter_ns()
        cpu = checked_logits(np, self.cpu.run(None, {'image': image})[0]).copy()
        ended = time.perf_counter_ns()
        reference_ns = ended-started
        self.admission_live()
        started = time.perf_counter_ns()
        try:
            actual = self.runner.run(image, run_id=run_id)
        except BaseException as error:
            self.failed = True
            raise ResidentRunUncertain('resident Runner invocation failed: '+str(error)) from error
        ended = time.perf_counter_ns()
        self.last_end_returned = run_id
        try:
            return self.complete_result(sample, run_id, folder, raw, cpu, actual,
                                        input_start, input_end, reference_ns, started, ended)
        except BaseException as error:
            # END alone is not INFO/guard/persisted-result proof. A failure
            # here must not become a supposedly safe pre-BEGIN CPU error.
            self.failed = True
            raise ResidentRunUncertain('resident post-END result proof failed: '+str(error)) from error

    def complete_result(self, sample, run_id, folder, raw, cpu, actual,
                        input_start, input_end, reference_ns, started, ended):
        ctx, np = self.ctx, self.np
        actual = checked_logits(np, actual).copy()
        difference = compare_logits(np, cpu, actual)

        def save(name, value):
            stream = io.BytesIO(); np.save(stream, value, allow_pickle=False)
            contents = stream.getvalue()
            ctx.entry.new_bytes(ctx.c, ctx.durable, folder/name, contents)
            return ctx.entry.identity(contents)

        record = {
            'schema': 'opt50_dense_resident_worker_result_v4',
            'status': 'MODEL_LOGITS_MATCH_BITWISE' if difference['bitwise_mismatches'] == 0 else 'MODEL_LOGITS_MISMATCH',
            'boot_id': ctx.plan['boot_id'], 'plan_token': ctx.plan['token'], 'run_id': run_id,
            'sample_ordinal': sample['ordinal'], 'sample_id': sample['id'], 'label': sample['label'],
            'image_sha256': sample['image']['sha256'], 'input_kind': 'real', 'input_sha256': sample['input']['sha256'],
            'input_file': ctx.entry.identity(raw), 'cpu_logits_file': save('cpu_reference.npy', cpu),
            'logits_file': save('model_logits.npy', actual),
            'cpu_logits_raw_sha256': ctx.entry.identity(cpu.tobytes(order='C'))['sha256'],
            'logits_raw_sha256': ctx.entry.identity(actual.tobytes(order='C'))['sha256'],
            'reference_sha256': ctx.plan['reference_sha256'], 'custom_graph_sha256': ctx.plan['custom_graph_sha256'],
            'source_model_sha256': ctx.plan['source_model_sha256'],
            'logical_weights_sha256': ctx.plan['logical_weights_sha256'],
            'model_manifest_sha256': ctx.plan['model_manifest_sha256'],
            'measurement_profile': ctx.entry.PROFILE, 'resident_profile': 'resident-bounded-batch-v4',
            'ORT_options': OPTIONS, 'numpy_version': np.__version__, 'ort_version': self.ort_version,
            'CPU_session_closed': False, 'Runner_closed': False,
            'completed_graphs_in_session': run_id, 'runner_calls': 1, 'expected_PL_starts': 36,
            'actual_PL_starts': 'PENDING_NATIVE_INFO_AND_BATCH_OWNER_EXIT_PROOF', 'compared_logits': 1000,
            'input_preparation_ns': input_end-input_start, 'CPU_reference_ns': reference_ns,
            'CPU_reference_ms': reference_ns/1e6, 'reference_ns': reference_ns, 'reference_ms': reference_ns/1e6,
            'runner_run_ns': ended-started, 'runner_run_ms': (ended-started)/1e6,
            'Runner_setup_ms_excluded': self.runner_setup_ns/1e6,
            'CPU_reference_setup_ms_excluded': self.cpu_setup_ns/1e6,
            'accuracy': ctx.a.metrics.compare(cpu, actual, sample['label']),
            'accuracy_verified': False, 'performance_verified': False,
            'scope': 'One image in a bounded resident batch; sessions and owner remain open until batch close',
            'timing_scope': 'Exactly one Runner.run; excludes CPU reference, preparation, shared setup, comparison, serialization and owner INFO',
            **difference,
        }
        ctx.durable.new_json(folder/'worker.json', record)
        self.completed = run_id
        self.numerical_failed = record['bitwise_mismatches'] != 0
        return {'action': 'result', 'run_id': run_id, 'ordinal': sample['ordinal'], 'worker': record}

    def admission_live(self):
        created = self.ctx.plan.get('created_monotonic_ns')
        need(type(created) is int and 0 <= time.monotonic_ns()-created <= 900_000_000_000,
             'resident batch admission expired; no next BEGIN')

    def close(self):
        need(not self.closed and not self.failed, 'resident close cannot resolve an uncertain Runner')
        if self.runner is not None:
            try:
                self.runner.close()
            except BaseException as error:
                self.failed = True
                raise ResidentRunUncertain('resident Runner close failed: '+str(error)) from error
        self.runner = None
        self.cpu = None
        gc.collect()
        self.closed = True
        return {'action': 'closed', 'CPU_session_closed': True, 'Runner_closed': True,
                'completed': self.completed, 'scope': 'Python sessions closed; Native INFO/STOP/exit proof remains with coordinator'}


def retain_resident(ctx, session, error):
    # Publish uncertainty and exit without CLOSE/STOP/ABORT or replay. The
    # Native owner retains BO ownership and the inherited board lock. Its
    # coordinator treats worker EOF as retained; this worker suppresses no
    # termination signal and adds no permanent Python wait.
    try:
        ctx.durable.new_json(ctx.execution/'worker_retained.json', {
            'schema': 'opt50_dense_resident_worker_retained_v4', 'plan_token': ctx.plan['token'],
            'stage': 'RETAINED_WORKER_MODEL_LIFETIME_UNCERTAIN', 'reason': type(error).__name__+': '+str(error),
            'worker_pid': os.getpid(), 'completed': session.completed,
            'last_END_returned_run_id': session.last_end_returned,
            'Runner_closed': False, 'CPU_session_closed': False,
            'Runner_close_called': False, 'Native_owner_closed': False})
    except BaseException:
        pass
    return 2


def resident_loop(ctx, source=None, destination=None, *, session_factory=ResidentSession):
    source = sys.stdin if source is None else source
    destination = sys.stdout if destination is None else destination
    session = session_factory(ctx)

    def idle_exit(reason):
        # At a command boundary, successful Runner.run has already returned
        # after END. A pipe failure needs no termination suppression: close the
        # known idle Python sessions and let the parent inspect Native closure.
        need(not session.failed, 'cannot close an uncertain Runner after pipe failure')
        try:
            closed = (session.close() if not session.closed else
                      {'action': 'closed', 'CPU_session_closed': True, 'Runner_closed': True,
                       'completed': session.completed})
        except ResidentRunUncertain as error:
            return retain_resident(ctx, session, error)
        ctx.durable.new_json(ctx.execution/'worker_idle_close.json', dict(
            closed, schema='opt50_dense_resident_worker_idle_close_v4', reason=reason,
            plan_token=ctx.plan['token'], Native_owner_closed=False))
        return 2

    def emit(value):
        try:
            destination.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n')
            destination.flush()
            return True
        except (OSError, ValueError) as error:
            idle_exit('parent output pipe failed: '+str(error))
            return False

    try:
        if not emit(session.initialize()):
            return 2
    except ResidentRunUncertain as error:
        return retain_resident(ctx, session, error)
    except BaseException as error:
        session.failed = False
        if not emit({'action': 'error', 'phase': 'initialization', 'completed': session.completed,
                     'reason': type(error).__name__+': '+str(error), 'safe_to_request_close': True}):
            return 2
        session.numerical_failed = True
    while True:
        try:
            raw = source.readline(256*1024+1)
        except (OSError, ValueError) as error:
            return idle_exit('parent input pipe failed: '+str(error))
        if not raw:
            return idle_exit('parent pipe closed before explicit resident close')
        try:
            need(len(raw.encode('utf-8')) <= 256*1024 and raw.endswith('\n'), 'bounded complete JSON line required')
            command = ctx.runtime.decode_json(raw.encode('utf-8'))
            need(type(command) is dict, 'resident command must be an object')
            if command == {'action': 'close'}:
                return 0 if emit(session.close()) else 2
            if not emit(session.run(command)):
                return 2
        except ResidentRunUncertain as error:
            return retain_resident(ctx, session, error)
        except BaseException as error:
            # No active Runner remains on ordinary validation/CPU/I/O failure.
            # Consume no further graph. Only an explicit parent close is valid.
            session.numerical_failed = True
            if not emit({'action': 'error', 'phase': 'sample', 'completed': session.completed,
                         'reason': type(error).__name__+': '+str(error), 'safe_to_request_close': True}):
                return 2


def run_model(ctx):
    np, _ = ctx.runtime.dependencies()
    image = image_input(ctx, np)
    cpu, reference_record_sha = read_cpu(ctx, np)
    setup_start = time.perf_counter_ns()
    runner = ctx.runtime.Runner(ctx.plan['model_root'], ctx.plan['plugin'], ctx.plan['socket'])
    setup_end = time.perf_counter_ns()
    try:
        started = time.perf_counter_ns()
        actual = runner.run(image, run_id=1)
        ended = time.perf_counter_ns()
        actual = checked_logits(np, actual).copy()
        runner.close()  # Only after successful END; no model invocation can be replayed.
    except BaseException as error:
        retain(ctx, runner, error)
        raise AssertionError('retained worker unexpectedly returned')
    difference = compare_logits(np, cpu, actual)
    accuracy = ctx.a.metrics.compare(cpu, actual, ctx.plan['sample']['label'])
    record = dict(base_record(ctx), schema='opt50_dense_model_worker_result_v1',
                  status='MODEL_LOGITS_MATCH_BITWISE' if difference['bitwise_mismatches'] == 0 else 'MODEL_LOGITS_MISMATCH',
                  run_id=1, custom_graph_sha256=ctx.plan['custom_graph_sha256'],
                  cpu_reference_record_sha256=reference_record_sha,
                  logits_file=write_npy(ctx, np, 'model_logits.npy', actual),
                  logits_raw_sha256=ctx.entry.identity(actual.tobytes(order='C'))['sha256'], compared_logits=1000,
                  Runner_closed=True, runner_calls=1, expected_PL_starts=36,
                  actual_PL_starts='PENDING_NATIVE_END_AND_OWNER_EXIT_PROOF',
                  setup_ns=setup_end-setup_start, setup_ms=(setup_end-setup_start)/1e6,
                  runner_run_ns=ended-started, runner_run_ms=(ended-started)/1e6,
                  scope='One selected real/zero input through the complete DENSE model; no dataset accuracy claim',
                  timing_scope='Exactly one Runner.run including BEGIN/END and client sync; excludes setup, comparison, serialization, close and owner INFO postrun checks',
                  accuracy=accuracy, **difference)
    ctx.durable.new_json(ctx.execution/'worker_result.json', record)
    # A numerical mismatch is a completed, closed invocation. Return zero so the
    # parent can validate END, INFO and STOP safely, then classify FAIL_NUMERICAL.
    return record, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('prepare', 'prepare-input', 'prepare-cpu', 'run', 'resident'), required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument(AUTH, action='store_true', dest='authorized')
    parser.add_argument(RESIDENT_AUTH, action='store_true', dest='resident_authorized')
    args = parser.parse_args(argv)
    try:
        if args.action == 'resident':
            return resident_loop(bootstrap_resident(args.plan, args.resident_authorized))
        context = bootstrap(args.plan, args.action, args.authorized)
        value, code = {'prepare': prepare, 'prepare-input': prepare_input,
                       'prepare-cpu': prepare_cpu, 'run': run_model}[args.action](context)
        print(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False), flush=True)
        return code
    except BaseException as error:
        print(type(error).__name__+': '+str(error), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
