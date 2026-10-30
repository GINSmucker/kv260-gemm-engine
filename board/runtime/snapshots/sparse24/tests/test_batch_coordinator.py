"""Device-free lifecycle tests. Windows fixtures mock only POSIX ownership/fsync.

No owner, device, MMIO, image decoding or START is executed by this suite.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


r = module('batch_coordinator_test', ROOT/'run_model.py')
w = module('batch_worker_test', ROOT/'model_worker.py')
front = module('batch_front_test', ROOT/'run_once.py')
metrics = module('batch_metrics_test', ROOT/'accuracy_metrics.py')


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


class Journal:
    def __init__(self, events): self.events = events
    def append(self, name, phase, **fields): self.events.append((name, phase, fields))


class Fixture:
    def __init__(self, root):
        self.root, self.events = root, []
        self.c = types.SimpleNamespace(read_file=lambda p, **_: Path(p).read_bytes(),
                                       read_json=lambda p, **_: json.loads(Path(p).read_bytes()))
        def new_json(path, value):
            with Path(path).open('xb') as stream: stream.write(json.dumps(value).encode())
        self.durable = types.SimpleNamespace(new_json=new_json, replace_json=write_json,
            mkdir_new=lambda p: Path(p).mkdir(), fsync_dir=lambda _: None, Journal=lambda _: Journal(self.events))
        self.a = types.SimpleNamespace(c=self.c, durable=self.durable, metrics=metrics)
        self.a.guard = types.SimpleNamespace(initial_review={}, boundary=Mock(return_value={}),
                                             monitor=Mock(return_value={}), finish=Mock(return_value={}))
        self.a.guardian_checkpoint = types.SimpleNamespace(bind_checkpoint=Mock(return_value=self.a.guard))
        self.a.history_index = types.SimpleNamespace(admit=Mock(return_value={}),
                                                    append_closed=Mock(return_value={}))
        self.plan = {'plan_path': str(root/'plan.json'), 'token': '0123456789ab', 'boot_id': 'fixture-boot',
                     'unit': 'opt50-b24-model-0123456789ab', 'guardian_pid': 17, 'sample_start': 0,
                     'sample_count': 2, 'total_START_budget': 72, 'created_monotonic': 0,
                     'dataset_manifest': {'bytes': 1, 'sha256': 'a'*64}}
        self.samples = [self.sample(i) for i in range(2)]
        (root/'batch_events').mkdir(); (root/'samples').mkdir()

    @staticmethod
    def sample(i):
        return {'ordinal': i, 'id': 'val_%05d' % i, 'label': i,
                'image': {'file': 'images/val_%05d.jpg' % i, 'sha256': '%064x' % (i+1), 'bytes': 4},
                'input': {'bytes': 602240, 'sha256': '%064x' % (i+100)}}

    def record(self, i=0):
        sample = self.samples[i]
        return {'ordinal': i, 'id': sample['id'], 'label': sample['label'],
                'image_sha256': sample['image']['sha256'], 'input_sha256': sample['input']['sha256'],
                'CPU_logits_sha256': 'e'*64, 'PL_logits_sha256': 'e'*64, 'CPU_top5': [i, 4, 5, 6, 7],
                'PL_top5': [i, 4, 5, 6, 7], 'bitwise_mismatches': 0, 'max_abs_difference': 0,
                'CPU_reference_ms': 300.0, 'inference_ms': 100.0, 'completed_PL_calls': 36, 'Dense_calls': 12,
                'Sparse_calls': 24, 'owner_exit_code': 0, 'device_closed': True,
                'postrun_buffer_checks_verified': True, 'native_END_verified': True}

    def rotation(self):
        execution = self.root/'execution'; execution.mkdir()
        sample = self.samples[0]
        raw = b'fixture-npy'; (self.root/'input.npy').write_bytes(raw)
        sample['input'] = r.identity(raw)
        view = dict(self.plan, sample=sample, input_pin=sample['input'])
        selection = r.sample_selection(self.plan, sample); write_json(self.root/'current_sample.json', selection)
        comparison = {k: v for k, v in self.record().items() if k in
                      ('CPU_logits_sha256', 'PL_logits_sha256', 'CPU_top5', 'PL_top5', 'bitwise_mismatches', 'max_abs_difference')}
        cpu = {'reference_ms': 300.0}
        worker = {'accuracy': comparison, 'runner_run_ms': 100.0, 'runner_calls': 1, 'compared_logits': 1000}
        native = {'completed_PL_calls': 36, 'PL_dense': 12, 'PL_sparse': 24, 'native_END_completed': True,
                  'postrun_buffer_checks_verified': True, 'device_closed': True, 'hot_path_disk_events': 0,
                  'graph_owner_sync_calls': 0}
        complete = {'CPU_reference': cpu, 'worker': worker, 'native': native, 'owner_exit_code': 0}
        state = {'stage': r.IMAGE_PASS, 'owner_pid': None, 'child_pid': None, 'completion': complete}
        write_json(execution/'status.json', state); write_json(execution/'completion.json', complete)
        self.a.proof = types.SimpleNamespace(native_completion=lambda *_, **__: native)
        self.a.runtime = types.SimpleNamespace(verify_bundle=lambda _: {'manifest': {}})
        return view, complete, cpu, worker


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.b24-batch-test-', dir=ROOT.parent)
        self.f = Fixture(Path(self.tmp.name))
    def tearDown(self): self.tmp.cleanup()

    def test_resource_estimate_requires_real_pilot_for_large_chunk(self):
        with self.assertRaisesRegex(ValueError, 'pilot'): r.resource_requirement(6, 100)
        value = r.resource_requirement(50, 100, {'count': 5, 'bytes': 5000, 'inodes': 50})
        self.assertEqual(value['required_free_inodes'], 10750)
        self.assertGreater(value['required_free_bytes'], 512*1024*1024)

    def test_fifty_is_hard_chunk_bound(self):
        for count in (0, 51, 1800, True):
            with self.subTest(count=count), self.assertRaises(ValueError): r.resource_requirement(count, 0)

    def test_full_and_partial_accuracy_denominators_are_distinct(self):
        result = r.batch_accuracy(self.f.a, [self.f.record()], self.f.samples)
        self.assertEqual(result['status'], 'INCOMPLETE'); self.assertEqual(result['missing_images'], 1)
        self.assertFalse(result['full_1800_acceptance'])
        empty = r.batch_accuracy(self.f.a, [], self.f.samples)
        self.assertEqual(empty['completed_images'], 0)

    def test_budget_identity_rejected_before_admission(self):
        root = self.f.root; (root/'dataset').mkdir()
        samples = [Fixture.sample(i) for i in range(1800)]
        raw = json.dumps({'count': 1800, 'samples': samples}).encode(); (root/'dataset/manifest.json').write_bytes(raw)
        self.f.plan['dataset_manifest'] = r.identity(raw)
        self.f.a.dataset = types.SimpleNamespace(validate_manifest=lambda b: json.loads(b))
        self.f.plan['total_START_budget'] = 71
        with self.assertRaisesRegex(ValueError, 'budget'): r.selected_samples(self.f.a, self.f.plan)
        self.assertEqual(self.f.events, [])

    def test_sample_binding_rejects_label_or_input_tamper(self):
        choice = r.sample_selection(self.f.plan, self.f.samples[0]); choice['sample'] = dict(choice['sample'], label=9)
        write_json(self.f.root/'current_sample.json', choice)
        with patch.object(r, 'selected_samples', return_value=self.f.samples):
            with self.assertRaisesRegex(ValueError, 'selection'): r.sample_plan(self.f.a, self.f.plan)

    def test_copy_dataset_includes_full_manifest_but_only_chunk_jpeg(self):
        prepared = self.f.root/'prepared'; prepared.mkdir(); (prepared/'images').mkdir()
        (prepared/'manifest.json').write_bytes(b'full manifest')
        files = {'manifest.json': r.identity(b'full manifest')}
        samples = []
        for i in range(3):
            item = Fixture.sample(i); raw = ('image%d' % i).encode()
            (prepared/item['image']['file']).write_bytes(raw)
            item['image'].update(r.identity(raw)); files[item['image']['file']] = r.identity(raw); samples.append(item)
        verified = {'root': prepared, 'files': files, 'manifest': {'samples': samples}}
        def copy(source, target, pin):
            raw = Path(source).read_bytes(); self.assertEqual(r.identity(raw), pin); Path(target).write_bytes(raw)
        self.f.a.durable.copy_new = copy
        r.copy_dataset(self.f.a, verified, self.f.root, 1, 1)
        actual = {x.relative_to(self.f.root/'dataset').as_posix() for x in (self.f.root/'dataset').rglob('*') if x.is_file()}
        self.assertEqual(actual, {'manifest.json', samples[1]['image']['file']})

    def test_rotation_requires_closed_end_guards_and_exit(self):
        view, complete, _, _ = self.f.rotation()
        for key in ('device_closed', 'native_END_completed', 'postrun_buffer_checks_verified'):
            changed = dict(complete, native=dict(complete['native'], **{key: False}))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'closure'):
                r.rotate_closed_sample(self.f.a, self.f.plan, view, changed)
        self.assertTrue((self.f.root/'input.npy').exists())
        self.assertFalse((self.f.root/'samples/000000').exists())

    def test_closed_rotation_archives_input_selection_and_complete_file_pins(self):
        view, complete, cpu, worker = self.f.rotation()
        with patch.object(r, 'validate_cpu', return_value=cpu), patch.object(r, 'validate_worker', return_value=worker), \
             patch.object(r, 'sample_parent_proof', return_value={}):
            record = r.rotate_closed_sample(self.f.a, self.f.plan, view, complete)
        target = self.f.root/'samples/000000'
        self.assertFalse((self.f.root/'execution').exists()); self.assertFalse((self.f.root/'input.npy').exists())
        self.assertEqual(record['completed_PL_calls'], 36)
        commit = json.loads((target/'commit.json').read_bytes())
        self.assertTrue({'input.npy', 'current_sample.json', 'completion.json', 'accuracy_record.json'} <= commit['files'].keys())
        for name, pin in commit['files'].items(): self.assertEqual(r.identity((target/name).read_bytes()), pin)

    def test_partial_rename_failure_retains_evidence_without_new_execution(self):
        view, complete, cpu, worker = self.f.rotation()
        original = r.os.rename; calls = []
        def fail_second(a, b):
            calls.append((a, b))
            if len(calls) == 2: raise OSError('simulated directory failure')
            original(a, b)
        with patch.object(r, 'validate_cpu', return_value=cpu), patch.object(r, 'validate_worker', return_value=worker), \
             patch.object(r, 'sample_parent_proof', return_value={}), \
             patch.object(r.os, 'rename', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'simulated'): r.rotate_closed_sample(self.f.a, self.f.plan, view, complete)
        self.assertTrue((self.f.root/'execution/input.npy').exists())
        self.assertTrue((self.f.root/'current_sample.json').exists())
        self.assertFalse((self.f.root/'samples/000000').exists())

    def test_unexpected_child_spawn_or_journal_failure_holds_and_never_admits_next(self):
        with patch.object(r, 'selected_samples', return_value=self.f.samples), patch.object(r.time, 'monotonic', return_value=10):
            batch = r.BatchCoordinator(self.f.a, self.f.plan, 3)
            hold = Mock(side_effect=RuntimeError('HELD')); batch.hold = hold
            fake = Mock(); fake.execute.side_effect = OSError('spawn result uncertain')
            with patch.object(r, 'sample_plan', return_value=self.f.plan), patch.object(r, 'Coordinator', return_value=fake) as factory:
                with self.assertRaisesRegex(RuntimeError, 'HELD'): batch.execute()
            self.assertEqual(factory.call_count, 1); hold.assert_called_once()
        phases = [x[1] for x in self.f.events]
        self.assertEqual(phases.count('ADMISSION_DURABLE_BEFORE_ANY_CHILD'), 1)
        self.assertTrue((self.f.root/'execution').exists())

    def test_expired_chunk_does_not_spawn_any_child_or_renew(self):
        with patch.object(r, 'selected_samples', return_value=self.f.samples), patch.object(r.time, 'monotonic', return_value=901):
            batch = r.BatchCoordinator(self.f.a, self.f.plan, 3)
            with patch.object(r, 'Coordinator') as factory:
                self.assertEqual(batch.execute(), 1); factory.assert_not_called()
        done = json.loads((self.f.root/'batch_completion.json').read_bytes())
        self.assertEqual(done['stage'], 'PARTIAL_CLOSED_PLAN_EXPIRED'); self.assertTrue(done['closed'])
        self.assertEqual(done['committed_samples'], 0)

    def test_index_commit_failure_never_publishes_final_pass(self):
        with patch.object(r, 'selected_samples', return_value=self.f.samples[:1]):
            batch = r.BatchCoordinator(self.f.a, self.f.plan, 3)
        batch.records = [self.f.record(0)]
        batch.plan['sample_count'] = 1
        batch.plan['total_START_budget'] = 36
        self.f.a.history_index.append_closed.side_effect = OSError('index publication uncertain')
        with self.assertRaisesRegex(OSError, 'index publication uncertain'):
            batch.finish(r.PASS)
        self.assertTrue((self.f.root/'batch_completion.json').exists())
        self.assertFalse((self.f.root/'batch_status.json').exists())
        self.assertFalse(any(phase == r.PASS for _, phase, _ in self.f.events))
        self.f.a.guard.finish.assert_called_once_with(batch.plan)

    def test_prepared_overlapping_plan_rechecked_at_run_before_any_start_intent(self):
        # A and B can both be prepared before either runs. A completes, then B
        # must fail under the live companion lock before batch_started/children.
        self.f.a.c.root_linux = lambda: None
        self.f.a.c.configure = lambda _: None
        class Lock:
            def __enter__(self): return 3
            def __exit__(self, *_): pass
        self.f.a.c.companion_locked = Lock
        plan = dict(self.f.plan, guardian_plan='guardian.json')
        with patch.object(r, 'api', return_value=self.f.a), patch.object(r, 'load_plan', return_value=plan), \
             patch.object(r, 'previous_clear', return_value=[{'plan': 'closed-A'}]), \
             patch.object(self.f.a.history_index, 'admit', side_effect=ValueError('already has a closed Native execution')) as replay, \
             patch.object(r, 'BatchCoordinator') as batch:
            with self.assertRaisesRegex(ValueError, 'closed Native'): r.run(plan['plan_path'], True)
            replay.assert_called_once(); batch.assert_not_called()
        self.assertFalse((self.f.root/'batch_started.json').exists())

    def test_repacked_transport_manifest_cannot_replay_same_canonical_samples(self):
        previous = self.f.root/'previous'; previous.mkdir()
        self.f.a.tools = {'manifest': {'bytes': 123, 'sha256': '1'*64}, 'files': {}}
        write_json(previous/'plan.json', {'schema': r.PLAN_SCHEMA, 'dataset_manifest': {'bytes': 1, 'sha256': '2'*64},
                                          'tool_files': self.f.a.tools})
        with patch.object(r, 'historical_records', return_value=({}, [self.f.record()], previous, {})) as records:
            with self.assertRaisesRegex(ValueError, 'closed Native'):
                r.prevent_replay(self.f.a, [{'plan': str(previous/'plan.json')}],
                                 {'bytes': 1, 'sha256': '3'*64}, 0, 1)
            records.assert_called_once()

    def test_input_materialization_hash_failure_does_not_publish_input(self):
        sample = self.f.samples[0]; view = dict(self.f.plan, sample=sample, input_pin=sample['input'], input=str(self.f.root/'input.npy'))
        ctx = types.SimpleNamespace(a=types.SimpleNamespace(dataset=types.SimpleNamespace(materialize_input=lambda *_: b'wrong')),
                                    plan=dict(view, dataset_root=str(self.f.root/'dataset')), entry=r)
        with self.assertRaisesRegex(ValueError, 'materialized'): w.prepare_input(ctx)
        self.assertFalse((self.f.root/'input.npy').exists())

    def test_each_owner_rechecks_original_900_seconds_after_cpu_preparation(self):
        self.f.plan.update(interpreter={}, guardian_plan='guardian.json')
        (self.f.root/'execution').mkdir()
        self.f.a.runtime = types.SimpleNamespace(verify_bundle=lambda _: {'manifest': {}})
        image = r.Coordinator(self.f.a, self.f.plan, 3)
        image.update = Mock(); image.worker_call = Mock(return_value={'CPU_reference': {}, 'input': {}}); image.hold = Mock(side_effect=AssertionError('must not hold a known no-owner path'))
        with patch.object(r, 'job_bytes', return_value=b'job'), patch.object(r, 'new_bytes'), \
             patch.object(r, 'guardian_review', return_value={}), patch.object(r, 'validate_parent'), \
             patch.object(r.time, 'monotonic', return_value=901), patch.object(r.subprocess, 'Popen') as spawn:
            self.assertEqual(image.execute(), 1); spawn.assert_not_called()
        image.hold.assert_not_called(); self.assertFalse(image.possibly_accessed)
        image.worker_call.assert_called_once_with('prepare')

    def parent_fixture(self):
        root, plan = self.f.root, self.f.plan
        argv = ['/usr/bin/python3', '-B', str(root/'tools/run_model.py'), 'run', '--plan', plan['plan_path'], w.AUTH]
        group = ('0::/system.slice/'+plan['unit']+'.service\n').encode()
        values = {'cmdline': b''.join(x.encode()+b'\0' for x in argv), 'cgroup': group, 'status': b'Name: fixture\nUid:\t0\t0\t0\t0\n'}
        c = types.SimpleNamespace(_proc_read=lambda path, *args: values[Path(path).name])
        return root, plan, values, c

    def test_worker_parent_keeps_exact_legacy_per_owner_argv(self):
        root, plan, values, c = self.parent_fixture()
        with patch.object(w.os, 'getppid', return_value=42):
            self.assertEqual(w.parent_identity(c, root, plan), 42)
            values['cmdline'] += b'--authorized-batch\0'
            with self.assertRaisesRegex(ValueError, 'exact coordinator'): w.parent_identity(c, root, plan)

    def test_worker_refuses_foreign_cgroup_or_nonroot_parent(self):
        root, plan, values, c = self.parent_fixture()
        with patch.object(w.os, 'getppid', return_value=42):
            original = values['cgroup']
            # Both parent and child can share the wrong cgroup; fixed unit still matters.
            values['cgroup'] = b'0::/system.slice/wrong.service\n'
            with self.assertRaisesRegex(ValueError, 'cgroup'): w.parent_identity(c, root, plan)
            values['cgroup'] = original; values['status'] = b'Uid:\t1000\t1000\t1000\t1000\n'
            with self.assertRaisesRegex(ValueError, 'root coordinator'): w.parent_identity(c, root, plan)

    def test_native_sources_remain_byte_identical(self):
        previous = ROOT.parent/'b24_full_model_aligned_reentry_20261027_v1'
        for path in (ROOT/'source').rglob('*'):
            if path.is_file(): self.assertEqual(path.read_bytes(), (previous/'source'/path.relative_to(ROOT/'source')).read_bytes())

    def test_frontend_refuses_expired_or_incomplete_pass(self):
        self.assertEqual(front.terminal({'stage': 'PARTIAL_CLOSED_PLAN_EXPIRED'}), 1)
        with self.assertRaises(ValueError): front.terminal({'stage': front.PASS, 'sample_count': 5, 'committed_samples': 4})


if __name__ == '__main__': unittest.main()
