"""Real three-release receipt/pin fixtures; ARM execution and POSIX are fakes.

The unchanged published fix1 builder is loaded and exercises its actual v4
native-command verifier. No ELF/owner/compiler executes. Only the two new CPU
self-test invocations, root-ARM admission and durable writes are injected.
"""
import copy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).absolute().parents[1]
FIX1 = ROOT.parent/'b24_batch_acceptance_resident_v4_fix1'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


previous_verify = sys.modules.get('verify_tools')
try:
    sys.modules['verify_tools'] = load(ROOT/'verify_tools.py', '_fix2_reuse_verify_fixture_loader')
    b = load(ROOT/'build_arm.py', '_fix2_reuse_builder_fixture_loader')
finally:
    if previous_verify is None:
        sys.modules.pop('verify_tools', None)
    else:
        sys.modules['verify_tools'] = previous_verify


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def save(path, value):
    put(path, (json.dumps(value, sort_keys=True, indent=2)+'\n').encode())


class Fix2ReuseTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'.test_build'; parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent, prefix='reuse-fix2-')
        self.base = Path(self.temp.name).resolve()
        self.assertEqual(self.base.parent, parent.resolve())
        self.addCleanup(self.temp.cleanup)
        self.native = self.base/'v4'; self.old = self.base/'fix1'; self.new = self.base/'fix2'
        for folder in (self.native, self.old, self.new): folder.mkdir()
        names = ('source/kernel.cpp', 'source/native_gate.hpp', 'source/model_core.hpp', 'source/owner.cpp',
                 'source/b_plugin.cpp', 'source/client.cpp', 'test_model_core.cpp', 'test_performance_core.cpp',
                 'support_py/durable.py', 'model/weights.bin', 'model/pl_w_data.bin', 'model/pl_w_meta.bin',
                 'model/manifest.json', 'data/labels.csv', 'verify_tools.py', 'build_arm.py')
        native_files = {name: ('fixed fixture bytes '+name).encode() for name in names}
        for index in range(140-len(native_files)):
            native_files['docs/fixture_%03d.txt'%index] = b'fixed fixture data'
        self.native_listing = self.make_release(self.native, native_files)
        native_raw = (self.native/'tools_manifest.json').read_bytes()
        fix1_files = dict(native_files)
        fix1_files.update({'verify_tools.py': (FIX1/'verify_tools.py').read_bytes(),
                          'build_arm.py': (FIX1/'build_arm.py').read_bytes(),
                          'predecessor_v4_tools_manifest.json': native_raw,
                          'stage_arm.py': b'# fixture existing stage helper\n',
                          'before_owner.py': b'# fixture existing no-Native helper\n',
                          'prepare_fix1.py': b'# fixture existing preparation\n',
                          'tests/fixture_fix1_1.py': b'# fixture test\n',
                          'tests/fixture_fix1_2.py': b'# fixture test\n',
                          'tests/fixture_fix1_3.py': b'# fixture test\n'})
        self.assertEqual(len(fix1_files), 147)
        self.old_listing = self.make_release(self.old, fix1_files)
        self.old_raw = (self.old/'tools_manifest.json').read_bytes()
        new_files = dict(fix1_files, **{'predecessor_v4_fix1_tools_manifest.json': self.old_raw,
                                      'support_py/publisher_snapshot.py': b'# fixture publication reader\n'})
        self.new_listing = self.make_release(self.new, new_files)
        self.output = self.new/'build_arm'
        self.compiler = self.base/'compiler'
        put(self.compiler, b'fixture compiler hash; no executable is invoked')
        self.compiler_info = {'path': str(self.compiler.resolve()), 'version': 'fixture',
                              **b.identity(self.compiler.read_bytes())}
        self.cpu = {'CPU_core_self_test': {'returncode': 0, 'stderr': '', 'stdout':
            'PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS\nRESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n'},
            'performance_core_self_test': {'returncode': 0, 'stderr': '', 'stdout':
            'PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n'}}
        for folder in (self.native/'build_arm', self.old/'build_arm'):
            folder.mkdir()
            for name in b.BINARY_NAMES:
                raw = bytearray(64); raw[:7] = b'\x7fELF\x02\x01\x01'
                struct.pack_into('<H', raw, 16, 3 if name.endswith('.so') else 2)
                struct.pack_into('<H', raw, 18, 183)
                put(folder/name, bytes(raw)+name.encode())
        self.native_receipt = {'schema': 'opt50_b24_resident_arm_build_v4', 'status': 'ARM_BUILD_PASS_NOT_BOARD_RUN',
            'source_release_manifest': b.identity(native_raw), 'build_method': 'native_compile',
            'measurement_profile': b.PROFILE, 'lifecycle_profile': b.LIFECYCLE,
            'hardware_executed': False, 'native_owner_executed': False,
            'sources': b.source_pins(self.native), 'compiler': copy.deepcopy(self.compiler_info),
            'binaries': {name: b.elf((self.native/'build_arm'/name).read_bytes()) for name in b.BINARY_NAMES},
            'model_manifest_sha256': b.identity((self.native/'model/manifest.json').read_bytes())['sha256'],
            'commands': b.compile_commands(self.compiler, self.native/'model', self.native/'build_arm', root=self.native),
            **copy.deepcopy(self.cpu)}
        save(self.native/'build_arm/build_receipt.json', self.native_receipt)
        original_context = b.predecessor_builder
        self.context_count = 0

        @contextmanager
        def predecessor(source, listing):
            self.context_count += 1
            with original_context(source, listing) as fixed:
                with patch.object(fixed, 'ORIGINAL_ROOT', self.native), \
                     patch.object(fixed, 'ORIGINAL_MANIFEST', b.identity(native_raw)), \
                     patch.object(fixed, 'verify', side_effect=self.verify_fixture), \
                     patch.object(fixed, 'verify_compiler', side_effect=self.verify_compiler_fixture):
                    yield fixed
        self.predecessor_context = predecessor
        with predecessor(self.old, self.old_listing) as fixed:
            origin, checked_native = fixed.original_build(self.old)
            self.assertEqual(checked_native, self.native_receipt)
            self.old_receipt = {'schema': 'opt50_b24_resident_arm_reuse_fix1', 'status': b.REUSE_STATUS,
                'source_release_manifest': b.identity(self.old_raw), 'build_method': 'reuse_verified_source_identical',
                'measurement_profile': b.PROFILE, 'lifecycle_profile': b.LIFECYCLE,
                'hardware_executed': False, 'native_owner_executed': False,
                'sources': b.source_pins(self.old), 'compiler': copy.deepcopy(self.compiler_info),
                'binaries': copy.deepcopy(self.native_receipt['binaries']), 'reuse_proof': origin,
                'model_manifest_sha256': b.identity((self.old/'model/manifest.json').read_bytes())['sha256'],
                **copy.deepcopy(self.cpu)}
            save(self.old/'build_arm/build_receipt.json', self.old_receipt)
            self.assertEqual(fixed.verify_build(self.old/'build_arm', root=self.old), self.old_receipt)
        self.old_receipt_raw = (self.old/'build_arm/build_receipt.json').read_bytes()
        self.patches = [patch.object(b, 'ORIGINAL_ROOT', self.old),
                        patch.object(b, 'ORIGINAL_MANIFEST', b.identity(self.old_raw)),
                        patch.object(b, 'predecessor_builder', side_effect=predecessor),
                        patch.object(b, 'verify', side_effect=self.verify_fixture),
                        patch.object(b, 'native_arm')]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)

        def copy_new(source, target, expected, mode=0o600):
            raw = b.read(source); b.need(b.identity(raw) == expected, 'fixture copy source differs')
            with Path(target).open('xb') as stream: stream.write(raw)
            b.need(b.identity(b.read(target)) == expected, 'fixture copied bytes differ')
        def new_json(path, value):
            with Path(path).open('xb') as stream: stream.write((json.dumps(value, sort_keys=True)+'\n').encode())
        self.durable = types.SimpleNamespace(mkdir_new=lambda path: Path(path).mkdir(), copy_new=copy_new, new_json=new_json)
        item = patch.object(b, 'durable_api', return_value=self.durable); item.start(); self.addCleanup(item.stop)
        def execute(argv, **kwargs):
            name = Path(argv[0]).name
            self.assertIn(name, b.BINARY_NAMES[:2], 'owner/compiler/shared library must never execute')
            field = 'CPU_core_self_test' if name == b.BINARY_NAMES[0] else 'performance_core_self_test'
            return types.SimpleNamespace(**self.cpu[field])
        item = patch.object(b.subprocess, 'run', side_effect=execute)
        self.execute = item.start(); self.addCleanup(item.stop)

    @staticmethod
    def make_release(root, files):
        for name, raw in files.items(): put(root/name, raw)
        listing = {'schema': 'opt50_b24_batch_tools_v1', 'hardware_executed': False,
                   'files': {name: b.identity(raw) for name, raw in files.items()}}
        save(root/'tools_manifest.json', listing)
        return listing

    @staticmethod
    def verify_fixture(root):
        root = Path(root); listing = json.loads((root/'tools_manifest.json').read_bytes())
        for name, expected in listing['files'].items():
            b.need(b.identity(b.read(root/name)) == expected, 'fixture release changed: '+name)
        return listing

    def verify_compiler_fixture(self, value):
        b.need(value['compiler'] == self.compiler_info and b.identity(self.compiler.read_bytes()) ==
               {name: self.compiler_info[name] for name in ('bytes', 'sha256')}, 'fixture compiler changed')

    def mutate_receipt(self, folder, transform):
        path = folder/'build_arm/build_receipt.json'; value = json.loads(path.read_bytes())
        transform(value); save(path, value)

    def test_complete_receipt_reuses_fix1_but_retains_original_native_commands(self):
        value = b.reuse_build(self.output, self.new)
        self.assertEqual(value['schema'], 'opt50_b24_resident_arm_reuse_fix2')
        self.assertEqual(value['reuse_proof']['source_root'], str(self.old))
        self.assertEqual(value['reuse_proof']['receipt_rawpin'], b.identity(self.old_receipt_raw))
        self.assertEqual(value['reuse_proof']['original_native_reuse_proof'], self.old_receipt['reuse_proof'])
        self.assertEqual(value['reuse_proof']['original_native_reuse_proof']['original_commands'], self.native_receipt['commands'])
        self.assertNotIn('commands', value)
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual([Path(call.args[0][0]).name for call in self.execute.call_args_list], list(b.BINARY_NAMES[:2]))
        self.assertFalse(value['hardware_executed'] or value['native_owner_executed'])
        for name in b.BINARY_NAMES:
            self.assertEqual((self.output/name).read_bytes(), (self.old/'build_arm'/name).read_bytes())
        self.assertEqual(b.verify_build(self.output, self.new), value, 'read-only verification returns the full sealed board_build receipt')
        self.assertEqual(self.execute.call_count, 2, 'verify-only must not rerun any program')

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir(); put(self.output/'keep', b'keep')
        with self.assertRaisesRegex(ValueError, 'never overwrite'): b.reuse_build(self.output, self.new)
        self.assertEqual((self.output/'keep').read_bytes(), b'keep'); self.execute.assert_not_called()

    def test_alternate_output_is_refused(self):
        target = self.base/'unrelated'
        with self.assertRaisesRegex(ValueError, 'only this release'): b.reuse_build(target, self.new)
        self.assertFalse(target.exists()); self.execute.assert_not_called()

    def test_wrong_fixed_fix1_manifest_is_refused(self):
        put(self.new/'predecessor_v4_fix1_tools_manifest.json', b'changed predecessor')
        self.new_listing['files']['predecessor_v4_fix1_tools_manifest.json'] = b.identity(b'changed predecessor')
        save(self.new/'tools_manifest.json', self.new_listing)
        with self.assertRaisesRegex(ValueError, 'predecessor fix1'): b.reuse_build(self.output, self.new)
        self.assertFalse(self.output.exists())

    def test_changed_fix1_public_file_is_refused_before_copy(self):
        put(self.old/'model/weights.bin', b'changed')
        with self.assertRaisesRegex(ValueError, 'public fix1 file'): b.reuse_build(self.output, self.new)
        self.assertFalse(self.output.exists())

    def test_older_native_commands_are_not_rewritten_to_fix2(self):
        self.mutate_receipt(self.native, lambda value: value.__setitem__('commands', b.compile_commands(
            self.compiler, self.new/'model', self.output, root=self.new)))
        with self.assertRaisesRegex(ValueError, 'compiler flags'): b.reuse_build(self.output, self.new)
        self.assertFalse(self.output.exists())

    def test_native_origin_missing_resident_flag_is_refused(self):
        self.mutate_receipt(self.native, lambda value: value['commands'][3].remove('-DOPT50_RESIDENT_BATCH=1'))
        with self.assertRaisesRegex(ValueError, 'compiler flags'): b.reuse_build(self.output, self.new)

    def test_native_origin_selftest_failure_is_refused(self):
        self.mutate_receipt(self.native, lambda value: value['performance_core_self_test'].__setitem__('stderr', 'bad'))
        with self.assertRaisesRegex(ValueError, 'self-test'): b.reuse_build(self.output, self.new)

    def test_changed_fix1_receipt_provenance_boolean_is_refused(self):
        self.mutate_receipt(self.old, lambda value: value.__setitem__('reuse_proof', {'source_bytes_identical': True}))
        with self.assertRaisesRegex(ValueError, 'provenance differs'): b.reuse_build(self.output, self.new)

    def test_changed_fix1_elf_is_refused(self):
        put(self.old/'build_arm/opt50_model_owner', b'broken ELF')
        with self.assertRaises(ValueError): b.reuse_build(self.output, self.new)
        self.assertFalse(self.output.exists())

    def test_new_native_bytes_even_resealed_require_recompile(self):
        name = 'source/kernel.cpp'; put(self.new/name, b'changed kernel')
        self.new_listing['files'][name] = b.identity((self.new/name).read_bytes()); save(self.new/'tools_manifest.json', self.new_listing)
        with self.assertRaisesRegex(ValueError, 'recompile required'): b.reuse_build(self.output, self.new)

    def test_new_data_bytes_even_resealed_require_recompile(self):
        name = 'data/labels.csv'; put(self.new/name, b'changed labels')
        self.new_listing['files'][name] = b.identity((self.new/name).read_bytes()); save(self.new/'tools_manifest.json', self.new_listing)
        with self.assertRaisesRegex(ValueError, 'model/data/native inventory'): b.reuse_build(self.output, self.new)

    def test_new_cpu_failure_keeps_copies_without_success_receipt(self):
        self.cpu['performance_core_self_test']['stdout'] = 'missing marker'
        with self.assertRaisesRegex(ValueError, 'CPU-only'): b.reuse_build(self.output, self.new)
        self.assertTrue(self.output.exists()); self.assertFalse((self.output/'build_receipt.json').exists())

    def test_readonly_verify_reaudits_original_native_chain(self):
        b.reuse_build(self.output, self.new)
        self.mutate_receipt(self.native, lambda value: value['CPU_core_self_test'].__setitem__('stderr', 'changed'))
        with self.assertRaisesRegex(ValueError, 'self-test'): b.verify_build(self.output, self.new)

    def test_rewritten_fix2_proof_is_not_accepted(self):
        b.reuse_build(self.output, self.new)
        self.mutate_receipt(self.new, lambda value: value['reuse_proof'].__setitem__('original_native_reuse_proof', {}))
        with self.assertRaisesRegex(ValueError, 'provenance differs'): b.verify_build(self.output, self.new)

    def test_copied_binary_corruption_is_rejected_without_reexecution(self):
        b.reuse_build(self.output, self.new); put(self.output/'libopt50_model.so', b'bad copy')
        with self.assertRaises(ValueError): b.verify_build(self.output, self.new)
        self.assertEqual(self.execute.call_count, 2)

    def test_isolated_predecessor_loader_restores_ambient_verify_module(self):
        ambient = types.ModuleType('ambient-verify-must-be-restored')
        with patch.dict(sys.modules, {'verify_tools': ambient}):
            b.original_build(self.new)
            self.assertIs(sys.modules['verify_tools'], ambient)


if __name__ == '__main__':
    unittest.main(verbosity=2)
