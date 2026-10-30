"""Synthetic fault injection only. No board, command, driver or device access."""
from copy import deepcopy
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

import board
import durable
import window

TEST_BOOT = '5ed7d791-830f-4a29-b19a-ae2995bddae5'  # Synthetic fixture, not a production boot restriction.


def obs(value):
    return {'ok': True, 'value': value}


def baseline():
    return {'kernel': obs(board.KERNEL), 'architecture': 'aarch64',
            'boot_id': obs('1db5ef5e-1934-4a3d-b374-692eb8f184be'),
            'fpga_state': obs('operating'), 'overlays': obs([board.STARTER_OVERLAY]),
            'render_nodes': obs([]), 'zocl_module_present': False, 'zocl_driver_present': False,
            'zocl_registered_drivers': obs([]), 'drm_driver_bindings': obs([]),
            'pl0_clock': obs({'rate_hz': 99999999, 'enable_count': 0, 'prepare_count': 0}),
            'device_occupancy': {'coverage_complete': True, 'error_count': 0,
                                 'observed_device_fds': 0, 'users': []},
            'kernel_log_full': {'severe_matches': []},
            'candidate_module': {'matches_expected': True}, 'case_manifest': {'matches_expected': True},
            'starter_files': {n: {'matches_expected': True} for n in ('bit', 'dtbo', 'shell')},
            'candidate_srcversion': board.SRCVERSION,
            'commands': {'candidate_vermagic': {'ok': True, 'stdout_truncated': False,
                        'stdout': board.KERNEL + ' SMP preempt mod_unload modversions aarch64\n'},
                         'dfx_service': {'ok': True, 'stdout': 'active\n'}},
            'base_symbols': {k: obs(v) for k, v in board.SYMBOLS.items()},
            'root_mounts': obs({'filesystem': 'ext4', 'options': 'rw,relatime'}),
            'project_disk': obs({'free_bytes': 1024 ** 3})}


def loaded():
    value = baseline()
    value.update(overlays=obs([board.APP + '_image_1']), render_nodes=obs(['renderD128']),
                 zocl_module_present=True, zocl_driver_present=True, loaded_srcversion=board.SRCVERSION,
                 pl0_clock=obs({'rate_hz': 124999999, 'enable_count': 1, 'prepare_count': 1}))
    value['zocl_registered_drivers'] = obs(['zocl-drm', 'zocl_csr_intc'])
    value['drm_driver_bindings'] = obs([{'name': 'renderD128', 'driver': board.EXPECTED_DRIVER,
        'module': board.EXPECTED_MODULE, 'of_node': board.EXPECTED_OF_NODE,
        'compatible': ['xlnx,zocl'], 'status': 'okay', 'character_device': True,
        'sysfs_device': '/sys/devices/platform/axi/axi:zyxclmm_drm', 'sysfs_dev': '226:128',
        'major': 226, 'minor': 128, 'sysfs_char_matches': True,
        'reverse_devices': ['/sys/devices/platform/axi/axi:zyxclmm_drm']}])
    return value


class BoardTests(unittest.TestCase):
    def test_baseline_does_not_require_starter_clock_enable(self):
        self.assertEqual(board.baseline(baseline())['boot_id'], baseline()['boot_id']['value'])

    def test_loaded_not_CSR_or_reset_proof(self):
        result = board.loaded(loaded(), baseline()['boot_id']['value'])
        self.assertEqual(result['CSR'], 'BLOCKED')
        self.assertEqual(result['START'], 'BLOCKED')
        self.assertEqual(result['reset_and_PS_widths'], 'NOT_CHECKED')
        self.assertNotEqual(result['physical_PL_starts'], 0)

    def test_loaded_clock_requires_rate_and_both_reference_counts(self):
        for rate, enable, prepare in ((124998749, 1, 1), (125000000, 0, 1), (125000000, 1, 0)):
            value = loaded()
            value['pl0_clock'] = obs(dict(rate_hz=rate, enable_count=enable, prepare_count=prepare))
            with self.assertRaises(ValueError):
                board.loaded(value, baseline()['boot_id']['value'])

    def test_occupancy_and_identity_fail_closed(self):
        for field, replacement in (
                ('device_occupancy', {'coverage_complete': False, 'error_count': 1, 'observed_device_fds': 0, 'users': []}),
                ('candidate_module', {'matches_expected': False}),
                ('kernel_log_full', {'severe_matches': ['Oops:']}),
                ('overlays', obs(['old_opt50_image_1'])), ('candidate_srcversion', 'different'),
                ('root_mounts', obs({'filesystem': 'ext4', 'options': 'ro'}))):
            value = baseline(); value[field] = replacement
            with self.assertRaises(ValueError, msg=field):
                board.baseline(value)

    def test_boot_change_rejected(self):
        value = baseline(); value['boot_id'] = obs('00000000-0000-0000-0000-000000000000')
        with self.assertRaises(ValueError):
            board.same_baseline(baseline(), value)

    def test_snapshot_helper_identity(self):
        self.assertEqual(board.snapshot_api().EXPECTED_KERNEL, board.KERNEL)

    def test_snapshot_queries_use_sanitized_PATH_and_restore_environment(self):
        seen = []
        def collect(_):
            seen.append(os.environ['PATH'])
            return baseline()
        with mock.patch.dict(os.environ, {'PATH': '/synthetic/untrusted'}), \
                mock.patch.object(board, 'root_linux'), mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(board, 'snapshot_api', return_value=types.SimpleNamespace(
                    collect=collect, observe=lambda f, *args: obs(f(*args)))), \
                mock.patch.object(board, 'registered_drivers', return_value=[]), \
                mock.patch.object(board, 'binding_observations', return_value=[]), \
                mock.patch.object(board, 'query', side_effect=['all kernel warnings retained', board.SRCVERSION]):
            value = board.collect(Path('/synthetic/cases'), {})
            self.assertEqual(os.environ['PATH'], '/synthetic/untrusted')
        self.assertEqual(seen, [board.SAFE_PATH])
        self.assertEqual(value['kernel_log_full']['text'], 'all kernel warnings retained')

    def test_no_hardware_observation_imports(self):
        source = Path(board.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        imports = {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
        self.assertFalse({'mmap', 'xrt', 'board_checks'} & imports)
        self.assertNotIn("open('/dev/mem", source)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).parent / '.test_tmp'
        parent.mkdir(exist_ok=True)
        tmp = tempfile.TemporaryDirectory(prefix='synthetic_controller_', dir=parent)
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        (self.folder / 'events').mkdir()
        self.baseline = baseline(); self.baseline['boot_id'] = obs(TEST_BOOT)
        self.loaded = loaded(); self.loaded['boot_id'] = obs(TEST_BOOT)
        self.plan = {'boot_id': TEST_BOOT, 'unit': 'opt50-loadonly-v3-0123456789ab',
                     'created_monotonic': time.monotonic(), 'expires_seconds': 900,
                     'package': str(self.folder), 'cases': str(self.folder), 'before': self.baseline,
                     'artifacts': {'source_paths': {}, 'files': {}},
                     'command_tools': {n: {'entry': '/usr/sbin/' + n} for n in board.TOOL_NAMES}}
        self.lock = types.SimpleNamespace(fileno=lambda: 77)
        self.directory_sync = mock.patch.object(durable, 'fsync_dir')
        self.directory_sync.start(); self.addCleanup(self.directory_sync.stop)
        if os.name == 'nt':
            # Windows writer-close timestamps differ; real Windows directory
            # fsync is intentionally unsupported. Linux production is unchanged.
            identity = durable._identity
            portable = mock.patch.object(durable, '_identity', side_effect=lambda info: (*identity(info)[:3], 0, 0))
            portable.start(); self.addCleanup(portable.stop)
        self.guardian = window.Guardian(self.plan, self.folder, self.lock)
        barrier = mock.patch.object(self.guardian, 'safety_barrier')
        barrier.start(); self.addCleanup(barrier.stop)
        firmware_barrier = mock.patch.object(self.guardian, 'firmware_barrier')
        firmware_barrier.start(); self.addCleanup(firmware_barrier.stop)
        self.addCleanup(setattr, window, 'stop_requested', False)
        window.stop_requested = False

    def test_no_permission_no_run(self):
        with mock.patch.object(board, 'root_linux'), mock.patch.object(window, 'load_plan') as plan:
            with self.assertRaises(ValueError):
                window.run(self.folder / 'plan.json', False)
        plan.assert_not_called()

    def test_check_requires_uart_before_queries_or_writes(self):
        with mock.patch.object(board, 'root_linux'), mock.patch.object(board, 'collect') as collect:
            with self.assertRaises(ValueError):
                window.check(self.folder, self.folder, False)
        collect.assert_not_called()

    def test_service_command_has_no_autokill_or_restart(self):
        unit, command, status = window.commands(self.folder / 'plan.json', '0123456789ab')
        for flag in ('ExitType=cgroup', 'SendSIGKILL=no', 'Restart=no', 'OOMPolicy=continue', '--authorized-load-only'):
            self.assertIn(flag, command)
        self.assertNotIn('--authorized-pl-start', command)
        self.assertIn('status', status)

    def test_persistent_intent_precedes_spawn_and_lock_inherited(self):
        child = types.SimpleNamespace(pid=123456, poll=lambda: 0, returncode=0)
        def spawn(*args, **kwargs):
            records = [json.loads(p.read_bytes()) for p in sorted((self.folder / 'events').glob('event_*.json'))]
            self.assertTrue(any(x['phase'] == 'INTENT' for x in records))
            self.assertTrue(self.guardian.changed_or_uncertain)
            self.assertEqual(kwargs['pass_fds'], (77,))
            return child
        with mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=self.plan['boot_id']), \
                mock.patch.object(window.subprocess, 'Popen', side_effect=spawn) as process:
            self.guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        self.assertIsNone(self.guardian.child)
        self.assertTrue(self.guardian.changed_or_uncertain)

    def test_failed_intent_never_spawns(self):
        with mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=self.plan['boot_id']), \
                mock.patch.object(self.guardian.journal, 'append', side_effect=OSError('synthetic disk full')), \
                mock.patch.object(window.subprocess, 'Popen') as spawn:
            with self.assertRaises(OSError):
                self.guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        spawn.assert_not_called()
        self.assertFalse(self.guardian.changed_or_uncertain)

    def test_popen_failure_marks_uncertain(self):
        with mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=self.plan['boot_id']), \
                mock.patch.object(window.subprocess, 'Popen', side_effect=OSError('synthetic spawn uncertain')):
            with self.assertRaises(OSError):
                self.guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        self.assertTrue(self.guardian.changed_or_uncertain)

    def test_expired_plan_stops_before_command_even_after_preparation(self):
        self.plan['created_monotonic'] = time.monotonic() - 901
        with mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=self.plan['boot_id']), \
                mock.patch.object(window.subprocess, 'Popen') as spawn:
            with self.assertRaises(ValueError):
                self.guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        spawn.assert_not_called()

    def test_late_zero_exit_requires_review_instead_of_continuation(self):
        self.plan['created_monotonic'] = 100
        child = types.SimpleNamespace(pid=123456, poll=mock.Mock(side_effect=[None, 0]), returncode=0)
        with mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=self.plan['boot_id']), \
                mock.patch.object(window.time, 'monotonic', side_effect=[100, 100, 100, 100, 195, 195]), \
                mock.patch.object(window.time, 'sleep'), \
                mock.patch.object(window.subprocess, 'Popen', return_value=child):
            with self.assertRaisesRegex(ValueError, 'observation budget'):
                self.guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        self.assertTrue(self.guardian.changed_or_uncertain)
        self.assertIsNone(self.guardian.child)

    def test_expiry_or_stop_after_intent_does_not_spawn(self):
        for stop in (False, True):
            with self.subTest(stop=stop), mock.patch.object(board, 'validate_tools'), \
                    mock.patch.object(window.Path, 'read_text', return_value=self.plan['boot_id']), \
                    mock.patch.object(window.subprocess, 'Popen') as spawn, \
                    mock.patch.object(self.guardian, 'update') as update:
                self.plan['created_monotonic'] = 100
                window.stop_requested = False
                if stop:
                    update.side_effect = lambda *a, **k: setattr(window, 'stop_requested', True)
                    clocks = [100]
                else:
                    update.side_effect = None
                    clocks = [100, 1001]
                with mock.patch.object(window.time, 'monotonic', side_effect=clocks), self.assertRaises(ValueError):
                    self.guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
                spawn.assert_not_called()
                self.assertFalse(self.guardian.changed_or_uncertain)

    def test_safety_barrier_rejects_kernel_error_or_new_user(self):
        lock_path = self.folder / 'cooperative.lock'
        with lock_path.open('xb+') as stream, mock.patch.object(window, 'LOCK', lock_path):
            self.guardian.lock = stream
            free = {'coverage_complete': True, 'error_count': 0, 'observed_device_fds': 0, 'users': []}
            for kernel, occupancy in (('Oops: synthetic', free), ('okay', dict(free, observed_device_fds=1)),
                                      ('okay', dict(free, coverage_complete=False))):
                with mock.patch.object(board, 'query', return_value=kernel), \
                        mock.patch.object(board, 'snapshot_api', return_value=types.SimpleNamespace(device_users=lambda _: occupancy)):
                    with self.assertRaises(ValueError):
                        window.Guardian.safety_barrier(self.guardian, 'AFTER_REMOVE_STARTER')

    def test_safety_barrier_rejects_replaced_lock_path(self):
        first, replacement = self.folder / 'first.lock', self.folder / 'other.lock'
        with first.open('xb+') as stream, replacement.open('xb'):
            self.guardian.lock = stream
            with mock.patch.object(window, 'LOCK', replacement), mock.patch.object(board, 'query') as query:
                with self.assertRaisesRegex(ValueError, 'lock path was replaced'):
                    window.Guardian.safety_barrier(self.guardian, 'BEFORE_LOAD_OPT50')
            query.assert_not_called()

    def test_fresh_plan_boot_expiry_and_root_snapshot_checks(self):
        records = self.folder / 'records'
        path = records / 'plan_0123456789ab/plan.json'
        plan = dict(self.plan, schema='opt50_loadonly_plan_fd96539d_v3', token='0123456789ab',
                    hardware_executed=False, uart_logging_operator_confirmed=True, tool_files={},
                    scope=window.SCOPE, PL_START_commands_issued=0,
                    firmware={'directory': str(window.FIRMWARE), 'expected': {}})
        with mock.patch.object(window, 'RECORDS', records), mock.patch.object(window, 'HERE', path.parent / 'tools'), \
                mock.patch.object(window, 'private', side_effect=lambda p, *a: p), \
                mock.patch.object(window, 'json_read', return_value=plan), mock.patch.object(board, 'root_linux'), \
                mock.patch.object(window.Path, 'read_text', return_value=plan['boot_id']), \
                mock.patch.object(window, 'tools_manifest', return_value={}), mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(window.firmware, 'expected_from_audit', return_value={}):
            self.assertEqual(window.load_plan(path, fresh=True), plan)
            plan['created_monotonic'] = time.monotonic() - 901
            with self.assertRaisesRegex(ValueError, 'expired'):
                window.load_plan(path, fresh=True)
            plan['created_monotonic'] = time.monotonic()
            with mock.patch.object(window.Path, 'read_text', return_value='7082f6fb-bc83-4aee-b08e-1fdec8e8076e'):
                with self.assertRaisesRegex(ValueError, 'old boot'):
                    window.load_plan(path, fresh=True)

    def test_wrong_command_or_stop_never_spawns(self):
        with mock.patch.object(board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=self.plan['boot_id']), \
                mock.patch.object(window.subprocess, 'Popen') as spawn:
            with self.assertRaises(ValueError):
                self.guardian.command('LOAD_OPT50', 'dfx-mgr-client', '-load', 'old_app')
            window.stop_requested = True
            with self.assertRaises(ValueError):
                self.guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        spawn.assert_not_called()

    def test_pre_hardware_fault_does_not_cleanup_or_load(self):
        with mock.patch.object(self.guardian, 'stage_files', side_effect=OSError('synthetic copy failure')), \
                mock.patch.object(self.guardian, 'command') as command, \
                mock.patch.object(self.guardian, 'hold') as hold:
            self.assertEqual(self.guardian.execute(), 1)
        command.assert_not_called(); hold.assert_not_called()
        self.assertEqual(self.guardian.state['stage'], 'REFUSED_BEFORE_HARDWARE_CHANGE')

    def test_success_stops_at_OS_observations_holding_lock(self):
        phases = []
        def fake_command(phase, *_):
            self.guardian.changed_or_uncertain = True
            phases.append(phase)
        with mock.patch.object(self.guardian, 'stage_files'), \
                mock.patch.object(window.artifacts, 'audit', return_value=self.plan['artifacts']), \
                mock.patch.object(board, 'collect', side_effect=[self.baseline, self.loaded]), \
                mock.patch.object(board, 'snapshot_api', return_value=types.SimpleNamespace(list_names=lambda _: [])), \
                mock.patch.object(window.Path, 'read_text', return_value=board.SRCVERSION), \
                mock.patch.object(self.guardian, 'command', side_effect=fake_command), \
                mock.patch.object(self.guardian, 'hold') as hold:
            self.guardian.execute()
        self.assertEqual(phases, ['REMOVE_STARTER', 'LOAD_DRIVER', 'LOAD_OPT50', 'SETTLE'])
        self.assertEqual(hold.call_args.args[0], 'LOAD_ONLY_OBSERVED_AWAITING_REVIEW')
        self.assertEqual(hold.call_args.args[1]['CSR'], 'BLOCKED')
        self.assertEqual(self.guardian.state['PL_START_commands_issued'], 0)

    def test_failed_management_enters_retained_not_restoration(self):
        def uncertain(*_):
            self.guardian.changed_or_uncertain = True
            raise ValueError('synthetic nonzero load')
        with mock.patch.object(self.guardian, 'stage_files'), \
                mock.patch.object(window.artifacts, 'audit', return_value=self.plan['artifacts']), \
                mock.patch.object(board, 'collect', return_value=self.baseline), \
                mock.patch.object(self.guardian, 'command', side_effect=uncertain) as command, \
                mock.patch.object(self.guardian, 'hold') as hold:
            self.guardian.execute()
        self.assertEqual(command.call_count, 1)
        self.assertEqual(hold.call_args.args[0], 'RETAINED_STATE_UNCERTAIN')

    def test_cli_has_no_CSR_START_restore_entrypoint(self):
        for action in ('csr', 'start', 'restore', 'resume'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                window.main([action])


if __name__ == '__main__':
    unittest.main()
