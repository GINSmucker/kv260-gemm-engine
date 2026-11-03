"""Collect Linux pre-deployment evidence without accessing PL/device registers.

This is NOT a readiness gate or a hardware authorization. It writes only JSON
to stdout; it never imports a loader, opens a device, takes a board lock,
changes a module/overlay/clock, or invokes XRT device enumeration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

PROJECT = Path('/home/ubuntu/fpga_project')
PACKAGE = PROJECT / 'opt50_integration/data/axi_cases'
STARTER = Path('/usr/lib/firmware/xilinx/k26-starter-kits')
CANDIDATE = PROJECT / 'next_stage/overlay_fix_v2_20260929_01/zocl_candidate.ko'
EXPECTED_KERNEL = '6.8.0-1015-xilinx'
EXPECTED_CANDIDATE = 'cba324ac56c9bebe84fabbdf2635ea699b10ec718593cba7d44c4b004f782eca'
EXPECTED_MANIFEST = '6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf'
EXPECTED_STARTER = {
    'k26_starter_kits.bit.bin': 'f9dfe95d14881a2768b2c0e6a36fc363635e8ae7882ecad9725d270490395d3e',
    'k26_starter_kits.dtbo': '1e998ffd5d33a05da3746df3603e65603e1628006bc878209a836cc9bd3c4ae1',
    'shell.json': '2967ccbc1f75c8d4accd1b6fecc11b16a32a5d7992e56c6eebceb99a3410f777',
}
SYMBOLS = ('fpga_full', 'amba', 'zynqmp_clk', 'zynqmp_reset')
TOOL_NAMES = ('python3', 'g++-13', 'dtc', 'xclbinutil', 'modinfo',
              'ip', 'systemctl', 'dmesg', 'dfx-mgr-client')
MAX_OUTPUT = 20480


def error_value(error):
    # Do not print arbitrary exception payloads which may contain private data.
    return {'ok': False, 'error': type(error).__name__,
            'errno': getattr(error, 'errno', None)}


def observe(function, *args):
    try:
        return {'ok': True, 'value': function(*args)}
    except (OSError, ValueError, RuntimeError) as error:
        return error_value(error)


def read_text(path, limit=65536):
    with Path(path).open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('bounded text read exceeded')
    return value.decode('utf-8', errors='strict').strip()


def sha256_file(path):
    # Only ordinary files, never devices or FIFOs. Hashes reveal no file text.
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'sha256': digest.hexdigest(), 'bytes': path.stat().st_size}


def file_identity(path, expected):
    value = observe(sha256_file, path)
    value['path'] = str(path)
    value['expected_sha256'] = expected
    value['matches_expected'] = bool(value['ok'] and value['value']['sha256'] == expected)
    return value


def tool_identity(name):
    found = shutil.which(name)
    if found is None:
        return {'ok': False, 'error': 'ToolNotFound'}
    try:
        entry = Path(os.path.abspath(found))
        # Resolve the parent only: kmod's modinfo symlink dispatches by argv[0].
        entry = entry.parent.resolve(strict=True) / entry.name
        target = entry.resolve(strict=True)
        if not entry.is_file() or not target.is_file():
            raise ValueError('not an executable file')
        return {'ok': True, 'entry': str(entry), 'resolved_target': str(target)}
    except (OSError, ValueError) as error:
        return error_value(error)


def command(name, args, tools):
    allowed = {
        'modinfo': ('-F', 'vermagic', str(CANDIDATE)),
        'ip': ('-br', '-4', 'addr'),
        'systemctl': ('is-active', 'dfx-mgr'),
        'dmesg': ('--level=emerg,alert,crit,err,warn',),
    }
    if name not in allowed or tuple(args) != allowed[name]:
        raise ValueError('command not allowlisted')
    identity = tools[name]
    if not identity['ok']:
        return {'ok': False, 'error': 'ToolUnavailable', 'tool': name}
    argv = [identity['entry'], *args]
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=20,
                                check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'argv': argv, **error_value(error)}
    # Tail is useful for recent kernel errors; record truncation explicitly.
    limits = {'dmesg': 4000, 'ip': 2000, 'modinfo': 512, 'systemctl': 256}
    limit = limits[name]
    return {'ok': result.returncode == 0, 'argv': argv,
            'returncode': result.returncode, 'stdout': result.stdout[-limit:],
            'stderr': result.stderr[-512:],
            'stdout_truncated': len(result.stdout) > limit,
            'stderr_truncated': len(result.stderr) > 512}


def device_users(proc):
    """Inspect FD links only; permission failures make coverage incomplete."""
    users, errors = [], []
    races = 0
    error_count = 0
    user_count = 0

    def record(path, error):
        nonlocal error_count
        error_count += 1
        if len(errors) < 24:
            errors.append({'path': str(path), **error_value(error)})

    try:
        processes = sorted(proc.iterdir(), key=lambda item: item.name)
    except OSError as error:
        record(proc, error)
        processes = []
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / 'fd').iterdir())
        except (FileNotFoundError, ProcessLookupError):
            races += 1
            continue
        except OSError as error:
            record(process / 'fd', error)
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except (FileNotFoundError, ProcessLookupError):
                races += 1
                continue
            except OSError as error:
                record(descriptor, error)
                continue
            if (target == '/dev/mem' or target == '/dev/mem (deleted)' or
                    target.startswith(('/dev/dri/render', '/dev/zocl', '/dev/fpga'))):
                user_count += 1
                if len(users) < 32:
                    comm = observe(read_text, process / 'comm', 256)
                    users.append({'pid': int(process.name), 'fd': descriptor.name,
                                  'device': target[:256], 'comm': comm})
                    if not comm['ok']:
                        if comm['error'] in ('FileNotFoundError', 'ProcessLookupError'):
                            races += 1
                        else:
                            record(process / 'comm', OSError('unreadable comm'))
    return {'coverage_complete': error_count == 0,
            'users': users, 'observed_device_fds': user_count,
            'users_omitted': max(0, user_count - len(users)),
            'errors': errors, 'error_count': error_count,
            'errors_omitted': max(0, error_count - len(errors)),
            'transient_disappearance_count': races,
            'note': 'Point-in-time /proc scan; no device opened and no lock acquired'}


def clock_row(path):
    rows = [line.split() for line in read_text(path, 2 * 1024 * 1024).splitlines()
            if line.split() and line.split()[0] == 'pl0_ref']
    if len(rows) != 1 or len(rows[0]) < 5:
        raise ValueError('missing or ambiguous clock row')
    row = rows[0]
    return {'row': row[:16], 'rate_hz': int(row[4]),
            'enable_count': int(row[1]), 'prepare_count': int(row[2]),
            'scope': 'clk_summary only; no rate or reset register access'}


def symbol_value(base, name):
    value = read_text(base / '__symbols__' / name, 4096).rstrip('\0')
    if not value.startswith('/') or '\0' in value or '..' in Path(value).parts:
        raise ValueError('invalid symbol target')
    if not (base / value.lstrip('/')).is_dir():
        raise FileNotFoundError('symbol target missing')
    return value


def list_names(path, prefix=''):
    return sorted(item.name for item in path.iterdir() if item.name.startswith(prefix))


def disk_usage(path):
    usage = shutil.disk_usage(path)
    return {'total_bytes': usage.total, 'used_bytes': usage.used, 'free_bytes': usage.free}


def root_mounts(path):
    # Avoid publishing unrelated user/network mounts or their options.
    rows = [line.split() for line in read_text(path, 262144).splitlines()
            if len(line.split()) >= 4 and line.split()[1] == '/']
    if len(rows) != 1:
        raise ValueError('missing or ambiguous root mount')
    row = rows[0]
    return {'source': row[0], 'filesystem': row[2], 'options': row[3]}


def collect(package=PACKAGE, *, root=Path('/')):
    if platform.system() != 'Linux':
        raise RuntimeError('Linux required; no collection performed')
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise PermissionError('root required; no collection performed')
    # Alternate root exists for isolated tests only; CLI uses the live root.
    path = lambda absolute: root / str(absolute).lstrip('/')
    proc, sys = path('/proc'), path('/sys')
    tools = {name: tool_identity(name) for name in TOOL_NAMES}
    result = {
        'schema': 'opt50_predeployment_snapshot_v1',
        'status': 'SNAPSHOT_COLLECTED_REQUIRES_REVIEW',
        'collected_at_utc': datetime.now(timezone.utc).isoformat(),
        'mutations': 'NONE', 'physical_PL_starts': 0,
        'hardware_authorization': 'NONE',
        'scope': 'OS/filesystem evidence only; no /dev/mem, PL CSR, XRT or board load',
        'expected_kernel': EXPECTED_KERNEL,
        'kernel': observe(read_text, proc / 'sys/kernel/osrelease'),
        'architecture': platform.machine(),
        'boot_id': observe(read_text, proc / 'sys/kernel/random/boot_id'),
        'uptime': observe(read_text, proc / 'uptime'),
        'memory': observe(read_text, proc / 'meminfo', 8192),
        'project_disk': observe(disk_usage, path(PROJECT)),
        'root_mounts': observe(root_mounts, proc / 'mounts'),
        'fpga_state': observe(read_text, sys / 'class/fpga_manager/fpga0/state'),
        'overlays': observe(list_names, sys / 'kernel/config/device-tree/overlays'),
        'render_nodes': observe(list_names, path('/dev/dri'), 'renderD'),
        'zocl_module_present': (sys / 'module/zocl').exists(),
        'zocl_driver_present': (sys / 'bus/platform/drivers/zocl').exists(),
        'pl0_clock': observe(clock_row, sys / 'kernel/debug/clk/clk_summary'),
        'device_occupancy': device_users(proc),
        'base_symbols': {name: observe(symbol_value, sys / 'firmware/devicetree/base', name)
                         for name in SYMBOLS},
        'starter_files': {name: file_identity(path(STARTER / name), digest)
                          for name, digest in EXPECTED_STARTER.items()},
        'candidate_module': file_identity(path(CANDIDATE), EXPECTED_CANDIDATE),
        'case_manifest': file_identity(path(Path(package) / 'manifest.json'), EXPECTED_MANIFEST),
        'tools': tools,
    }
    result['commands'] = {
        'candidate_vermagic': command('modinfo', ('-F', 'vermagic', str(CANDIDATE)), tools),
        'ipv4_interfaces': command('ip', ('-br', '-4', 'addr'), tools),
        'dfx_service': command('systemctl', ('is-active', 'dfx-mgr'), tools),
        'kernel_warnings': command('dmesg', ('--level=emerg,alert,crit,err,warn',), tools),
    }
    result['limitations'] = [
        'Collection completion is not PASS or readiness; every observation requires review.',
        'No exclusive lock: state may change immediately after collection.',
        'No direct reset/fabric-width/loaded-design proof; no new hardware authorization.',
        'Manifest identity alone does not rehash or validate every case file.',
    ]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='snapshot', choices=('snapshot',))
    parser.add_argument('--package', type=Path, default=PACKAGE,
                        help='existing AXI case package (read its manifest identity only)')
    args = parser.parse_args(argv)
    try:
        result = collect(args.package)
        rendered = json.dumps(result, ensure_ascii=True, indent=2)
        if len(rendered.encode('utf-8')) > MAX_OUTPUT:
            # Keep all field names/errors/identities; suppress the verbose text.
            for name in ('root_mounts', 'memory'):
                result[name] = {'ok': False, 'error': 'OutputBudgetOmitted',
                                'previous_observation_ok': result[name]['ok']}
            for value in result['commands'].values():
                if 'stdout' in value:
                    value['stdout'] = value['stdout'][-256:]
                    value['stdout_truncated'] = True
            result['device_occupancy']['errors'] = result['device_occupancy']['errors'][:4]
            result['device_occupancy']['errors_omitted'] = (
                result['device_occupancy']['error_count'] -
                len(result['device_occupancy']['errors']))
            result['device_occupancy']['users'] = result['device_occupancy']['users'][:4]
            result['device_occupancy']['users_omitted'] = (
                result['device_occupancy']['observed_device_fds'] -
                len(result['device_occupancy']['users']))
            result['output_budget_reduction'] = True
            rendered = json.dumps(result, ensure_ascii=True, indent=2)
        if len(rendered.encode('utf-8')) > MAX_OUTPUT:
            raise RuntimeError('snapshot exceeds output budget; do not treat as complete')
        print(rendered)
        return 0  # Collection, NOT readiness or acceptance.
    except (RuntimeError, PermissionError) as error:
        print(json.dumps({'status': 'SNAPSHOT_REFUSED', 'error': str(error),
                          'mutations': 'NONE', 'physical_PL_starts': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
