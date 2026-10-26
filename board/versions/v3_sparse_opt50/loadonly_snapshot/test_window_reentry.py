"""New-boot and firmware identity gates, synthetic CPU-only tests."""
import contextlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import window


class ReentryTests(unittest.TestCase):
    def test_new_names_never_alias_old_records_or_unit(self):
        self.assertEqual(window.RECORDS.as_posix(), '/var/lib/opt50-loadonly-fd96539d-v2')
        unit, command, _ = window.commands(Path('/synthetic/plan_0123456789ab/plan.json'), '0123456789ab')
        self.assertEqual(unit, 'opt50-loadonly-v2-0123456789ab')
        self.assertIn('--authorized-load-only', command)
        self.assertNotIn('--authorized-one-model', command)
        self.assertNotIn('--authorized-observer', command)

    def test_wrong_boot_refuses_check_before_creating_plans(self):
        observed = {'boot_id': {'ok': True, 'value': '1db5ef5e-1934-4a3d-b374-692eb8f184be'}}
        with mock.patch.object(window.board, 'root_linux'), \
                mock.patch.object(window, 'tools_manifest'), mock.patch.object(window.board, 'tools'), \
                mock.patch.object(window, 'locked', return_value=contextlib.nullcontext()), \
                mock.patch.object(window.artifacts, 'audit'), mock.patch.object(window.board, 'baseline'), \
                mock.patch.object(window.board, 'collect', return_value=observed), \
                mock.patch.object(window.durable, 'mkdir_new') as mkdir, \
                mock.patch.object(window.firmware, 'inspect') as inspect:
            with self.assertRaisesRegex(ValueError, 'newly reviewed boot'):
                window.check(Path('/synthetic/package'), Path('/synthetic/cases'), True)
        mkdir.assert_not_called(); inspect.assert_not_called()

    def test_firmware_barrier_fails_before_any_management_effect(self):
        guardian = window.Guardian.__new__(window.Guardian)
        guardian.plan = {'boot_id': window.EXPECTED_BOOT, 'created_monotonic': 100,
                         'expires_seconds': 900, 'command_tools': {}}
        guardian.folder = Path('/synthetic/execution')
        guardian.changed_or_uncertain = False
        with mock.patch.object(window.board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=window.EXPECTED_BOOT), \
                mock.patch.object(window.time, 'monotonic', return_value=101), \
                mock.patch.object(guardian, 'firmware_barrier', side_effect=ValueError('firmware changed')) as barrier, \
                mock.patch.object(guardian, 'safety_barrier') as safety, \
                mock.patch.object(window.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'firmware changed'):
                guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        barrier.assert_called_once(); safety.assert_not_called(); spawn.assert_not_called()
        self.assertFalse(guardian.changed_or_uncertain)

    def test_unstaged_guardian_cannot_issue_management(self):
        guardian = window.Guardian.__new__(window.Guardian)
        guardian.staged_firmware = None
        with mock.patch.object(window.firmware, 'assert_same') as compare:
            with self.assertRaisesRegex(ValueError, 'precede'):
                guardian.firmware_barrier()
        compare.assert_not_called()

    def test_every_management_action_checks_frozen_firmware(self):
        source = Path(window.__file__).read_text(encoding='utf-8')
        body = source[source.index('    def command('):source.index('    def stage_files(')]
        self.assertLess(body.index('self.firmware_barrier()'), body.index('subprocess.Popen'))
        self.assertLess(body.index('self.firmware_barrier()'), body.index("self.safety_barrier('BEFORE_'"))
        self.assertEqual(body.count('self.firmware_barrier()'), 1)

    def test_run_checks_firmware_before_single_use_execution_directory(self):
        source = Path(window.__file__).read_text(encoding='utf-8')
        body = source[source.index('def run(path, authorized):'):source.index('def status(path):')]
        self.assertLess(body.index('firmware.assert_same('), body.index('durable.mkdir_new(folder)'))
        self.assertIn("'hardware_executed': False", source)

    def test_code_inventory_contains_new_firmware_gates_and_full_cpu_tests(self):
        self.assertEqual(len(window.CODE), len(set(window.CODE)))
        self.assertTrue({'firmware.py', 'test_firmware.py', 'test_window_reentry.py',
                         'test_controller.py', 'test_artifacts.py', 'test_bindings.py'} <= set(window.CODE))


if __name__ == '__main__':
    unittest.main()
