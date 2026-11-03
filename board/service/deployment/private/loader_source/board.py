"""Linux OS/sysfs observations only: no device/MMIO/XRT opens or mutations."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import uuid

KERNEL = '6.8.0-1015-xilinx'
DRIVER = Path('/home/ubuntu/fpga_project/next_stage/overlay_fix_v2_20260929_01/zocl_candidate.ko')
DRIVER_SHA = 'cba324ac56c9bebe84fabbdf2635ea699b10ec718593cba7d44c4b004f782eca'
SRCVERSION = '9806AB60AF7AE94483CDDC9'
APP = 'kv260_opt50_fd96539d_4cbcf732'
STARTER_OVERLAY = 'k26-starter-kits_image_1'
SNAPSHOT_SHA = '1f4f955608ede7ead9e8bd064011449627485a2045356b53defb2766dc6ff59e'
SAFE_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'
SEVERE = re.compile(r'BUG:|kernel BUG at|Oops:|Kernel panic|scheduling while atomic|OF:\s*ERROR:|refcount_t:')
TOOL_NAMES = ('dfx-mgr-client', 'insmod', 'udevadm', 'systemctl', 'dmesg', 'modinfo', 'ip', 'python3')
SYMBOLS = {'fpga_full': '/fpga-region', 'amba': '/axi',
           'zynqmp_clk': '/firmware/zynqmp-firmware/clock-controller',
           'zynqmp_reset': '/firmware/zynqmp-firmware/reset-controller'}
EXPECTED_DRIVER = '/sys/bus/platform/drivers/zocl-drm'
EXPECTED_MODULE = '/sys/module/zocl'
EXPECTED_OF_NODE = '/sys/firmware/devicetree/base/axi/zyxclmm_drm'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def root_linux():
    require(platform.system() == 'Linux' and platform.machine() == 'aarch64' and
            hasattr(os, 'geteuid') and os.geteuid() == 0, 'ARM Linux/root required')


def record(path):
    path = Path(path)
    require(stat.S_ISREG(path.stat().st_mode), 'nonregular tool/file')
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def snapshot_api():
    here = Path(__file__).resolve().parent
    path = here / 'board_snapshot.py'
    if not path.exists():  # Source-tree tests; release package contains the pinned copy.
        path = here.parent / 'board_snapshot.py'
    require(record(path)['sha256'] == SNAPSHOT_SHA, 'snapshot helper identity changed')
    spec = importlib.util.spec_from_file_location('opt50_snapshot_readonly', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tools():
    out = {}
    for name in TOOL_NAMES:
        found = shutil.which(name, path=SAFE_PATH)
        require(found is not None, 'required tool missing: ' + name)
        # Preserve insmod/modinfo argv[0]; do not execute their common kmod target.
        entry = Path(found).parent.resolve(strict=True) / Path(found).name
        target = entry.resolve(strict=True)
        info = target.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022 and os.access(entry, os.X_OK),
                'tool must be root-owned, executable, non-writable by others')
        out[name] = {'entry': str(entry), 'target': str(target), **record(target)}
    return out


def validate_tools(expected):
    require(tools() == expected, 'command entrypoint/target/hash changed')


def query(command_tools, name, *args):
    allowed = {'dmesg': [()], 'systemctl': [('is-active', 'dfx-mgr')],
               'modinfo': [('-F', 'srcversion', str(DRIVER))]}
    require(tuple(args) in allowed.get(name, []), 'read-only query not allowed')
    run = subprocess.run([command_tools[name]['entry'], *args], capture_output=True,
                         text=True, encoding='utf-8', errors='replace', timeout=20,
                         stdin=subprocess.DEVNULL, check=False)
    require(run.returncode == 0, 'read-only query failed: ' + name)
    require(len(run.stdout.encode('utf-8')) <= 4 * 1024 * 1024 and len(run.stderr) <= 8192,
            'query output too large; no truncated readiness decision')
    return run.stdout


def registered_drivers():
    """Directory names only: no driver/device opens, bind or unbind writes."""
    root = Path('/sys/bus/platform/drivers')
    require(root.is_dir(), 'platform driver registry missing')
    return sorted(path.name for path in root.iterdir() if path.name.startswith('zocl'))


def binding_observations(names):
    """Read DRM character identity and sysfs links/OF text, never a device FD."""
    require(type(names) is list and len(names) <= 8 and
            all(type(name) is str and re.fullmatch(r'renderD[0-9]+', name) for name in names),
            'bounded render-node names required')
    rows = []
    for name in names:
        node = Path('/dev/dri') / name
        info = node.lstat()
        require(stat.S_ISCHR(info.st_mode), 'render node is not a real character device')
        drm = Path('/sys/class/drm') / name
        driver = (drm/'device/driver').resolve(strict=True)
        module = (drm/'device/driver/module').resolve(strict=True)
        of_node = (drm/'device/of_node').resolve(strict=True)
        compatible = (of_node/'compatible').read_bytes()
        status = (of_node/'status').read_bytes()
        require(len(compatible) <= 256 and compatible.endswith(b'\0') and
                len(status) <= 32 and status.endswith(b'\0'), 'bounded OF properties required')
        sys_dev = (drm/'dev').read_text().strip()
        require(re.fullmatch(r'[0-9]+:[0-9]+', sys_dev), 'invalid DRM sysfs dev number')
        device = (drm/'device').resolve(strict=True)
        reverse = sorted(str(path.resolve(strict=True)) for path in driver.iterdir()
                         if path.is_symlink() and path.resolve(strict=True) == device)
        rows.append({'name': name, 'driver': str(driver), 'module': str(module),
                     'of_node': str(of_node), 'sysfs_device': str(device), 'reverse_devices': reverse,
                     'compatible': compatible[:-1].decode('ascii').split('\0'),
                     'status': status[:-1].decode('ascii'), 'sysfs_dev': sys_dev,
                     'sysfs_char_matches': (Path('/sys/dev/char')/sys_dev).resolve(strict=True) == drm.resolve(strict=True),
                     'major': os.major(info.st_rdev), 'minor': os.minor(info.st_rdev),
                     'character_device': True})
    return rows


def validate_binding(row):
    require(type(row) is dict and row.get('name') == 'renderD128' and
            row.get('driver') == EXPECTED_DRIVER and row.get('module') == EXPECTED_MODULE and
            row.get('of_node') == EXPECTED_OF_NODE and row.get('compatible') == ['xlnx,zocl'] and
            row.get('status') == 'okay' and row.get('character_device') is True and
            type(row.get('major')) is int and type(row.get('minor')) is int and
            (row['major'], row['minor']) == (226, 128) and row.get('sysfs_dev') == '226:128' and
            type(row.get('sysfs_device')) is str and row['sysfs_device'].startswith('/sys/devices/'),
            'render node driver/module/OF/character identity differs')
    require(row.get('reverse_devices') == [row['sysfs_device']] and row.get('sysfs_char_matches') is True,
            'render class/character device/reverse platform binding differs')


def collect(cases, command_tools):
    root_linux()
    validate_tools(command_tools)
    previous_path = os.environ.get('PATH')
    try:
        os.environ['PATH'] = SAFE_PATH
        value = snapshot_api().collect(cases)
    finally:
        if previous_path is None:
            os.environ.pop('PATH', None)
        else:
            os.environ['PATH'] = previous_path
    full = query(command_tools, 'dmesg')
    value['snapshot_legacy_zocl_driver_present'] = value['zocl_driver_present']
    value['zocl_registered_drivers'] = snapshot_api().observe(registered_drivers)
    value['drm_driver_bindings'] = snapshot_api().observe(
        binding_observations, observed(value, 'render_nodes'))
    # Preserve the fixed old snapshot but correct its obsolete /drivers/zocl
    # assumption explicitly. Directory presence alone never proves a binding.
    value['zocl_driver_present'] = bool(observed(value, 'zocl_registered_drivers'))
    value['kernel_log_full'] = {'bytes': len(full.encode('utf-8')),
                              'sha256': hashlib.sha256(full.encode('utf-8')).hexdigest(),
                              'severe_matches': SEVERE.findall(full), 'text': full}
    value['candidate_srcversion'] = query(command_tools, 'modinfo', '-F', 'srcversion', str(DRIVER)).strip()
    if value['zocl_module_present']:
        value['loaded_srcversion'] = Path('/sys/module/zocl/srcversion').read_text().strip()
    return value


def observed(value, key):
    item = value[key]
    require(item.get('ok') is True, 'incomplete observation: ' + key)
    return item['value']


def common(value):
    require(observed(value, 'kernel') == KERNEL and value['architecture'] == 'aarch64', 'kernel/architecture changed')
    boot = observed(value, 'boot_id')
    require(str(uuid.UUID(boot)) == boot, 'invalid boot ID')
    require(observed(value, 'fpga_state') == 'operating', 'FPGA manager not operating')
    drivers = observed(value, 'zocl_registered_drivers')
    bindings = observed(value, 'drm_driver_bindings')
    require(type(drivers) is list and len(drivers) <= 32 and
            all(type(name) is str and re.fullmatch(r'zocl[-_a-zA-Z0-9]*', name) for name in drivers) and
            len(set(drivers)) == len(drivers) and type(bindings) is list and len(bindings) <= 8 and
            value['zocl_driver_present'] == bool(drivers), 'driver/binding observation incomplete')
    occupancy = value['device_occupancy']
    require(occupancy['coverage_complete'] and occupancy['error_count'] == 0 and
            occupancy['observed_device_fds'] == 0 and not occupancy['users'], 'device occupancy incomplete/busy')
    require(not value['kernel_log_full']['severe_matches'], 'severe kernel log; review before any hardware action')
    require(value['candidate_module']['matches_expected'] and value['case_manifest']['matches_expected'] and
            all(x['matches_expected'] for x in value['starter_files'].values()) and len(value['starter_files']) == 3,
            'starter/driver/case manifest identity differs')
    require(value['candidate_srcversion'] == SRCVERSION, 'candidate srcversion differs')
    cmd = value['commands']['candidate_vermagic']
    require(cmd['ok'] and not cmd['stdout_truncated'] and cmd['stdout'].strip() ==
            KERNEL + ' SMP preempt mod_unload modversions aarch64', 'driver vermagic differs')
    require(value['commands']['dfx_service']['ok'] and
            value['commands']['dfx_service']['stdout'].strip() == 'active', 'dfx manager inactive')
    require({k: observed({'item': v}, 'item') for k, v in value['base_symbols'].items()} == SYMBOLS,
            'base-tree symbols differ')
    mounts = observed(value, 'root_mounts')
    require(mounts['filesystem'] == 'ext4' and 'rw' in mounts['options'].split(','), 'filesystem not read-write ext4')
    require(observed(value, 'project_disk')['free_bytes'] >= 256 * 1024 * 1024, 'insufficient free disk')
    return boot


def baseline(value):
    boot = common(value)
    require(observed(value, 'overlays') == [STARTER_OVERLAY] and
            observed(value, 'render_nodes') == [] and not value['zocl_module_present'] and
            not value['zocl_driver_present'] and observed(value, 'zocl_registered_drivers') == [] and
            observed(value, 'drm_driver_bindings') == [], 'not an unused starter baseline')
    clk = observed(value, 'pl0_clock')
    require(99999000 <= clk['rate_hz'] <= 100000000, 'starter clock record differs')
    return {'boot_id': boot, 'clock_framework': clk,
            'note': 'Counts/rate are OS observations, not physical reset/clock proof'}


def same_baseline(old, current):
    baseline(current)
    for name in ('boot_id', 'kernel', 'architecture', 'overlays', 'render_nodes',
                 'zocl_module_present', 'zocl_driver_present', 'starter_files',
                 'candidate_module', 'candidate_srcversion', 'base_symbols',
                 'zocl_registered_drivers', 'drm_driver_bindings'):
        require(current[name] == old[name], 'baseline changed: ' + name)


def loaded(value, boot):
    require(common(value) == boot, 'boot changed during load')
    require(observed(value, 'overlays') == [APP + '_image_1'] and value['zocl_module_present'] and
            value['zocl_driver_present'] and value.get('loaded_srcversion') == SRCVERSION and
            'zocl-drm' in observed(value, 'zocl_registered_drivers') and
            observed(value, 'render_nodes') == ['renderD128'], 'candidate overlay/driver/nodes differ')
    bindings = observed(value, 'drm_driver_bindings')
    require(len(bindings) == 1, 'one complete render binding required')
    validate_binding(bindings[0])
    clock = observed(value, 'pl0_clock')
    require(clock['rate_hz'] in (125000000, 124999999) and
            clock['enable_count'] > 0 and clock['prepare_count'] > 0,
            'clock framework contract not met; do not read CSR or change clock speculatively')
    return {'status': 'LOAD_OS_OBSERVATIONS_MATCH_NOT_CSR_OR_RESET_PROOF',
            'driver_binding': bindings[0],
            'clock_framework': clock, 'PL_START_commands_issued': 0,
            'physical_PL_starts': 'NOT_MEASURED_NO_CSR_ACCESS',
            'reset_and_PS_widths': 'NOT_CHECKED', 'CSR': 'BLOCKED', 'START': 'BLOCKED'}
