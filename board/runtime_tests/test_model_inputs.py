"""Synthetic ARM receipts; never compile, load or run an ARM binary."""
import copy
import hashlib
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import model_inputs as m


class InputTests(unittest.TestCase):
    def setUp(self):
        self.perf = json.loads((Path(m.__file__).parent/'source_manifest.json').read_bytes())
        self.baseline = json.loads((Path(m.__file__).parent/'expected_arm_build_receipt.json').read_bytes())
        raw = (Path(m.__file__).parent/'source_manifest.json').read_bytes()
        self.source_pin = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.flags = ['-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                      '-DKV5_DIAGNOSTIC', '-DOPT50_PERFORMANCE_LIGHT=1', '-DOPT50_H576_ALIGNED=1', '-DOPT50_FAIR_COMPARE=1', '-fno-fast-math', '-ffp-contract=off']
        self.build = dict(schema='opt50_fair_arm_build_v1', status='ARM_PERFORMANCE_BUILD_PASS_NOT_BOARD_RUN',
            supported_execution_modes=['all_dense', 'mixed'], active_data_capacity_bytes=3981312,
            measurement_profile=m.PROFILE, hot_path_disk_logging=False, poll_delay_us=50, per_layer_deadline_seconds=120,
            graph_owner_sync_calls=0, graph_owner_payload_access=False,
            full_validation_scope='Initialization and post-timer INFO after client close; not per-layer or END',
            source_manifest=self.source_pin, sources={n: pin for n, pin in self.perf['files'].items()
                if n.startswith('source/') or n in ('test_model_core.cpp', 'test_performance_core.cpp')},
            model_manifest=m.MODEL, hardware_executed=False, native_owner_executed=False,
            compiler=self.baseline['compiler'], commands=[[self.baseline['compiler']['path'], *self.flags] for _ in range(4)],
            binaries={n: {'bytes': 64, 'sha256': '0'*64} for n in
                ('opt50_model_owner', 'libopt50_model.so', 'model_core_selftest', 'performance_core_selftest')},
            CPU_core_self_test=self.baseline['CPU_core_self_test'], performance_core_self_test={
                'returncode': 0, 'stderr': '', 'stdout': 'PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n'})
        self.c = types.SimpleNamespace(decode_json=json.loads)
        self.patch = patch.object(m, 'pins', return_value=({'performance_release': self.source_pin}, {}, self.baseline, self.perf))
        self.patch.start(); self.addCleanup(self.patch.stop)

    def validate(self, value):
        return m.validate_build(self.c, json.dumps(value).encode())

    def test_same_compiler_model_new_profile_exact_four_binaries(self):
        self.assertEqual(len(self.validate(self.build)['sources']), 25)

    def test_old_diagnostic_receipt_or_source_changes_refused(self):
        for key, wrong in [('schema', 'opt50_model_arm_build_v1'), ('measurement_profile', 'diagnostic'),
                           ('hot_path_disk_logging', True), ('hardware_executed', True), ('poll_delay_us', 10000),
                           ('per_layer_deadline_seconds', 1), ('source_manifest', {}), ('sources', {}),
                           ('compiler', {}), ('model_manifest', {}), ('graph_owner_sync_calls', 1),
                           ('graph_owner_payload_access', True), ('full_validation_scope', 'per-layer')]:
            value = copy.deepcopy(self.build); value[key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(value)

    def test_missing_profile_flag_and_fast_math_refused(self):
        for flag in ('-DOPT50_PERFORMANCE_LIGHT=1', '-DOPT50_H576_ALIGNED=1', '-DOPT50_FAIR_COMPARE=1', '-ffp-contract=off', '-Werror'):
            value = copy.deepcopy(self.build); value['commands'][0].remove(flag)
            with self.assertRaises(ValueError):
                self.validate(value)
        value = copy.deepcopy(self.build); value['commands'][0].append('-ffast-math')
        with self.assertRaises(ValueError):
            self.validate(value)

    def test_selftests_and_binary_inventory_required(self):
        value = copy.deepcopy(self.build); value['binaries'].pop('performance_core_selftest')
        with self.assertRaises(ValueError):
            self.validate(value)
        value = copy.deepcopy(self.build); value['performance_core_self_test']['returncode'] = 1
        with self.assertRaises(ValueError):
            self.validate(value)


if __name__ == '__main__':
    unittest.main()
