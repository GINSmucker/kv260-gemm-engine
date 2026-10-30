"""Device-free safety/identity tests; no board, process signaling or XRT access."""
import copy
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).absolute().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item


r = module('test_b24_coordinator_entry', ROOT/'run_layer02.py')
resident = module('test_b24_resident_entry', ROOT/'support_py/resident.py')
BOOT = 'f0f2eed8-5820-4df5-ba19-d05527720f97'
TOKEN = 'a123456789ab'


def seed_plan():
    path = r.RECORDS/('plan_'+TOKEN)/'plan.json'
    return {'schema': 'opt50_b24_layer02_plan_v1', 'token': TOKEN,
            'unit': 'opt50-b24-layer02-'+TOKEN, 'plan_path': str(path),
            'scope': r.SCOPE, 'expires_seconds': 900, 'uart_logging_operator_confirmed': True,
            'old_board_lock_transferred': False, 'case_id': r.CASE, 'shape_MKN': r.SHAPE.copy(),
            'expected_START_commands': 2, 'expected_compared_INT32_outputs': 226944,
            'metadata': r.METADATA.copy(), 'source_model_sha256': r.MODEL_SHA,
            'source_archive_sha256': r.ARCHIVE_SHA, 'boot_id': BOOT,
            'guardian_pid': 2988, 'guardian_start_ticks': 123456,
            'created_monotonic': 10.0}


class PureContracts(unittest.TestCase):
    def test_plan_accepts_dynamic_current_identity_not_yesterday_constant(self):
        plan = seed_plan()
        self.assertEqual(r.validate_plan(plan, Path(plan['plan_path'])), plan)
        plan['boot_id'] = '7082f6fb-bc83-4aee-b08e-1fdec8e8076e'
        plan['guardian_pid'] = 43210
        self.assertEqual(r.validate_plan(plan, Path(plan['plan_path']))['guardian_pid'], 43210)

    def test_plan_model_route_and_identity_mutations_are_rejected(self):
        edits = {'source_model_sha256': '0'*64, 'case_id': 'layer_26_public',
                 'shape_MKN': [197, 576, 192], 'expected_START_commands': 36,
                 'metadata': {'bytes': 8560, 'sha256': '0'*64},
                 'boot_id': '../not-a-boot', 'created_monotonic': float('nan'),
                 'guardian_pid': True, 'old_board_lock_transferred': True}
        for key, value in edits.items():
            with self.subTest(field=key):
                plan = seed_plan(); plan[key] = value
                with self.assertRaises(ValueError):
                    r.validate_plan(plan, Path(plan['plan_path']))

    def test_exact_native_result_binds_same_new_weights_and_modes(self):
        plan = seed_plan()
        result = r.expected_result(plan)
        self.assertEqual(r.validate_result(result, plan)['compared_INT32_outputs'], 226944)
        for key, value in (('PL_START_commands_issued_by_worker', True),
                           ('source_model_sha256', '0'*64), ('physical_PL_starts', 2),
                           ('modes', [0, 0]), ('device_closed', False),
                           ('outputs_sha256', [r.PAYLOADS['Y_golden'][2], '0'*64])):
            with self.subTest(field=key):
                broken = copy.deepcopy(result); broken[key] = value
                with self.assertRaises(ValueError):
                    r.validate_result(broken, plan)

    def test_dense_and_sparse_golden_are_same_pruned_matrix(self):
        result = r.expected_result(seed_plan())
        self.assertEqual(len(set(result['outputs_sha256'])), 1)
        self.assertTrue(result['dense_reference_uses_same_pruned_weights'])
        self.assertFalse(result['accuracy_verified'])
        self.assertFalse(result['performance_verified'])

    def test_explicit_run_permission_required_before_helpers_or_native(self):
        with patch.object(r, 'helpers', side_effect=AssertionError('must not load helpers')):
            with self.assertRaisesRegex(ValueError, 'explicit'):
                r.run(Path('/not-a-plan'), False)

    def test_commands_match_native_exact_parent_argv(self):
        plan = seed_plan()
        plan['plan_path'] = '/var/lib/opt50-b24-layer02-v1/plan_'+TOKEN+'/plan.json'
        with patch.object(r, 'Path', PurePosixPath):
            run, status = r.command_lines(plan)
        self.assertIn('/tools/run_layer02.py run --plan ', run)
        self.assertTrue(run.endswith('--authorized-layer02-two-starts'))
        for property_value in ('SendSIGKILL=no', 'KillMode=process', 'RuntimeMaxSec=infinity',
                               'TimeoutStopSec=infinity', 'OOMPolicy=continue', 'Restart=no'):
            self.assertIn('--property='+property_value, run)
        self.assertIn(' status --plan ', status)

    def test_relative_paths_duplicate_json_and_nonfinite_values_refuse(self):
        for name in ('../old', 'data/../old', '/var/lib/anything', 'data\\x', 'C:/x', 'data//x'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                r.safe_relative(name)
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}'):
            with self.assertRaises(ValueError):
                r.decode_json(raw)

    def test_proc_flock_parser_does_not_accept_waiters_or_other_inode(self):
        raw = b'2: FLOCK ADVISORY WRITE 2988 08:01:91 0 EOF\n'
        self.assertEqual(resident.parse_flock(raw, 2988, (8, 1), 91)['inode'], 91)
        for changed in (raw.replace(b'WRITE', b'READ'), raw.replace(b'91', b'92'),
                        raw.replace(b'FLOCK', b'-> FLOCK'), raw+raw):
            with self.assertRaises(ValueError):
                resident.parse_flock(changed, 2988, (8, 1), 91)

    def test_guardian_profile_path_rejects_previous_namespace_before_io(self):
        with patch.object(resident, 'root_linux'):
            for path in ('/tmp/plan_a123456789ab/plan.json',
                         '/var/lib/opt50-fd96539d-v1/plan_a123456789ab/plan.json',
                         '/var/lib/opt50-loadonly-fd96539d-v2/../plan_a123456789ab/plan.json'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    resident.configure(Path(path))

    def test_guardian_changed_boot_rejected_before_bundle_review(self):
        path = PurePosixPath('/var/lib/opt50-loadonly-fd96539d-v2/plan_'+TOKEN+'/plan.json')
        value = {'boot_id': BOOT, 'token': TOKEN, 'unit': 'opt50-loadonly-v2-'+TOKEN}
        with patch.object(resident, 'Path', PurePosixPath), \
                patch.object(resident, 'root_linux'), patch.object(resident, 'private_dir'), \
                patch.object(resident, 'read_json', return_value=value), \
                patch.object(resident, '_proc_read', return_value=b'7082f6fb-bc83-4aee-b08e-1fdec8e8076e\n'), \
                patch.object(resident, '_guardian_bundle', side_effect=AssertionError('must not import old helper')):
            with self.assertRaisesRegex(ValueError, 'another boot'):
                resident.configure(path)

    def test_guardian_review_detects_pid_reuse_even_when_boot_matches(self):
        plan = seed_plan()
        plan['guardian_plan_pin'] = {'bytes': 2, 'sha256': r.identity(b'{}')['sha256']}
        plan['guardian_plan_inode'] = {'device': 1, 'inode': 9}
        c = types.SimpleNamespace(
            configure=lambda _: None,
            held_review=lambda: {'guardian': {'boot_id': BOOT, 'pid': 2988, 'start_ticks': 999999}})
        with self.assertRaisesRegex(ValueError, 'guardian identity changed'):
            r.review_guardian(c, Path('/ignored'), plan)


class NewFileFixtures(unittest.TestCase):
    def setUp(self):
        # Caller should set TEMP/TMP to a task-owned workspace path on Windows.
        self.temporary = tempfile.TemporaryDirectory(prefix='b24_coordinator_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fake_c(self):
        return types.SimpleNamespace(
            LOCK=resident.LOCK,
            decode_json=r.decode_json,
            read_json=lambda path, **_: r.decode_json(Path(path).read_bytes()),
            private_dir=lambda path: None,
        )

    def write_events(self, phases, extra=None):
        execution = self.root/'execution'; execution.mkdir(exist_ok=True)
        native = execution/'native_events'; native.mkdir(exist_ok=True)
        for sequence, phase in enumerate(phases, 1):
            record = {'schema': 'opt50_b24_layer02_native_event_v1', 'sequence': sequence,
                      'phase': phase, 'details': '', 'monotonic_ns': sequence*100}
            (native/('event_%06d.json' % sequence)).write_text(json.dumps(record))
        (native/('refusal.json' if extra == 'refusal' else 'result.json')).write_text('{}')
        return execution

    def test_native_journal_requires_done_for_both_modes_and_safe_close(self):
        start = ['DMA_LIFETIME_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED', 'DMA_COMPLETION_PROVEN']
        phases = ['RUNTIME_ENTERED', *start, *start, 'XRT_RESOURCE_CLOSE_INTENT', 'XRT_RESOURCES_CLOSED']
        execution = self.write_events(phases)
        proof = r.native_events(self.fake_c(), execution, 2)
        self.assertEqual(proof['START_commands_issued'], 2)
        path = execution/'native_events/event_000009.json'
        value = json.loads(path.read_text()); value['phase'] = 'XRT_MMIO_READ_RETURNED'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'lifecycle'):
            r.native_events(self.fake_c(), execution, 2)

    def test_native_journal_gaps_and_unknown_files_are_not_success(self):
        execution = self.write_events(['RUNTIME_ENTERED', 'XRT_RESOURCE_CLOSE_INTENT', 'XRT_RESOURCES_CLOSED'], 'refusal')
        self.assertEqual(r.native_events(self.fake_c(), execution, 0)['START_commands_issued'], 0)
        (execution/'native_events/unexpected.txt').write_text('not part of native evidence')
        with self.assertRaisesRegex(ValueError, 'unexpected native file'):
            r.native_events(self.fake_c(), execution, 0)

    def test_attempted_start_can_never_be_classified_as_zero_start_refusal(self):
        execution = self.write_events(['RUNTIME_ENTERED', 'DMA_LIFETIME_ACTIVE_BEFORE_START',
                                      'START_WRITE_INTENT', 'XRT_RESOURCE_CLOSE_INTENT', 'XRT_RESOURCES_CLOSED'], 'refusal')
        with self.assertRaisesRegex(ValueError, 'lifecycle'):
            r.native_events(self.fake_c(), execution, 0)

    def test_error_after_possible_spawn_is_retained_and_never_reported_as_pre_access_refusal(self):
        # Popen can create the child and still raise on parent-side setup.
        # The lifetime flag must already be true when that call happens.
        class Retained(Exception):
            pass
        execution = self.root/'execution'; execution.mkdir()
        journal = types.SimpleNamespace(append=lambda *_, **__: None)
        durable = types.SimpleNamespace(Journal=lambda _: journal, new_json=lambda *_, **__: None,
                                        fsync_dir=lambda _: None)
        value = seed_plan(); value['plan_path'] = str(self.root/'plan.json')
        value['guardian_plan'] = '/var/lib/opt50-loadonly-fd96539d-v2/plan_'+TOKEN+'/plan.json'
        value['created_monotonic'] = r.time.monotonic()
        coordinator = r.Coordinator(types.SimpleNamespace(), durable, value, 7)
        observations = []
        coordinator.update = lambda stage, **_: observations.append(stage)
        def fail_after_flag(*_, **__):
            self.assertTrue(coordinator.possibly_accessed)
            raise OSError('parent-side spawn setup failed')
        coordinator.hold = lambda reason: (_ for _ in ()).throw(Retained(reason))
        with patch.object(r, 'review_guardian', return_value={}), patch.object(r, 'validate_parent'), \
                patch.object(r, 'write_job', return_value=execution/'job.kv'), \
                patch.object(os, 'O_NOFOLLOW', 0, create=True), \
                patch.object(r.subprocess, 'Popen', side_effect=fail_after_flag), \
                patch.object(r, 'stop_requested', False):
            with self.assertRaisesRegex(Retained, 'spawn setup failed'):
                coordinator.execute()
        self.assertNotIn(r.REFUSED, observations)

    def test_spawned_worker_is_preserved_when_spawn_receipt_write_fails(self):
        class Retained(Exception):
            pass
        execution = self.root/'execution'; execution.mkdir()
        def append(action, phase, **_):
            if (action, phase) == ('worker', 'SPAWNED'):
                raise OSError('durable spawned receipt failed')
        journal = types.SimpleNamespace(append=append)
        durable = types.SimpleNamespace(Journal=lambda _: journal, new_json=lambda *_, **__: None,
                                        fsync_dir=lambda _: None)
        value = seed_plan(); value.update(plan_path=str(self.root/'plan.json'), guardian_plan='/known',
                                           created_monotonic=r.time.monotonic())
        worker = types.SimpleNamespace(pid=12345)
        coordinator = r.Coordinator(self.fake_c(), durable, value, 7)
        stages = []
        coordinator.update = lambda stage, **_: stages.append(stage)
        coordinator.hold = lambda reason: (_ for _ in ()).throw(Retained(reason))
        with patch.object(r, 'review_guardian', return_value={}), patch.object(r, 'validate_parent'), \
                patch.object(r, 'write_job', return_value=execution/'job.kv'), \
                patch.object(os, 'O_NOFOLLOW', 0, create=True), \
                patch.object(r.subprocess, 'Popen', return_value=worker), patch.object(r, 'stop_requested', False):
            with self.assertRaisesRegex(Retained, 'spawned receipt failed'):
                coordinator.execute()
        self.assertIs(coordinator.child, worker)
        self.assertTrue(coordinator.possibly_accessed)
        self.assertNotIn(r.PASS, stages)
        self.assertNotIn(r.REFUSED, stages)

    def _completed_worker(self, warning=b''):
        class Retained(Exception):
            pass
        start = ['DMA_LIFETIME_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED', 'DMA_COMPLETION_PROVEN']
        execution = self.write_events(['RUNTIME_ENTERED', *start, *start,
                                       'XRT_RESOURCE_CLOSE_INTENT', 'XRT_RESOURCES_CLOSED'])
        value = seed_plan(); value.update(plan_path=str(self.root/'plan.json'), guardian_plan='/known',
                                           created_monotonic=r.time.monotonic())
        result = r.expected_result(value)
        (execution/'native_events/result.json').write_text(json.dumps(result))
        stages, lifecycle = [], []
        def new_json(path, record):
            Path(path).write_text(json.dumps(record))
        journal = types.SimpleNamespace(append=lambda action, phase, **_: lifecycle.append((action, phase)))
        durable = types.SimpleNamespace(Journal=lambda _: journal, new_json=new_json, fsync_dir=lambda _: None)
        coordinator = r.Coordinator(self.fake_c(), durable, value, 7)
        coordinator.update = lambda stage, **_: stages.append(stage)
        coordinator.hold = lambda reason: (_ for _ in ()).throw(Retained(reason))
        polled = []
        def poll():
            polled.append(True)
            return 0
        worker = types.SimpleNamespace(pid=12345, returncode=0, poll=poll)
        def spawn(*_, **kwargs):
            kwargs['stdout'].write((json.dumps(result)+'\n').encode())
            kwargs['stderr'].write(warning)
            return worker
        def review(*_, **__):
            if polled:
                self.assertIsNone(coordinator.child)
                self.assertIn(('worker', 'RETURNED'), lifecycle)
            return {'status': 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START'}
        with patch.object(r, 'review_guardian', side_effect=review), patch.object(r, 'validate_parent'), \
                patch.object(r, 'write_job', return_value=execution/'job.kv'), \
                patch.object(r, 'read', side_effect=lambda path, **_: Path(path).read_bytes()), \
                patch.object(os, 'O_NOFOLLOW', 0, create=True), patch.object(r.subprocess, 'Popen', side_effect=spawn), \
                patch.object(r, 'stop_requested', False):
            if warning:
                with self.assertRaisesRegex(Retained, 'diagnostics'):
                    coordinator.execute()
            else:
                self.assertEqual(coordinator.execute(), 0)
        return execution, stages, lifecycle

    def test_closed_native_success_reaped_before_after_review_and_completion(self):
        execution, stages, lifecycle = self._completed_worker()
        self.assertEqual(stages[-1], r.PASS)
        self.assertIn(('worker', 'RETURNED'), lifecycle)
        completion = json.loads((execution/'completion.json').read_text())
        self.assertTrue(completion['worker_reaped'])
        self.assertTrue(completion['device_closed'])
        self.assertEqual(completion['worker_returncode'], 0)

    def test_even_zero_exit_native_warning_is_retained_instead_of_passing(self):
        execution, stages, _ = self._completed_worker(b'unexpected warning\n')
        self.assertNotIn(r.PASS, stages)
        self.assertFalse((execution/'completion.json').exists())

    def test_job_has_exact_26_fields_new_pins_and_exclusive_creation(self):
        root = self.root/('plan_'+TOKEN); root.mkdir()
        data = root/'tools/data'; data.mkdir(parents=True)
        for name, _, _ in r.PAYLOADS.values():
            shutil.copyfile(ROOT/'data'/name, data/name)
        (data/'source').mkdir()
        shutil.copyfile(ROOT/'data/source/B_MANIFEST.json', data/'source/B_MANIFEST.json')
        execution = root/'execution'; execution.mkdir()
        value = seed_plan(); value['plan_path'] = str(root/'plan.json')
        durable = types.SimpleNamespace(fsync_dir=lambda _: None)
        # File privacy is a Linux-board contract, not a simulated Windows claim.
        portable_read = lambda path, **_: Path(path).read_bytes()
        with patch.object(r, 'read', side_effect=portable_read), patch.object(os, 'O_NOFOLLOW', 0, create=True):
            path = r.write_job(self.fake_c(), durable, value, execution)
            fields = dict(line.split('=', 1) for line in path.read_text(encoding='utf-8').splitlines())
            self.assertEqual(len(fields), 26)
            self.assertEqual(fields['source_model_sha256'], r.MODEL_SHA)
            self.assertEqual(fields['package_manifest_sha256'], r.PACKAGE_SHA)
            self.assertTrue(fields['package_manifest'].endswith('source'+os.sep+'B_MANIFEST.json'))
            self.assertEqual(fields['W_sparse_meta_sha256'], r.PAYLOADS['W_sparse_meta'][2])
            with self.assertRaises(FileExistsError):
                r.write_job(self.fake_c(), durable, value, execution)

    def test_data_verification_uses_b_new_golden_not_old_layer26(self):
        # lstat/fstat ctime differ on this Windows/Python combination. This
        # fixture tests fixed payload pins; the POSIX reader stays unchanged.
        with patch.object(r, 'read', side_effect=lambda path, **_: Path(path).read_bytes()):
            result = r.verify_inputs(ROOT)
        self.assertEqual(result['case_id'], r.CASE)
        self.assertEqual(result['files']['Y_golden.bin']['sha256'], r.PAYLOADS['Y_golden'][2])


if __name__ == '__main__':
    unittest.main()
