"""Fake-front tests: no XRT, subprocess, Native owner, or Linux roots are used.

Directory privacy/durable JSON writes are explicit device-free fakes. Production
uses the previously verified native durable module and root-private reader.
"""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parent
sys.path.insert(0, str(HERE.parent))
import run_first50 as first

BOOT = '2b0eaebd-2dd9-45c5-b135-26315bfdbb7f'
OTHER_BOOT = '405624bf-6e23-4d7f-996d-b5bfec36aa7f'
GUARDIAN = '/var/lib/opt50-loadonly-fd96539d-v3/plan_95074ccbbd4d/plan.json'


class FakeAccess:
    def __init__(self, runtime):
        self.runtime = runtime

    def root_linux(self):
        self.runtime.root_checks += 1

    def real(self, path):
        text = Path(path).as_posix()
        if text == '/var/lib/opt50-b24-model-v1':
            return self.runtime.root/'records'
        if text.startswith('/var/lib/opt50-b24-model-v1/'):
            return self.runtime.root/'records'/text.split('/var/lib/opt50-b24-model-v1/', 1)[1]
        return Path(path)

    def private_dir(self, path):
        if not self.real(path).is_dir():
            raise ValueError('fake private directory missing')

    def read_file(self, path, private=False, limit=32*1024*1024):
        if not private:
            raise AssertionError('phase receipt reads must request private input')
        raw = self.real(path).read_bytes()
        if len(raw) > limit:
            raise ValueError('fake bounded read exceeded')
        return raw

    def read_json(self, path, private=False):
        return first.decode(self.read_file(path, private=private))

    def companion_locked(self):
        raise AssertionError('the first50 wrapper must never acquire the companion lock')


class FakeDurable:
    @staticmethod
    def mkdir_new(path):
        Path(path).mkdir()

    @staticmethod
    def new_json(path, value):
        with Path(path).open('x', encoding='utf-8') as out:
            json.dump(value, out)

    @staticmethod
    def replace_json(path, value):
        # Explicit fake: atomic POSIX persistence is covered by native tests.
        Path(path).write_text(json.dumps(value), encoding='utf-8')


class FakeRuntime:
    def __init__(self, root, scenarios=()):
        self.root = root
        self.clock = 1000.0
        self.scenarios = list(scenarios)
        self.calls = []
        self.root_checks = 0
        self.platform_checks = 0
        self.service_calls = 0
        self.service_waits = 0
        self.service_timeout = False
        self.lock_failed = False
        self.current_boot = BOOT
        self.api_calls = 0
        self.change_source_api_call = None
        self.tools_raw = b'{"schema":"fake exact sealed source"}'
        self.tools = {'manifest': first.identity(self.tools_raw), 'files': {'run_first50.py': first.identity(b'fake source')}}
        self.a = types.SimpleNamespace(c=FakeAccess(self), durable=FakeDurable(), tools=self.tools)

    def ensure_native(self):
        self.platform_checks += 1  # Fake; does not claim Windows is Linux.

    def api(self):
        self.api_calls += 1
        if self.api_calls == self.change_source_api_call:
            return types.SimpleNamespace(c=self.a.c, durable=self.a.durable,
                                         tools={'manifest': first.identity(b'changed source'), 'files': {}})
        return self.a

    def boot(self):
        return self.current_boot

    def lock(self, a):
        if self.lock_failed:
            raise ValueError('fake wrapper lock already held')
        return nullcontext()

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    def service(self, unit, timeout=2.0):
        self.service_calls += 1
        if self.service_timeout:
            raise subprocess.TimeoutExpired(['systemctl', 'show', unit], timeout)
        if self.service_waits:
            self.service_waits -= 1
            return {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running',
                    'MainPID': '123', 'ExecMainStatus': '0', 'Result': 'success'}
        return {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'exited',
                'MainPID': '0', 'ExecMainStatus': '0', 'Result': 'success'}

    def event_paths(self, root):
        actual = self.a.c.real(root/'batch_events')
        return [root/'batch_events'/path.name for path in sorted(actual.glob('event_*.json'))]

    def open_log(self, a, path):
        return Path(path).open('x', encoding='utf-8')

    def write(self, path, value):
        actual = self.a.c.real(path)
        actual.parent.mkdir(parents=True, exist_ok=True)
        actual.write_text(json.dumps(value), encoding='utf-8')

    def front(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls)
        scenario = self.scenarios[index-1] if index <= len(self.scenarios) else {}
        if scenario.get('refuse_before_plan'):
            print('{"stage":"REFUSED_BEFORE_ANY_NATIVE_OWNER"}', flush=True)
            return 2
        token = '%012x' % index
        path = '/var/lib/opt50-b24-model-v1/plan_'+token+'/plan.json'
        root = Path(path).parent
        for name in ('tools', 'model', 'batch_events'):
            self.a.c.real(root/name).mkdir(parents=True)
        self.a.c.real(root/'tools/tools_manifest.json').write_bytes(self.tools_raw if not scenario.get('wrong_source') else b'wrong source')
        start, count = kwargs['start'], kwargs['count']
        report = {'stage': first.NEW_PLAN, 'plan': path, 'sample_start': start,
                  'sample_count': count, 'total_START_budget': count*36,
                  'automatic_renewal': False, 'ORT_intra_threads': 4, 'ORT_inter_threads': 1}
        if scenario.get('wrong_namespace'):
            report['plan'] = '/tmp/plan_000000000001/plan.json'
        if scenario.get('bad_budget'):
            report['total_START_budget'] += 1
        print(json.dumps(report), flush=True)
        if scenario.get('duplicate_plan'):
            print(json.dumps(report), flush=True)
        if scenario.get('crash_after_plan'):
            raise RuntimeError('fake front exception after possible submission')
        created = self.clock + 1
        duration = scenario.get('seconds_per_image', 3.0)
        gap = scenario.get('gap_seconds', 0.0)
        before, after = scenario.get('fixed_before', 2.0), scenario.get('fixed_after', 2.0)
        plan = {'schema': 'opt50_b24_acceptance_batch_plan_v1', 'plan_path': path,
                'token': token, 'unit': 'opt50-b24-model-'+token, 'measurement_profile': first.PROFILE,
                'source_model_sha256': first.SOURCE_MODEL, 'tool_files': self.tools,
                'boot_id': OTHER_BOOT if scenario.get('wrong_boot') else BOOT,
                'guardian_plan': GUARDIAN, 'sample_start': start, 'sample_count': count,
                'total_START_budget': count*36, 'expires_seconds': 900, 'expected_PL_starts': 36,
                'expected_Dense_calls': 12, 'expected_Sparse_calls': 24,
                'created_monotonic': created-10000 if scenario.get('old_plan') else created,
                'dataset_manifest': {'bytes': 100, 'sha256': 'a'*64}}
        if scenario.get('wrong_tool_table'):
            plan['tool_files'] = {'manifest': first.identity(b'unknown release'), 'files': {}}
        if scenario.get('wrong_profile'):
            plan['measurement_profile'] = 'unknown-profile'
        if scenario.get('wrong_guardian'):
            plan['guardian_plan'] = '/var/lib/another-guardian/plan.json'
        if scenario.get('future_plan'):
            plan['created_monotonic'] = created+100000
        if scenario.get('wrong_dataset'):
            plan['dataset_manifest']['sha256'] = 'b'*64
        accuracy = {'numerical_status': 'NUMERICAL_PASS', 'all_devices_closed': True,
                    'all_native_END_verified': True, 'all_postrun_buffer_checks_verified': True,
                    'images': count, 'compared_logits': count*1000, 'completed_PL_calls': count*36,
                    'Dense_calls': count*12, 'Sparse_calls': count*24,
                    'bitwise_mismatches': 1 if scenario.get('mismatch') else 0,
                    'bitwise_mismatch_images': 1 if scenario.get('mismatch') else 0,
                    'max_abs_difference': 0.0}
        done = {'stage': first.PASS, 'schema': 'opt50_b24_batch_completion_v1', 'plan_token': token,
                'closed': not scenario.get('not_closed'), 'owner_pid': None, 'child_pid': None,
                'sample_start': start, 'sample_count': count, 'committed_samples': count,
                'completed_PL_calls': count*36, 'Dense_calls': count*12, 'Sparse_calls': count*24,
                'compared_logits': count*1000, 'total_START_budget': count*36,
                'dataset_manifest': plan['dataset_manifest'], 'accuracy': accuracy}
        if scenario.get('partial'):
            done['committed_samples'] -= 1
        if scenario.get('unverified_buffers'):
            accuracy['all_postrun_buffer_checks_verified'] = False
        last_commit = created+before+(count-1)*(duration+gap)+duration
        state = {'stage': first.PASS, 'completion': done, 'boot_id': BOOT,
                 'owner_pid': None, 'child_pid': None, 'committed_samples': count,
                 'completed_PL_calls': count*36, 'heartbeat_monotonic': last_commit+after}
        self.write(root/'plan.json', plan)
        self.write(root/'batch_completion.json', done)
        self.write(root/'batch_status.json', state)
        rows = []
        for i in range(count):
            stamp = created+before+i*(duration+gap)
            for phase, timepoint in (('SAMPLE_ADMITTED_BEFORE_ANY_CHILD', stamp),
                                     ('SAMPLE_COMMITTED_NATIVE_CLOSED', stamp+duration)):
                snapshot = {'stage': phase, 'boot_id': BOOT, 'unit': plan['unit'],
                            'current_ordinal': start+i, 'heartbeat_monotonic': timepoint}
                rows.append({'schema': 'opt50_durable_event_v1', 'sequence': len(rows)+1,
                             'action': 'batch', 'phase': phase, 'state': snapshot})
        if scenario.get('missing_commit'):
            rows.pop()
        for row in rows:
            self.write(root/'batch_events'/('event_%06d.json' % row['sequence']), row)
        self.clock = last_commit+after+0.01
        self.service_waits = scenario.get('service_waits', 0)
        return scenario.get('returncode', 0)


class First50Tests(unittest.TestCase):
    def setUp(self):
        self.parent = HERE.parent/'.test_build/first50_fakes'
        self.parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='first50_', dir=self.parent)
        self.root = Path(self.tmp.name).resolve()
        self.assertEqual(self.root.parent, self.parent.resolve())
        self.addCleanup(self.cleanup)
        patch = mock.patch.object(first, 'ACTION_ROOT', self.root/'actions')
        patch.start(); self.addCleanup(patch.stop)

    def cleanup(self):
        self.assertEqual(self.root.parent, self.parent.resolve())
        self.assertTrue(self.root.name.startswith('first50_'))
        self.tmp.cleanup()

    def call(self, runtime, **kwargs):
        with redirect_stdout(io.StringIO()):
            return first.run_first50(GUARDIAN, '/var/lib/opt50-b24-layer02-v1/plan_012345678abc/plan.json',
                '/var/lib/opt50-b24-model-v1/plan_6efa0640b22b/plan.json', '/prepared/dataset',
                uart=True, authorized=True, runtime=runtime, **kwargs)

    def test_fast_complete_pilot_then_one_disjoint_45_image_phase(self):
        runtime = FakeRuntime(self.root)
        result = self.call(runtime)
        self.assertEqual(result['stage'], 'PASS_FIRST50_REQUESTED_RANGE_CLOSED')
        self.assertEqual([(x['start'], x['count']) for x in runtime.calls], [(20, 5), (25, 45)])
        self.assertIsNone(runtime.calls[0]['pilot_plan'])
        self.assertEqual(runtime.calls[1]['pilot_plan'].as_posix(), result['phase_plans'][0]['plan'])
        self.assertEqual(result['completed_PL_calls'], 1800)
        self.assertEqual(sum(x['count']*36 for x in runtime.calls), result['total_START_budget'])
        action = first.decode((Path(result['action'])/'action.json').read_bytes())
        self.assertFalse(action['index_prepared_by_wrapper'])
        self.assertEqual(action['maximum_new_images'], 50)
        self.assertTrue((Path(result['action'])/'phase_01.stdout').exists())
        self.assertTrue((Path(result['action'])/'phase_02.stdout').exists())

    def test_slow_pilot_saves_success_without_checking_main(self):
        runtime = FakeRuntime(self.root, [{'seconds_per_image': 30}])
        result = self.call(runtime)
        self.assertEqual(result['stage'], 'PASS_PILOT_ONLY_MAIN_NOT_SUBMITTED')
        self.assertEqual(result['completed_images'], 5)
        self.assertEqual(len(runtime.calls), 1)
        self.assertFalse(result['pilot_prediction']['fits_with_headroom'])
        self.assertEqual(result['pilot_prediction']['reserved_headroom_seconds'], 225)

    def test_inter_image_archive_gaps_are_included_in_prediction(self):
        runtime = FakeRuntime(self.root, [{'seconds_per_image': 1, 'gap_seconds': 30}])
        result = self.call(runtime)
        self.assertEqual(result['stage'], 'PASS_PILOT_ONLY_MAIN_NOT_SUBMITTED')
        self.assertEqual(result['pilot_prediction']['max_inter_image_gap_seconds'], 30)
        self.assertEqual(len(runtime.calls), 1)

    def test_requested_three_images_does_not_run_five_or_a_second_phase(self):
        runtime = FakeRuntime(self.root)
        result = self.call(runtime, start=100, count=3)
        self.assertEqual([(x['start'], x['count']) for x in runtime.calls], [(100, 3)])
        self.assertEqual(result['completed_PL_calls'], 108)

    def test_six_images_use_five_plus_one(self):
        runtime = FakeRuntime(self.root)
        result = self.call(runtime, count=6)
        self.assertEqual([x['count'] for x in runtime.calls], [5, 1])
        self.assertEqual(result['completed_images'], 6)

    def test_unknown_authority_missing_uart_and_invalid_budgets_refused_before_front(self):
        cases = [{'authorized': False, 'uart': True}, {'authorized': True, 'uart': False},
                 {'authorized': True, 'uart': True, 'count': 51},
                 {'authorized': True, 'uart': True, 'count': True},
                 {'authorized': True, 'uart': True, 'start': 1790, 'count': 50}]
        for kwargs in cases:
            runtime = FakeRuntime(self.root)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                first.run_first50(GUARDIAN, 'layer', 'previous', 'dataset', runtime=runtime, **kwargs)
            self.assertFalse(runtime.calls)

    def test_bad_pilot_receipts_never_submit_main(self):
        scenarios = ('duplicate_plan', 'wrong_namespace', 'bad_budget', 'wrong_source', 'wrong_boot',
                     'old_plan', 'not_closed', 'mismatch', 'partial', 'missing_commit',
                     'wrong_tool_table', 'wrong_profile', 'wrong_guardian', 'future_plan', 'unverified_buffers')
        for scenario in scenarios:
            # Use a fresh fake filesystem namespace for each scenario.
            subroot = self.root/scenario; subroot.mkdir()
            runtime = FakeRuntime(subroot, [{scenario: True}])
            with mock.patch.object(first, 'ACTION_ROOT', subroot/'actions'):
                with self.subTest(scenario=scenario), self.assertRaises(ValueError):
                    self.call(runtime)
            self.assertEqual(len(runtime.calls), 1)
            self.assertEqual(len(list((subroot/'actions').glob('action_*/failure.json'))), 1)

    def test_front_refusal_and_exception_are_never_retried(self):
        for scenario in ('refuse_before_plan', 'crash_after_plan'):
            subroot = self.root/scenario; subroot.mkdir()
            runtime = FakeRuntime(subroot, [{scenario: True}])
            with mock.patch.object(first, 'ACTION_ROOT', subroot/'actions'):
                with self.subTest(scenario=scenario), self.assertRaises((ValueError, RuntimeError)):
                    self.call(runtime)
            self.assertEqual(len(runtime.calls), 1)
            if scenario == 'crash_after_plan':
                action_path = next((subroot/'actions').glob('action_*/action.json'))
                action = first.decode(action_path.read_bytes())
                self.assertEqual(len(action['phase_plans']), 1)

    def test_failed_durable_plan_capture_stops_before_any_second_phase(self):
        runtime = FakeRuntime(self.root)
        original = FakeDurable.replace_json
        def fail_capture(path, value):
            if value.get('stage', '').endswith('_PLAN_CAPTURED_BEFORE_SUBMISSION'):
                raise OSError('fake durable capture failed')
            return original(path, value)
        with mock.patch.object(FakeDurable, 'replace_json', staticmethod(fail_capture)):
            with self.assertRaisesRegex(OSError, 'durable capture'):
                self.call(runtime)
        self.assertEqual(len(runtime.calls), 1)

    def test_second_phase_failure_never_causes_a_third_submission(self):
        runtime = FakeRuntime(self.root, [{}, {'returncode': 1}])
        with self.assertRaises(ValueError):
            self.call(runtime)
        self.assertEqual(len(runtime.calls), 2)

    def test_second_phase_dataset_identity_cannot_change(self):
        runtime = FakeRuntime(self.root, [{}, {'wrong_dataset': True}])
        with self.assertRaisesRegex(ValueError, 'dataset differs'):
            self.call(runtime)
        self.assertEqual(len(runtime.calls), 2)

    def test_public_source_change_after_pilot_prevents_the_main_check(self):
        runtime = FakeRuntime(self.root)
        runtime.change_source_api_call = 3
        with self.assertRaisesRegex(ValueError, 'source release changed'):
            self.call(runtime)
        self.assertEqual(len(runtime.calls), 1)

    def test_service_exit_is_readonly_waited_without_replaying_phase(self):
        runtime = FakeRuntime(self.root, [{'service_waits': 2}])
        result = self.call(runtime, count=5)
        self.assertEqual(result['completed_images'], 5)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.service_calls, 3)

    def test_service_uncertain_after_15_seconds_prevents_main(self):
        runtime = FakeRuntime(self.root, [{'service_waits': 100}])
        with self.assertRaisesRegex(ValueError, '15s'):
            self.call(runtime)
        self.assertEqual(len(runtime.calls), 1)
        self.assertLessEqual(runtime.service_calls, 15)

    def test_readonly_query_timeout_does_not_retry_or_submit_main(self):
        runtime = FakeRuntime(self.root); runtime.service_timeout = True
        with self.assertRaises(subprocess.TimeoutExpired):
            self.call(runtime)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.service_calls, 1)

    def test_production_query_bounds_only_systemctl_show_helper(self):
        unit = 'opt50-b24-model-012345678abc.service'
        with mock.patch.object(first.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired(['systemctl', 'show', unit], 2)) as query:
            with self.assertRaises(subprocess.TimeoutExpired):
                first.Runtime().service(unit)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(query.call_args.args[0][:3], ['systemctl', 'show', unit])
        self.assertEqual(query.call_args.kwargs['timeout'], 2.0)

    def test_independent_wrapper_lock_failure_prevents_any_front(self):
        runtime = FakeRuntime(self.root); runtime.lock_failed = True
        with self.assertRaisesRegex(ValueError, 'wrapper lock'):
            self.call(runtime)
        self.assertFalse(runtime.calls)

    def test_duplicate_keys_are_not_silently_accepted_as_metadata(self):
        line = ('{"stage":"'+first.NEW_PLAN+'","plan":"a","plan":"b"}\n').encode()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            first.plan_metadata(line, 20, 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
