"""OPT50 fd96539d preparation and LOAD-ONLY guardian. No CSR or START implementation.

audit is read-only. check writes a fresh root-owned plan and tool snapshots.
run requires a separate explicit hardware authorization and a guarded systemd
service. A successful load intentionally retains the lock for human review.
There is no automatic recovery, stop, unload, retry, clock write or reboot.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid

import artifacts
import board
import durable
import firmware

HERE = Path(__file__).resolve().parent
PACKAGE = Path('/home/ubuntu/fpga_project/opt50_integration/prepare_20261024_fd96539d_v1')
CASES = Path('/home/ubuntu/fpga_project/opt50_integration/data/axi_cases')
RECORDS = Path('/var/lib/opt50-loadonly-fd96539d-v3')
LOCK = Path('/home/ubuntu/fpga_project/deploy/.f8_board.lock')
FIRMWARE = Path('/usr/lib/firmware/xilinx') / board.APP
CODE = ('window.py', 'artifacts.py', 'board.py', 'durable.py', 'board_snapshot.py', 'test_durable.py',
        'firmware.py', 'test_firmware.py', 'test_window_reentry.py', 'test_controller.py',
        'test_artifacts.py', 'test_bindings.py', 'README.md')
SCOPE = 'Temporary load only; no CSR/XRT device access, no START, no automatic restoration'
SYSTEMD = {'Type': 'exec', 'ExitType': 'cgroup', 'KillMode': 'process',
           'SendSIGKILL': 'no', 'TimeoutStopUSec': 'infinity', 'RuntimeMaxUSec': 'infinity',
           'Restart': 'no', 'RemainAfterExit': 'yes', 'OOMPolicy': 'continue'}
stop_requested = False


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical_boot(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value)
            is not None, 'canonical boot UUID required')
    require(str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0, 'canonical nonzero boot UUID required')
    return value


def assert_plan_fresh(plan):
    """Bind a mutation to the boot frozen by check, never a compiled boot ID."""
    boot = canonical_boot(plan.get('boot_id'))
    require(canonical_boot(Path('/proc/sys/kernel/random/boot_id').read_text().strip()) == boot,
            'old boot plan refused; boot changed')
    created = plan.get('created_monotonic')
    require(type(created) in (int, float) and math.isfinite(created) and created >= 0 and
            type(plan.get('expires_seconds')) is int and plan['expires_seconds'] == 900,
            'invalid plan monotonic/900-second lifetime')
    age = time.monotonic() - created
    require(math.isfinite(age) and 0 <= age < 900, 'plan expired; do not reuse')


def json_read(path):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'duplicate JSON field')
            out[key] = value
        return out
    path = Path(path)
    raw = path.read_bytes()
    require(len(raw) <= 16 * 1024 * 1024, 'oversized JSON')
    return json.loads(raw, object_pairs_hook=pairs)


def no_links(path):
    path = Path(path).absolute()
    require(all(not p.is_symlink() for p in (path, *path.parents)), 'symlink path refused')
    return path


def private(path, directory=False):
    path = no_links(path)
    info = path.stat()
    require(info.st_uid == 0 and not info.st_mode & 0o077 and
            (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
            'root-owned/private regular plan or directory required')
    return path


def tools_manifest(folder=HERE):
    folder = no_links(folder)
    manifest = json_read(folder / 'tools_manifest.json')
    require(manifest['schema'] == 'opt50_loadonly_tools_fd96539d_v3' and
            manifest['hardware_executed'] is False and set(manifest['files']) == set(CODE),
            'unreviewed tool inventory')
    for name in CODE:
        require(board.record(no_links(folder / name)) == manifest['files'][name], 'tool source changed: ' + name)
    return {'manifest': board.record(folder / 'tools_manifest.json'), 'files': manifest['files']}


@contextlib.contextmanager
def locked():
    import fcntl
    path = no_links(LOCK)
    require(path.is_file(), 'existing cooperative board lock is missing')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield stream


def commands(plan_path, token):
    unit = 'opt50-loadonly-v3-' + token
    script = plan_path.parent / 'tools/window.py'
    flags = ['--property=' + key + '=' + value for key, value in (
        ('Type', 'exec'), ('ExitType', 'cgroup'), ('KillMode', 'process'),
        ('SendSIGKILL', 'no'), ('TimeoutStopSec', 'infinity'), ('RuntimeMaxSec', 'infinity'),
        ('Restart', 'no'), ('RemainAfterExit', 'yes'), ('OOMPolicy', 'continue'))]
    run = ['sudo', 'systemd-run', '--unit=' + unit, *flags, '/usr/bin/python3', '-B', str(script),
           'run', '--plan', str(plan_path), '--authorized-load-only']
    status = ['sudo', '/usr/bin/python3', '-B', str(script), 'status', '--plan', str(plan_path)]
    return unit, shlex.join(run), shlex.join(status)


def check(package, cases, uart_confirmed):
    board.root_linux()
    require(uart_confirmed, 'confirm an independent UART session is logging before creating a load-only plan')
    tool_files = tools_manifest()
    command_tools = board.tools()
    with locked():
        inventory = artifacts.audit(package, cases)
        before = board.collect(cases, command_tools)
        board.baseline(before)
        boot = canonical_boot(board.observed(before, 'boot_id'))
        expected_firmware = firmware.expected_from_audit(package, inventory, board.APP)
        firmware_view = firmware.inspect(FIRMWARE, expected_firmware, board.APP)
        # Freeze the actual boot only after a second complete starter snapshot.
        # No plan, tool copy or firmware write may precede this consistency gate.
        confirmed = board.collect(cases, command_tools)
        board.same_baseline(before, confirmed)
        require(canonical_boot(board.observed(confirmed, 'boot_id')) == boot,
                'boot changed between check snapshots; no plan created')
        if not RECORDS.exists():
            durable.mkdir_new(RECORDS)
        private(RECORDS, True)
        token = secrets.token_hex(6)
        folder = RECORDS / ('plan_' + token)
        durable.mkdir_new(folder)
        code = folder / 'tools'
        durable.mkdir_new(code)
        for name in CODE:
            durable.copy_new(HERE / name, code / name, tool_files['files'][name])
        durable.copy_new(HERE / 'tools_manifest.json', code / 'tools_manifest.json', tool_files['manifest'])
        plan_path = folder / 'plan.json'
        unit, run_command, status_command = commands(plan_path, token)
        plan = {'schema': 'opt50_loadonly_plan_fd96539d_v3', 'token': token, 'unit': unit,
                'boot_id': boot, 'created_monotonic': time.monotonic(),
                'expires_seconds': 900, 'package': str(Path(package).resolve()), 'cases': str(Path(cases).resolve()),
                'before': before, 'artifacts': inventory, 'tool_files': tool_files,
                'firmware': firmware_view,
                'command_tools': command_tools, 'uart_logging_operator_confirmed': True,
                'scope': SCOPE,
                'hardware_executed': False, 'PL_START_commands_issued': 0}
        durable.new_json(plan_path, plan)
        return {'status': 'LOADONLY_REENTRY_PLAN_CREATED_NO_HARDWARE_CHANGE', 'plan': str(plan_path),
                'boot_id': plan['boot_id'], 'expires_seconds': 900, 'scope': plan['scope'],
                'firmware_disposition': firmware_view['disposition'],
                'writes': 'New root-owned plan/tool copies under /var/lib; source package unchanged',
                'next_requires_separate_hardware_permission': run_command, 'status_command': status_command,
                'warning': 'Successful run remains resident holding the lock. Do not force-stop it; later steps require review.'}


def load_plan(path, *, fresh):
    path = Path(path).absolute()
    require(path.name == 'plan.json' and path.parent.parent == RECORDS and
            re.fullmatch(r'plan_[a-f0-9]{12}', path.parent.name), 'unexpected plan location')
    private(RECORDS, True)
    private(path.parent, True)
    private(path)
    plan = json_read(path)
    token = path.parent.name[5:]
    require(plan['schema'] == 'opt50_loadonly_plan_fd96539d_v3' and plan['token'] == token and
            plan['unit'] == 'opt50-loadonly-v3-' + token and plan['expires_seconds'] == 900 and
            canonical_boot(plan.get('boot_id')) == board.observed(plan['before'], 'boot_id') and
            plan.get('scope') == SCOPE and type(plan.get('PL_START_commands_issued')) is int and
            plan['PL_START_commands_issued'] == 0 and
            plan['hardware_executed'] is False and plan['uart_logging_operator_confirmed'] is True,
            'plan identity/scope differs')
    expected_firmware = firmware.expected_from_audit(plan['package'], plan['artifacts'], board.APP)
    require(type(plan.get('firmware')) is dict and plan['firmware'].get('expected') == expected_firmware and
            plan['firmware'].get('directory') == str(FIRMWARE), 'frozen firmware staging identity differs')
    if fresh:
        board.root_linux()
        assert_plan_fresh(plan)
        require(HERE == path.parent / 'tools', 'run only the root-owned copied guardian')
        private(HERE, True)
        require(tools_manifest() == plan['tool_files'], 'root-owned tool snapshot differs')
        for name in (*CODE, 'tools_manifest.json'):
            private(HERE / name)
        board.validate_tools(plan['command_tools'])
    return plan


def require_service(plan):
    require(str(Path(sys.executable).resolve(strict=True)) == plan['command_tools']['python3']['target'],
            'Python interpreter differs from pinned system tool')
    unit = plan['unit'] + '.service'
    cgroup = Path('/proc/self/cgroup').read_text()
    require(any(line.endswith('/' + unit) for line in cgroup.splitlines()), 'guarded systemd service required')
    run = subprocess.run([plan['command_tools']['systemctl']['entry'], 'show', unit,
                          '--property=' + ','.join((*SYSTEMD, 'MainPID'))], capture_output=True,
                         text=True, timeout=20, stdin=subprocess.DEVNULL, check=False)
    fields = dict(line.split('=', 1) for line in run.stdout.splitlines() if '=' in line)
    require(run.returncode == 0 and all(fields.get(k) == v for k, v in SYSTEMD.items()) and
            fields.get('MainPID') == str(os.getpid()), 'service guard properties differ')


class Guardian:
    def __init__(self, plan, folder, lock):
        self.plan, self.folder, self.lock = plan, folder, lock
        self.journal = durable.Journal(folder / 'events')
        self.changed_or_uncertain = False
        self.child = None
        self.index = 0
        self.staged_firmware = None
        self.state = {'schema': 'opt50_loadonly_status_fd96539d_v3', 'boot_id': plan['boot_id'],
                      'unit': plan['unit'], 'guardian_pid': os.getpid(), 'run_dir': str(folder),
                      'PL_START_commands_issued': 0, 'physical_PL_starts': 'NOT_MEASURED_NO_CSR_ACCESS',
                      'CSR': 'BLOCKED', 'START': 'BLOCKED', 'saved_status_only': True,
                      'source_package_modified': False, 'automatic_restoration': 'DISABLED'}

    def firmware_barrier(self):
        require(self.staged_firmware is not None, 'verified firmware staging must precede management actions')
        expected = firmware.expected_from_audit(self.plan['package'], self.plan['artifacts'], board.APP)
        firmware.assert_same(FIRMWARE, self.staged_firmware, expected, board.APP)

    def update(self, stage, **details):
        self.state.update(stage=stage, heartbeat_monotonic=time.monotonic(), **details)
        self.journal.append('state', stage, state=self.state.copy())
        durable.replace_json(self.folder / 'status.json', self.state)

    def safety_barrier(self, phase):
        descriptor = os.fstat(self.lock.fileno())
        path = no_links(LOCK).stat()
        require((descriptor.st_dev, descriptor.st_ino) == (path.st_dev, path.st_ino),
                'cooperative board lock path was replaced')
        full = board.query(self.plan['command_tools'], 'dmesg')
        users = board.snapshot_api().device_users(Path('/proc'))
        self.journal.append('safety_barrier', phase, kernel_log=full, occupancy=users)
        require(not board.SEVERE.search(full), 'severe kernel error; do not issue next management action')
        require(users['coverage_complete'] and users['error_count'] == 0 and
                users['observed_device_fds'] == 0 and not users['users'], 'board device became busy/incompletely observed')

    def command(self, phase, name, *arguments):
        require(not stop_requested, 'stop requested before next action')
        board.validate_tools(self.plan['command_tools'])
        assert_plan_fresh(self.plan)
        allowed = {'REMOVE_STARTER': ('dfx-mgr-client', '-remove', '0'),
                   'LOAD_DRIVER': ('insmod', str(self.folder / 'zocl_candidate.ko')),
                   'LOAD_OPT50': ('dfx-mgr-client', '-load', board.APP),
                   'SETTLE': ('udevadm', 'settle', '--timeout=5')}
        require((name, *map(str, arguments)) == allowed.get(phase), 'hardware command not allowlisted')
        self.firmware_barrier()
        self.safety_barrier('BEFORE_' + phase)
        self.index += 1
        prefix = self.folder / ('command_%02d' % self.index)
        argv = [self.plan['command_tools'][name]['entry'], *map(str, arguments)]
        with prefix.with_suffix('.stdout').open('xb', buffering=0) as out, \
                prefix.with_suffix('.stderr').open('xb', buffering=0) as err:
            os.fsync(out.fileno()); os.fsync(err.fileno()); durable.fsync_dir(self.folder)
            self.journal.append('command', 'INTENT', action_stage=phase, argv=argv,
                                stdout=str(prefix.with_suffix('.stdout')), stderr=str(prefix.with_suffix('.stderr')))
            self.update(phase + '_INTENT_DURABLE')
            require(not stop_requested, 'stop requested after durable intent; command not started')
            # Recheck the actual boot AND expiry after durable intent, directly
            # before the only management-process mutation point.
            assert_plan_fresh(self.plan)
            # From this point Popen failure is also uncertain: never drop the lock.
            self.changed_or_uncertain = True
            self.child = subprocess.Popen(argv, cwd=self.folder, stdin=subprocess.DEVNULL,
                                          stdout=out, stderr=err,
                                          pass_fds=(self.lock.fileno(),),
                                          env={'PATH': board.SAFE_PATH, 'LANG': 'C.UTF-8', 'HOME': '/root'})
            self.journal.append('command', 'SPAWNED', action_stage=phase, pid=self.child.pid, argv=argv)
            started = last = time.monotonic()
            overdue = False
            while self.child.poll() is None:
                os.fsync(out.fileno()); os.fsync(err.fileno())
                now = time.monotonic()
                overdue = overdue or now - started > 90
                if now - last >= 5:
                    self.update('RETAINED_COMMAND_RUNNING' if now - started > 90 else phase + '_RUNNING',
                                child_pid=self.child.pid, elapsed_seconds=now - started,
                                warning='No kill/timeout recovery; keep process and lock, inspect UART')
                    last = now
                time.sleep(1)
            code = self.child.returncode
            os.fsync(out.fileno()); os.fsync(err.fileno())
            self.journal.append('command', 'RETURNED', action_stage=phase, pid=self.child.pid, returncode=code,
                                stdout=board.record(prefix.with_suffix('.stdout')),
                                stderr=board.record(prefix.with_suffix('.stderr')))
            self.child = None
            require(code == 0, 'management command failed; state not safe to retry: ' + phase)
            require(not overdue, 'management command returned after its observation budget; manual review required')
            self.safety_barrier('AFTER_' + phase)

    def stage_files(self):
        assert_plan_fresh(self.plan)
        paths = self.plan['artifacts']['source_paths']
        for source_key, dest, mode in (
                ('firmware', 'candidate.bit.bin', 0o600), ('dtbo', 'candidate.dtbo', 0o600),
                ('metadata', 'metadata.xclbin', 0o600), ('runtime', 'runtime', 0o700),
                ('xrt_ini', 'xrt.ini', 0o600)):
            source = Path(paths[source_key])
            relative = source.relative_to(Path(self.plan['package'])).as_posix()
            expected = self.plan['artifacts']['files'][relative]
            self.journal.append('snapshot', 'INTENT', source=str(source), destination=dest, expected=expected)
            durable.copy_new(source, self.folder / dest, expected, mode=mode)
            self.journal.append('snapshot', 'COMMITTED', destination=dest, expected=expected)
        driver = board.record(board.DRIVER)
        require(driver['sha256'] == board.DRIVER_SHA, 'candidate driver changed')
        durable.copy_new(board.DRIVER, self.folder / 'zocl_candidate.ko', driver)
        shell = Path(self.plan['package']) / 'firmware/shell.json'
        durable.copy_new(shell, self.folder / 'shell.json', self.plan['artifacts']['files']['firmware/shell.json'])
        expected = firmware.expected_from_audit(self.plan['package'], self.plan['artifacts'], board.APP)
        sources = {board.APP + '.bit.bin': self.folder/'candidate.bit.bin',
                   board.APP + '.dtbo': self.folder/'candidate.dtbo', 'shell.json': self.folder/'shell.json'}
        self.journal.append('firmware_directory', 'INTENT', destination=str(FIRMWARE),
                            disposition=self.plan['firmware']['disposition'], expected=expected)
        assert_plan_fresh(self.plan)
        self.staged_firmware = firmware.stage(FIRMWARE, self.plan['firmware'], expected,
                                             board.APP, sources, durable)
        self.journal.append('firmware_directory', 'COMMITTED', destination=str(FIRMWARE),
                            observation=self.staged_firmware,
                            existing_firmware_modified=False)

    def hold(self, stage, reason):
        """Keep cooperative lock, evidence and any child alive until separately reviewed."""
        last = None
        while True:
            try:
                if last is None or time.monotonic() - last >= 30:
                    self.update(stage, reason=reason, child_pid=None if self.child is None else self.child.pid,
                                warning='Do not force-stop, retry, unload, reset or reboot; no automatic next stage')
                    last = time.monotonic()
                time.sleep(5)
            except BaseException:
                # A disk/status/signal failure must not release the guardian lock.
                try:
                    os.write(2, b'RETAINED: evidence write failed; lock/process preserved\n')
                except BaseException:
                    pass

    def execute(self):
        try:
            self.update('PREPARING_ROOT_COPIES')
            self.stage_files()
            require(artifacts.audit(Path(self.plan['package']), Path(self.plan['cases'])) == self.plan['artifacts'],
                    'sources changed while taking root copies')
            before = board.collect(Path(self.plan['cases']), self.plan['command_tools'])
            board.same_baseline(self.plan['before'], before)
            self.update('FILES_DURABLE_NO_HARDWARE_CHANGE')
            self.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
            require(board.snapshot_api().list_names(Path('/sys/kernel/config/device-tree/overlays')) == [],
                    'starter did not disappear')
            self.command('LOAD_DRIVER', 'insmod', str(self.folder / 'zocl_candidate.ko'))
            require(Path('/sys/module/zocl/srcversion').read_text().strip() == board.SRCVERSION,
                    'loaded driver identity differs')
            self.command('LOAD_OPT50', 'dfx-mgr-client', '-load', board.APP)
            self.command('SETTLE', 'udevadm', 'settle', '--timeout=5')
            after = board.collect(Path(self.plan['cases']), self.plan['command_tools'])
            durable.new_json(self.folder / 'after_load_observations.json', after)
            result = board.loaded(after, self.plan['boot_id'])
            self.hold('LOAD_ONLY_OBSERVED_AWAITING_REVIEW', result)
        except BaseException as error:
            reason = type(error).__name__ + ': ' + str(error)
            if self.changed_or_uncertain:
                self.hold('RETAINED_STATE_UNCERTAIN', reason)
            self.update('REFUSED_BEFORE_HARDWARE_CHANGE', reason=reason,
                        warning='Any newly staged firmware files are preserved; no automatic cleanup/retry')
            return 1


def request_stop(*_):
    global stop_requested
    stop_requested = True  # Do not interrupt MMIO/management actions or release a loaded guardian.


def run(path, authorized):
    board.root_linux()
    require(authorized, 'separate explicit --authorized-load-only permission required')
    plan = load_plan(path, fresh=True)
    require_service(plan)
    with locked() as lock:
        require(artifacts.audit(Path(plan['package']), Path(plan['cases'])) == plan['artifacts'], 'artifacts changed')
        board.same_baseline(plan['before'], board.collect(Path(plan['cases']), plan['command_tools']))
        expected = firmware.expected_from_audit(plan['package'], plan['artifacts'], board.APP)
        firmware.assert_same(FIRMWARE, plan['firmware'], expected, board.APP)
        assert_plan_fresh(plan)
        folder = Path(path).parent / 'execution'
        durable.mkdir_new(folder)  # Atomic single-use claim. Never resume this plan.
        durable.mkdir_new(folder / 'events')
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, request_stop)
        return Guardian(plan, folder, lock).execute()


def status(path):
    plan = load_plan(path, fresh=False)
    state = Path(path).parent / 'execution/status.json'
    result = json_read(private(state)) if state.exists() else {'stage': 'NOT_STARTED'}
    result.update(saved_status_only=True, plan_boot_id=plan['boot_id'],
                  warning='Saved receipt only; no new hardware inspection. Never retry/kill/unload from this status alone.')
    return result


def public_audit(value):
    return {'status': value['status'], 'metadata_uuid': value['identity']['metadata_uuid'],
            'observer_tags': value['identity']['observer_tags'], 'package_files_checked': len(value['files']),
            'xclbin_sections': sorted(value['identity']['section_files']),
            'dtbo': value['dtbo'], 'case_payloads_checked': value['cases'].get('payloads_checked', 0),
            'previous_offline_jobs': value['offline_regression']['jobs'],
            'previous_offline_compared_INT32_outputs': value['offline_regression']['compared_INT32_outputs'],
            'physical_PL_starts': 0, 'board': 'NOT_RUN',
            'next': 'Review audit and POSIX fsync test before creating a plan; no load permission granted'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('audit', 'check'):
        item = sub.add_parser(action)
        item.add_argument('--package', type=Path, default=PACKAGE)
        item.add_argument('--cases', type=Path, default=CASES)
        if action == 'check':
            item.add_argument('--uart-log-confirmed', action='store_true')
    for action in ('run', 'status'):
        item = sub.add_parser(action)
        item.add_argument('--plan', type=Path, required=True)
        if action == 'run':
            item.add_argument('--authorized-load-only', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'run':
        return run(args.plan, args.authorized_load_only)
    if args.action == 'audit':
        tools_manifest()
        value = public_audit(artifacts.audit(args.package, args.cases))
    elif args.action == 'check':
        value = check(args.package, args.cases, args.uart_log_confirmed)
    else:
        value = status(args.plan)
    print(json.dumps(value, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit('REFUSED: ' + str(error))
