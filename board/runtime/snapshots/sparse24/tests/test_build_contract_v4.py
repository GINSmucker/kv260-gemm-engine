"""Device-free v4 ARM build receipt contract; compiler/hardware are never run."""
import copy
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(HERE))
import build_arm
import stage_arm


class ResidentBuildTests(unittest.TestCase):
    def setUp(self):
        self.root = HERE
        self.output = HERE/'_receipt_fixture'
        self.source = {'source/kernel.cpp': build_arm.identity(b'new resident state machine')}
        self.raws = {self.root/'tools_manifest.json': b'fixed-v4-tools',
                     self.root/'model/manifest.json': b'fixed-v4-model'}
        binary = bytearray(64)
        binary[:7] = b'\x7fELF\x02\x01\x01'
        struct.pack_into('<H', binary, 16, 2)
        struct.pack_into('<H', binary, 18, 183)
        for name in build_arm.BINARY_NAMES:
            self.raws[self.output/name] = bytes(binary)
        self.value = {'schema': 'opt50_b24_resident_arm_build_v4',
            'status': 'ARM_BUILD_PASS_NOT_BOARD_RUN', 'hardware_executed': False,
            'native_owner_executed': False, 'measurement_profile': build_arm.PROFILE,
            'lifecycle_profile': build_arm.LIFECYCLE, 'build_method': 'native_compile',
            'source_release_manifest': build_arm.identity(self.raws[self.root/'tools_manifest.json']),
            'model_manifest_sha256': build_arm.identity(self.raws[self.root/'model/manifest.json'])['sha256'],
            'sources': self.source, 'binaries': {name: build_arm.identity(bytes(binary)) for name in build_arm.BINARY_NAMES},
            'compiler': {'path': '/usr/bin/aarch64-linux-gnu-g++-13'},
            'CPU_core_self_test': {'returncode': 0, 'stderr': '', 'stdout': 'PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS\nRESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS'},
            'performance_core_self_test': {'returncode': 0, 'stderr': '', 'stdout': 'PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS'}}
        self.value['commands'] = build_arm.compile_commands(self.value['compiler']['path'], self.root/'model', self.output, root=self.root)

    def verify(self):
        self.raws[self.output/'build_receipt.json'] = json.dumps(self.value).encode()
        with mock.patch.object(build_arm, 'verify', return_value={'files': self.source}), \
             mock.patch.object(build_arm, 'verify_compiler'), \
             mock.patch.object(build_arm, 'source_pins', return_value=self.source), \
             mock.patch.object(build_arm, 'read', side_effect=lambda p: self.raws[Path(p)]):
            return build_arm.verify_build(self.output, self.root)

    def test_complete_new_receipt_accepted(self):
        self.assertEqual(self.verify(), self.value)

    def test_old_schema_rejected(self):
        self.value['schema'] = 'opt50_b24_batch_arm_build_v1'
        with self.assertRaisesRegex(ValueError, 'bind|reuse receipt'):
            self.verify()

    def test_old_binary_reuse_method_rejected(self):
        self.value['build_method'] = 'reuse_verified_source_identical'
        with self.assertRaisesRegex(ValueError, 'bind'):
            self.verify()

    def test_missing_resident_compile_flag_rejected(self):
        self.value['commands'][3].remove('-DOPT50_RESIDENT_BATCH=1')
        with self.assertRaisesRegex(ValueError, 'compiler flags'):
            self.verify()

    def test_changed_float_flags_rejected(self):
        self.value['commands'][2].remove('-ffp-contract=off')
        with self.assertRaisesRegex(ValueError, 'compiler flags'):
            self.verify()

    def test_wrong_lifecycle_rejected(self):
        self.value['lifecycle_profile'] = 'single-shot'
        with self.assertRaisesRegex(ValueError, 'bind'):
            self.verify()

    def test_wrong_source_pin_rejected(self):
        self.value['sources'] = {'source/kernel.cpp': build_arm.identity(b'old single graph')}
        with self.assertRaisesRegex(ValueError, 'bind'):
            self.verify()

    def test_stale_binary_hash_rejected(self):
        self.value['binaries']['opt50_model_owner'] = build_arm.identity(b'old ELF')
        with self.assertRaisesRegex(ValueError, 'binary identity'):
            self.verify()

    def test_selftest_failure_rejected(self):
        self.value['performance_core_self_test']['returncode'] = 1
        with self.assertRaisesRegex(ValueError, 'self-test'):
            self.verify()

    def test_old_single_graph_selftest_markers_do_not_prove_resident_state(self):
        self.value['CPU_core_self_test']['stdout'] = 'PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS'
        with self.assertRaisesRegex(ValueError, 'resident state-machine'):
            self.verify()

    def test_stage_uses_only_controlled_source_identical_reuse(self):
        value={'status':'ARM_RESIDENT_SOURCE_IDENTICAL_REUSE_PASS_NOT_BOARD_RUN',
               'build_method':'reuse_verified_source_identical'}
        with mock.patch.object(sys,'argv',['stage_arm.py']), \
             mock.patch.object(stage_arm,'reuse_build',return_value=value) as reuse, \
             mock.patch.object(stage_arm,'verify_build',side_effect=AssertionError('not verify-only')), \
             mock.patch('builtins.print'):
            self.assertEqual(stage_arm.main(),0)
            reuse.assert_called_once_with(stage_arm.ROOT/'build_arm')



if __name__ == '__main__':
    unittest.main(verbosity=2)
