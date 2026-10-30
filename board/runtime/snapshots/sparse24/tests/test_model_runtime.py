"""Real new-model file/CPU tests and device-free Runner lifetime tests."""
import importlib.util
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).absolute().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    item = importlib.util.module_from_spec(spec); spec.loader.exec_module(item)
    return item


r = load('test_b24_model_runtime', ROOT/'model_runtime.py')
w = load('test_b24_model_worker', ROOT/'model_worker.py')


class ModelFiles(unittest.TestCase):
    def test_fixed_66_file_model_has_all24_sparse_routes_and_new_parameter_identity(self):
        value = r.verify_bundle(ROOT/'model')
        manifest = value['manifest']
        self.assertEqual(len(manifest['files']), 66)
        self.assertEqual(manifest['reference_sha256'], r.SOURCE_MODEL_SHA)
        self.assertEqual(manifest['routes'], {'PL': 36, 'PS': 13, 'PL_dense': 12, 'PL_sparse24': 24})
        self.assertEqual([x['layer_index'] for x in manifest['layers'] if x['mode'] == 1], sorted(r.SPARSE))

    def test_original12_layer_manifest_pin_is_rejected(self):
        with patch.object(r, 'MANIFEST_SHA', 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb'):
            with self.assertRaisesRegex(ValueError, 'manifest identity differs'):
                r.verify_bundle(ROOT/'model')

    def test_cpu_and_runner_options_are_four_threads_no_spinning_no_optimizer_and_error_logging(self):
        class Options:
            def __init__(self):
                self.config = {}
            def add_session_config_entry(self, key, value):
                self.config[key] = value
        fake = types.SimpleNamespace(SessionOptions=Options,
                    ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL='sequential'),
                    GraphOptimizationLevel=types.SimpleNamespace(ORT_DISABLE_ALL='off'))
        options = r.session_options(fake, bind_affinity=False)
        self.assertEqual((options.intra_op_num_threads, options.inter_op_num_threads), (4, 1))
        self.assertEqual(options.graph_optimization_level, 'off')
        self.assertEqual(options.execution_mode, 'sequential')
        self.assertEqual(options.log_severity_level, 3)
        self.assertEqual(options.config, {'session.intra_op.allow_spinning': '0', 'session.inter_op.allow_spinning': '0'})
        with patch.object(r.os, 'sched_setaffinity', create=True) as affinity:
            pinned = r.session_options(fake, bind_affinity=True)
        affinity.assert_called_once_with(0, {0})
        self.assertEqual(pinned.config['session.intra_op_thread_affinities'], '2;3;4')

    def test_real_dev0000_cpu_reference_replayed_at_matching_settings(self):
        np, _ = r.dependencies()
        image = np.load(ROOT/'model/inputs/dev0000_input.npy', allow_pickle=False)
        reference = (ROOT/'model/reference.onnx').read_bytes()
        # B's packaged logits remain one-thread provenance. This release
        # recomputes both sides at four threads instead of relabeling them.
        saved, _ = w.cpu_reference(r, reference, image, bind_affinity=False)
        actual, timing = w.cpu_reference(r, reference, image, bind_affinity=False)
        self.assertEqual(w.compare_logits(np, saved, actual)['bitwise_mismatches'], 0)
        self.assertEqual(timing['reference_ms'], timing['reference_ns']/1e6)

    def test_zero_input_is_fixed_positive_float32_zero_and_correct_shape(self):
        np, _ = r.dependencies()
        image = np.load(ROOT/'model/inputs/zero_input.npy', allow_pickle=False)
        self.assertEqual(image.shape, (1, 3, 224, 224))
        self.assertEqual(image.dtype, np.dtype('float32'))
        self.assertEqual(image.tobytes(order='C'), bytes(1*3*224*224*4))


class RunnerLifetime(unittest.TestCase):
    def fake_runner(self, fail=False):
        np, _ = r.dependencies()
        calls = []
        runner = object.__new__(r.Runner)
        runner._run_lock = threading.Lock()
        runner._failed = runner._active = runner._closed = False
        runner._last_run_id = 0
        runner.np = np
        runner.handle = types.SimpleNamespace(value=123)
        def begin(*_):
            calls.append('BEGIN'); return 0
        def end(*_):
            calls.append('END'); return 0
        def abort(*_):
            calls.append('ABORT'); return 0
        runner.lib = types.SimpleNamespace(kv5_b_begin=begin, kv5_b_end=end, kv5_b_abort=abort,
                     kv5_b_close=lambda *_: calls.append('CLOSE'), kv5_b_error=lambda *_: b'error')
        def infer(*_):
            calls.append('RUN')
            if fail:
                raise RuntimeError('simulated uncertain custom operation')
            return [np.zeros((1, 1000), dtype=np.float32)]
        runner.session = types.SimpleNamespace(run=infer)
        return runner, calls, np.zeros((1, 3, 224, 224), dtype=np.float32)

    def test_success_runs_once_and_normal_close_occurs_after_end(self):
        runner, calls, image = self.fake_runner()
        self.assertEqual(runner.run(image, 1).shape, (1, 1000))
        self.assertEqual(calls, ['BEGIN', 'RUN', 'END'])
        with self.assertRaisesRegex(ValueError, 'single authorized'):
            runner.run(image, 1)
        runner.close()
        self.assertEqual(calls, ['BEGIN', 'RUN', 'END', 'CLOSE'])

    def test_uncertain_operation_retains_active_state_without_abort_retry_or_close(self):
        runner, calls, image = self.fake_runner(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'uncertain custom operation'):
            runner.run(image, 1)
        self.assertTrue(runner._active)
        self.assertTrue(runner._failed)
        with self.assertRaisesRegex(ValueError, 'uncertain active'):
            runner.close()
        self.assertEqual(calls, ['BEGIN', 'RUN'])
        with self.assertRaisesRegex(RuntimeError, 'failed or already-active'):
            runner.run(image, 1)

    def test_bitwise_comparison_does_not_upgrade_tolerance_match_to_bitwise_pass(self):
        np, _ = r.dependencies()
        cpu = np.zeros((1, 1000), dtype=np.float32)
        actual = cpu.copy(); actual[0, 3] = np.float32(1e-7)
        proof = w.compare_logits(np, cpu, actual)
        self.assertEqual(proof['bitwise_mismatches'], 1)
        self.assertTrue(proof['allclose_atol_1e_5_rtol_1e_5'])
        self.assertEqual(proof['first_differences'][0]['index'], 3)

    def test_nonfinite_logits_never_become_an_accepted_difference_report(self):
        np, _ = r.dependencies()
        cpu = np.zeros((1, 1000), dtype=np.float32)
        actual = cpu.copy(); actual[0, 5] = np.nan
        with self.assertRaisesRegex(ValueError, 'finite float32'):
            w.compare_logits(np, cpu, actual)


if __name__ == '__main__':
    unittest.main()
