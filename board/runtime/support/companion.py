"""Versioned companion coordinator: PS reads only, never PL CSR or START.

The old guardian retains the original cooperative board lock. This distinct,
explicitly authorized protocol serializes new companions with one root-private
lock; it is NOT delegation or transfer of the old guardian's lock. A child that
may have attempted MMIO is never killed, retried, or automatically recovered.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import time
import types

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RECORDS = Path('/var/lib/opt50-first-access-fd96539d-v1')
LOCK = RECORDS / 'companion.lock'
OLD_PLAN = Path('/var/lib/opt50-fd96539d-v1/plan_73930607cf95/plan.json')
BOOT = '1db5ef5e-1934-4a3d-b374-692eb8f184be'
OLD_PID = 11420
OLD_START = 1101286
DURABLE_SHA = 'b2ac23fbebdabe2acadf3609fd492c8b7eb61064c299a7d2a32b027bc9be2537'
SERVICE = {'Type': 'exec', 'ExitType': 'cgroup', 'KillMode': 'process',
           'SendSIGKILL': 'no', 'TimeoutStopUSec': 'infinity', 'RuntimeMaxUSec': 'infinity',
           'Restart': 'no', 'RemainAfterExit': 'yes', 'OOMPolicy': 'continue'}
REVIEW_FILES = {'review.py', 'evidence.py', 'proc_checks.py', 'artifacts.py',
                'board.py', 'board_snapshot.py', 'README.md', 'review_manifest.json'}
REQUIRED = {'companion.py', 'probe_worker.py', 'ps_contract.py', 'durable.py'} | {
    'review_lib/' + name for name in REVIEW_FILES}
stop_requested = False


def require(ok, message):
    if not ok:
        raise ValueError(message)


def root_linux():
    import platform
    require(os.name == 'posix' and platform.system() == 'Linux' and
            platform.machine() == 'aarch64' and os.geteuid() == 0, 'ARM Linux/root required')


def no_links(path):
    path = Path(path).absolute()
    require(all(not p.is_symlink() for p in (path, *path.parents)), 'linked ordinary-file path refused')
    return path


def private_dir(path):
    info = no_links(path).stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
            'root private directory required')


def private(path, directory=False):
    """Validate an existing root-private ordinary path without creating it."""
    if directory:
        private_dir(path)
    else:
        read_file(path, private=True)
    return Path(path)


def read_file(path, private=False, limit=32 * 1024 * 1024):
    path = no_links(path)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'nonregular/oversized file')
    if private:
        require(before.st_uid == 0 and before.st_nlink == 1 and not before.st_mode & 0o077,
                'root private single-link file required')
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        require(key(os.fstat(stream.fileno())) == key(before), 'file replaced before read')
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and key(os.fstat(stream.fileno())) == key(before),
                'file changed during read')
    require(key(path.stat()) == key(before), 'file replaced after read')
    return raw


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def decode_json(raw):
    def pairs(items):
        out = {}
        for name, value in items:
            require(name not in out, 'duplicate JSON field')
            out[name] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def read_json(path, private=False):
    return decode_json(read_file(path, private, 16 * 1024 * 1024))


def verify_tools(folder=None):
    folder = HERE if folder is None else Path(folder)
    raw = read_file(folder / 'tools_manifest.json')
    value = decode_json(raw)
    require(value.get('schema') == 'opt50_first_access_tools_v1' and
            value.get('hardware_executed') is False and isinstance(value.get('files'), dict) and
            REQUIRED <= set(value['files']) and len(value['files']) <= 128,
            'unreviewed companion tool inventory')
    for name, expected in value['files'].items():
        relative = PurePosixPath(name)
        require(isinstance(name, str) and str(relative) == name and not relative.is_absolute() and
                all(p not in ('', '.', '..') for p in name.split('/')) and
                not any(c in name for c in '\\:\0\r\n'), 'unsafe tool path')
        require(isinstance(expected, dict) and set(expected) == {'bytes', 'sha256'} and
                type(expected['bytes']) is int and expected['bytes'] >= 0 and
                isinstance(expected['sha256'], str) and re.fullmatch('[0-9a-f]{64}', expected['sha256']),
                'invalid tool file identity')
        require(identity(read_file(folder.joinpath(*relative.parts))) == expected,
                'companion tool changed: ' + name)
    require(value['files']['durable.py']['sha256'] == DURABLE_SHA, 'durability helper changed')
    return {'manifest': identity(raw), 'files': value['files']}


def compiled_module(path, expected_sha=None):
    raw = read_file(path)
    require(expected_sha is None or identity(raw)['sha256'] == expected_sha, 'helper changed')
    module = types.ModuleType('_opt50_companion_' + Path(path).stem)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def durable_api():
    return compiled_module(HERE / 'durable.py', DURABLE_SHA)


def held_review():
    """Run frozen read-only review, never import the old window/loader."""
    folder = HERE / 'review_lib'
    # Local imports in the frozen review must resolve only its pinned copies.
    previous_path = sys.path[:]
    previous_modules = {name: sys.modules.pop(name, None) for name in ('evidence', 'proc_checks')}
    try:
        sys.path.insert(0, str(folder))
        module = compiled_module(folder / 'review.py')
        result = module.review()
    finally:
        sys.path[:] = previous_path
        for name, previous in previous_modules.items():
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous
    require(result.get('status') == 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START' and result.get('boot_id') == BOOT and
            result.get('guardian', {}).get('pid') == OLD_PID and
            result['guardian'].get('start_ticks') == OLD_START and
            result.get('CSR') == 'BLOCKED' and result.get('START') == 'BLOCKED',
            'held guardian review does not match the authorized attempt')
    return result


def validate_parent_service(plan, expected_pid):
    require(type(expected_pid) is int and expected_pid > 0, 'invalid coordinator PID')
    fields = (*SERVICE, 'ActiveState', 'SubState', 'MainPID')
    result = subprocess.run([plan['command_tools']['systemctl']['entry'], 'show', plan['unit'] + '.service',
                             '--property=' + ','.join(fields)], capture_output=True, text=True,
                            timeout=20, stdin=subprocess.DEVNULL, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 16384 and len(result.stderr) <= 8192,
            'coordinator service query failed')
    rows = [line.split('=', 1) for line in result.stdout.splitlines() if '=' in line]
    require(len({k for k, _ in rows}) == len(rows), 'duplicate coordinator service field')
    actual = dict(rows)
    require(all(actual.get(k) == v for k, v in SERVICE.items()) and actual.get('MainPID') == str(expected_pid) and
            actual.get('ActiveState') == 'active' and actual.get('SubState') in ('running', 'start'),
            'coordinator service guard differs')
    cgroup = Path('/proc/%d/cgroup' % expected_pid).read_text()
    require(any(line.endswith('/' + plan['unit'] + '.service') for line in cgroup.splitlines()),
            'coordinator service cgroup differs')
    argv = Path('/proc/%d/cmdline' % expected_pid).read_bytes().rstrip(b'\0').split(b'\0')
    expected = ['/usr/bin/python3', '-B', str(Path(plan['plan_path']).parent / 'tools/companion.py'),
                'run', '--plan', plan['plan_path'], '--authorized-ps-reads-with-held-guardian']
    require(argv == [x.encode() for x in expected], 'coordinator argv differs')
    return actual


def _lock_info():
    info = no_links(LOCK).stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
            not info.st_mode & 0o077, 'root private companion lock required')
    require(read_json(LOCK, private=True) == {'schema': 'opt50_first_access_global_companion_lock_v1'},
            'companion lock identity differs')
    return info


@contextlib.contextmanager
def companion_locked():
    import fcntl
    before = _lock_info()
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        actual = os.fstat(fd)
        require((actual.st_dev, actual.st_ino) == (before.st_dev, before.st_ino), 'companion lock was replaced')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)  # This closes only the NEW protocol's lock, never the old board lock.


def check_companion_fd(fd, parentpid, plan):
    """Verify inherited new-lock identity without taking or altering any lock."""
    root_linux()
    require(type(fd) is int and 0 <= fd <= 4096 and type(parentpid) is int and parentpid > 0,
            'invalid inherited companion lock/parent')
    require(os.getpid() == parentpid or os.getppid() == parentpid, 'worker parent identity differs')
    validate_parent_service(plan, parentpid)
    expected, actual = _lock_info(), os.fstat(fd)
    require(stat.S_ISREG(actual.st_mode) and (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino),
            'wrong inherited companion lock FD')
    require(os.readlink('/proc/self/fd/%d' % fd) == str(LOCK), 'inherited companion lock path differs')
    pattern = re.compile(r'^\s*\d+:\s+FLOCK\s+ADVISORY\s+WRITE\s+(\d+)\s+([0-9a-fA-F]+):([0-9a-fA-F]+):(\d+)\s+0\s+EOF\s*$')
    matches = []
    for line in Path('/proc/locks').read_text().splitlines():
        match = pattern.fullmatch(line)
        if match and (int(match[1]), int(match[2], 16), int(match[3], 16), int(match[4])) == (
                parentpid, os.major(actual.st_dev), os.minor(actual.st_dev), actual.st_ino):
            matches.append(line)
    require(len(matches) == 1, 'coordinator does not hold the new companion flock')
    return {'lock_path': str(LOCK), 'owner_pid': parentpid, 'inherited_fd': fd,
            'old_board_lock_transferred': False}


def validate_plan(plan, path):
    token = path.parent.name[5:]
    require(plan.get('schema') == 'opt50_first_access_plan_v1' and plan.get('token') == token and
            plan.get('unit') == 'opt50-first-access-' + token and plan.get('boot_id') == BOOT and
            plan.get('plan_path') == str(path) and plan.get('expires_seconds') == 900 and
            plan.get('guardian_pid') == OLD_PID and plan.get('guardian_start_ticks') == OLD_START and
            plan.get('uart_logging_operator_confirmed') is True and
            plan.get('scope') == 'PS_REGISTER_READS_ONLY_WITH_HELD_GUARDIAN' and
            plan.get('PL_CSR_accessed') is False and plan.get('START_issued') is False,
            'companion plan identity/scope differs')
    require(type(plan.get('created_monotonic')) in (int, float) and
            0 <= plan['created_monotonic'] < float('inf'), 'invalid plan monotonic time')


def load_plan(path, fresh):
    path = Path(path).absolute()
    require(path.name == 'plan.json' and path.parent.parent == RECORDS and
            re.fullmatch('plan_[a-f0-9]{12}', path.parent.name), 'unexpected companion plan path')
    private_dir(RECORDS); private_dir(path.parent); private_dir(path.parent / 'tools')
    plan = read_json(path, private=True)
    validate_plan(plan, path)
    require(verify_tools(path.parent / 'tools') == plan['tool_files'], 'root companion tool snapshot differs')
    for name in (*plan['tool_files']['files'], 'tools_manifest.json'):
        read_file(path.parent / 'tools' / name, private=True)
    if fresh:
        root_linux()
        require(HERE == path.parent / 'tools', 'execute only the root-owned copied companion')
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT, 'boot changed')
        require(0 <= time.monotonic() - plan['created_monotonic'] <= 900, 'plan expired; do not reuse')
    return plan


def previous_attempts_clear(exclude=None):
    """Refuse orphaned/uncertain attempts even if their OS lock disappeared.

    Call only while holding the one companion lock. A saved PS-only PASS is
    historical completion evidence, never a fresh hardware observation.
    """
    reviewed = []
    folders = sorted(RECORDS.glob('plan_*'))
    require(len(folders) <= 1000, 'too many companion plans; manual review required')
    for folder in folders:
        require(re.fullmatch('plan_[a-f0-9]{12}', folder.name), 'unrecognized companion plan folder')
        if exclude is not None and folder == Path(exclude).parent:
            continue
        private_dir(folder)
        execution = folder / 'execution'
        if not execution.exists():
            require(not execution.is_symlink(), 'linked prior execution refused')
            continue  # A check-only or expired, never-executed plan has no MMIO effects.
        private_dir(execution)
        plan = load_plan(folder / 'plan.json', fresh=False)
        state = read_json(execution / 'status.json', private=True)
        require(state.get('schema') == 'opt50_ps_only_status_v1' and state.get('boot_id') == BOOT and
                state.get('unit') == plan['unit'] and state.get('guardian_pid') == OLD_PID and
                state.get('old_board_lock_transferred') is False and
                state.get('PL_CSR_accessed') is False and state.get('START_issued') is False and
                state.get('physical_PL_starts') == 'NOT_MEASURED_NO_CSR_ACCESS',
                'prior execution scope differs; manual review required')
        stage = state.get('stage')
        require(stage in ('PASS_PS_ONLY_NOT_PL_CSR_OR_START', 'REFUSED_BEFORE_WORKER_NO_MMIO'),
                'prior companion execution unresolved; preserve and inspect manually')
        private_dir(execution / 'events')
        events = sorted((execution / 'events').glob('event_*.json'))
        require(0 < len(events) <= 10000, 'prior execution journal incomplete')
        values = []
        for index, event in enumerate(events, 1):
            require(event.name == 'event_%06d.json' % index, 'prior execution journal gap')
            value = read_json(event, private=True)
            require(value.get('schema') == 'opt50_durable_event_v1' and value.get('sequence') == index,
                    'prior execution journal identity differs')
            values.append(value)
        require(values[-1].get('action') == 'state' and values[-1].get('phase') == stage and
                values[-1].get('state') == state, 'prior final state/journal differ')
        worker = [event for event in values if event.get('action') == 'worker']
        if stage == 'REFUSED_BEFORE_WORKER_NO_MMIO':
            require(all(event.get('phase') == 'INTENT' for event in worker) and len(worker) <= 1,
                    'prior refused attempt may have spawned a worker')
        else:
            require(state.get('child_pid', 'missing') is None and
                    [event.get('phase') for event in worker] == ['INTENT', 'SPAWNED', 'RETURNED'] and
                    type(worker[-1].get('returncode')) is int and worker[-1]['returncode'] == 0 and
                    worker[-1].get('pid') == worker[-2].get('pid'),
                    'prior PASS lacks a complete reaped worker')
            for suffix in ('stdout', 'stderr'):
                require(identity(read_file(execution / ('worker.' + suffix), private=True)) == worker[-1][suffix],
                        'prior worker log differs')
            result = read_json(execution / 'ps_result.json', private=True)
            require(result == state.get('result') == decode_json(read_file(execution / 'worker.stdout', private=True)),
                    'prior PASS/result differs')
            compiled_module(folder / 'tools/ps_contract.py').validate_result(result)
            after = read_json(execution / 'held_after.json', private=True)
            require(after.get('status') == 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START' and after.get('boot_id') == BOOT and
                    after.get('guardian', {}).get('pid') == OLD_PID and
                    after['guardian'].get('start_ticks') == OLD_START,
                    'prior PASS lacks historical after-review')
        reviewed.append({'plan': str(folder / 'plan.json'), 'stage': stage,
                         'scope': 'Historical PS-only outcome; not fresh physical or CSR evidence'})
    return reviewed


def check(uart_confirmed):
    root_linux()
    require(uart_confirmed, 'independent UART logging confirmation required')
    tool_files = verify_tools()
    held = held_review()
    old_plan = read_json(OLD_PLAN, private=True)
    durable = durable_api()
    if not RECORDS.exists():
        durable.mkdir_new(RECORDS)
    private_dir(RECORDS)
    if not LOCK.exists():
        durable.new_json(LOCK, {'schema': 'opt50_first_access_global_companion_lock_v1'})
    with companion_locked():
        previous_attempts_clear()
        token = secrets.token_hex(6)
        folder = RECORDS / ('plan_' + token)
        durable.mkdir_new(folder); durable.mkdir_new(folder / 'tools')
        directories = sorted({PurePosixPath(name).parent.as_posix() for name in tool_files['files'] if '/' in name},
                             key=lambda item: (item.count('/'), item))
        for directory in directories:
            target = folder / 'tools' / directory
            for parent in reversed(target.parents):
                if parent.is_relative_to(folder / 'tools') and not parent.exists():
                    durable.mkdir_new(parent)
            if not target.exists():
                durable.mkdir_new(target)
        for name, expected in tool_files['files'].items():
            durable.copy_new(HERE / name, folder / 'tools' / name, expected)
        durable.copy_new(HERE / 'tools_manifest.json', folder / 'tools/tools_manifest.json', tool_files['manifest'])
        path = folder / 'plan.json'
        plan = {'schema': 'opt50_first_access_plan_v1', 'token': token, 'unit': 'opt50-first-access-' + token,
                'boot_id': BOOT, 'plan_path': str(path), 'expires_seconds': 900, 'created_monotonic': time.monotonic(),
                'guardian_pid': OLD_PID, 'guardian_start_ticks': OLD_START, 'held_review': held,
                'command_tools': old_plan['command_tools'], 'tool_files': tool_files,
                'uart_logging_operator_confirmed': True, 'scope': 'PS_REGISTER_READS_ONLY_WITH_HELD_GUARDIAN',
                'old_guardian_delegation': False, 'PL_CSR_accessed': False, 'START_issued': False}
        durable.new_json(path, plan)
    properties = ('Type=exec', 'ExitType=cgroup', 'KillMode=process', 'SendSIGKILL=no',
                  'TimeoutStopSec=infinity', 'RuntimeMaxSec=infinity', 'Restart=no',
                  'RemainAfterExit=yes', 'OOMPolicy=continue')
    command = ['sudo', 'systemd-run', '--unit=' + plan['unit'], *['--property=' + p for p in properties],
               '/usr/bin/python3', '-B', str(folder / 'tools/companion.py'), 'run', '--plan', str(path),
               '--authorized-ps-reads-with-held-guardian']
    return {'status': 'PS_ONLY_PLAN_CREATED_NO_MMIO_OR_HARDWARE_CHANGE', 'plan': str(path), 'boot_id': BOOT,
            'expires_seconds': 900, 'old_guardian_and_board_lock': 'PRESERVED_NOT_TRANSFERRED',
            'writes': 'New root-private companion lock, plan and copied tools only',
            'next_requires_separate_PS_MMIO_permission': shlex.join(command),
            'status_command': shlex.join(['sudo', '/usr/bin/python3', '-B', str(folder / 'tools/companion.py'),
                                          'status', '--plan', str(path)]),
            'PL_CSR_accessed': False, 'START_issued': False}


class Coordinator:
    def __init__(self, plan, lock, durable):
        self.plan, self.lock, self.durable = plan, lock, durable
        self.folder = Path(plan['plan_path']).parent / 'execution'
        self.journal = durable.Journal(self.folder / 'events')
        self.child = None
        self.possibly_accessed = False
        self.state = {'schema': 'opt50_ps_only_status_v1', 'boot_id': BOOT, 'unit': plan['unit'],
                      'coordinator_pid': os.getpid(), 'guardian_pid': OLD_PID, 'old_board_lock_transferred': False,
                      'scope': 'PS reads only; no PL CSR/START, no unload/restore/reload',
                      'PL_CSR_accessed': False, 'START_issued': False,
                      'physical_PL_starts': 'NOT_MEASURED_NO_CSR_ACCESS', 'saved_status_only': True}

    def update(self, stage, **details):
        self.state.update(stage=stage, heartbeat_monotonic=time.monotonic(), **details)
        self.journal.append('state', stage, state=self.state.copy())
        self.durable.replace_json(self.folder / 'status.json', self.state)

    def hold(self, reason):
        while True:
            try:
                self.update('RETAINED_PS_READ_STATE_UNCERTAIN', reason=reason,
                            child_pid=None if self.child is None else self.child.pid,
                            warning='Do not kill/retry/unload/reboot; old and new locks remain held')
                time.sleep(30)
            except BaseException:
                try:
                    os.write(2, b'RETAINED: preserve companion and old guardian; no automatic recovery\n')
                except BaseException:
                    pass

    def execute(self):
        try:
            self.update('REVIEWING_HELD_GUARDIAN_BEFORE_PS_READS')
            before = held_review()
            self.durable.new_json(self.folder / 'held_before.json', before)
            check_companion_fd(self.lock, os.getpid(), self.plan)
            require(not stop_requested, 'stop requested before worker; no MMIO attempted')
            require(0 <= time.monotonic() - self.plan['created_monotonic'] <= 900, 'plan expired before worker')
            argv = ['/usr/bin/python3', '-B', str(Path(self.plan['plan_path']).parent / 'tools/probe_worker.py'),
                    '--plan', self.plan['plan_path'], '--lock-fd', str(self.lock), '--parent-pid', str(os.getpid())]
            outpath, errpath = self.folder / 'worker.stdout', self.folder / 'worker.stderr'
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            with os.fdopen(os.open(outpath, flags, 0o600), 'wb', buffering=0) as out, \
                    os.fdopen(os.open(errpath, flags, 0o600), 'wb', buffering=0) as err:
                os.fsync(out.fileno()); os.fsync(err.fileno()); self.durable.fsync_dir(self.folder)
                self.journal.append('worker', 'INTENT', argv=argv, stdout=str(outpath), stderr=str(errpath),
                                    old_guardian_delegation=False, scope='PS_REGISTER_READS_ONLY')
                self.update('PS_READ_WORKER_INTENT_DURABLE')
                require(not stop_requested, 'stop requested after intent; worker not started')
                self.possibly_accessed = True  # Popen failure is also retained conservatively.
                self.child = subprocess.Popen(argv, cwd=self.folder, stdin=subprocess.DEVNULL,
                                              stdout=out, stderr=err, pass_fds=(self.lock,),
                                              env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/root'})
                self.journal.append('worker', 'SPAWNED', pid=self.child.pid, argv=argv)
                started = last = time.monotonic()
                overdue = False
                while self.child.poll() is None:
                    now = time.monotonic()
                    overdue = overdue or now - started > 90
                    os.fsync(out.fileno()); os.fsync(err.fileno())
                    if now - last >= 5:
                        self.update('RETAINED_PS_WORKER_RUNNING' if overdue else 'PS_WORKER_RUNNING',
                                    child_pid=self.child.pid, elapsed_seconds=now - started)
                        last = now
                    time.sleep(1)
                code = self.child.returncode
                child_pid = self.child.pid
                os.fsync(out.fileno()); os.fsync(err.fileno())
            self.journal.append('worker', 'RETURNED', pid=child_pid, returncode=code,
                                stdout=identity(read_file(outpath)), stderr=identity(read_file(errpath)))
            self.child = None
            require(code == 0 and not overdue, 'PS worker failed or returned after observation budget')
            result = decode_json(read_file(outpath))
            contract = compiled_module(HERE / 'ps_contract.py')
            contract.validate_result(result)
            self.durable.new_json(self.folder / 'ps_result.json', result)
            after = held_review()
            self.durable.new_json(self.folder / 'held_after.json', after)
            self.update('PASS_PS_ONLY_NOT_PL_CSR_OR_START', child_pid=None, result=result,
                        old_guardian_and_original_board_lock='PRESERVED_NOT_TRANSFERRED')
            return 0
        except BaseException as error:
            reason = type(error).__name__ + ': ' + str(error)
            if self.possibly_accessed:
                self.hold(reason)
            self.update('REFUSED_BEFORE_WORKER_NO_MMIO', reason=reason)
            return 1


def request_stop(*_):
    global stop_requested
    stop_requested = True  # Never interrupt a worker or release an uncertain MMIO owner.


def run(path, authorized):
    root_linux()
    require(authorized, 'separate explicit permission for PS reads while old guardian retains original lock required')
    plan = load_plan(path, fresh=True)
    validate_parent_service(plan, os.getpid())
    with companion_locked() as fd:
        previous_attempts_clear(exclude=path)
        held_review()
        durable = durable_api()
        folder = Path(path).parent / 'execution'
        durable.mkdir_new(folder); durable.mkdir_new(folder / 'events')
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, request_stop)
        return Coordinator(plan, fd, durable).execute()


def status(path):
    plan = load_plan(path, fresh=False)
    file = Path(path).parent / 'execution/status.json'
    result = read_json(file, private=True) if file.exists() else {'stage': 'NOT_STARTED'}
    result.update(saved_status_only=True, plan_boot_id=plan['boot_id'],
                  warning='Saved receipt only; not new hardware inspection or recovery permission')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    check_args = actions.add_parser('check')
    check_args.add_argument('--uart-log-confirmed', action='store_true')
    for action in ('run', 'status'):
        item = actions.add_parser(action)
        item.add_argument('--plan', type=Path, required=True)
        if action == 'run':
            item.add_argument('--authorized-ps-reads-with-held-guardian', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'run':
            return run(args.plan, args.authorized_ps_reads_with_held_guardian)
        result = check(args.uart_log_confirmed) if args.action == 'check' else status(args.plan)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'COMPANION_REFUSED_NO_AUTOMATIC_RECOVERY', 'error': str(error),
                          'PL_CSR_accessed': False, 'START_issued': False}, ensure_ascii=True, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
