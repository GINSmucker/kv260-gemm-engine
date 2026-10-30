"""Fixed fix1 historical dispatch audits all147 immutable source/model files."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('_fix2_previous_compat_tests',ROOT/'tests/test_release_compatibility.py')
fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
c=fixture.compat


class FixedFix1CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old=ROOT.parent/'b24_batch_acceptance_resident_v4_fix1'
        cls.raw=(ROOT/'predecessor_v4_fix1_tools_manifest.json').read_bytes()
        cls.manifest=json.loads(cls.raw)
        cls.files={name:(old/name).read_bytes() for name in cls.manifest['files']}
        cls.current={'manifest':{'bytes':1,'sha256':'f'*64},'files':cls.manifest['files']}

    def test_fixed_pin_and_all147_files_precede_execution_of_historical_entry(self):
        self.assertEqual(c.identity(self.raw),c.PREDECESSOR_V4_FIX1)
        self.assertEqual(len(self.files),147)
        storage=fixture.VirtualPrivate(self.files,self.raw)
        entry,root,info=c.load_closed_entry(storage.path,self.current,access=storage)
        self.assertEqual(info['release'],'predecessor_v4_fix1')
        self.assertEqual(info['tools_manifest'],c.PREDECESSOR_V4_FIX1)
        self.assertEqual(set(storage.reads),set(storage.files))
        self.assertEqual(entry.HERE,root/'tools')

    def test_changed_model_or_header_is_refused_before_historical_dispatch(self):
        for name in ('model/manifest.json','source/kernel.cpp','source/quant_neon.h'):
            with self.subTest(file=name):
                storage=fixture.VirtualPrivate(self.files,self.raw)
                target=storage.root/name if name.startswith('model/') else storage.root/'tools'/name
                storage.files[target]+=b'changed'
                with self.assertRaisesRegex(ValueError,'root-copied release changed'):
                    c.load_closed_entry(storage.path,self.current,access=storage)

    def test_same_numerical_contract_admits_cpu_dense_fix1_record_without_old_execution(self):
        self.assertEqual(c.dense_source(c.PREDECESSOR_V4_FIX1,self.current,folder=ROOT),'predecessor_v4_fix1')
        self.assertEqual(c.predecessor_manifest(ROOT,version='predecessor_v4_fix1'),self.manifest)

    def test_similar_unknown_manifest_cannot_extend_release_allowlist(self):
        storage=fixture.VirtualPrivate(self.files,self.raw)
        listing=dict(self.manifest,software_revision='arbitrary-fixed-release')
        storage.files[storage.root/'tools/tools_manifest.json']=json.dumps(listing).encode()
        with self.assertRaisesRegex(ValueError,'unknown coordinator release'):
            c.load_closed_entry(storage.path,self.current,access=storage)


if __name__=='__main__': unittest.main()
