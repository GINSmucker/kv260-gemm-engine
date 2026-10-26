"""Synthetic new-boot fair coordinator gates; no owner/service/device actions."""
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

import run_model as window


class FairCoordinatorTests(unittest.TestCase):
    def test_authorization_precedes_any_helper_or_plan_access(self):
        with patch.object(window, 'api') as api, self.assertRaisesRegex(ValueError, 'permission'):
            window.run(Path('/not/a/plan'), False)
        api.assert_not_called()

    def test_old_profile_timing_is_not_accepted_for_new_plan(self):
        value = dict(measurement_profile='single-shot-low-io-v1', single_sample_only=True,
                     timing_scope='one sample', runner_run_ns=1, runner_run_ms=0.000001)
        with self.assertRaises(ValueError):
            window.validate_timing(value, ('runner_run',))
        value['measurement_profile'] = window.PROFILE
        window.validate_timing(value, ('runner_run',))

    def test_new_namespace_does_not_import_previous_boot_history(self):
        with tempfile.TemporaryDirectory(prefix='v2_prior_', dir=Path(__file__).parent) as name:
            records = Path(name)
            (records/'plan_0123456789ab').mkdir()
            c = types.SimpleNamespace(private_dir=lambda _: None)
            with patch.object(window, 'RECORDS', records), \
                 patch.object(window, 'load_plan', side_effect=AssertionError('unstarted plan cannot be run implicitly')):
                result = window.previous_models_clear(c)
            self.assertEqual(result, [])

    def test_empty_fresh_boot_namespace_is_valid(self):
        with tempfile.TemporaryDirectory(prefix='v2_missing_', dir=Path(__file__).parent) as name:
            with patch.object(window, 'RECORDS', Path(name)):
                self.assertEqual(window.previous_models_clear(types.SimpleNamespace()), [])

    def test_completion_mode_is_taken_from_validated_plan_not_receipt(self):
        c = Mock(); proof = Mock()
        with patch.object(window, 'load_plan', return_value={'execution_mode': 'all_dense'}), \
             patch.object(window, 'proof_api', return_value=proof):
            window.native_completion(c, Path('/synthetic/execution'), closed=True)
        proof.native_completion.assert_called_once_with(c, Path('/synthetic/execution'), window.read_events,
                                                        closed=True, execution_mode='all_dense')

    def test_guardian_monitor_uses_the_frozen_new_plan_identity(self):
        c, plan = Mock(), {'guardian_pid': 2988, 'guardian_start_ticks': 123}
        window.guardian_monitor(c, plan)
        c.monitor_guardian.assert_called_once_with(plan)

    def test_bad_execution_mode_rejected_before_helper_access(self):
        with patch.object(window, 'api') as api, self.assertRaisesRegex(ValueError, 'choose exactly'):
            window.check(True, 'unknown')
        api.assert_not_called()

    def test_native_job_exactly_binds_selected_execution_mode(self):
        c = types.SimpleNamespace(LOCK=Path('/synthetic/companion.lock'))
        p = {'plan_path': '/synthetic/plan_0123456789ab/plan.json',
             'token': '0123456789ab', 'execution_mode': 'all_dense'}
        raw = window.job_bytes(c, p, 123)
        fields = dict(line.split('=', 1) for line in raw.decode().splitlines())
        self.assertEqual(len(fields), 19)
        self.assertEqual(fields['schema'], 'opt50_fair_native_job_v1')
        self.assertEqual(fields['execution_mode'], 'all_dense')
        self.assertEqual(fields['expected_PL_starts'], '36')

    def test_unknown_prior_plan_is_not_skipped(self):
        with tempfile.TemporaryDirectory(prefix='v2_unknown_', dir=Path(__file__).parent) as name:
            records = Path(name)
            (records/'plan_0123456789ab/execution').mkdir(parents=True)
            c = types.SimpleNamespace(private_dir=lambda _: None)
            with patch.object(window, 'RECORDS', records), \
                 patch.object(window, 'load_plan', side_effect=ValueError('unknown profile')), \
                 self.assertRaisesRegex(ValueError, 'unknown profile'):
                window.previous_models_clear(c)

    def test_owner_observation_budget_never_kills_or_retries(self):
        coordinator = window.Coordinator.__new__(window.Coordinator)
        coordinator.owner = Mock(); coordinator.owner.poll.return_value = None
        coordinator.logs = []; coordinator.tick = Mock()
        process = Mock(); process.poll.return_value = None
        with patch.object(window, 'stop_requested', False), \
             patch.object(window.time, 'monotonic', side_effect=[0, 301]), \
             patch.object(window.time, 'sleep') as sleep, \
             self.assertRaisesRegex(ValueError, 'not killed or retried'):
            coordinator.wait_child(process, 300, 'RUNNING', watch_owner=True)
        process.kill.assert_not_called(); process.terminate.assert_not_called(); process.wait.assert_not_called()
        sleep.assert_not_called()

    def test_postrun_checks_are_proven_after_worker_and_before_single_stop(self):
        raw = Path(window.__file__).read_text(encoding='utf-8')
        begin = raw.index("result = self.worker_call('run')")
        body = raw[begin:raw.index('code = self.wait_child(self.owner', begin)]
        self.assertLess(body.index('metrics = admin.metrics()'), body.index('proof_before_stop = native_completion'))
        self.assertLess(body.index('proof_before_stop = native_completion'), body.index('admin.stop()'))
        self.assertIn('postrun_buffer_checks_verified=True', body)
        self.assertEqual(body.count('admin.stop()'), 1)


if __name__ == '__main__':
    unittest.main()
