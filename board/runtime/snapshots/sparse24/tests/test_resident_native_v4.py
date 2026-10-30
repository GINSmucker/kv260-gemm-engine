"""Compile and run real device-free C++ resident state/trace checks.

No XRT source is linked, and no owner is executed. The fake backend runs the
same per-layer control contract against guarded local memory. ARM/XRT build and
board execution remain separate evidence.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import unittest

ROOT = Path(__file__).absolute().parent.parent
WINDOWS_CXX = Path('D:/Vivado/2026.1/Vitis/tps/mingw/10.0.0/win64.o/nt/bin/g++.exe')


class ResidentNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidate = shutil.which('g++-13') or shutil.which('g++')
        if candidate is None and WINDOWS_CXX.is_file():
            candidate = str(WINDOWS_CXX)
        if candidate is None:
            raise RuntimeError('installed CPU C++ compiler required; no compiler installed automatically')
        cls.compiler = Path(candidate).absolute()
        cls.output = ROOT/'.test_build'/('resident_native_'+secrets.token_hex(4))
        cls.output.mkdir(parents=True)
        cls.environment = dict(os.environ)
        cls.environment['PATH'] = str(cls.compiler.parent)+os.pathsep+cls.environment.get('PATH', '')
        cls.binaries = {}
        suffix = '.exe' if os.name == 'nt' else ''
        for source, name in (('test_model_core.cpp', 'resident_model_core'),
                             ('test_performance_core.cpp', 'resident_performance_core')):
            binary = cls.output/(name+suffix)
            argv = [str(cls.compiler), '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-DOPT50_RESIDENT_BATCH=1', '-DOPT50_PERFORMANCE_LIGHT=1',
                    '-DOPT50_H576_ALIGNED=1', '-DKV5_DIAGNOSTIC', '-Imodel', source, '-o', str(binary)]
            compiled = subprocess.run(argv, cwd=ROOT, env=cls.environment, capture_output=True, text=True, check=False)
            if compiled.returncode:
                raise AssertionError('CPU compile failed: '+compiled.stdout+compiled.stderr)
            cls.binaries[name] = binary
        raw = cls.run_binary('resident_performance_core', '--emit-json')
        cls.performance = json.loads(raw)
        cls.performance_path = cls.output/'performance_cpp_run2.json'
        cls.performance_path.write_text(raw, encoding='utf-8')
        print('RESIDENT_NATIVE_LOCAL_ARTIFACTS '+str(cls.output), flush=True)

    @classmethod
    def run_binary(cls, name, *args):
        result = subprocess.run([str(cls.binaries[name]), *args], cwd=ROOT, env=cls.environment,
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise AssertionError('CPU self-test failed: '+result.stdout+result.stderr)
        if result.stderr:
            raise AssertionError('CPU self-test emitted diagnostics: '+result.stderr)
        return result.stdout

    def test_01_real_core_has_exact50_budget_missing_info_duplicate_expiry_failures(self):
        raw = self.run_binary('resident_model_core')
        self.assertIn('RESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS', raw)
        self.assertIn('MODEL_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS', raw)

    def test_02_actual_model_pack_remains_same49_layer_dense_sparse_weights(self):
        raw = self.run_binary('resident_model_core', 'model/weights.bin', 'model/pl_w_data.bin', 'model/pl_w_meta.bin')
        self.assertIn('PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS', raw)

    def test_03_real_cpp_receipt_distinguishes_second_graph_and_lifetime(self):
        value = self.performance
        self.assertEqual(value['schema'], 'opt50_performance_native_resident_v4')
        self.assertEqual(value['profile'], 'b24-resident-low-overhead-v4')
        self.assertEqual(value['run_id'], 2)
        self.assertEqual(value['max_graphs'], 2)
        self.assertEqual(value['total_START_budget'], 72)
        self.assertEqual(value['lifetime_START_commands'], 72)
        self.assertEqual(value['lifetime_completed_graphs'], 2)
        self.assertEqual(value['prior_verified_run'], 1)
        self.assertEqual(value['last_verified_run'], 2)
        self.assertEqual(value['START_commands_issued'], 36)
        self.assertEqual((value['PL_dense'], value['PL_sparse']), (12, 24))

    def test_04_weight_h2d_is_lifetime_once_and_graph_checks_reset(self):
        value = self.performance
        self.assertEqual(value['initial_weight_sync_scope'], 'owner_lifetime')
        self.assertEqual(value['initial_weight_to_device_sync_calls'], 2)
        self.assertEqual(value['initial_weight_to_device_sync_bytes'], 2994176)
        self.assertEqual(value['postrun_owner_sync_calls'], 4)
        self.assertEqual(value['postrun_guard_checks'], 4)
        self.assertEqual(value['postrun_static_weight_sha_checks'], 2)
        self.assertTrue(value['postrun_buffer_checks_verified'])
        self.assertFalse(value['device_closed'])
        self.assertEqual(value['graph_owner_sync_calls'], 0)
        self.assertEqual(value['hot_path_fsync_count'], 0)

    def test_05_each_graph_trace_is_182_events_and_end_before_info(self):
        value = self.performance
        rows = value['ram_events']
        self.assertEqual(len(rows), 182)
        self.assertEqual(rows[0]['phase'], 'MODEL_BEGIN')
        self.assertEqual(rows[-1]['phase'], 'MODEL_END_COMPLETED')
        self.assertGreater(value['postrun_checks_started_ns'], rows[-1]['monotonic_ns'])
        self.assertGreater(value['postrun_checks_completed_ns'], value['postrun_checks_started_ns'])
        for index in range(36):
            group = rows[1+5*index:1+5*(index+1)]
            self.assertEqual([x['command'] for x in group], [index+1]*5)
            self.assertEqual([x['phase'] for x in group], [
                'DMA_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED',
                'DMA_COMPLETION_PROVEN', 'LAYER_COMPLETION_VERIFIED'])

    def test_06_performance_marker_retained_for_real_arm_build_script(self):
        self.assertEqual(self.run_binary('resident_performance_core').strip(),
                         'PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS')

    def test_07_plugin_arithmetic_and_all_numeric_helpers_are_unchanged(self):
        text = (ROOT/'source/b_plugin.cpp').read_text(encoding='utf-8')
        body = text[text.index('struct Kernel {'):text.index('struct CustomOp:', text.index('struct Kernel {'))]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(),
                         '23ee767aa3ebd804742d1fd5dae129f557438c09d103a29c90c12ba0f3286f94')
        expected = {
            'quant_neon.h': '9dc4467440058740b034d386baeebcd24173c4fa2df2533551102452198a6cc6',
            'qkv_dequant.h': '6a6c428ba19164296b0f264a1a03f9d76d41ef84cdb30f71bd58db003240e864',
            'gelu4.h': '19c042ee5734b034d184813267532f96ac6722f48f886457118874dd0f7ec6e4',
            'layout4.h': '9b4b2ed6c49cfd7687c14de24528eb53ff9d888175855170cc8ed9fd5bf29dc5',
            'route.h': 'd5aaf98aa2945158d9210cae4d3a4441328bf11d02b1636c8224ad2022e0cb1c',
            'protocol.h': '558b986e5936999a43240e72660b347ef84d5527d8d9a6fe9f7e6b1776dce440'}
        for name, wanted in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((ROOT/'source'/name).read_bytes()).hexdigest(), wanted)


if __name__ == '__main__':
    unittest.main(verbosity=2)
