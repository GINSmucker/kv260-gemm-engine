"""Explicit unattended-demo profile. Reuses pinned v3 observation helpers.

This does not claim UART logging. It replaces the old human-review checkpoint
with durable local records and a mandatory current-boot CPU/PL self-test.
Only an unused starter baseline is accepted; there is no recovery/unload path.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from common import APP, STATE, create, encoded, need, pin, replace

PACKAGE = Path('/home/ubuntu/fpga_project/opt50_integration/prepare_20261024_fd96539d_v1')
CASES = Path('/home/ubuntu/fpga_project/opt50_integration/data/axi_cases')
FIRMWARE = Path('/usr/lib/firmware/xilinx/kv260_opt50_fd96539d_4cbcf732')


def helpers():
    # Module names match the original helpers' explicit imports.
    sys.path.insert(0, str(APP / 'loader_source'))
    import board, artifacts, firmware
    return board, artifacts, firmware


def barriers(board, tools):
    board.validate_tools(tools)
    full = board.query(tools, 'dmesg')
    need(not board.SEVERE.search(full), 'severe kernel diagnostic; preserve state')
    users = board.snapshot_api().device_users(Path('/proc'))
    need(users['coverage_complete'] and users['error_count'] == 0 and
         users['observed_device_fds'] == 0 and not users['users'], 'device busy or incomplete ownership observation')


def prepare_files():
    board, artifacts, firmware = helpers()
    board.root_linux()
    tools = board.tools()
    audit = artifacts.audit(PACKAGE, CASES)
    before = board.collect(CASES, tools)
    board.baseline(before)
    expected = firmware.expected_from_audit(PACKAGE, audit, board.APP)
    view = firmware.inspect(FIRMWARE, expected, board.APP)
    need(view['disposition'] == 'REUSE_VERIFIED_READ_ONLY', 'verified installed OPT50 firmware required; no implicit installation')
    return board, firmware, tools, audit, before, expected, view


def load(boot, folder, update):
    board, firmware, tools, audit, before, expected, view = prepare_files()
    need(board.observed(before, 'boot_id') == boot, 'boot changed')
    create(folder / 'load_preflight.json', encoded({'schema': 'opt50_demo_headless_load_v1',
           'boot_id': boot, 'uart_logging_claimed': False, 'authorization': 'installed_autoboot_demo_v1',
           'before': before, 'audit': audit, 'firmware': view, 'tools': tools}))
    driver = folder / 'zocl_candidate.ko'
    raw = board.DRIVER.read_bytes()
    need(pin(raw)['sha256'] == board.DRIVER_SHA, 'driver changed')
    create(driver, raw)
    metadata = Path(audit['source_paths']['metadata']).read_bytes()
    need(pin(metadata)['sha256'] == '715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551', 'metadata differs')
    create(folder / 'metadata.xclbin', metadata)
    create(folder / 'xrt.ini', Path(audit['source_paths']['xrt_ini']).read_bytes())
    board.same_baseline(before, board.collect(CASES, tools))
    started = time.monotonic()
    for index, (phase, name, args) in enumerate((
            ('REMOVE_STARTER', 'dfx-mgr-client', ['-remove', '0']),
            ('LOAD_DRIVER', 'insmod', [str(driver)]),
            ('LOAD_OPT50', 'dfx-mgr-client', ['-load', board.APP]),
            ('SETTLE', 'udevadm', ['settle', '--timeout=5'])), 1):
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot
             and time.monotonic()-started < 900, 'load admission expired')
        firmware.assert_same(FIRMWARE, view, expected, board.APP)
        barriers(board, tools)
        argv = [tools[name]['entry'], *args]
        create(folder / ('load_%02d_intent.json' % index), encoded({'phase': phase, 'argv': argv}))
        update(phase + '_INTENT_DURABLE')
        with open(folder / ('load_%02d.stdout' % index), 'xb', buffering=0) as out, \
             open(folder / ('load_%02d.stderr' % index), 'xb', buffering=0) as err:
            # No subprocess timeout/kill/retry after a hardware mutation begins.
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       env={'PATH': board.SAFE_PATH, 'LANG': 'C.UTF-8', 'HOME': '/root'})
            code = process.wait()
            os.fsync(out.fileno()); os.fsync(err.fileno())
        create(folder / ('load_%02d_return.json' % index), encoded({'phase': phase, 'returncode': code}))
        need(code == 0, 'load command failed; no automatic retry: ' + phase)
        barriers(board, tools)
        if phase == 'REMOVE_STARTER':
            need(board.snapshot_api().list_names(Path('/sys/kernel/config/device-tree/overlays')) == [], 'starter remains')
        if phase == 'LOAD_DRIVER':
            need(Path('/sys/module/zocl/srcversion').read_text().strip() == board.SRCVERSION, 'driver identity differs')
    after = board.collect(CASES, tools)
    proof = board.loaded(after, boot)
    create(folder / 'loaded.json', encoded({'observations': after, 'proof': proof}))
    update('OPT50_LOADED_SELFTEST_PENDING')
