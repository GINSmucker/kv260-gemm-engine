"""Local fake-owner/fake-ORT tests; never open an XRT or PL device."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('perf_worker_under_test', HERE/'perf_worker.py')
w = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(w)


def decode(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate JSON')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='worker_tests_', dir=HERE)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.execution = self.root/'execution'; self.execution.mkdir()
        self.model = self.root/'model'; self.model.mkdir()
        self.reference = b'fake fixed CPU graph; never executed by real ORT'
        (self.model/'reference.onnx').write_bytes(self.reference)
        self.reference_patch = patch.object(w, 'REFERENCE_SHA', w.identity(self.reference)['sha256'])
        self.reference_patch.start(); self.addCleanup(self.reference_patch.stop)
        self.events, self.calls, self.options = [], [], []
        image = np.zeros((1, 3, 224, 224), dtype=np.float32)
        output = io.BytesIO(); np.save(output, image, allow_pickle=False)
        self.input = self.root/'input_zero.npy'; self.input.write_bytes(output.getvalue())
        self.cpu = np.arange(1000, dtype=np.float32).reshape(1, 1000)/np.float32(32)
        self.actual = self.cpu.copy()
        self.runner_error = None
        self.close_error = None
        self.runner_closed = False
        owner = self

        class Options:
            def __init__(self):
                self.entries = {}; owner.options.append(self)
            def add_session_config_entry(self, name, value):
                self.entries[name] = value

        class Session:
            def __init__(self, graph, options, providers):
                owner.calls.append(('CPU', graph, providers))
            def run(self, outputs, feeds):
                owner.calls.append(('CPU_RUN', outputs, feeds['image'].shape))
                return [owner.cpu]
            def __del__(self):
                owner.events.append('cpu_closed')

        class Runner:
            def __init__(self, root, plugin, socket):
                owner.calls.append(('RUNNER', root, plugin, socket))
            def __enter__(self):
                return self
            def run(self, image, run_id):
                owner.calls.append(('MODEL_RUN', run_id, image.shape))
                if owner.runner_error:
                    raise owner.runner_error
                return owner.actual
            def __exit__(self, *_):
                self.close()
            def close(self):
                if owner.close_error:
                    raise owner.close_error
                owner.runner_closed = True; owner.events.append('runner_closed')

        ort = types.SimpleNamespace(__version__='1.22.1', SessionOptions=Options, InferenceSession=Session,
                                   ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL='sequential'),
                                   GraphOptimizationLevel=types.SimpleNamespace(ORT_DISABLE_ALL='disabled'))

        def new_json(path, value):
            if path.name == 'worker_result.json':
                self.assertTrue(self.runner_closed)
            if path.name == 'cpu_reference.json':
                self.assertIn('cpu_closed', self.events)
            self.events.append(path.name)
            with path.open('xb') as stream:
                stream.write((json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode())

        c = types.SimpleNamespace(read_file=lambda path, **_: Path(path).read_bytes(),
                                  decode_json=decode, private_dir=lambda _: None)
        self.ctx = types.SimpleNamespace(c=c, execution=self.execution,
             durable=types.SimpleNamespace(fsync_dir=lambda _: None, new_json=new_json),
             runtime=types.SimpleNamespace(dependencies=lambda: (np, ort), Runner=Runner),
             plan={'token': '0123456789ab', 'execution_mode': 'mixed', 'input': str(self.input),
                   'input_pin': w.identity(self.input.read_bytes()), 'model_root': str(self.model),
                   'plugin': str(self.root/'plugin.so'), 'socket': str(self.execution/'runtime.sock')})

    def prepare(self):
        with patch.object(w.os, 'sched_setaffinity', create=True) as affinity:
            record, code = w.prepare_cpu(self.ctx)
        affinity.assert_called_once_with(0, {0})
        self.assertEqual(code, 0)
        return record

    def test_cpu_prepared_once_and_closed_before_receipt(self):
        record = self.prepare()
        self.assertEqual(record['status'], 'CPU_REFERENCE_READY_NO_PL_ACCESS')
        self.assertEqual(record['PL_START_commands_issued_by_worker'], 0)
        self.assertIs(record['hardware_accessed'], False)
        self.assertEqual([call[0] for call in self.calls], ['CPU', 'CPU_RUN'])
        self.assertEqual(self.calls[0][2], ['CPUExecutionProvider'])
        options = self.options[0]
        self.assertEqual((options.intra_op_num_threads, options.inter_op_num_threads), (4, 1))
        self.assertEqual(options.execution_mode, 'sequential')
        self.assertEqual(options.graph_optimization_level, 'disabled')
        self.assertEqual(options.entries, {'session.intra_op_thread_affinities': '2;3;4',
              'session.intra_op.allow_spinning': '0', 'session.inter_op.allow_spinning': '0'})

    def test_one_runner_run_and_no_cpu_fallback_with_exact_match(self):
        self.prepare(); self.calls.clear()
        record, code = w.run_model(self.ctx)
        self.assertEqual(code, 0)
        self.assertEqual([call[0] for call in self.calls], ['RUNNER', 'MODEL_RUN'])
        self.assertEqual(self.calls[-1][1], 1)
        self.assertEqual(record['status'], 'MODEL_LOGITS_MATCH_BITWISE')
        self.assertEqual(record['compared_logits'], 1000)
        self.assertEqual(record['bitwise_mismatches'], 0)
        self.assertEqual(record['max_abs_difference'], 0.0)
        self.assertIs(record['Runner_closed'], True)
        self.assertEqual(record['actual_PL_starts'], 'PENDING_NATIVE_END_AND_OWNER_EXIT_PROOF')
        self.assertIs(record['accuracy_verified'], False)
        self.assertIs(record['performance_verified'], False)
        saved = decode((self.execution/'worker_result.json').read_bytes())
        self.assertEqual(saved, record)

    def test_signed_zero_is_bitwise_mismatch_even_if_absdiff_zero(self):
        self.cpu[0, 0] = np.float32(0)
        self.actual = self.cpu.copy(); self.actual[0, 0] = np.float32(-0.0)
        self.prepare()
        record, code = w.run_model(self.ctx)
        self.assertEqual((code, record['status']), (1, 'MODEL_LOGITS_MISMATCH'))
        self.assertEqual(record['bitwise_mismatches'], 1)
        self.assertEqual(record['max_abs_difference'], 0.0)

    def test_difference_uses_float64_not_overflowing_float32(self):
        self.cpu[0, 0] = np.finfo(np.float32).max
        self.actual = self.cpu.copy(); self.actual[0, 0] *= -1
        self.prepare()
        record, code = w.run_model(self.ctx)
        self.assertEqual(code, 1)
        self.assertTrue(np.isfinite(record['max_abs_difference']))
        self.assertGreater(record['max_abs_difference'], float(np.finfo(np.float32).max))

    def test_missing_or_changed_cpu_evidence_refuses_before_runner(self):
        with self.assertRaises(FileNotFoundError):
            w.run_model(self.ctx)
        self.assertFalse(self.calls)
        self.prepare(); self.calls.clear()
        (self.execution/'cpu_reference.npy').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'CPU logits NPY changed'):
            w.run_model(self.ctx)
        self.assertFalse(self.calls)

    def test_cpu_receipt_cannot_claim_hardware_or_different_input(self):
        self.prepare(); self.calls.clear()
        path = self.execution/'cpu_reference.json'
        record = decode(path.read_bytes()); record['hardware_accessed'] = True
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'CPU-only reference receipt'):
            w.run_model(self.ctx)
        self.assertFalse(self.calls)

    def test_bad_inputs_refuse_before_owner_contact(self):
        for image in (np.zeros((1, 3, 224, 224), dtype=np.float64),
                      np.zeros((1, 224, 224, 3), dtype=np.float32),
                      np.ones((1, 3, 224, 224), dtype=np.float32),
                      np.full((1, 3, 224, 224), np.nan, dtype=np.float32),
                      np.full((1, 3, 224, 224), -0.0, dtype=np.float32)):
            with self.subTest(dtype=image.dtype, shape=image.shape):
                buffer = io.BytesIO(); np.save(buffer, image, allow_pickle=False)
                self.input.write_bytes(buffer.getvalue())
                self.ctx.plan['input_pin'] = w.identity(buffer.getvalue())
                with self.assertRaisesRegex(ValueError, 'positive-zero'):
                    w.image_input(self.ctx, np)
                self.assertFalse(self.calls)

    def test_runner_exception_retains_all_client_refs_without_close_or_retry(self):
        self.prepare(); self.calls.clear(); self.runner_error = RuntimeError('ambiguous hardware outcome')
        class RetainedSentinel(Exception):
            pass
        with patch.object(w, 'retained', side_effect=RetainedSentinel('held')) as retain, \
             self.assertRaises(RetainedSentinel):
            w.run_model(self.ctx)
        self.assertEqual([call[0] for call in self.calls], ['RUNNER', 'MODEL_RUN'])
        self.assertFalse(self.runner_closed)
        self.assertIs(retain.call_args.args[0], self.ctx)
        self.assertIsInstance(retain.call_args.args[2], RuntimeError)
        self.assertFalse((self.execution/'worker_result.json').exists())
        self.assertFalse((self.execution/'model_logits.npy').exists())

    def test_exclusive_outputs_are_never_overwritten(self):
        self.prepare()
        original = (self.execution/'cpu_reference.npy').read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.execution/'cpu_reference.npy').read_bytes(), original)

    def test_close_failure_is_retained_without_success_receipt(self):
        self.prepare(); self.calls.clear(); self.close_error = RuntimeError('close failed')
        class RetainedSentinel(Exception):
            pass
        with patch.object(w, 'retained', side_effect=RetainedSentinel('held')) as retain, \
             self.assertRaises(RetainedSentinel):
            w.run_model(self.ctx)
        self.assertEqual([call[0] for call in self.calls], ['RUNNER', 'MODEL_RUN'])
        self.assertFalse(self.runner_closed)
        self.assertEqual(str(retain.call_args.args[2]), 'close failed')
        self.assertFalse((self.execution/'worker_result.json').exists())

    def test_authority_scope_refused_before_helper_or_process_access(self):
        with patch.object(w, 'bootstrap') as helper:
            for action, authorized in (('run', False), ('prepare-cpu', True), ('unknown', False)):
                with self.subTest(action=action, authorized=authorized):
                    with self.assertRaisesRegex(ValueError, 'authority'):
                        w.context('/not/a/plan', action, authorized)
            helper.assert_not_called()

    def test_root_tool_inventory_requires_new_profile_not_only_self_hashes(self):
        raw = b'synthetic pinned manifest'
        files = {name: w.identity(b'fake') for name in ('model_worker.py', 'model_runtime.py',
                  'model_inputs.py', 'run_model.py', 'support/companion.py', 'support/durable.py')}
        manifest = dict(schema='opt50_fair_model_tools_v1', hardware_executed=False,
                        measurement_profile='single-shot-low-io-v1', files=files)
        c = types.SimpleNamespace(read_file=lambda *_a, **_kw: raw, decode_json=lambda _: manifest)
        plan = {'tool_files': {'manifest': w.identity(raw), 'files': files}}
        with self.assertRaisesRegex(ValueError, 'root tool inventory'):
            w.verify_tools(c, plan, self.root)

    def test_strict_job_and_safe_paths(self):
        self.assertEqual(w.job_fields(b'a=b\nc=d\n'), {'a': 'b', 'c': 'd'})
        for raw in (b'a=b\na=c\n', b'a=b=c\n', b'a=\n', b'a=b\0\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                w.job_fields(raw)
        for path in ('../escape', '/absolute', 'a\\b', 'a//b', 'a/./b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                w.relative(path)

    def test_main_prints_one_saved_json_and_failure_prints_no_stdout(self):
        with patch.object(w, 'context', return_value=self.ctx), \
             patch.object(w.os, 'sched_setaffinity', create=True):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = w.main(['--action', 'prepare-cpu', '--plan', '/fake/plan'])
        self.assertEqual(code, 0); self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(decode(stdout.getvalue()), decode((self.execution/'cpu_reference.json').read_bytes()))
        with patch.object(w, 'context', side_effect=ValueError('closed gate')):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = w.main(['--action', 'run', '--plan', '/fake/plan', w.AUTH])
        self.assertEqual(code, 2); self.assertEqual(stdout.getvalue(), '')
        self.assertIn('closed gate', stderr.getvalue())

    def test_reference_counter_excludes_setup_validation_and_serialization(self):
        stamps = iter((100, 300, 1000, 6000))
        def counter():
            stamp = next(stamps); self.events.append(('counter', stamp)); return stamp
        original = w.logits_checked
        def validate(*args):
            self.events.append('validate_logits'); return original(*args)
        with patch.object(w.time, 'perf_counter_ns', side_effect=counter), \
             patch.object(w.os, 'sched_setaffinity', create=True), \
             patch.object(w, 'logits_checked', side_effect=validate):
            record, code = w.prepare_cpu(self.ctx)
        self.assertEqual(code, 0)
        self.assertEqual(record['reference_setup_ns'], 200)
        self.assertEqual(record['reference_ns'], 5000)
        self.assertEqual(record['reference_ms'], 0.005)
        self.assertLess(self.events.index(('counter', 6000)), self.events.index('validate_logits'))
        self.assertLess(self.events.index(('counter', 6000)), self.events.index('cpu_closed'))
        self.assertLess(self.events.index(('counter', 6000)), self.events.index('cpu_reference.json'))
        self.assertEqual(sum(call[0] == 'CPU_RUN' for call in self.calls), 1)  # No warmup.

    def test_runner_counter_only_surrounds_single_run_body(self):
        self.prepare(); self.calls.clear(); self.events.clear()
        stamps = iter((100, 1100, 2000, 9000))
        def counter():
            stamp = next(stamps); self.events.append(('counter', stamp)); return stamp
        original = w.logits_checked
        def validate(*args):
            self.events.append('validate_logits'); return original(*args)
        def snapshot(_):
            self.events.append('plugin_snapshot'); return {'status': 'TEST_MEMORY_ONLY'}
        with patch.object(w.time, 'perf_counter_ns', side_effect=counter), \
             patch.object(w, 'logits_checked', side_effect=validate), \
             patch.object(w, 'plugin_timing', side_effect=snapshot):
            record, code = w.run_model(self.ctx)
        self.assertEqual(code, 0)
        self.assertEqual(record['setup_ns'], 1000)
        self.assertEqual(record['runner_run_ns'], 7000)
        self.assertEqual(record['runner_run_ms'], 0.007)
        last_validate = max(i for i, e in enumerate(self.events) if e == 'validate_logits')
        self.assertLess(self.events.index(('counter', 9000)), last_validate)
        for event in ('plugin_snapshot', 'runner_closed', 'worker_result.json'):
            self.assertLess(self.events.index(('counter', 9000)), self.events.index(event))
        self.assertLess(self.events.index('plugin_snapshot'), self.events.index('runner_closed'))
        self.assertEqual(sum(call[0] == 'MODEL_RUN' for call in self.calls), 1)
        self.assertIs(record['single_sample_only'], True)
        self.assertEqual(record['measurement_profile'], 'same-weights-fair-single-shot-v1')

    def test_all_dense_arm_uses_same_graph_runner_and_cpu_reference_numerics(self):
        self.ctx.plan['execution_mode'] = 'all_dense'
        cpu = self.prepare(); self.calls.clear()
        record, code = w.run_model(self.ctx)
        self.assertEqual(code, 0)
        self.assertEqual(cpu['execution_mode'], 'all_dense')
        self.assertEqual(record['execution_mode'], 'all_dense')
        self.assertEqual(record['logical_weights_sha256'], w.WEIGHTS_SHA)
        self.assertEqual(record['reference_sha256'], w.REFERENCE_SHA)
        self.assertEqual(record['custom_graph_sha256'], w.GRAPH_SHA)
        self.assertEqual(record['bitwise_mismatches'], 0)
        self.assertEqual([call[0] for call in self.calls], ['RUNNER', 'MODEL_RUN'])
        # Mode is native-owner policy, never an alternative ONNX or Runner.
        self.assertEqual(self.calls[0][1:], (str(self.model), self.ctx.plan['plugin'], self.ctx.plan['socket']))

    def test_cpu_record_from_other_arm_cannot_be_relabelled_for_run(self):
        self.prepare(); self.calls.clear()
        self.ctx.plan['execution_mode'] = 'all_dense'
        with self.assertRaisesRegex(ValueError, 'CPU-only reference receipt'):
            w.run_model(self.ctx)
        self.assertFalse(self.calls)

    def test_only_two_explicit_execution_modes_are_accepted(self):
        for mode in ('all_dense', 'mixed'):
            self.assertEqual(w.execution_mode({'execution_mode': mode}), mode)
        for mode in (None, '', 'dense', 'sparse', True, 0, [], {}):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                w.execution_mode({'execution_mode': mode})

    def test_plugin_exports_are_memory_only_and_expose_mixed_gemm_scope(self):
        aggregate = [49, 98, 147, 343]
        rows = [1, 2, 3, 7]*49
        calls = []
        def totals(handle, out):
            calls.append(('total', handle)); out[:] = aggregate; return 0
        def layers(handle, out):
            calls.append(('layers', handle)); out[:] = rows; return 0
        runner = types.SimpleNamespace(lib=types.SimpleNamespace(kv5_b_timing=totals, boundary_layers=layers), handle=42)
        record = w.plugin_timing(runner)
        self.assertEqual(calls, [('total', 42), ('layers', 42)])
        self.assertEqual(record['status'], 'AVAILABLE_PLUGIN_MEMORY_ONLY')
        self.assertEqual(record['aggregate_ns']['quantization'], 49)
        self.assertEqual(record['aggregate_ns']['client_gemm_or_PS_matmul'], 98)
        self.assertEqual(len(record['layers']), 49)
        self.assertEqual(sum(row['route'] == 'PL' for row in record['layers']), 36)
        self.assertIn('13 PS MatMul', record['scope'])
        self.assertEqual(record['hardware_commands_issued'], 0)
        missing = w.plugin_timing(types.SimpleNamespace(lib=object()))
        self.assertEqual(missing['status'], 'UNKNOWN_NOT_EXPORTED')
        def rejected(*_):
            return 1
        bad = w.plugin_timing(types.SimpleNamespace(lib=types.SimpleNamespace(kv5_b_timing=rejected, boundary_layers=layers), handle=42))
        self.assertEqual(bad['status'], 'UNKNOWN_CAPTURE_FAILED')
        self.assertIsNone(bad['aggregate_ns'])

    def test_only_exact_plain_or_consistently_colored_known_warnings(self):
        lines = []
        for index, name in enumerate(w.WARNING_NAMES):
            lines.append(('2026-10-24 12:00:00.%09d' % index+
                ' [W:onnxruntime:, graph.cc:4454 CleanUnusedInitializersAndNodeArgs] '+
                "Removing initializer '"+name+"'. It is not used by any node and should be removed from the model.").encode())
        plain = b''.join(line+b'\n' for line in lines)
        ansi = b''.join(b'\x1b[0;93m'+line+b'\x1b[m\n' for line in lines)
        self.assertEqual(w.validate_warnings(b'')['classification'], 'EMPTY')
        for raw, suffix in ((plain, 'PLAIN'), (ansi, 'ANSI')):
            result = w.validate_warnings(raw)
            self.assertTrue(result['classification'].endswith(suffix))
            self.assertEqual(result['raw'], w.identity(raw))
            self.assertEqual(result['initializer_names'], list(w.WARNING_NAMES))
        for raw in (plain+b'unknown\n', ansi.replace(b'\x1b[m', b'\x1b[0m'),
                    b'\x1b[0;93m'+lines[0]+b'\x1b[m\n'+b''.join(line+b'\n' for line in lines[1:]),
                    plain.replace(b'Constant_2_output_0', b'Constant_3_output_0'),
                    plain.replace(b'2026-10-24', b'2026-13-24')):
            with self.subTest(raw=raw[:60]), self.assertRaises(ValueError):
                w.validate_warnings(raw)


if __name__ == '__main__':
    unittest.main()


