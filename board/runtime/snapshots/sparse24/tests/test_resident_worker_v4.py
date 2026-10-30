"""Bounded multi-image lifecycle tests with no DLL, owner, device or START."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).absolute().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


r = module('resident_runtime_v4_test', 'model_runtime.py')
w = module('resident_worker_v4_test', 'model_worker.py')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def npy(value):
    stream = io.BytesIO()
    np.save(stream, value, allow_pickle=False)
    return stream.getvalue()


class ResidentRunnerTests(unittest.TestCase):
    def fake(self, count=2, fail=False):
        runner = object.__new__(r.ResidentRunner)
        runner._run_lock = threading.Lock()
        runner._failed = runner._active = runner._closed = False
        runner._last_run_id = 0
        runner.max_runs = count
        runner.np = np
        runner.handle = types.SimpleNamespace(value=123)
        calls = []
        runner.lib = types.SimpleNamespace(
            kv5_b_begin=lambda h, n: calls.append(('BEGIN', n)) or 0,
            kv5_b_end=lambda h: calls.append(('END',)) or 0,
            kv5_b_close=lambda h: calls.append(('CLOSE',)),
            kv5_b_error=lambda h: b'failed')
        def infer(*args):
            calls.append(('RUN',))
            if fail:
                raise RuntimeError('uncertain simulated custom op')
            return [np.zeros((1, 1000), dtype=np.float32)]
        runner.session = types.SimpleNamespace(run=infer)
        return runner, calls, np.zeros((1, 3, 224, 224), dtype=np.float32)

    def test_increasing_ids_share_one_session_then_close_once(self):
        runner, calls, image = self.fake()
        session = runner.session
        for run_id in (1, 2):
            self.assertEqual(runner.run(image, run_id).shape, (1, 1000))
            self.assertIs(runner.session, session)
        runner.close()
        self.assertEqual(calls, [('BEGIN', 1), ('RUN',), ('END',),
                                 ('BEGIN', 2), ('RUN',), ('END',), ('CLOSE',)])

    def test_skip_duplicate_boolean_and_over_budget_rejected_before_begin(self):
        runner, calls, image = self.fake()
        for run_id in (0, True, 2, 3):
            with self.assertRaisesRegex(ValueError, 'next unused ID'):
                runner.run(image, run_id)
        self.assertEqual(calls, [])
        runner.run(image, 1)
        with self.assertRaises(ValueError): runner.run(image, 1)
        runner.run(image, 2)
        with self.assertRaises(ValueError): runner.run(image, 3)
        self.assertEqual([x for x in calls if x[0] == 'BEGIN'], [('BEGIN', 1), ('BEGIN', 2)])

    def test_uncertain_run_never_aborts_replays_or_closes_active_graph(self):
        runner, calls, image = self.fake(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): runner.run(image, 1)
        self.assertTrue(runner._active)
        with self.assertRaisesRegex(ValueError, 'uncertain active'): runner.close()
        with self.assertRaisesRegex(RuntimeError, 'failed or already-active'): runner.run(image, 2)
        self.assertEqual(calls, [('BEGIN', 1), ('RUN',)])

    def test_lifecycle_export_rejects_one_shot_plugin(self):
        runner, _, _ = self.fake()
        with self.assertRaisesRegex(ValueError, 'export missing'): runner._validate_profile()
        export = Mock(return_value=b'one-shot')
        runner.lib.opt50_lifecycle_profile = export
        with self.assertRaisesRegex(ValueError, 'profile differs'): runner._validate_profile()
        export.return_value = b'resident-bounded-batch-v4'
        runner._validate_profile()
        self.assertEqual(export.argtypes, [])

    def test_plain_receipt_cannot_bypass_model_byte_validation(self):
        with patch.object(r.C, 'CDLL', side_effect=AssertionError('must not load DLL')):
            with self.assertRaisesRegex(ValueError, 'in-process verified bundle'):
                r.ResidentRunner(ROOT/'model', 'unused', 'unused', max_runs=2, checked_bundle={})

    def test_invalid_budget_never_opens_runtime(self):
        with patch.object(r, 'verify_bundle', side_effect=AssertionError('must not verify/load')):
            for count in (0, 51, True, 1.5):
                with self.assertRaisesRegex(ValueError, '1..50'):
                    r.ResidentRunner('unused', 'unused', 'unused', max_runs=count)


class ResidentSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = ROOT/'.test_build'
        temporary.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary, prefix='resident_worker_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'samples').mkdir()
        (self.root/'execution').mkdir()
        self.samples, self.inputs = [], {}
        for ordinal in (25, 26, 27):
            image = np.full((1, 3, 224, 224), ordinal, dtype=np.float32)
            raw = npy(image)
            self.inputs[ordinal] = raw
            self.samples.append({'ordinal': ordinal, 'id': 'image%d' % ordinal, 'label': 3,
                                 'image': {'sha256': 'a'*64}, 'input': pin(raw)})
        self.reference = b'verified-reference-bytes'
        self.bundle = types.SimpleNamespace(reference_bytes=self.reference, unchanged=Mock())
        self.plan = {'sample_start': 25, 'sample_count': 3, 'owner_graph_count': 3,
                     'total_START_budget': 108, 'created_monotonic_ns': time.monotonic_ns(),
                     'model_root': str(self.root/'model'), 'plugin': str(self.root/'plugin.so'),
                     'socket': str(self.root/'execution/runtime.sock'), 'dataset_root': str(self.root/'dataset'),
                     'boot_id': 'fixture-boot', 'token': 'fixture-token',
                     'reference_sha256': pin(self.reference)['sha256'], 'custom_graph_sha256': 'b'*64,
                     'source_model_sha256': 'c'*64, 'logical_weights_sha256': 'd'*64,
                     'model_manifest_sha256': 'e'*64}
        self.calls = []
        def output(image):
            return np.full((1, 1000), image[0, 0, 0, 0], dtype=np.float32)
        self.cpu = types.SimpleNamespace(run=lambda _, inp: self.calls.append('CPU') or [output(inp['image'])])
        self.runner = types.SimpleNamespace(
            run=lambda image, run_id: self.calls.append(('PL', run_id)) or output(image),
            close=lambda: self.calls.append('CLOSE'))
        self.cpu_factory = Mock(return_value=self.cpu)
        self.runner_factory = Mock(return_value=self.runner)
        self.runtime = types.SimpleNamespace(
            dependencies=lambda: (np, types.SimpleNamespace(__version__='1.22.1')),
            session_options=Mock(return_value='fixed-options'),
            verify_bundle=Mock(side_effect=AssertionError('already verified bundle must be reused')),
            decode_json=r.decode_json)
        def write_bytes(c, durable, path, raw):
            with Path(path).open('xb') as stream: stream.write(raw)
        def write_json(path, value):
            with Path(path).open('x', encoding='utf-8') as stream: json.dump(value, stream)
        self.ctx = types.SimpleNamespace(root=self.root, execution=self.root/'execution',
            samples=copy.deepcopy(self.samples), plan=self.plan, bundle=self.bundle, runtime=self.runtime,
            c=types.SimpleNamespace(), entry=types.SimpleNamespace(identity=pin, new_bytes=write_bytes, PROFILE='fixture-profile'),
            durable=types.SimpleNamespace(mkdir_new=lambda p: Path(p).mkdir(), new_json=write_json),
            a=types.SimpleNamespace(dataset=types.SimpleNamespace(materialize_input=lambda root,sample:self.inputs[sample['ordinal']]),
                                    metrics=types.SimpleNamespace(compare=lambda cpu,pl,label:{'label':label,'match':bool(np.array_equal(cpu,pl))})))

    def create(self):
        return w.ResidentSession(self.ctx, cpu_factory=self.cpu_factory,
                                 runner_factory=self.runner_factory, bind_affinity=False)

    def command(self, run_id):
        return {'action': 'run', 'run_id': run_id, 'sample': copy.deepcopy(self.samples[run_id-1])}

    def test_two_samples_share_sessions_and_remain_open_until_real_close(self):
        session = self.create()
        ready = session.initialize()
        self.assertEqual(ready['action'], 'ready')
        self.assertFalse(ready['Runner_closed'])
        for run_id in (1, 2):
            reply = session.run(self.command(run_id))
            record = reply['worker']
            self.assertEqual(record['bitwise_mismatches'], 0)
            self.assertFalse(record['CPU_session_closed'])
            self.assertFalse(record['Runner_closed'])
            self.assertEqual(record['expected_PL_starts'], 36)
            folder = self.root/'samples'/('%06d' % self.samples[run_id-1]['ordinal'])
            self.assertEqual(json.loads((folder/'worker.json').read_text()), record)
            self.assertEqual(pin((folder/'cpu_reference.npy').read_bytes()), record['cpu_logits_file'])
            self.assertEqual(pin((folder/'model_logits.npy').read_bytes()), record['logits_file'])
        self.cpu_factory.assert_called_once()
        self.runner_factory.assert_called_once()
        self.runtime.verify_bundle.assert_not_called()
        self.assertEqual(self.calls, ['CPU', ('PL', 1), 'CPU', ('PL', 2)])
        closed = session.close()
        self.assertTrue(closed['CPU_session_closed'] and closed['Runner_closed'])
        self.assertEqual(closed['completed'], 2)  # Explicit closed prefix, no fabricated third graph.
        self.assertEqual(self.calls[-1], 'CLOSE')
        with self.assertRaisesRegex(ValueError, 'cannot admit'): session.run(self.command(3))

    def test_wrong_sample_or_id_is_rejected_before_cpu_or_pl(self):
        session = self.create(); session.initialize()
        command = self.command(1); command['sample']['label'] += 1
        with self.assertRaisesRegex(ValueError, 'frozen selection'): session.run(command)
        with self.assertRaisesRegex(ValueError, 'next sealed sample'): session.run(self.command(2))
        self.assertEqual(self.calls, [])
        self.assertEqual(list((self.root/'samples').iterdir()), [])

    def test_duplicate_does_not_repeat_or_overwrite_committed_worker_output(self):
        session = self.create(); session.initialize(); session.run(self.command(1))
        before = (self.root/'samples/000025/worker.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'next sealed sample'): session.run(self.command(1))
        self.assertEqual((self.root/'samples/000025/worker.json').read_bytes(), before)
        self.assertEqual(self.calls, ['CPU', ('PL', 1)])

    def test_expired_batch_prevents_even_cpu_and_next_begin(self):
        session = self.create(); session.initialize()
        self.plan['created_monotonic_ns'] = time.monotonic_ns()-901_000_000_000
        with self.assertRaisesRegex(ValueError, 'expired'): session.run(self.command(1))
        self.assertEqual(self.calls, [])
        self.assertEqual(session.close()['completed'], 0)

    def test_cpu_failure_reports_safe_error_then_only_explicit_close(self):
        self.cpu.run = Mock(side_effect=ValueError('CPU failed'))
        source = io.StringIO(json.dumps(self.command(1))+'\n'+json.dumps(self.command(2))+'\n{"action":"close"}\n')
        destination = io.StringIO()
        code = w.resident_loop(self.ctx, source, destination, session_factory=lambda ctx:self.create())
        rows = [json.loads(row) for row in destination.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual([row['action'] for row in rows], ['ready', 'error', 'error', 'closed'])
        self.assertTrue(rows[1]['safe_to_request_close'])
        self.assertEqual(rows[-1]['completed'], 0)
        self.assertEqual(self.calls, ['CLOSE'])

    def test_mismatch_stops_next_image_but_allows_parent_close(self):
        self.runner.run = lambda image,run_id: np.zeros((1, 1000), dtype=np.float32)
        session = self.create(); session.initialize()
        self.assertEqual(session.run(self.command(1))['worker']['bitwise_mismatches'], 1000)
        with self.assertRaisesRegex(ValueError, 'cannot admit'): session.run(self.command(2))
        self.assertEqual(session.close()['completed'], 1)

    def test_pl_uncertainty_is_distinct_from_cpu_validation_failure(self):
        self.runner.run = Mock(side_effect=RuntimeError('uncertain PL failure'))
        session = self.create(); session.initialize()
        with self.assertRaisesRegex(w.ResidentRunUncertain, 'invocation failed'):
            session.run(self.command(1))
        self.assertTrue(session.failed)
        self.assertIs(session.runner, self.runner)
        self.assertFalse(session.closed)
        self.assertEqual(session.completed, 0)
        with self.assertRaisesRegex(ValueError, 'cannot resolve'): session.close()
        self.assertNotIn('CLOSE', self.calls)

    def test_uncertain_worker_exits_with_receipt_without_signal_suppression_cleanup_or_next_run(self):
        self.runner.run = Mock(side_effect=RuntimeError('uncertain PL failure'))
        source = io.StringIO(json.dumps(self.command(1))+'\n'+json.dumps(self.command(2))+'\n{"action":"close"}\n')
        destination = io.StringIO()
        with patch.object(w.signal, 'signal', side_effect=AssertionError('must not suppress signals')), \
             patch.object(w.time, 'sleep', side_effect=AssertionError('must not wait forever')):
            code = w.resident_loop(self.ctx, source, destination, session_factory=lambda ctx:self.create())
        self.assertEqual(code, 2)
        self.assertEqual([json.loads(line)['action'] for line in destination.getvalue().splitlines()], ['ready'])
        receipt = json.loads((self.root/'execution/worker_retained.json').read_text())
        self.assertEqual(receipt['completed'], 0)
        self.assertEqual(receipt['last_END_returned_run_id'], 0)
        self.assertFalse(receipt['Runner_close_called'])
        self.assertFalse(receipt['Native_owner_closed'])
        self.assertNotIn('CLOSE', self.calls)
        self.runner.run.assert_called_once()

    def test_post_end_persist_failure_is_unknown_not_safe_cpu_error(self):
        original = self.ctx.entry.new_bytes
        def damaged(c, durable, path, raw):
            if Path(path).name == 'model_logits.npy':
                raise OSError('fixture output write failed after END')
            return original(c, durable, path, raw)
        self.ctx.entry.new_bytes = damaged
        source = io.StringIO(json.dumps(self.command(1))+'\n'+json.dumps(self.command(2))+'\n{"action":"close"}\n')
        destination = io.StringIO()
        code = w.resident_loop(self.ctx, source, destination, session_factory=lambda ctx:self.create())
        self.assertEqual(code, 2)
        receipt = json.loads((self.root/'execution/worker_retained.json').read_text())
        self.assertEqual(receipt['completed'], 0)
        self.assertEqual(receipt['last_END_returned_run_id'], 1)
        self.assertIn('post-END result proof failed', receipt['reason'])
        self.assertEqual(self.calls, ['CPU', ('PL', 1)])
        self.assertEqual([json.loads(line)['action'] for line in destination.getvalue().splitlines()], ['ready'])
        self.assertFalse((self.root/'samples/000025/worker.json').exists())

    def test_post_end_compare_failure_prevents_another_image_or_safe_close_claim(self):
        session = self.create(); session.initialize()
        with patch.object(w, 'compare_logits', side_effect=ValueError('fixture compare failed')):
            with self.assertRaisesRegex(w.ResidentRunUncertain, 'post-END'):
                session.run(self.command(1))
        self.assertEqual(session.last_end_returned, 1)
        self.assertTrue(session.failed)
        with self.assertRaisesRegex(ValueError, 'cannot admit'): session.run(self.command(2))
        with self.assertRaisesRegex(ValueError, 'cannot resolve'): session.close()
        self.assertEqual(self.calls, ['CPU', ('PL', 1)])

    def test_successful_protocol_returns_one_result_per_id_and_truthful_final_close(self):
        source = io.StringIO(''.join(json.dumps(self.command(i))+'\n' for i in (1,2,3))+'{"action":"close"}\n')
        destination = io.StringIO()
        code = w.resident_loop(self.ctx, source, destination, session_factory=lambda ctx:self.create())
        rows = [json.loads(row) for row in destination.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual([row['action'] for row in rows], ['ready','result','result','result','closed'])
        self.assertEqual(rows[-1]['completed'], 3)
        self.assertEqual(self.calls.count('CLOSE'), 1)

    def test_idle_eof_really_closes_and_exits_without_retaining_or_claiming_native_closed(self):
        with patch.object(w, 'retain_resident', side_effect=AssertionError('idle EOF must not retain')):
            code = w.resident_loop(self.ctx, io.StringIO(''), io.StringIO(),
                                   session_factory=lambda ctx:self.create())
        self.assertEqual(code, 2)
        receipt = json.loads((self.root/'execution/worker_idle_close.json').read_text())
        self.assertTrue(receipt['CPU_session_closed'] and receipt['Runner_closed'])
        self.assertFalse(receipt['Native_owner_closed'])
        self.assertEqual(self.calls, ['CLOSE'])

    def test_broken_result_pipe_closes_idle_sessions_without_signal_suppression(self):
        class BrokenPipe:
            def write(self, value): raise BrokenPipeError('fixture parent gone')
            def flush(self): pass
        with patch.object(w, 'retain_resident', side_effect=AssertionError('idle broken pipe must not retain')):
            code = w.resident_loop(self.ctx, io.StringIO(''), BrokenPipe(),
                                   session_factory=lambda ctx:self.create())
        self.assertEqual(code, 2)
        self.assertEqual(self.calls, ['CLOSE'])
        self.assertTrue((self.root/'execution/worker_idle_close.json').is_file())


if __name__ == '__main__':
    unittest.main()
