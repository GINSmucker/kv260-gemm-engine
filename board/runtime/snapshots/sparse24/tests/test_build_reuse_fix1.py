"""Source-identical reuse provenance tests; no ARM ELF or owner is executed.

Four small fixture ELF headers, source inventories and file copies are real.
Only native ARM admission, durable OS primitives and CPU program execution are
injected on Windows. Production durable copy has its own native smoke tests.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
# This is a historical fix1 contract, not the current fix2 reuse implementation.
# Re-read its fixed predecessor pin before executing the local archived builder.
OLD = ROOT.parent/'b24_batch_acceptance_resident_v4_fix1'
old_manifest_raw=(ROOT/'predecessor_v4_fix1_tools_manifest.json').read_bytes()
assert len(old_manifest_raw)==23152 and hashlib.sha256(old_manifest_raw).hexdigest()== \
    '03d0b13bb25bc04dbffd561e08267812db8bdd6697ad474ee0b2f40294773fb1'
old_builder_raw=(OLD/'build_arm.py').read_bytes()
assert {'bytes':len(old_builder_raw),'sha256':hashlib.sha256(old_builder_raw).hexdigest()}== \
    json.loads(old_manifest_raw)['files']['build_arm.py']
spec = importlib.util.spec_from_file_location('fix1_reuse_builder_test', OLD/'build_arm.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def write_json(path, value):
    put(path, (json.dumps(value, sort_keys=True, indent=2)+'\n').encode())


class BuildReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.build-reuse-fix1-', dir=ROOT.parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.old = self.base/'original'; self.new = self.base/'fix1'
        self.old.mkdir(); self.new.mkdir()
        files = {name: ('fixed bytes '+name).encode() for name in (
            'source/kernel.cpp', 'source/native_gate.hpp', 'source/model_core.hpp', 'source/owner.cpp',
            'source/b_plugin.cpp', 'source/client.cpp', 'test_model_core.cpp', 'test_performance_core.cpp',
            'support_py/durable.py', 'model/weights.bin', 'model/pl_w_data.bin', 'model/pl_w_meta.bin')}
        files['model/manifest.json'] = b'{"fixture":true}\n'
        for number in range(140-len(files)):
            files['docs/file_%03d.txt' % number] = b'unchanged fixture data'
        for name, raw in files.items():
            put(self.old/name, raw); put(self.new/name, raw)
        self.old_manifest = {'schema': 'opt50_b24_batch_tools_v1', 'hardware_executed': False,
                             'files': {name: b.identity(raw) for name,raw in files.items()}}
        write_json(self.old/'tools_manifest.json', self.old_manifest)
        self.old_manifest_raw = (self.old/'tools_manifest.json').read_bytes()
        put(self.new/'predecessor_v4_tools_manifest.json', self.old_manifest_raw)
        self.current_manifest = copy.deepcopy(self.old_manifest)
        self.current_manifest['files']['predecessor_v4_tools_manifest.json'] = b.identity(self.old_manifest_raw)
        write_json(self.new/'tools_manifest.json', self.current_manifest)
        self.compiler = self.base/'compiler-fixture'
        put(self.compiler, b'fixture compiler identity; never executed')
        self.compiler.chmod(0o444)
        self.addCleanup(lambda:self.compiler.chmod(0o644) if self.compiler.exists() else None)
        old_output = self.old/'build_arm'; old_output.mkdir()
        for name in b.BINARY_NAMES:
            raw = bytearray(64)
            raw[:7] = b'\x7fELF\x02\x01\x01'
            struct.pack_into('<H', raw, 16, 3 if name.endswith('.so') else 2)
            struct.pack_into('<H', raw, 18, 183)
            put(old_output/name, bytes(raw)+name.encode())
        self.tests = {
            'CPU_core_self_test': {'returncode':0, 'stderr':'',
                'stdout':'PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS\nRESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n'},
            'performance_core_self_test': {'returncode':0, 'stderr':'',
                'stdout':'PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n'}}
        self.receipt = {'schema':'opt50_b24_resident_arm_build_v4', 'status':'ARM_BUILD_PASS_NOT_BOARD_RUN',
            'hardware_executed':False, 'native_owner_executed':False, 'measurement_profile':b.PROFILE,
            'lifecycle_profile':b.LIFECYCLE, 'build_method':'native_compile',
            'source_release_manifest':b.identity(self.old_manifest_raw),
            'model_manifest_sha256':b.identity((self.old/'model/manifest.json').read_bytes())['sha256'],
            'sources':b.source_pins(self.old), 'binaries':{n:b.elf((old_output/n).read_bytes()) for n in b.BINARY_NAMES},
            'compiler':{'path':str(self.compiler.resolve()), **b.identity(self.compiler.read_bytes()), 'version':'fixture'},
            'commands':b.compile_commands(self.compiler.resolve(),self.old/'model',old_output,root=self.old),
            **copy.deepcopy(self.tests)}
        write_json(old_output/'build_receipt.json', self.receipt)
        self.old_raw_receipt = (old_output/'build_receipt.json').read_bytes()
        self.addCleanup(patch.stopall)
        patch.object(b, 'ORIGINAL_ROOT', self.old).start()
        patch.object(b, 'ORIGINAL_MANIFEST', b.identity(self.old_manifest_raw)).start()
        self.admission = patch.object(b, 'native_arm').start()
        def verify_fixture(root):
            root = Path(root)
            listing = json.loads((root/'tools_manifest.json').read_bytes())
            for name, expected in listing['files'].items():
                b.need(b.identity(b.read(root/name)) == expected, 'fixture release changed: '+name)
            return listing
        patch.object(b, 'verify', side_effect=verify_fixture).start()
        def copy_new(src,dst,pin,mode=0o600):
            raw = b.read(src); b.need(b.identity(raw) == pin, 'fixture source hash differs')
            with Path(dst).open('xb') as stream: stream.write(raw)
            b.need(b.identity(b.read(dst)) == pin, 'fixture copied bytes differ')
        def new_json(path,value):
            with Path(path).open('xb') as stream:
                stream.write((json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
        self.durable = types.SimpleNamespace(mkdir_new=lambda p:Path(p).mkdir(), copy_new=copy_new, new_json=new_json)
        patch.object(b, 'durable_api', return_value=self.durable).start()
        def execute(argv,**kwargs):
            name = Path(argv[0]).name
            field = 'CPU_core_self_test' if name == 'model_core_selftest' else 'performance_core_self_test'
            self.assertIn(name, b.BINARY_NAMES[:2], 'native owner or shared object must never execute')
            return types.SimpleNamespace(**self.tests[field])
        self.execute = patch.object(b.subprocess, 'run', side_effect=execute).start()
        self.output = self.new/'build_arm'

    def original_receipt(self, transform):
        value = copy.deepcopy(self.receipt); transform(value)
        write_json(self.old/'build_arm/build_receipt.json', value)

    def test_reuse_preserves_original_command_paths_and_runs_both_cpu_tests(self):
        value = b.reuse_build(self.output,self.new)
        self.assertEqual(value['schema'], b.REUSE_SCHEMA)
        self.assertEqual(value['status'], b.REUSE_STATUS)
        self.assertEqual(value['build_method'], 'reuse_verified_source_identical')
        self.assertEqual(value['reuse_proof']['receipt_rawpin'], b.identity(self.old_raw_receipt))
        self.assertEqual(value['reuse_proof']['original_commands'], self.receipt['commands'])
        self.assertNotIn('commands', value)
        self.assertFalse(value['hardware_executed'] or value['native_owner_executed'])
        self.assertEqual(value['source_release_manifest'], b.identity((self.new/'tools_manifest.json').read_bytes()))
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual([Path(call.args[0][0]).name for call in self.execute.call_args_list], list(b.BINARY_NAMES[:2]))
        for name in b.BINARY_NAMES:
            self.assertEqual((self.output/name).read_bytes(), (self.old/'build_arm'/name).read_bytes())
        self.assertEqual(b.verify_build(self.output,self.new), value)
        self.assertEqual(self.execute.call_count, 2, '--verify-only must not rerun CPU tests')

    def test_existing_output_is_preserved_and_never_overwritten(self):
        self.output.mkdir(); put(self.output/'keep', b'keep')
        with self.assertRaisesRegex(ValueError,'never overwrite'): b.reuse_build(self.output,self.new)
        self.assertEqual((self.output/'keep').read_bytes(), b'keep')
        self.execute.assert_not_called()

    def test_custom_output_outside_fixed_build_directory_is_refused(self):
        outside = self.base/'unrelated-output'
        with self.assertRaisesRegex(ValueError,'only this release build_arm'):
            b.reuse_build(outside,self.new)
        self.assertFalse(outside.exists())
        self.execute.assert_not_called()

    def test_wrong_fixed_predecessor_manifest_refused(self):
        put(self.new/'predecessor_v4_tools_manifest.json', b'changed')
        with self.assertRaises(ValueError): b.reuse_build(self.output,self.new)
        self.assertFalse(self.output.exists())

    def test_changed_original_public_model_is_refused_before_copy(self):
        put(self.old/'model/weights.bin', b'changed original weights')
        with self.assertRaisesRegex(ValueError,'original public v4 file'): b.reuse_build(self.output,self.new)
        self.assertFalse(self.output.exists())

    def test_new_compiled_source_change_requires_recompile_even_with_new_manifest(self):
        name = 'source/kernel.cpp'; put(self.new/name, b'new arithmetic')
        self.current_manifest['files'][name] = b.identity((self.new/name).read_bytes())
        write_json(self.new/'tools_manifest.json', self.current_manifest)
        with self.assertRaisesRegex(ValueError,'recompile required'): b.reuse_build(self.output,self.new)
        self.assertFalse(self.output.exists())

    def test_new_model_change_requires_recompile_even_with_new_manifest(self):
        name = 'model/weights.bin'; put(self.new/name, b'new numerical model')
        self.current_manifest['files'][name] = b.identity((self.new/name).read_bytes())
        write_json(self.new/'tools_manifest.json', self.current_manifest)
        with self.assertRaisesRegex(ValueError,'model inventory differs'): b.reuse_build(self.output,self.new)

    def test_original_command_rewritten_to_new_root_is_refused(self):
        self.original_receipt(lambda v:v.__setitem__('commands',b.compile_commands(
            self.compiler.resolve(),self.new/'model',self.output,root=self.new)))
        with self.assertRaisesRegex(ValueError,'compiler flags'): b.reuse_build(self.output,self.new)
        self.assertFalse(self.output.exists())

    def test_original_single_shot_or_non_native_receipt_is_refused(self):
        for field,value in (('lifecycle_profile','one-shot'),('build_method','reuse_verified_source_identical'),
                            ('schema',b.REUSE_SCHEMA)):
            self.original_receipt(lambda v:v.__setitem__(field,value))
            with self.assertRaises(ValueError): b.reuse_build(self.output,self.new)
        self.assertFalse(self.output.exists())

    def test_original_binary_sha_change_is_refused(self):
        put(self.old/'build_arm/opt50_model_owner', b'corrupt ELF')
        with self.assertRaises(ValueError): b.reuse_build(self.output,self.new)
        self.assertFalse(self.output.exists())

    def test_original_native_selftest_missing_resident_marker_is_refused(self):
        self.original_receipt(lambda v:v['CPU_core_self_test'].__setitem__('stdout','PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS'))
        with self.assertRaisesRegex(ValueError,'resident state-machine'): b.reuse_build(self.output,self.new)

    def test_new_cpu_failure_preserves_copies_but_never_publishes_success_receipt(self):
        self.tests['performance_core_self_test']['stdout'] = 'failed CPU fixture'
        with self.assertRaisesRegex(ValueError,'CPU-only'): b.reuse_build(self.output,self.new)
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output/'build_receipt.json').exists())

    def test_verify_only_revalidates_original_receipt_instead_of_trusting_booleans(self):
        b.reuse_build(self.output,self.new)
        self.original_receipt(lambda v:v['CPU_core_self_test'].__setitem__('stderr','new error'))
        with self.assertRaisesRegex(ValueError,'self-test missing'): b.verify_build(self.output,self.new)

    def test_reuse_proof_boolean_or_modified_original_commands_is_refused(self):
        b.reuse_build(self.output,self.new)
        receipt=json.loads((self.output/'build_receipt.json').read_bytes())
        receipt['reuse_proof']={'source_bytes_identical':True}
        write_json(self.output/'build_receipt.json', receipt)
        with self.assertRaisesRegex(ValueError,'provenance differs'): b.verify_build(self.output,self.new)

    def test_copied_binary_change_is_refused_on_readonly_verify(self):
        b.reuse_build(self.output,self.new)
        put(self.output/'libopt50_model.so', b'changed destination')
        with self.assertRaises(ValueError): b.verify_build(self.output,self.new)


if __name__ == '__main__': unittest.main()
