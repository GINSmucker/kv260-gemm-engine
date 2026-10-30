"""Device-free profile selection tests: namespaces never imply trusted content."""
import copy
import importlib.util
from pathlib import Path, PurePosixPath
import unittest
from unittest.mock import patch

ROOT = Path(__file__).absolute().parents[1]
spec = importlib.util.spec_from_file_location('b24_resident_profile_test', ROOT/'support_py/resident.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

BOOT = '5ed7d791-830f-4a29-b19a-ae2995bddae5'
TOKEN = 'b123456789ab'


class ProfileTests(unittest.TestCase):
    def selection(self, version, *, edit=None):
        namespace = '/var/lib/opt50-loadonly-fd96539d-'+version
        profile = r.PROFILES[namespace]
        value = {'boot_id': BOOT, 'token': TOKEN, 'unit': profile['unit_prefix']+TOKEN,
                 'schema': profile['plan_schema']}
        if edit:
            value.update(edit)
        selected = PurePosixPath(namespace+'/plan_'+TOKEN+'/plan.json')
        with patch.object(r, 'Path', PurePosixPath), patch.object(r, 'root_linux'), \
                patch.object(r, 'private_dir'), patch.object(r, 'read_json', return_value=value), \
                patch.object(r, '_proc_read', return_value=(BOOT+'\n').encode()), \
                patch.object(r, '_guardian_bundle', return_value=(value, {})) as bundle:
            result = r.configure(selected)
            bundle.assert_called_once_with()
        return result

    def setUp(self):
        names = ('OLD_PLAN', 'BOOT', 'GUARDIAN_UNIT', 'LOAD_MANIFEST', 'LOAD_TOOLS_SCHEMA',
                 'LOAD_PLAN_SCHEMA', 'LOAD_STATUS_SCHEMA', 'LOAD_CODE')
        original = {name: copy.deepcopy(getattr(r, name)) for name in names}
        self.addCleanup(lambda: [setattr(r, key, value) for key, value in original.items()])

    def test_v2_uses_original_literal_pin_with_dynamic_live_identity(self):
        result = self.selection('v2')
        self.assertEqual(result['boot_id'], BOOT)
        self.assertEqual(r.GUARDIAN_UNIT, 'opt50-loadonly-v2-'+TOKEN)
        self.assertEqual(r.LOAD_MANIFEST, {'bytes': 1952, 'sha256':
                         '58d1fd2e90db76e74024126f38f05cd0640abfdfad63f020a0c471d964942a98'})
        self.assertEqual(r.LOAD_PLAN_SCHEMA, 'opt50_loadonly_plan_fd96539d_v2')

    def test_v3_requires_its_own_frozen_source_pin(self):
        profile = r.PROFILES['/var/lib/opt50-loadonly-fd96539d-v3']
        self.assertIsNotNone(profile['manifest'], 'v3 must be independently frozen before publishing B24 v2')
        result = self.selection('v3')
        self.assertEqual(result['unit'], 'opt50-loadonly-v3-'+TOKEN)
        self.assertEqual(r.LOAD_MANIFEST, profile['manifest'])
        self.assertNotEqual(r.LOAD_MANIFEST, r.PROFILES['/var/lib/opt50-loadonly-fd96539d-v2']['manifest'])
        self.assertEqual(r.LOAD_TOOLS_SCHEMA, 'opt50_loadonly_tools_fd96539d_v3')
        self.assertEqual(r.LOAD_STATUS_SCHEMA, 'opt50_loadonly_status_fd96539d_v3')

    def test_unknown_namespace_rejected_before_reading_any_source(self):
        with patch.object(r, 'Path', PurePosixPath), patch.object(r, 'root_linux'), \
                patch.object(r, 'read_json', side_effect=AssertionError('unknown source must not be read')):
            for name in ('/var/lib/opt50-loadonly-fd96539d-v4', '/var/lib/opt50-loadonly-fd96539d-v3-extra',
                         '/tmp/opt50-loadonly-fd96539d-v3'):
                with self.subTest(namespace=name), self.assertRaises(ValueError):
                    r.configure(PurePosixPath(name+'/plan_'+TOKEN+'/plan.json'))

    def test_v2_namespace_cannot_claim_v3_schema_or_service(self):
        for edit in ({'schema': 'opt50_loadonly_plan_fd96539d_v3'},
                     {'unit': 'opt50-loadonly-v3-'+TOKEN}):
            with self.subTest(edit=edit), self.assertRaisesRegex(ValueError, 'schema/token/unit/namespace'):
                self.selection('v2', edit=edit)

    def test_any_manifest_content_mismatch_is_rejected_before_helper_execution(self):
        self.selection('v2')
        with patch.object(r, 'private_dir'), patch.object(r, 'read_file', return_value=b'{"files":{}}'), \
                patch.object(r, 'compiled_module', side_effect=AssertionError('untrusted helper must not execute')):
            with self.assertRaisesRegex(ValueError, 'manifest differs'):
                r._guardian_bundle()

    def test_unfrozen_profile_does_not_fall_back_to_v2_identity(self):
        profiles = copy.deepcopy(r.PROFILES)
        profiles['/var/lib/opt50-loadonly-fd96539d-v3']['manifest'] = None
        with patch.object(r, 'PROFILES', profiles), patch.object(r, 'Path', PurePosixPath), \
                patch.object(r, 'root_linux'), patch.object(r, 'read_json', side_effect=AssertionError('not frozen')):
            with self.assertRaisesRegex(ValueError, 'not a frozen release'):
                r.configure(PurePosixPath('/var/lib/opt50-loadonly-fd96539d-v3/plan_'+TOKEN+'/plan.json'))


if __name__ == '__main__':
    unittest.main()
