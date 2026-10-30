"""Dynamic-boot/expiry gates: synthetic CPU fixtures only, no board commands."""
import contextlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import window
from test_controller import baseline, obs

BOOT_A = '5ed7d791-830f-4a29-b19a-ae2995bddae5'
BOOT_B = '7082f6fb-bc83-4aee-b08e-1fdec8e8076e'


class ReentryTests(unittest.TestCase):
    def test_new_names_never_alias_old_records_or_unit(self):
        self.assertEqual(window.RECORDS.as_posix(), '/var/lib/opt50-loadonly-fd96539d-v3')
        unit, command, _ = window.commands(Path('/synthetic/plan_0123456789ab/plan.json'), '0123456789ab')
        self.assertEqual(unit, 'opt50-loadonly-v3-0123456789ab')
        self.assertIn('--authorized-load-only', command)
        self.assertNotIn('--authorized-one-model', command)
        self.assertNotIn('--authorized-observer', command)
        self.assertFalse(hasattr(window, 'EXPECTED_BOOT'))

    def checked_plan(self, first_boot, second_boot):
        first, second = baseline(), baseline()
        first['boot_id'], second['boot_id'] = obs(first_boot), obs(second_boot)
        files = {name: {'bytes': 1, 'sha256': '0'*64} for name in window.CODE}
        with mock.patch.object(window.board, 'root_linux'), \
                mock.patch.object(window, 'tools_manifest', return_value={'files': files, 'manifest': {}}), \
                mock.patch.object(window.board, 'tools', return_value={}), \
                mock.patch.object(window, 'locked', return_value=contextlib.nullcontext()), \
                mock.patch.object(window.artifacts, 'audit', return_value={}), \
                mock.patch.object(window.board, 'collect', side_effect=[first, second]) as collect, \
                mock.patch.object(window.firmware, 'expected_from_audit', return_value={}), \
                mock.patch.object(window.firmware, 'inspect', return_value={'disposition': 'REUSE'}), \
                mock.patch.object(window, 'private'), mock.patch.object(window.secrets, 'token_hex', return_value='0123456789ab'), \
                mock.patch.object(window.durable, 'mkdir_new') as mkdir, \
                mock.patch.object(window.durable, 'copy_new'), mock.patch.object(window.durable, 'new_json') as write:
            try:
                result = window.check(Path('/synthetic/package'), Path('/synthetic/cases'), True)
            except BaseException:
                mkdir.assert_not_called(); write.assert_not_called()
                raise
        self.assertEqual(collect.call_count, 2)
        return result, write.call_args.args[1]

    def test_different_valid_boots_can_each_create_new_frozen_plan(self):
        for boot in (BOOT_A, BOOT_B):
            with self.subTest(boot=boot):
                result, plan = self.checked_plan(boot, boot)
                self.assertEqual(result['boot_id'], boot)
                self.assertEqual(plan['boot_id'], boot)
                self.assertEqual(plan['before']['boot_id']['value'], boot)
                self.assertEqual(plan['expires_seconds'], 900)
                self.assertEqual(plan['schema'], 'opt50_loadonly_plan_fd96539d_v3')
                self.assertFalse(plan['hardware_executed'])

    def test_boot_change_between_check_snapshots_never_creates_plan(self):
        with self.assertRaisesRegex(ValueError, 'baseline changed|boot changed'):
            self.checked_plan(BOOT_A, BOOT_B)

    def test_noncanonical_or_zero_boot_refused_before_plan(self):
        for boot in (BOOT_A.upper(), 'not-a-uuid', '00000000-0000-0000-0000-000000000000'):
            with self.subTest(boot=boot), self.assertRaises((ValueError, AttributeError)):
                self.checked_plan(boot, boot)

    def test_reboot_refuses_old_plan_before_mutation(self):
        plan = {'boot_id': BOOT_A, 'created_monotonic': 100, 'expires_seconds': 900}
        with mock.patch.object(window.Path, 'read_text', return_value=BOOT_B), \
                mock.patch.object(window.time, 'monotonic', return_value=101):
            with self.assertRaisesRegex(ValueError, 'old boot plan'):
                window.assert_plan_fresh(plan)

    def test_exact_900_seconds_expired_and_future_nan_bool_times_refused(self):
        for created, now, lifetime in ((100, 1000, 900), (100, 1001, 900), (101, 100, 900),
                                       (float('nan'), 100, 900), (True, 100, 900), (100, 101, True)):
            plan = {'boot_id': BOOT_A, 'created_monotonic': created, 'expires_seconds': lifetime}
            with self.subTest(created=created, now=now, lifetime=lifetime), \
                    mock.patch.object(window.Path, 'read_text', return_value=BOOT_A), \
                    mock.patch.object(window.time, 'monotonic', return_value=now), self.assertRaises(ValueError):
                window.assert_plan_fresh(plan)
        with mock.patch.object(window.Path, 'read_text', return_value=BOOT_A), \
                mock.patch.object(window.time, 'monotonic', return_value=999.999):
            window.assert_plan_fresh({'boot_id': BOOT_A, 'created_monotonic': 100, 'expires_seconds': 900})

    def guardian(self, folder):
        guardian = window.Guardian.__new__(window.Guardian)
        guardian.plan = {'boot_id': BOOT_A, 'created_monotonic': 100, 'expires_seconds': 900,
                         'command_tools': {'dfx-mgr-client': {'entry': '/synthetic/dfx-mgr-client'}}}
        guardian.folder = folder
        guardian.changed_or_uncertain = False
        guardian.index = 0
        guardian.child = None
        guardian.lock = types.SimpleNamespace(fileno=lambda: 77)
        guardian.journal = types.SimpleNamespace(append=mock.Mock())
        guardian.update = mock.Mock()
        return guardian

    def test_boot_change_before_command_has_no_management_or_staging_write(self):
        guardian = self.guardian(Path('/synthetic/execution'))
        with mock.patch.object(window.board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=BOOT_B), \
                mock.patch.object(guardian, 'firmware_barrier') as firmware, \
                mock.patch.object(guardian, 'safety_barrier') as safety, \
                mock.patch.object(window.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'old boot plan'):
                guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
        firmware.assert_not_called(); safety.assert_not_called(); spawn.assert_not_called()
        guardian.journal.append.assert_not_called(); guardian.update.assert_not_called()
        self.assertFalse(guardian.changed_or_uncertain)

    def test_boot_change_after_durable_intent_never_spawns(self):
        parent = Path(__file__).parent/'.test_tmp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='synthetic_boot_', dir=parent) as temporary:
            guardian = self.guardian(Path(temporary))
            with mock.patch.object(window.board, 'validate_tools'), \
                    mock.patch.object(window.Path, 'read_text', side_effect=[BOOT_A, BOOT_B]), \
                    mock.patch.object(window.time, 'monotonic', return_value=101), \
                    mock.patch.object(guardian, 'firmware_barrier'), mock.patch.object(guardian, 'safety_barrier'), \
                    mock.patch.object(window.os, 'fsync'), mock.patch.object(window.durable, 'fsync_dir'), \
                    mock.patch.object(window.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, 'old boot plan'):
                    guardian.command('REMOVE_STARTER', 'dfx-mgr-client', '-remove', '0')
            self.assertTrue(any(call.args[:2] == ('command', 'INTENT') for call in guardian.journal.append.call_args_list))
            spawn.assert_not_called()
            self.assertFalse(guardian.changed_or_uncertain)

    def test_stage_files_rejects_reboot_before_copies(self):
        guardian = self.guardian(Path('/synthetic/execution'))
        with mock.patch.object(window.Path, 'read_text', return_value=BOOT_B), \
                mock.patch.object(window.durable, 'copy_new') as copy, \
                mock.patch.object(window.firmware, 'stage') as stage:
            with self.assertRaisesRegex(ValueError, 'old boot plan'):
                guardian.stage_files()
        copy.assert_not_called(); stage.assert_not_called()

    def run_patches(self, plan):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(window.board, 'root_linux'))
        stack.enter_context(mock.patch.object(window, 'load_plan', return_value=plan))
        stack.enter_context(mock.patch.object(window, 'require_service'))
        stack.enter_context(mock.patch.object(window, 'locked', return_value=contextlib.nullcontext()))
        stack.enter_context(mock.patch.object(window.artifacts, 'audit', return_value={}))
        stack.enter_context(mock.patch.object(window.board, 'collect'))
        stack.enter_context(mock.patch.object(window.board, 'same_baseline'))
        stack.enter_context(mock.patch.object(window.firmware, 'expected_from_audit'))
        return stack

    def run_plan(self):
        return {'boot_id': BOOT_A, 'created_monotonic': 100, 'expires_seconds': 900,
                'package': '/synthetic/package', 'cases': '/synthetic/cases', 'artifacts': {},
                'before': {}, 'command_tools': {}, 'firmware': {}}

    def test_run_reboot_after_baseline_refuses_before_single_use_claim(self):
        plan = self.run_plan()
        with self.run_patches(plan), mock.patch.object(window.firmware, 'assert_same') as firmware, \
                mock.patch.object(window.Path, 'read_text', return_value=BOOT_B), \
                mock.patch.object(window.durable, 'mkdir_new') as mkdir, \
                mock.patch.object(window, 'Guardian') as create:
            with self.assertRaisesRegex(ValueError, 'old boot plan'):
                window.run(Path('/synthetic/plan/plan.json'), True)
        firmware.assert_called_once()
        mkdir.assert_not_called(); create.assert_not_called()

    def test_changed_firmware_refuses_before_single_use_claim(self):
        plan = self.run_plan()
        with self.run_patches(plan), \
                mock.patch.object(window.firmware, 'assert_same', side_effect=ValueError('firmware changed')), \
                mock.patch.object(window, 'assert_plan_fresh') as fresh, \
                mock.patch.object(window.durable, 'mkdir_new') as mkdir, \
                mock.patch.object(window, 'Guardian') as create:
            with self.assertRaisesRegex(ValueError, 'firmware changed'):
                window.run(Path('/synthetic/plan/plan.json'), True)
        fresh.assert_not_called(); mkdir.assert_not_called(); create.assert_not_called()

    def test_already_claimed_execution_never_starts_guardian(self):
        plan = self.run_plan()
        with self.run_patches(plan), mock.patch.object(window.firmware, 'assert_same'), \
                mock.patch.object(window, 'assert_plan_fresh'), \
                mock.patch.object(window.durable, 'mkdir_new', side_effect=FileExistsError('single-use already claimed')), \
                mock.patch.object(window, 'Guardian') as create:
            with self.assertRaises(FileExistsError):
                window.run(Path('/synthetic/plan/plan.json'), True)
        create.assert_not_called()

    def test_firmware_barrier_fails_before_any_management_effect(self):
        guardian = self.guardian(Path('/synthetic/execution'))
        with mock.patch.object(window.board, 'validate_tools'), \
                mock.patch.object(window.Path, 'read_text', return_value=BOOT_A), \
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

    def test_code_inventory_keeps_original_13_file_closure(self):
        self.assertEqual(len(window.CODE), len(set(window.CODE)))
        self.assertEqual(len(window.CODE), 13)
        self.assertTrue({'firmware.py', 'test_firmware.py', 'test_window_reentry.py',
                         'test_controller.py', 'test_artifacts.py', 'test_bindings.py'} <= set(window.CODE))


if __name__ == '__main__':
    unittest.main()
