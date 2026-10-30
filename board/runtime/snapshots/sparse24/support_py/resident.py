"""Read-only resident OPT50/guardian proof for the new B24 layer02 window.

Never imports a loader, opens a device, maps MMIO, signals a process or changes
firmware. The cooperative board lock stays owned by the existing load guardian.
Only companion_locked() acquires the existing, separate global companion lock.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import time
import types


HERE = Path(__file__).resolve().parent
OLD_PLAN = None  # Configured only after canonical path, private-file and current-boot checks.
BOOT = None
KERNEL = '6.8.0-1015-xilinx'
GUARDIAN_UNIT = None
BOARD_LOCK = Path('/home/ubuntu/fpga_project/deploy/.f8_board.lock')
LOCK = Path('/var/lib/opt50-first-access-fd96539d-v1/companion.lock')
DURABLE_SHA = 'b2ac23fbebdabe2acadf3609fd492c8b7eb61064c299a7d2a32b027bc9be2537'
LOAD_MANIFEST = None
LOAD_TOOLS_SCHEMA = None
LOAD_PLAN_SCHEMA = None
LOAD_STATUS_SCHEMA = None
LOAD_SCOPE = 'Temporary load only; no CSR/XRT device access, no START, no automatic restoration'
LOAD_CODE = {'window.py', 'artifacts.py', 'board.py', 'durable.py', 'board_snapshot.py',
             'test_durable.py', 'firmware.py', 'test_firmware.py', 'test_window_reentry.py',
             'test_controller.py', 'test_artifacts.py', 'test_bindings.py', 'README.md'}
PROFILES = {
    '/var/lib/opt50-loadonly-fd96539d-v2': {
        'version': 'v2', 'unit_prefix': 'opt50-loadonly-v2-',
        'manifest': {'bytes': 1952, 'sha256': '58d1fd2e90db76e74024126f38f05cd0640abfdfad63f020a0c471d964942a98'},
        'tools_schema': 'opt50_loadonly_tools_fd96539d_v2',
        'plan_schema': 'opt50_loadonly_plan_fd96539d_v2',
        'status_schema': 'opt50_loadonly_status_fd96539d_v2',
        'code': frozenset(LOAD_CODE),
    },
    '/var/lib/opt50-loadonly-fd96539d-v3': {
        'version': 'v3', 'unit_prefix': 'opt50-loadonly-v3-',
        'manifest': {'bytes': 1953, 'sha256': 'b099d6206eea122c6cff4c5af937091d35cb1bc3ae50000b38c34612569d505a'},
        'tools_schema': 'opt50_loadonly_tools_fd96539d_v3',
        'plan_schema': 'opt50_loadonly_plan_fd96539d_v3',
        'status_schema': 'opt50_loadonly_status_fd96539d_v3',
        'code': frozenset(LOAD_CODE),
    },
}
SERVICE = {'Type': 'exec', 'ExitType': 'cgroup', 'KillMode': 'process',
           'SendSIGKILL': 'no', 'TimeoutStopUSec': 'infinity', 'RuntimeMaxUSec': 'infinity',
           'Restart': 'no', 'RemainAfterExit': 'yes', 'OOMPolicy': 'continue'}
PHASES = ('REMOVE_STARTER', 'LOAD_DRIVER', 'LOAD_OPT50', 'SETTLE')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def root_linux():
    require(os.name == 'posix' and platform.system() == 'Linux' and
            platform.machine() == 'aarch64' and os.geteuid() == 0 and
            platform.release() == KERNEL, 'fixed ARM Linux/kernel/root required')


def no_links(path):
    original = Path(path)
    require('..' not in original.parts, 'parent traversal refused')
    path = original.absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and
                not getattr(info, 'st_file_attributes', 0) & 0x400, 'linked ordinary-file path refused')
    return path


def private_dir(path):
    path = no_links(path)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
            'root private directory required')
    return path


def _key(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, private=False, limit=32*1024*1024):
    path = no_links(path)
    before = path.lstat()
    require(type(limit) is int and 0 < limit <= 64*1024*1024 and
            stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit,
            'bounded regular input required')
    if private:
        require(before.st_uid == 0 and before.st_nlink == 1 and not before.st_mode & 0o077,
                'root private single-link input required')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        require(_key(os.fstat(stream.fileno())) == _key(before), 'input replaced before read')
        raw = stream.read(limit+1)
        require(len(raw) == before.st_size and _key(os.fstat(stream.fileno())) == _key(before),
                'input changed during read')
    require(_key(path.lstat()) == _key(before), 'input replaced after read')
    return raw


def private(path, directory=False):
    if directory:
        return private_dir(path)
    read_file(path, private=True)
    return Path(path)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def decode_json(raw):
    def pairs(items):
        value = {}
        for name, item in items:
            require(name not in value, 'duplicate JSON field')
            value[name] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def read_json(path, private=False):
    return decode_json(read_file(path, private=private, limit=16*1024*1024))


def compiled_module(path, expected_sha=None):
    raw = read_file(path)
    require(expected_sha is None or identity(raw)['sha256'] == expected_sha, 'compiled helper identity differs')
    module = types.ModuleType('_opt50_resident_' + Path(path).stem)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def durable_api():
    return compiled_module(HERE/'durable.py', DURABLE_SHA)


def _pin(value):
    require(type(value) is dict and set(value) == {'bytes', 'sha256'} and
            type(value['bytes']) is int and 0 <= value['bytes'] <= 32*1024*1024 and
            type(value['sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['sha256']),
            'invalid bounded content identity')
    return value


def _guardian_log_identity(info):
    return (*_key(info), info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            getattr(info, 'st_file_attributes', 0))


def _guardian_log_info(info, expected_bytes):
    require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and
            not getattr(info, 'st_file_attributes', 0) & 0x400 and
            info.st_uid == 0 and info.st_nlink == 1 and
            stat.S_IMODE(info.st_mode) in (0o600, 0o644) and
            0 <= info.st_size <= 32*1024*1024 and info.st_size == expected_bytes,
            'guardian command log must be root-owned, single-link, regular, bounded and exactly 0600/0644')


def _guardian_log_parents(execution):
    result = {}
    for folder in (OLD_PLAN.parent.parent, OLD_PLAN.parent, execution):
        private_dir(folder)
        info = folder.lstat()
        require(stat.S_IMODE(info.st_mode) == 0o700, 'guardian command log parents must be root-private 0700')
        result[str(folder)] = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    return result


def read_guardian_command_log(path, expected_pin):
    """Read only the eight hash-bound logs made by the fixed load guardian.

    Their producer uses Path.open('xb'): a usual 0022 umask makes 0644 files
    inside root-private 0700 directories. This narrowly accepts that producer
    contract; ordinary private plan/model/code reads remain strictly unchanged.
    No chmod, repair, copying, fallback or wider public-file policy exists here.
    """
    expected = _pin(expected_pin)
    original = Path(path)
    execution = OLD_PLAN.parent/'execution'
    require(original.is_absolute() and '..' not in original.parts and original.parent == execution and
            re.fullmatch(r'command_0[1-4]\.(stdout|stderr)', original.name),
            'only the fixed guardian command_01..04 stdout/stderr paths are permitted')
    path = no_links(original)
    parents = _guardian_log_parents(execution)
    before = path.lstat()
    _guardian_log_info(before, expected['bytes'])
    nofollow = getattr(os, 'O_NOFOLLOW', None)
    require(type(nofollow) is int, 'POSIX O_NOFOLLOW is required for guardian command logs')
    descriptor = os.open(path, os.O_RDONLY | nofollow | getattr(os, 'O_CLOEXEC', 0) |
                         getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        _guardian_log_info(opened, expected['bytes'])
        require(_guardian_log_identity(opened) == _guardian_log_identity(before),
                'guardian command log replaced/metadata changed before read')
        raw = stream.read(expected['bytes']+1)
        after = os.fstat(stream.fileno())
        _guardian_log_info(after, expected['bytes'])
        require(len(raw) == expected['bytes'] and
                _guardian_log_identity(after) == _guardian_log_identity(before),
                'guardian command log changed during read')
    current = no_links(path).lstat()
    _guardian_log_info(current, expected['bytes'])
    require(_guardian_log_identity(current) == _guardian_log_identity(before) and
            _guardian_log_parents(execution) == parents,
            'guardian command log or private parent replaced/metadata changed after read')
    require(identity(raw) == expected, 'guardian command log does not match its durable returned SHA/size')
    return raw


def _guardian_bundle():
    root = OLD_PLAN.parent
    for folder in (root.parent, root, root/'tools', root/'execution'):
        private_dir(folder)
    raw = read_file(root/'tools/tools_manifest.json', private=True)
    require(identity(raw) == LOAD_MANIFEST, 'new load-guardian tool manifest differs')
    manifest = decode_json(raw)
    require(manifest.get('schema') == LOAD_TOOLS_SCHEMA and
            manifest.get('hardware_executed') is False and type(manifest.get('files')) is dict and
            set(manifest['files']) == LOAD_CODE, 'exact published load-only tool inventory required')
    require({p.name for p in (root/'tools').iterdir()} == LOAD_CODE | {'tools_manifest.json'},
            'extra/missing root-copied guardian tool')
    for name, expected in manifest['files'].items():
        require(identity(read_file(root/'tools'/name, private=True)) == _pin(expected),
                'root-copied guardian tool changed: '+name)
    plan = read_json(OLD_PLAN, private=True)
    require(plan.get('schema') == LOAD_PLAN_SCHEMA and
            plan.get('token') == OLD_PLAN.parent.name[5:] and plan.get('unit') == GUARDIAN_UNIT and
            plan.get('boot_id') == BOOT and plan.get('scope') == LOAD_SCOPE and
            plan.get('hardware_executed') is False and plan.get('uart_logging_operator_confirmed') is True and
            type(plan.get('PL_START_commands_issued')) is int and plan['PL_START_commands_issued'] == 0 and
            plan.get('expires_seconds') == 900 and
            plan.get('tool_files') == {'manifest': LOAD_MANIFEST, 'files': manifest['files']},
            'new resident load plan identity/scope differs')
    return plan, manifest


def _load_readonly(root, manifest, name):
    require(name in ('board.py', 'artifacts.py', 'firmware.py'), 'only read-only load helpers are importable')
    return compiled_module(root/'tools'/name, manifest['files'][name]['sha256'])


def _validate_state(state, plan, *, now=None):
    require(type(state) is dict and state.get('schema') == LOAD_STATUS_SCHEMA and
            state.get('stage') == 'LOAD_ONLY_OBSERVED_AWAITING_REVIEW' and
            state.get('boot_id') == BOOT and state.get('unit') == GUARDIAN_UNIT and
            state.get('run_dir') == str(OLD_PLAN.parent/'execution') and
            type(state.get('guardian_pid')) is int and state['guardian_pid'] > 1 and
            state.get('child_pid') is None and state.get('CSR') == state.get('START') == 'BLOCKED' and
            type(state.get('PL_START_commands_issued')) is int and state['PL_START_commands_issued'] == 0 and
            state.get('physical_PL_starts') == 'NOT_MEASURED_NO_CSR_ACCESS' and
            state.get('source_package_modified') is False and state.get('automatic_restoration') == 'DISABLED' and
            state.get('saved_status_only') is True, 'resident guardian has not reached reviewed load-only state')
    current = time.monotonic() if now is None else now
    heartbeat = state.get('heartbeat_monotonic')
    require(type(heartbeat) in (int, float) and math.isfinite(heartbeat) and 0 <= current-heartbeat <= 90,
            'resident guardian heartbeat stale/future/invalid')


PUBLICATION_SHA='b234d0fd9232b71abfd938da2f0f8460f8040d0a7cef68fb4a54e2c1e6a3b386'


def _publication_api():
    path=HERE/'publisher_snapshot.py'
    raw=read_file(path,private=HERE.parent.name=='tools')
    require(identity(raw)['sha256']==PUBLICATION_SHA,'fixed guardian publication helper changed')
    value=types.ModuleType('_resident_publisher_snapshot'); value.__file__=str(path)
    exec(compile(raw,value.__file__,'exec'),value.__dict__)
    return value


def _publication_stamp(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,
            info.st_birthtime_ns if os.name=='nt' else info.st_ctime_ns,
            info.st_mode,info.st_uid,info.st_gid,info.st_nlink)


def _publication_private(info):
    require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode & 0o077 and info.st_nlink==1,
            'root-private single-link guardian publication file required')


def _journal_and_state(plan):
    deadline=time.monotonic()+10.0
    execution=OLD_PLAN.parent/'execution'; folder=execution/'events'
    private_dir(folder); pub=_publication_api(); first=folder.lstat()
    directory=(first.st_dev,first.st_ino,first.st_mode,first.st_uid,first.st_gid)
    candidates={}; baseline=None; current_status=[None]; live_identity=[None]; last_live=[-float('inf')]
    def guard():
        private_dir(folder); info=folder.lstat()
        now=time.monotonic()
        if now-last_live[0]>=0.1:
            value=status()
            expected=None if live_identity[0] is None else {
                'boot_id':BOOT,'guardian_pid':live_identity[0]['pid'],'guardian_start_ticks':live_identity[0]['start_ticks']}
            observed=_observe_guardian(plan,value,expected,deadline=deadline)
            if live_identity[0] is not None:
                require(observed.get('flock')==live_identity[0].get('flock'),'guardian live flock changed during publication wait')
            live_identity[0]=observed; last_live[0]=time.monotonic()
        require((info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)==directory,
                'guardian journal directory identity changed')
        for name,(event,pin) in candidates.items():
            info=(folder/name).lstat(); _publication_private(info)
            require(_publication_stamp(info)==pin,'guardian immutable journal prefix changed')
    def status():
        nonlocal baseline
        value=pub.read_status(execution/'status.json',decode_json,_publication_stamp,_publication_private)
        _validate_state(value,plan)
        if baseline is None: baseline=pub.immutable(value)
        current_status[0]=value
        return value
    def scan(check):
        check(); guard(); names=sorted(p.name for p in folder.iterdir())
        require(0 < len(names) <= 10000 and names==['event_%06d.json'%n for n in range(1,len(names)+1)],
                'guardian durable journal gap/inventory differs')
        records=[]; states=[]; held=False; heartbeat=-1
        phases={'safety_barrier':{prefix+phase for phase in PHASES for prefix in ('BEFORE_','AFTER_')},'command':{'INTENT','SPAWNED','RETURNED'},
                'snapshot':{'INTENT','COMMITTED'},'firmware_directory':{'INTENT','COMMITTED'}}
        state_phases={'PREPARING_ROOT_COPIES','FILES_DURABLE_NO_HARDWARE_CHANGE',
                      'LOAD_ONLY_OBSERVED_AWAITING_REVIEW'} | {phase+suffix for phase in PHASES for suffix in ('_INTENT_DURABLE','_RUNNING')}
        for number,name in enumerate(names,1):
            check(); path=folder/name
            if name in candidates: event,pin=candidates[name]
            else:
                try:
                    event,pin=pub.read_event(path,read_file,decode_json,_publication_stamp,_publication_private,
                                            last_new=number==len(names),check=check)
                except pub.PublicationPending as pending:
                    pending.known_states=states; raise
                require(type(event) is dict and event.get('schema')=='opt50_durable_event_v1' and
                        type(event.get('sequence')) is int and event['sequence']==number and
                        type(event.get('action')) is str and type(event.get('phase')) is str,
                        'guardian durable journal gap/identity differs')
                candidates[name]=(event,pin)
            action,phase=event['action'],event['phase']
            if held:
                require(action=='state' and phase=='LOAD_ONLY_OBSERVED_AWAITING_REVIEW',
                        'guardian unexpected action/phase after held state')
            if action=='state':
                require(phase in state_phases,'guardian unknown historical state phase')
                state=event.get('state')
                require(type(state) is dict and state.get('stage')==phase and state.get('schema')==LOAD_STATUS_SCHEMA and
                        state.get('boot_id')==BOOT and state.get('unit')==GUARDIAN_UNIT and
                        state.get('guardian_pid')==baseline['guardian_pid'], 'guardian historical state identity differs')
                if phase=='LOAD_ONLY_OBSERVED_AWAITING_REVIEW':
                    held=True; stamp=state.get('heartbeat_monotonic')
                    _validate_state(state,plan,now=stamp)
                    require(pub.immutable(state)==baseline and stamp>heartbeat,'guardian non-heartbeat state/heartbeat changed')
                    heartbeat=stamp; states.append(state)
            else:
                require(action in phases and phase in phases[action],'guardian unknown historical action/phase')
                if action=='command': require(event.get('action_stage') in PHASES,'guardian command action stage differs')
            records.append(event)
        require(bool(states) and records[-1]['action']=='state','guardian final held heartbeat missing')
        return {'states':states,'last_state':states[-1],'payload':records}
    return pub.consistent_snapshot(status,scan,lambda state:_validate_state(state,plan),guard,deadline=deadline)


def _proc_read(path, limit=65536):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0))
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= limit,
                'bounded regular proc observation required')
        chunks, size = [], 0
        while size <= limit:
            block = os.read(descriptor, min(65536, limit+1-size))
            if not block:
                break
            size += len(block); chunks.append(block)
        require(size <= limit, 'proc observation exceeds bound')
        return b''.join(chunks)
    finally:
        os.close(descriptor)


def parse_proc_stat(raw):
    require(isinstance(raw, bytes) and len(raw) <= 65536, 'bounded raw proc stat required')
    text = raw.decode('utf-8').rstrip('\n')
    first, last = text.find(' ('), text.rfind(') ')
    require(first > 0 and last > first+1 and re.fullmatch('[1-9][0-9]*', text[:first]),
            'malformed proc stat prefix/comm')
    fields = text[last+2:].split()
    require(len(fields) >= 20 and fields[0] in 'RSDZTWtXxKWPIN' and len(fields[0]) == 1 and
            all(re.fullmatch('-?[0-9]+', value) for value in fields[1:]) and int(fields[19]) >= 0,
            'malformed proc stat fields')
    return {'pid': int(text[:first]), 'state': fields[0], 'ppid': int(fields[1]), 'start_ticks': int(fields[19])}


def parse_flock(raw, pid, device, inode):
    require(type(raw) is bytes and len(raw) <= 4*1024*1024 and type(pid) is int and pid > 1 and
            isinstance(device, tuple) and len(device) == 2 and type(inode) is int and inode > 0,
            'bounded flock identity required')
    pattern = re.compile(r'^\s*\d+:\s+FLOCK\s+ADVISORY\s+WRITE\s+(\d+)\s+([a-fA-F0-9]+):([a-fA-F0-9]+):(\d+)\s+0\s+EOF\s*$')
    matches = []
    for line in raw.decode('ascii').splitlines():
        found = pattern.fullmatch(line)
        if found and (int(found[1]), int(found[2], 16), int(found[3], 16), int(found[4])) == (
                pid, device[0], device[1], inode):
            matches.append(line)
    require(len(matches) == 1, 'guardian must uniquely hold the whole cooperative board WRITE flock')
    return {'pid': pid, 'device_major': device[0], 'device_minor': device[1], 'inode': inode, 'range': '0 EOF'}


def _regular_board_lock():
    path = no_links(BOARD_LOCK)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), 'ordinary existing cooperative board lock required')
    return info


def _command_tool(plan, name):
    value = plan['command_tools'][name]
    require(type(value) is dict and set(value) == {'entry', 'target', 'bytes', 'sha256'} and
            type(value['entry']) is str and type(value['target']) is str,
            'pinned system command identity missing')
    target = Path(value['target'])
    info = no_links(target).lstat()
    require(info.st_uid == 0 and not info.st_mode & 0o022 and stat.S_ISREG(info.st_mode) and
            Path(value['entry']).resolve(strict=True) == target and
            identity(read_file(target)) == {key: value[key] for key in ('bytes', 'sha256')},
            'pinned system command changed: '+name)
    return value


def _service(plan,*,deadline=None):
    tool = _command_tool(plan, 'systemctl')
    fields = (*SERVICE, 'ActiveState', 'SubState', 'MainPID')
    timeout=20 if deadline is None else min(20,deadline-time.monotonic())
    require(timeout>0,'guardian publication deadline expired before read-only service query')
    run = subprocess.run([tool['entry'], 'show', GUARDIAN_UNIT+'.service', '--property='+','.join(fields)],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    require(run.returncode == 0 and len(run.stdout) <= 32768 and len(run.stderr) <= 8192,
            'bounded guardian service observation failed')
    rows = [line.split('=', 1) for line in run.stdout.splitlines() if '=' in line]
    require(len({key for key, _ in rows}) == len(rows), 'duplicate guardian service property')
    actual = dict(rows)
    require(all(actual.get(key) == value for key, value in SERVICE.items()) and
            actual.get('ActiveState') == 'active' and actual.get('SubState') == 'running' and
            re.fullmatch('[1-9][0-9]*', actual.get('MainPID', '')), 'guardian service is not guarded/running')
    return actual


def _observe_guardian(plan, state, expected=None,*,deadline=None):
    require(_proc_read(Path('/proc/sys/kernel/random/boot_id'), 128).decode().strip() == BOOT,
            'resident boot changed')
    actual = _service(plan,deadline=deadline)
    pid = int(actual['MainPID'])
    require(pid == state['guardian_pid'], 'guardian saved PID and current service differ')
    process = Path('/proc')/str(pid)
    before = parse_proc_stat(_proc_read(process/'stat'))
    require(before['pid'] == pid and before['state'] not in ('Z', 'X', 'x'), 'guardian not alive')
    command = ['/usr/bin/python3', '-B', str(OLD_PLAN.parent/'tools/window.py'),
               'run', '--plan', str(OLD_PLAN), '--authorized-load-only']
    require(_proc_read(process/'cmdline') == b''.join(value.encode()+b'\0' for value in command),
            'guardian exact argv differs')
    require(_proc_read(process/'cgroup') == ('0::/system.slice/'+GUARDIAN_UNIT+'.service\n').encode(),
            'guardian unified cgroup differs')
    require(re.search(rb'(?m)^Uid:\s+0\s+0\s+0\s+0\s*$', _proc_read(process/'status')) is not None,
            'guardian must retain root credentials')
    python = _command_tool(plan, 'python3')
    require(python['entry'] == '/usr/bin/python3' and str((process/'exe').resolve(strict=True)) == python['target'],
            'guardian executable target differs')
    lock = _regular_board_lock()
    names = []
    for count, path in enumerate((process/'fd').iterdir(), 1):
        require(count <= 4096 and path.name.isdecimal(), 'guardian FD inventory exceeds bound')
        names.append(path)
    matches = []
    for path in names:
        try:
            target = os.readlink(path)
        except FileNotFoundError:
            continue
        if target == str(BOARD_LOCK):
            opened = path.stat()
            require(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino) == (lock.st_dev, lock.st_ino),
                    'guardian cooperative-lock FD inode differs')
            matches.append(path.name)
    require(matches, 'guardian no longer holds cooperative board-lock FD')
    flock = parse_flock(_proc_read(Path('/proc/locks'), 4*1024*1024), pid,
                        (os.major(lock.st_dev), os.minor(lock.st_dev)), lock.st_ino)
    after_lock = _regular_board_lock()
    after = parse_proc_stat(_proc_read(process/'stat'))
    require((after_lock.st_dev, after_lock.st_ino) == (lock.st_dev, lock.st_ino) and
            after['pid'] == pid and after['state'] not in ('Z', 'X', 'x') and
            after['start_ticks'] == before['start_ticks'], 'guardian PID/lock changed during observation')
    guardian = {'pid': pid, 'start_ticks': before['start_ticks'], 'boot_id': BOOT,
                'unit': GUARDIAN_UNIT, 'heartbeat_monotonic': state['heartbeat_monotonic'],
                'old_board_lock_transferred': False, 'lock_fds': sorted(matches, key=int), 'flock': flock}
    if expected is not None:
        require(type(expected.get('guardian_pid')) is int and type(expected.get('guardian_start_ticks')) is int and
                expected.get('boot_id') == BOOT and expected['guardian_pid'] == pid and
                expected['guardian_start_ticks'] == before['start_ticks'],
                'guardian differs from identity frozen in the NEW model plan')
    return guardian


def _validate_commands(plan, records):
    root = OLD_PLAN.parent/'execution'
    command_records = [row for row in records if row.get('action') == 'command']
    require(len(command_records) == 12, 'exact four management command lifecycles required')
    allowed = [('dfx-mgr-client', '-remove', '0'), ('insmod', str(root/'zocl_candidate.ko')),
               ('dfx-mgr-client', '-load', 'kv260_opt50_fd96539d_4cbcf732'),
               ('udevadm', 'settle', '--timeout=5')]
    for index, (phase, command) in enumerate(zip(PHASES, allowed), 1):
        intent, spawned, returned = command_records[3*(index-1):3*index]
        argv = [plan['command_tools'][command[0]]['entry'], *command[1:]]
        require([row.get('phase') for row in (intent, spawned, returned)] == ['INTENT', 'SPAWNED', 'RETURNED'] and
                all(row.get('action_stage') == phase for row in (intent, spawned, returned)) and
                intent.get('argv') == spawned.get('argv') == argv and
                type(spawned.get('pid')) is int and spawned['pid'] > 1 and
                spawned['pid'] == returned.get('pid') and type(returned.get('returncode')) is int and
                returned['returncode'] == 0, 'management sequence/argv/reaped-zero-exit differs: '+phase)
        for suffix in ('stdout', 'stderr'):
            path = root/('command_%02d.%s' % (index, suffix))
            raw = read_guardian_command_log(path, returned.get(suffix))
            require(intent.get(suffix) == str(path) and returned.get(suffix) == identity(raw),
                    'management log content identity differs: '+phase)
            if suffix == 'stderr':
                require(raw == b'', 'unexpected management diagnostics: '+phase)


def _validate_staged(plan, records, board, firmware):
    root = OLD_PLAN.parent/'execution'
    package = Path(plan['package'])
    for key, name in (('firmware', 'candidate.bit.bin'), ('dtbo', 'candidate.dtbo'),
                      ('metadata', 'metadata.xclbin'), ('runtime', 'runtime'), ('xrt_ini', 'xrt.ini')):
        relative = Path(plan['artifacts']['source_paths'][key]).relative_to(package).as_posix()
        require(identity(read_file(root/name, private=True)) == plan['artifacts']['files'][relative],
                'staged root artifact changed: '+name)
    require(identity(read_file(root/'zocl_candidate.ko', private=True))['sha256'] == board.DRIVER_SHA and
            identity(read_file(root/'shell.json', private=True)) == plan['artifacts']['files']['firmware/shell.json'],
            'staged driver/shell changed')
    expected = firmware.expected_from_audit(package, plan['artifacts'], board.APP)
    committed = [row for row in records if row.get('action') == 'firmware_directory' and row.get('phase') == 'COMMITTED']
    require(len(committed) == 1 and committed[0].get('existing_firmware_modified') is False,
            'one committed firmware staging receipt required')
    target = Path('/usr/lib/firmware/xilinx')/board.APP
    require(committed[0].get('destination') == str(target), 'firmware directory target differs')
    firmware.assert_same(target, committed[0]['observation'], expected, board.APP)
    require(plan.get('firmware', {}).get('expected') == expected and
            plan['firmware'].get('directory') == str(target), 'plan firmware inventory differs')


def held_review():
    """Fresh full OS/file review, requiring an unused resident CU; no hardware access."""
    root_linux()
    plan, manifest = _guardian_bundle()
    state, records = _journal_and_state(plan)
    guardian = _observe_guardian(plan, state)
    root = OLD_PLAN.parent
    board = _load_readonly(root, manifest, 'board.py')
    artifacts = _load_readonly(root, manifest, 'artifacts.py')
    firmware = _load_readonly(root, manifest, 'firmware.py')
    _validate_commands(plan, records)
    _validate_staged(plan, records, board, firmware)
    require(artifacts.audit(Path(plan['package']), Path(plan['cases'])) == plan['artifacts'],
            'resident source artifacts/cases changed')
    saved_observation = read_json(root/'execution/after_load_observations.json', private=True)
    board.loaded(saved_observation, BOOT)
    current = board.collect(Path(plan['cases']), plan['command_tools'])
    observation = board.loaded(current, BOOT)
    # A second identity/heartbeat read bounds process changes during the heavier
    # artifact/sysfs audit; it does not open or alter the guardian's board lock.
    final_state = read_json(root/'execution/status.json', private=True)
    _validate_state(final_state, plan)
    final = _observe_guardian(plan, final_state,
                              {'boot_id': BOOT, 'guardian_pid': guardian['pid'],
                               'guardian_start_ticks': guardian['start_ticks']})
    return {'schema': 'opt50_b24_layer02_resident_review_v1', 'status': 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START',
            'boot_id': BOOT, 'load_plan': str(OLD_PLAN), 'guardian': final,
            'load_tools_manifest': LOAD_MANIFEST, 'load_tools_schema': LOAD_TOOLS_SCHEMA,
            'command_tools': plan['command_tools'],
            'resident_OS_observations': observation, 'driver_bindings': current['drm_driver_bindings'],
            'CSR': 'BLOCKED', 'START': 'BLOCKED', 'PL_START_commands_issued': 0,
            'physical_PL_starts': 'NOT_MEASURED_NO_CSR_ACCESS', 'old_board_lock_transferred': False,
            'scope': 'Read-only OS/files/service/proc proof; not reset/PS-width/CSR/compute evidence'}


def monitor_guardian(model_plan,*,deadline=None):
    """No occupancy scan while our own legitimate worker may hold XRT FDs."""
    root_linux()
    plan, _ = _guardian_bundle()
    pub=_publication_api()
    state=pub.read_status(OLD_PLAN.parent/'execution/status.json',decode_json,_publication_stamp,_publication_private)
    _validate_state(state,plan)
    return _observe_guardian(plan,state,model_plan,deadline=deadline)


def _lock_info():
    private_dir(LOCK.parent)
    info = no_links(LOCK).lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
            not info.st_mode & 0o077 and
            read_json(LOCK, private=True) == {'schema': 'opt50_first_access_global_companion_lock_v1'},
            'existing root-private global companion lock differs')
    return info


@contextlib.contextmanager
def companion_locked():
    import fcntl
    before = _lock_info()
    descriptor = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0))
    try:
        actual = os.fstat(descriptor)
        require((actual.st_dev, actual.st_ino) == (before.st_dev, before.st_ino), 'companion lock replaced')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptor
    finally:
        os.close(descriptor)  # Never closes or transfers the guardian board lock.


def configure(guardian_plan):
    """Select a current, exact published v2/v3 load-only guardian; no load/MMIO.

    The trusted loader source manifest is literal-pinned above. The plan token,
    saved identity and current boot are independently checked before review.
    A reboot with only the starter overlay needs a separate load workflow.
    """
    global OLD_PLAN, BOOT, GUARDIAN_UNIT, LOAD_MANIFEST, LOAD_TOOLS_SCHEMA
    global LOAD_PLAN_SCHEMA, LOAD_STATUS_SCHEMA, LOAD_CODE
    root_linux()
    selected = Path(guardian_plan)
    namespace = selected.parent.parent.as_posix()
    require(selected.is_absolute() and selected.name == 'plan.json' and
            namespace in PROFILES and
            re.fullmatch(r'plan_[a-f0-9]{12}', selected.parent.name),
            'only the exact published load-only-v2/v3 resident guardian namespaces are supported')
    profile = PROFILES[namespace]
    require(profile['manifest'] is not None, 'selected guardian profile is not a frozen release')
    for folder in (selected.parent.parent, selected.parent, selected.parent/'tools'):
        private_dir(folder)
    value = read_json(selected, private=True)
    current_boot = _proc_read(Path('/proc/sys/kernel/random/boot_id'), 128).decode().strip()
    require(re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', current_boot) and
            value.get('boot_id') == current_boot, 'guardian plan belongs to another boot')
    token = selected.parent.name[5:]
    require(value.get('token') == token and value.get('unit') == profile['unit_prefix']+token and
            value.get('schema') == profile['plan_schema'], 'guardian schema/token/unit/namespace mismatch')
    OLD_PLAN, BOOT, GUARDIAN_UNIT = selected, current_boot, profile['unit_prefix']+token
    LOAD_MANIFEST, LOAD_TOOLS_SCHEMA = profile['manifest'], profile['tools_schema']
    LOAD_PLAN_SCHEMA, LOAD_STATUS_SCHEMA, LOAD_CODE = profile['plan_schema'], profile['status_schema'], set(profile['code'])
    _guardian_bundle()  # Validates the exact literal-pinned profile code closure.
    return {'guardian_plan': str(selected), 'boot_id': BOOT, 'unit': GUARDIAN_UNIT}
