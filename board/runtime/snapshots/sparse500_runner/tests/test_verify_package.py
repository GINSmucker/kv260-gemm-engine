"""Real file/hash fixtures only; no runtime, owner, SSH, or dataset opened."""
import copy
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import types
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).absolute().parent.parent
spec=importlib.util.spec_from_file_location('_acceptance500_package_fixture',HERE/'verify_package.py')
v=importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
with patch.dict(sys.modules,{'verify_package':v}):
    source=importlib.util.spec_from_file_location('_reboot_static_installer_fixture',HERE/'install_reboot.py')
    installer=importlib.util.module_from_spec(source); source.loader.exec_module(installer)


class PackageTests(unittest.TestCase):
    def setUp(self):
        parent=HERE/'.test_package'; parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent,prefix='source_')
        self.root=Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent,parent.resolve()); self.addCleanup(self.temp.cleanup)
        self.files={name:('fixture source '+name).encode() for name in v.FILES}
        for name,raw in self.files.items():
            path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
        self.manifest={'schema':v.SCHEMA,'target_total':500,'source_fix2':copy.deepcopy(v.SOURCE_FIX2),
                       'files':{name:v.identity(raw) for name,raw in self.files.items()}}
        self.seal()

    def seal(self):
        raw=(json.dumps(self.manifest,sort_keys=True)+'\n').encode()
        (self.root/'files_manifest.json').write_bytes(raw); self.sha=v.identity(raw)['sha256']

    def test_exact_new_tools_pass_without_execution(self):
        self.assertEqual(v.verify_package(self.root,self.sha),self.manifest)

    def test_changed_tool_or_wrong_manifest_sha_refused(self):
        with self.assertRaisesRegex(ValueError,'manifest SHA256'):v.verify_package(self.root,'0'*64)
        (self.root/'report500.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'identity differs'):v.verify_package(self.root,self.sha)

    def test_target_cannot_be_extended_or_bool(self):
        for value in (1800,501,True):
            self.manifest['target_total']=value; self.seal()
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'fixed 500'):v.verify_package(self.root,self.sha)

    def test_fix2_identity_or_filecount_change_refused(self):
        self.manifest['source_fix2']['files']=154; self.seal()
        with self.assertRaisesRegex(ValueError,'fixed 500'):v.verify_package(self.root,self.sha)

    def test_unknown_payload_and_path_traversal_refused(self):
        for name in ('model/weights.bin','../escape','unexpected.py'):
            self.manifest['files'][name]=v.identity(b'x'); self.seal()
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'fixed 500'):v.verify_package(self.root,self.sha)
            del self.manifest['files'][name]

    def test_missing_known_tool_refused(self):
        (self.root/'report500.py').unlink()
        with self.assertRaises(FileNotFoundError):v.verify_package(self.root,self.sha)

    def test_linked_tool_path_refused_explicit_windows_metadata_fixture(self):
        original=Path.lstat; selected=self.root/'run_to_500.py'
        def observe(path,*args,**kwargs):
            if path==selected:return types.SimpleNamespace(st_mode=stat.S_IFLNK|0o777)
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',observe),self.assertRaisesRegex(ValueError,'linked'):
            v.verify_package(self.root,self.sha)


class StaticDeltaTests(unittest.TestCase):
    def setUp(self):
        files={'source/kernel.cpp':v.identity(b'fixed native')}
        files.update({'model/file_%03d'%n:v.identity(b'fixed model') for n in range(94)})
        files.update({name:v.identity(('fixed '+name).encode()) for name in installer.CHANGED})
        files.update({'helper_%03d.py'%n:v.identity(b'fixed helper') for n in range(56)})
        self.old={'schema':'fixture','software_revision':'resident-bounded-batch-v4-fix2','files':files}
        self.new=copy.deepcopy(self.old)
        for name in installer.CHANGED|installer.ADDED:self.new['files'][name]=v.identity(('new '+name).encode())
        self.new['software_revision']='resident-bounded-batch-v4-reboot-r1'
        self.new['predecessor_h1_tools_manifest']=installer.OLD

    def test_exact_155_to_158_inventory_and_seven_delta_pass(self):
        installer.delta(self.old,self.new)

    def test_any_second_source_change_is_refused(self):
        self.new['files']['source/kernel.cpp']={'bytes':1,'sha256':'0'*64}
        with self.assertRaisesRegex(ValueError,'exact four'):installer.delta(self.old,self.new)

    def test_static_metadata_and_inventory_must_not_change(self):
        self.new['software_revision']='other'
        with self.assertRaisesRegex(ValueError,'metadata'):installer.delta(self.old,self.new)
        self.new=copy.deepcopy(self.old); del self.new['files']['source/kernel.cpp']
        with self.assertRaisesRegex(ValueError,'inventory'):installer.delta(self.old,self.new)

    def test_old_and_new_fixed_manifest_pins_are_separate(self):
        self.assertEqual(installer.OLD['sha256'],'8f256e8eca931b80ea9513c62f0921ec0700558c5604476ea68902833411bd1e')
        self.assertEqual(installer.NEW['sha256'],v.SOURCE_FIX2['tools_manifest']['sha256'])

    def test_any_unlisted_new_python_path_is_refused(self):
        self.new['files']['arbitrary.py']=v.identity(b'not allowlisted')
        with self.assertRaisesRegex(ValueError,'fixed 155-to-158'):installer.delta(self.old,self.new)

    def test_missing_exact_added_path_is_refused(self):
        del self.new['files']['reboot_review.py']
        with self.assertRaisesRegex(ValueError,'fixed 155-to-158'):installer.delta(self.old,self.new)

    def test_unlisted_static_metadata_is_refused(self):
        self.new['unexpected_metadata']='not allowed'
        with self.assertRaisesRegex(ValueError,'fixed reboot static metadata'):installer.delta(self.old,self.new)


if __name__=='__main__':unittest.main(verbosity=2)
