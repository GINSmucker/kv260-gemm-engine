"""Root hardware supervisor for the opt-in, current-boot Sparse24 demo.

No acceptance history is read or rewritten. A fault retains the owner and its
buffers; HTTP requests cannot reload firmware, reset, stop, or retry a graph.
"""
import argparse
import io
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

from common import APP, STATE, RUN, VENV, MODEL_SHA, Ledger, create, encoded, need, pin, replace, receive, send
from management import Admin
from cpu_optimizations import ExactBundleAudit, preprocess as exact_preprocess
from runtime_options import pl_options

STOP = threading.Event()


def display_top5(np, actual, labels):
    """Display-only postprocessing; all 1000 original logits stay unchanged."""
    need(actual.shape == (1, 1000) and np.isfinite(actual).all(), 'invalid display logits')
    top = sorted(range(1000), key=lambda i: (-float(actual[0, i]), i))[:5]
    scores = actual[0].astype(np.float64)
    probabilities = np.exp(scores - scores.max())
    probabilities /= probabilities.sum()
    return [{'index': i, 'name': labels[i]['name'], 'logit': float(actual[0, i]),
             'probability': float(probabilities[i])} for i in top]


def verify_install():
    need(all(not p.is_symlink() for p in (APP, *APP.parents)), 'linked install root')
    config = json.loads((APP / 'install_receipt.json').read_bytes())
    for name, expected in config['files'].items():
        path = APP / name
        need(all(not p.is_symlink() for p in (path, *path.parents)) and path.is_file()
             and pin(path.read_bytes()) == expected, 'installed file differs: ' + name)
        info = path.stat()
        need(info.st_uid == 0 and not info.st_mode & 0o022, 'writable runtime refused')
    return config


def lifetime_lock(path, *, create_new=False):
    import fcntl
    path = Path(path)
    need(all(not p.is_symlink() for p in (path, *path.parents)), 'linked lock path')
    if create_new:
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if not path.exists():
            create(path, b'OPT50 demo global compute exclusion\n')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'regular lock required')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


class Session:
    def __init__(self, service):
        self.service = service
        self.token = secrets.token_hex(6)
        self.root = STATE / ('session_' + self.token)
        self.ex = self.root / 'execution'
        self.root.mkdir(mode=0o700)
        self.ex.mkdir(mode=0o700)
        self.created = time.monotonic_ns()
        self.completed = 0
        self.last_activity = self.created
        self.owner = self.runner = None
        self.admin = None
        self.closed = False
        self.plan = {'schema': 'opt50_demo_session_v1', 'token': self.token, 'boot_id': service.boot,
                     'created_monotonic_ns': self.created, 'sample_count': 50,
                     'total_START_budget': 1800, 'source_model_sha256': MODEL_SHA,
                     'measurement_profile': 'demo-live-exact-runscoped-v1', 'ORT_intra_spinning': 1, 'ORT_force_spinning_stop': 1, 'ORT_dynamic_block_base': 0}
        create(self.root / 'plan.json', encoded(self.plan))

    def initialize(self):
        s = self.service
        model = STATE / 'model'
        manifest = s.bundle['manifest']
        for name, source in [('native_owner', APP / 'build/opt50_model_owner'),
                             ('metadata.xclbin', s.boot_folder / 'metadata.xclbin'),
                             ('xrt.ini', s.boot_folder / 'xrt.ini')]:
            create(self.root / name, source.read_bytes(), 0o700 if name == 'native_owner' else 0o600)
        fields = {'schema': 'opt50_b24_resident_native_job_v4', 'boot_id': s.boot, 'plan_token': self.token,
                  'expected_parent_pid': str(os.getpid()),
                  'companion_lock': '/var/lib/opt50-first-access-fd96539d-v1/companion.lock',
                  'model_root': str(model), 'weights': str(model / 'weights.bin'),
                  'weights_sha256': manifest['logical_weights_sha256'], 'data': str(model / 'pl_w_data.bin'),
                  'data_sha256': manifest['pl_data_sha256'], 'meta': str(model / 'pl_w_meta.bin'),
                  'meta_sha256': manifest['pl_meta_sha256'], 'reference': str(model / 'reference.onnx'),
                  'reference_sha256': manifest['reference_sha256'], 'metadata': str(self.root / 'metadata.xclbin'),
                  'metadata_sha256': pin((self.root / 'metadata.xclbin').read_bytes())['sha256'],
                  'run_id': '1', 'expected_PL_starts': '36', 'source_model_sha256': MODEL_SHA,
                  'sample_count': '50', 'total_START_budget': '1800', 'created_monotonic_ns': str(self.created)}
        need(len(fields) == 22 and all(not any(c in v for c in '\n\r=\0') for v in fields.values()), 'job fields differ')
        create(self.root / 'job.kv', ''.join(k+'='+v+'\n' for k, v in fields.items()).encode())
        argv = [str(self.root / 'native_owner'), str(model / 'weights.bin'), str(self.ex / 'runtime.sock'),
                str(self.root / 'metadata.xclbin'), str(self.ex), str(self.root / 'job.kv'),
                str(s.compute_lock), '--authorized-resident-batch']
        create(self.root / 'owner_intent.json', encoded({'argv': argv, 'boot_id': s.boot}))
        self.out = open(self.ex / 'owner.stdout', 'xb', buffering=0)
        self.err = open(self.ex / 'owner.stderr', 'xb', buffering=0)
        self.owner = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=self.out, stderr=self.err,
                      pass_fds=(s.compute_lock,), cwd=self.ex,
                      env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                           'HOME': '/root', 'XRT_INI_PATH': str(self.root / 'xrt.ini')})
        began = time.monotonic()
        while (self.ex / 'owner.stdout').read_bytes() != ('KV5_READY %d\n' % self.owner.pid).encode():
            need(self.owner.poll() is None and time.monotonic()-began < 180, 'Native owner not ready; preserve state')
            time.sleep(0.1)
        # Retain the object even if initialization makes an ambiguous owner contact.
        self.runner = s.runtime.ResidentRunner.__new__(s.runtime.ResidentRunner)
        self.runner.__init__(model, APP / 'build/libopt50_model.so', self.ex / 'runtime.sock',
                             max_runs=50, checked_bundle=s.bundle, options=pl_options(s.runtime.dependencies()[1],1,force_stop=True))
        create(self.root / 'ready.json', encoded({'owner_pid': self.owner.pid, 'boot_id': s.boot}))

    def admission(self):
        need(not self.closed and self.owner.poll() is None and self.completed < 50
             and 0 <= time.monotonic_ns()-self.created < 840_000_000_000,
             'session admission expired or owner exited')
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.service.boot, 'boot differs')
        self.service.bundle_audit.unchanged(STATE / 'model')

    def rotation_due(self, now_ns=None):
        now = time.monotonic_ns() if now_ns is None else now_ns
        # The validated owner also has a 130-second idle socket timeout. Close
        # a healthy idle session at 60s, before either protocol socket expires.
        return self.completed >= 50 or now-self.created >= 780_000_000_000 or now-self.last_activity >= 60_000_000_000

    def infer(self, image, folder):
        s = self.service
        infer_started = time.perf_counter_ns()
        admission_started = infer_started
        self.admission()
        admission_ms = (time.perf_counter_ns()-admission_started)/1e6
        run = self.completed + 1
        cpu_start = time.perf_counter_ns()
        cpu = s.cpu.run(None, {'image': image})[0]
        cpu_ms = (time.perf_counter_ns()-cpu_start)/1e6
        admission_started = time.perf_counter_ns()
        self.admission()
        admission_ms += (time.perf_counter_ns()-admission_started)/1e6
        intent_started = time.perf_counter_ns()
        create(folder / 'graph_intent.json', encoded({'session': self.token, 'run_id': run,
               'boot_id': s.boot, 'expected_PL_calls': 36}))
        intent_ms = (time.perf_counter_ns()-intent_started)/1e6
        began = time.perf_counter_ns()
        actual = self.runner.run(image, run_id=run).copy()
        latency = (time.perf_counter_ns()-began)/1e6
        if self.admin is None:
            self.admin = Admin(self.ex / 'runtime.sock.ctl', self.owner.pid, s.bundle['manifest']['logical_weights_sha256'])
        info_started = time.perf_counter_ns()
        self.admin.call(8)
        info_ms = (time.perf_counter_ns()-info_started)/1e6
        proof_path = self.ex / ('performance_native_%06d.json' % run)
        proof = s.proof.performance(json.loads(proof_path.read_bytes()), self.plan, run)
        need(cpu.shape == actual.shape == (1, 1000) and cpu.dtype == actual.dtype == s.np.float32
             and s.np.isfinite(cpu).all() and s.np.isfinite(actual).all(), 'invalid logits')
        mismatch = int(s.np.count_nonzero(cpu.view(s.np.uint32) != actual.view(s.np.uint32)))
        need(mismatch == 0, 'CPU/FPGA logits differ; no next graph')
        persistence_started = time.perf_counter_ns()
        for name, value in [('cpu_logits.npy', cpu), ('pl_logits.npy', actual)]:
            out = io.BytesIO(); s.np.save(out, value, allow_pickle=False)
            create(folder / name, out.getvalue())
        persistence_ms = (time.perf_counter_ns()-persistence_started)/1e6
        self.completed = run
        self.last_activity = time.monotonic_ns()
        result = {'stage': 'PASS', 'execution_source': 'REAL_FPGA_OPT50_SPARSE24', 'session': self.token,
                  'run_id': run, 'boot_id': s.boot, 'source_model_sha256': MODEL_SHA,
                  'Runner_run_ms': latency, 'CPU_reference_ms': cpu_ms, 'bitwise_mismatches': mismatch,
                  'python_profile': 'exact-rgb-path-audit-v1', 'measurement_profile': 'demo-live-exact-runscoped-v1', 'ORT_intra_spinning': 1, 'ORT_force_spinning_stop': 1, 'ORT_dynamic_block_base': 0,
                  'CPU_logits_sha256': pin(cpu.tobytes())['sha256'], 'PL_logits_sha256': pin(actual.tobytes())['sha256'],
                  'native_proof_sha256': pin(proof_path.read_bytes())['sha256'], 'native': proof,
                  'top5': display_top5(s.np, actual, s.labels),
                  'probability_scope': 'Stable softmax over all 1000 logits; not calibrated confidence',
                  'accuracy_for_uploaded_image': 'UNLABELED_NOT_MEASURED', 'device_closed': False,
                  'scope': 'Runner.run includes PS work, BEGIN/END and client sync; excludes preprocessing, setup, CPU reference and INFO; not pure PL latency'}
        infer_total_ms = (time.perf_counter_ns()-infer_started)/1e6
        result.update(INFO_ms=info_ms, bundle_admission_ms=admission_ms,
                      graph_intent_persistence_ms=intent_ms, logit_persistence_ms=persistence_ms,
                      Session_infer_ms=infer_total_ms,
                      inference_other_ms=infer_total_ms-admission_ms-cpu_ms-intent_ms-latency-info_ms-persistence_ms)
        return result

    def close(self):
        need(not self.closed, 'session already closed')
        if self.runner is not None:
            self.runner.close()
            self.runner = None
        if self.admin is None:
            self.admin = Admin(self.ex / 'runtime.sock.ctl', self.owner.pid,
                               self.service.bundle['manifest']['logical_weights_sha256'])
        try:
            self.admin.call(7)
        finally:
            self.admin.close()
        # wait(timeout) only observes; it never signals/kills or repeats STOP.
        need(self.owner.wait(timeout=150) == 0, 'owner exit failed')
        expected = ('KV5_READY %d\nKV5_STOP calls=%d graphs=%d max_graphs=50\n' %
                    (self.owner.pid, 36*self.completed, self.completed)).encode()
        need((self.ex / 'owner.stdout').read_bytes() == expected and
             (self.ex / 'owner.stderr').read_bytes() == b'', 'normal close stdout differs')
        paths = sorted((self.ex / 'native_events').glob('event_*.json'))
        events = [json.loads(p.read_bytes()) for p in paths]
        need([e['sequence'] for e in events] == list(range(1, len(events)+1)), 'native event sequence differs')
        closed = self.service.proof.owner_close(events, self.completed)
        create(self.root / 'closed.json', encoded(closed))
        self.out.close(); self.err.close()
        self.closed = True


class Service:
    def __init__(self, config):
        self.config = config
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.boot_folder = STATE / ('boot_' + self.boot)
        # An already attempted boot is never re-entered automatically.
        self.boot_folder.mkdir(mode=0o700)
        self.mutex = threading.Lock()
        self.pending = queue.Queue(maxsize=1)
        self.busy = False
        self.fault = False
        self.session = None
        self.status = {}
        self.ledger = Ledger(STATE / 'requests', self.boot)
        self.board_lock = lifetime_lock('/home/ubuntu/fpga_project/deploy/.f8_board.lock')
        self.compute_lock = lifetime_lock('/var/lib/opt50-first-access-fd96539d-v1/companion.lock', create_new=True)

    def update(self, stage, **details):
        self.status = {'schema': 'opt50_live_demo_status_v1', 'stage': stage, 'boot_id': self.boot,
                       'heartbeat_monotonic': time.monotonic(), 'source_model_sha256': MODEL_SHA,
                       'python_profile': 'exact-rgb-path-audit-v1', 'measurement_profile': 'demo-live-exact-runscoped-v1', 'ORT_intra_spinning': 1, 'ORT_force_spinning_stop': 1, 'ORT_dynamic_block_base': 0,
                       'automatic_replay': False, **details}
        replace(RUN / 'status.json', self.status, 0o644)
        replace(self.boot_folder / 'status.json', self.status)
        print(json.dumps(self.status), flush=True)

    def initialize(self):
        self.update('VERIFYING_FILES')
        import model_runtime as runtime
        import resident_proof as proof
        import dataset
        self.runtime, self.proof, self.dataset = runtime, proof, dataset
        self.bundle = runtime.verify_bundle(STATE / 'model')
        self.bundle_audit = ExactBundleAudit(self.bundle, runtime)
        self.np, ort = runtime.dependencies()
        dataset.dependencies()  # Pin Pillow as well as NumPy.
        self.labels = json.loads((APP / 'labels_imagenet1k.json').read_bytes())
        need(len(self.labels) == 1000 and [r['index'] for r in self.labels] == list(range(1000)), 'label order differs')
        from loader import load
        load(self.boot, self.boot_folder, self.update)
        need(not STOP.is_set(), 'stop requested; no Native owner started')
        self.update('CPU_REFERENCE_INITIALIZING')
        self.cpu = ort.InferenceSession(self.bundle.reference_bytes, runtime.session_options(ort),
                                       providers=['CPUExecutionProvider'])
        self.update('CURRENT_BOOT_SELFTEST_RUNNING')
        folder = self.boot_folder / 'selftest'
        folder.mkdir(mode=0o700)
        self.session = Session(self)
        self.session.initialize()
        image = self.np.load(STATE / 'model/inputs/dev0000_input.npy', allow_pickle=False)
        result = self.session.infer(image, folder)
        create(folder / 'result.json', encoded(result))
        self.update('READY', selftest_bitwise_mismatches=0, selftest_completed_PL_calls=36,
                    url='http://192.168.137.2:8080', busy=False)

    def submit(self, token, raw):
        with self.mutex:
            previous = self.ledger.get(token)
            if previous is not None:
                need(previous['image_sha256'] == pin(raw)['sha256'], 'request ID image differs')
                return previous
            need(not self.fault and not STOP.is_set() and self.status.get('stage') == 'READY', 'hardware not READY')
            need(not self.busy, 'hardware busy; new request not admitted')
            need(shutil.disk_usage(STATE).free > 128*1024*1024, 'insufficient evidence storage; new request not admitted')
            value, fresh = self.ledger.admit(token, raw)
            need(fresh, 'unexpected prior request')
            self.busy = True
            self.pending.put_nowait((token, raw))
            return value

    def worker(self):
        while not STOP.is_set():
            try:
                token, raw = self.pending.get(timeout=1)
            except queue.Empty:
                # Close an idle lifetime before the 900s admission window. No
                # replacement Native owner is created until the next new image.
                if self.session and self.session.owner.poll() is not None:
                    self.retain(RuntimeError('Native owner exited unexpectedly; no replacement'))
                if self.session and self.session.rotation_due():
                    try:
                        self.session.close(); self.session = None
                    except BaseException as error:
                        self.retain(error)
                continue
            folder = STATE / 'requests' / token
            try:
                began = time.perf_counter_ns()
                try:
                    tensor, _, rgb_sha = exact_preprocess(raw, self.dataset)
                    image = self.np.load(io.BytesIO(tensor), allow_pickle=False)
                except (ValueError, OSError) as error:
                    create(folder / 'result.json', encoded({**self.ledger.get(token), 'stage': 'REJECTED_IMAGE_NO_GRAPH', 'reason': str(error)}))
                    continue
                preprocessing = (time.perf_counter_ns()-began)/1e6
                input_persistence_started = time.perf_counter_ns()
                create(folder / 'input.npy', tensor)
                input_persistence_ms = (time.perf_counter_ns()-input_persistence_started)/1e6
                lifecycle_started = time.perf_counter_ns()
                if self.session and self.session.rotation_due():
                    self.session.close(); self.session = None
                if self.session is None:
                    self.session = Session(self)
                    self.session.initialize()
                lifecycle_ms = (time.perf_counter_ns()-lifecycle_started)/1e6
                result = self.session.infer(image, folder)
                result.update(request_id=token, image_sha256=pin(raw)['sha256'], input_sha256=pin(tensor)['sha256'],
                              decoded_RGB_sha256=rgb_sha, preprocessing_ms=preprocessing,
                              request_work_ms=(time.perf_counter_ns()-began)/1e6)
                result.update(input_persistence_ms=input_persistence_ms, session_lifecycle_ms=lifecycle_ms,
                              request_other_ms=result['request_work_ms']-preprocessing-input_persistence_ms-lifecycle_ms-result['Session_infer_ms'])
                create(folder / 'result.json', encoded(result))
            except BaseException as error:
                try:
                    create(folder / 'result.json', encoded({**self.ledger.get(token), 'stage': 'RETAINED_NO_REPLAY', 'reason': str(error)}))
                finally:
                    self.retain(error)
            finally:
                with self.mutex:
                    self.busy = False
                self.pending.task_done()
        if not self.fault and self.session:
            try:
                self.session.close(); self.session = None
                self.cpu = None
                self.update('STOPPED_NORMAL_OWNER_CLOSED', device_closed=True)
            except BaseException as error:
                self.retain(error)

    def retain(self, error):
        self.fault = True
        self.update('RETAINED_FAULT_NO_RETRY', reason=type(error).__name__+': '+str(error))
        # Keep strong Runner/owner references, companion lock and cooperative
        # board lock. Do not run destructor, STOP, reset or a replacement owner.
        while True:
            time.sleep(5)


def serve():
    import grp
    need(sys.platform == 'linux' and os.geteuid() == 0 and os.uname().machine == 'aarch64', 'KV260 root ARM Linux only')
    os.umask(0o077)
    need(sys.argv == [str(APP / 'backend.py'), 'serve'], 'installed hardware service only')
    need(Path('/proc/self/cgroup').read_text() == '0::/system.slice/opt50-demo-hardware.service\n', 'hardware unit required')
    config = verify_install()
    service = Service(config)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: STOP.set())
    try:
        service.initialize()
    except BaseException as error:
        service.retain(error)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(RUN / 'backend.sock'))
    os.chown(RUN / 'backend.sock', 0, grp.getgrnam('opt50demo').gr_gid)
    os.chmod(RUN / 'backend.sock', 0o660)
    listener.listen(8); listener.settimeout(1)
    worker = threading.Thread(target=service.worker, daemon=False)
    worker.start()
    while not STOP.is_set():
        try:
            stream, _ = listener.accept()
        except socket.timeout:
            continue
        with stream:
            stream.settimeout(10)
            try:
                _, uid, _ = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                import pwd
                need(uid in (0, pwd.getpwnam('opt50demo').pw_uid), 'unknown API peer')
                header, body = receive(stream)
                action = header.get('action')
                if action == 'status':
                    need(not body, 'status has no body')
                    value = dict(service.status, busy=service.busy)
                elif action == 'result':
                    need(not body, 'result has no body')
                    value = service.ledger.get(header.get('request_id')) or {'stage': 'UNKNOWN_REQUEST'}
                elif action == 'history':
                    need(not body, 'history has no body')
                    value = {'records': service.ledger.history(), 'limit': 100,
                             'scope': 'Saved request receipts; not fresh hardware inspection'}
                elif action == 'infer':
                    value = service.submit(header.get('request_id'), body)
                else:
                    raise ValueError('unknown action')
                send(stream, {'ok': True, 'value': value})
            except (ValueError, OSError) as error:
                try:
                    send(stream, {'ok': False, 'error': str(error)})
                except OSError:
                    pass
    listener.close()
    worker.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['serve'])
    parser.parse_args()
    serve()
