"""Device-free receipt reader tests; Windows fixtures simulate root ownership.

Only file fixtures are touched. No model decoder, runtime, owner or device is
loaded. Ownership/mode rejection is separately tested with explicit metadata.
"""
import builtins
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('saved_receipt_test', ROOT/'status_receipt.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def write_json(path, value):
    path.write_text(json.dumps(value, separators=(',', ':')), encoding='utf-8')
    path.chmod(0o600)


class SavedReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = ROOT/'.test_build'
        temporary.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='status_receipt_', dir=temporary)
        self.addCleanup(self.temp.cleanup)
        self.records = Path(self.temp.name)/'records'
        self.root = self.records/'plan_0123456789ab'
        self.tools = self.root/'tools'
        for folder in (self.records, self.root, self.tools, self.root/'model', self.root/'dataset'):
            folder.mkdir(mode=0o700, exist_ok=True)
        self.path = self.root/'plan.json'
        (self.tools/'run_model.py').write_bytes((ROOT/'run_model.py').read_bytes())
        (self.tools/'status_receipt.py').write_bytes((ROOT/'status_receipt.py').read_bytes())
        for file in self.tools.iterdir():
            file.chmod(0o600)
        names = {'run_model.py', 'status_receipt.py', 'model_runtime.py', 'model_worker.py',
                 'support_py/resident.py', 'support_py/durable.py', 'support_py/model_proof.py',
                 'model/manifest.json', 'build_arm.py', 'dataset.py', 'accuracy_metrics.py'}
        self.manifest = {'schema': 'opt50_b24_batch_tools_v1', 'hardware_executed': False,
                         'source_model_sha256': r.SOURCE_MODEL,
                         'files': {name: {'bytes': 0, 'sha256': 'a'*64} for name in names}}
        for name in ('run_model.py', 'status_receipt.py'):
            self.manifest['files'][name] = r.identity((self.tools/name).read_bytes())
        self.manifest['files']['model/manifest.json']['sha256'] = r.MODEL_MANIFEST
        self.plan = {'schema': r.PLAN_SCHEMA, 'token': '0123456789ab',
                     'unit': 'opt50-b24-model-0123456789ab', 'plan_path': str(self.path),
                     'scope': r.SCOPE, 'measurement_profile': r.PROFILE, 'expires_seconds': 900,
                     'expected_PL_starts': 36, 'expected_Dense_calls': 12, 'expected_Sparse_calls': 24,
                     'run_id': 1, 'source_model_sha256': r.SOURCE_MODEL, 'model_manifest_sha256': r.MODEL_MANIFEST,
                     'old_board_lock_transferred': False, 'uart_logging_operator_confirmed': True,
                     'model_root': str(self.root/'model'), 'dataset_root': str(self.root/'dataset'),
                     'execution': str(self.root/'execution'), 'socket': str(self.root/'execution/runtime.sock'),
                     'plugin': str(self.root/'plugin.so'),
                     'python': '/home/ubuntu/fpga_project/model_b/.venv/bin/python',
                     'boot_id': '2b0eaebd-2dd9-45c5-b135-26315bfdbb7f', 'sample_start': 15,
                     'sample_count': 5, 'total_START_budget': 180, 'guardian_pid': 15656,
                     'dataset_manifest': {'bytes': 807589, 'sha256': 'b'*64}}
        self.save_manifest()
        self.state = {'schema': 'opt50_b24_acceptance_batch_status_v1', 'boot_id': self.plan['boot_id'],
                      'plan_token': self.plan['token'], 'unit': self.plan['unit'], 'guardian_pid': 15656,
                      'measurement_profile': r.PROFILE, 'sample_start': 15, 'sample_count': 5,
                      'total_START_budget': 180, 'stage': 'IMAGE_RUNNING', 'committed_samples': 2,
                      'completed_PL_calls': 72, 'current_ordinal': 17, 'current_id': 'val_000017',
                      'image_stage': 'MODEL_WORKER_RUNNING', 'owner_pid': 200, 'child_pid': 201}
        self.save_state()
        self.addCleanup(patch.stopall)
        patch.object(r, 'RECORDS', self.records).start()
        if os.name == 'nt':
            # Windows has no POSIX private mode/owner bits. Keep ordinary-file,
            # no-link, bounded-read, read stability and single-link checks real.
            def portable(info, directory=False):
                r.need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
                       (directory or info.st_nlink == 1), 'fixture file type/link differs')
            patch.object(r, 'private_metadata', side_effect=portable).start()

    def save_manifest(self):
        write_json(self.tools/'tools_manifest.json', self.manifest)
        self.plan['tool_files'] = {'manifest': r.identity((self.tools/'tools_manifest.json').read_bytes()),
                                   'files': copy.deepcopy(self.manifest['files'])}
        write_json(self.path, self.plan)

    def save_state(self):
        write_json(self.root/'batch_status.json', self.state)

    def read(self, summary=False):
        return r.read_saved(self.path, summary, folder=self.tools)

    def test_running_receipt_is_saved_only_and_not_closed(self):
        result = self.read(True)
        self.assertEqual(result['stage'], 'IMAGE_RUNNING')
        self.assertFalse(result['closed'])
        self.assertTrue(result['saved_status_only'])
        self.assertEqual(result['committed_samples'], 2)
        self.assertEqual(result['completed_PL_calls'], 72)
        self.assertEqual(result['ORT_intra_threads'], 4)
        self.assertEqual(result['ORT_inter_threads'], 1)
        self.assertIsNone(result['accuracy'])

    def test_missing_status_preserves_old_shape(self):
        (self.root/'batch_status.json').unlink()
        raw = self.read()
        self.assertEqual(raw, {'stage': 'NO_SAVED_STATUS_YET_SEE_SYSTEMD_JOURNAL',
                               'saved_status_only': True, 'plan_boot_id': self.plan['boot_id'],
                               'warning': r.WARNING})
        summary = self.read(True)
        self.assertFalse(summary['closed'])
        self.assertEqual(summary['committed_samples'], 0)
        self.assertIsNone(summary['accuracy'])

    def test_raw_shape_and_input_file_are_preserved(self):
        before = (self.root/'batch_status.json').read_bytes()
        result = self.read()
        self.assertEqual(result['owner_pid'], 200)
        self.assertEqual(result['plan_boot_id'], self.plan['boot_id'])
        self.assertEqual(before, (self.root/'batch_status.json').read_bytes())

    def test_foreign_state_binding_refused(self):
        for key, wrong in (('boot_id', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
                           ('plan_token', 'fedcba987654'), ('unit', 'some-other.service'),
                           ('measurement_profile', 'other-profile'), ('guardian_pid', 1)):
            with self.subTest(field=key):
                state = copy.deepcopy(self.state)
                self.state[key] = wrong
                self.save_state()
                with self.assertRaises(ValueError):
                    self.read()
                self.state = state
        self.save_state()

    def test_unknown_namespace_and_aliased_folder_refused(self):
        for path, folder in ((self.records/'plan_0123456789ab'/'other.json', self.tools),
                             (self.records/'plan_SHORT'/'plan.json', self.tools),
                             (self.path, ROOT), (Path('plan.json'), self.tools)):
            with self.subTest(path=str(path), folder=str(folder)), self.assertRaises(ValueError):
                r.read_saved(path, folder=folder)

    def test_damaged_coordinator_and_reader_sources_refused(self):
        for name in ('run_model.py', 'status_receipt.py'):
            path = self.tools/name
            raw = path.read_bytes()
            path.write_bytes(raw+b'\n# damaged\n')
            with self.subTest(source=name), self.assertRaises(ValueError):
                self.read()
            path.write_bytes(raw)

    def test_changed_manifest_pin_and_unsafe_inventory_refused(self):
        raw = (self.tools/'tools_manifest.json').read_bytes()
        (self.tools/'tools_manifest.json').write_bytes(raw+b'\n')
        with self.assertRaises(ValueError):
            self.read()
        self.manifest['files']['../escape'] = {'bytes': 0, 'sha256': 'c'*64}
        self.save_manifest()
        with self.assertRaises(ValueError):
            self.read()

    def test_unsafe_plan_scope_or_counter_types_refused(self):
        for key, wrong in (('scope', 'RUN_ANYTHING'), ('sample_count', True),
                           ('expected_PL_starts', True), ('total_START_budget', 181),
                           ('model_manifest_sha256', 'c'*64)):
            before = copy.deepcopy(self.plan)
            self.plan[key] = wrong
            write_json(self.path, self.plan)
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.read()
            self.plan = before
        write_json(self.path, self.plan)

    def test_saved_pass_requires_complete_closed_bound_receipt(self):
        self.state.update(stage=r.PASS, committed_samples=5, completed_PL_calls=180,
                          owner_pid=None, child_pid=None)
        self.save_state()
        with self.assertRaises(ValueError):
            self.read()
        self.state['completion'] = {
            'schema': 'opt50_b24_batch_completion_v1', 'stage': r.PASS,
            'plan_token': self.plan['token'], 'sample_start': 15, 'sample_count': 5,
            'total_START_budget': 180, 'committed_samples': 5, 'completed_PL_calls': 180,
            'compared_logits': 5000, 'Dense_calls': 60, 'Sparse_calls': 120,
            'dataset_manifest': self.plan['dataset_manifest'], 'closed': True,
            'owner_pid': None, 'child_pid': None,
            'accuracy': {'numerical_status': 'NUMERICAL_PASS', 'images': 5, 'bitwise_mismatches': 0}}
        self.save_state()
        result = self.read(True)
        self.assertEqual(result['stage'], r.PASS)
        self.assertTrue(result['closed'])
        self.assertEqual(result['accuracy']['bitwise_mismatches'], 0)
        self.state['completion']['plan_token'] = 'ffffffffffff'
        self.save_state()
        with self.assertRaises(ValueError):
            self.read(True)

    def test_nonfinite_and_duplicate_json_fields_refused(self):
        for raw in ('{"stage":"A","stage":"B"}', '{"value":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                r.decode(raw)

    def test_null_completion_and_boolean_counters_refused(self):
        self.state['completion'] = None
        self.save_state()
        with self.assertRaises(ValueError):
            self.read(True)
        self.state.pop('completion')
        self.state['committed_samples'] = True
        self.state['completed_PL_calls'] = 36
        self.save_state()
        with self.assertRaises(ValueError):
            self.read(True)

    def test_symlink_path_and_multi_link_file_refused(self):
        target = self.tools/'run_model.py'
        original = Path.lstat
        def linked(path):
            if path == target:
                return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0)
            return original(path)
        with patch.object(Path, 'lstat', linked), self.assertRaises(ValueError):
            self.read()
        def multiply_linked(path):
            if path == target:
                return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=0,
                                             st_uid=0, st_nlink=2)
            return original(path)
        with patch.object(Path, 'lstat', multiply_linked), self.assertRaises(ValueError):
            self.read()

    def test_reader_does_not_import_runtime_or_open_write_descriptors(self):
        before = {path.relative_to(self.root).as_posix(): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        original_import, original_open = builtins.__import__, os.open
        def guarded_import(name, *args, **kwargs):
            if name.split('.')[0] in ('model_runtime', 'run_model', 'numpy', 'onnxruntime'):
                raise AssertionError('saved status imported runtime')
            return original_import(name, *args, **kwargs)
        def guarded_open(path, flags, *args, **kwargs):
            self.assertEqual(flags & getattr(os, 'O_ACCMODE', 3), os.O_RDONLY)
            return original_open(path, flags, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded_import), patch.object(os, 'open', side_effect=guarded_open):
            result = self.read(True)
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(result['saved_status_only'])

    def coordinator(self):
        spec = importlib.util.spec_from_file_location('receipt_coordinator_integration', ROOT/'run_model.py')
        coordinator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(coordinator)
        return coordinator

    def bootstrap_read(self, path, private=False, limit=32*1024*1024):
        self.assertTrue(private)
        self.assertIn(Path(path), (self.tools/'tools_manifest.json', self.tools/'status_receipt.py'))
        return r.read_file(path, limit=limit)

    def test_coordinator_status_routes_through_pinned_reader_without_api(self):
        coordinator = self.coordinator()
        original_checked = coordinator.module_checked
        loaded = {}
        def checked(name, tools, folder, private=False):
            self.assertEqual(name, 'status_receipt.py')
            self.assertEqual(folder, self.tools)
            self.assertTrue(private)
            reader = original_checked(name, tools, folder, private)
            reader.RECORDS = self.records  # Isolated namespace, no /var/lib files.
            if os.name == 'nt':
                reader.private_metadata = r.private_metadata
            reader.read_saved = Mock(wraps=reader.read_saved)
            loaded['reader'] = reader
            return reader
        expected = self.read(True)
        with patch.object(coordinator, 'HERE', self.tools), \
             patch.object(coordinator, 'api', side_effect=AssertionError('status expanded full api')), \
             patch.object(coordinator, 'load_plan', side_effect=AssertionError('status expanded full plan')), \
             patch.object(coordinator, 'read', side_effect=self.bootstrap_read) as reads, \
             patch.object(coordinator, 'module_checked', side_effect=checked) as modules:
            actual = coordinator.status(self.path, summary=True)
        self.assertEqual(actual, expected)
        self.assertEqual(reads.call_count, 2)
        modules.assert_called_once()
        loaded['reader'].read_saved.assert_called_once_with(self.path, summary=True, folder=self.tools)

    def test_coordinator_rejects_damaged_reader_before_execution_without_api(self):
        coordinator = self.coordinator()
        path = self.tools/'status_receipt.py'
        path.write_bytes(path.read_bytes()+b'\nraise AssertionError("damaged reader executed")\n')
        with patch.object(coordinator, 'HERE', self.tools), \
             patch.object(coordinator, 'api', side_effect=AssertionError('status expanded full api')), \
             patch.object(coordinator, 'load_plan', side_effect=AssertionError('status expanded full plan')), \
             patch.object(coordinator, 'read', side_effect=self.bootstrap_read), \
             patch.object(coordinator, 'module_checked', wraps=coordinator.module_checked) as modules:
            with self.assertRaisesRegex(ValueError, 'source changed before module import'):
                coordinator.status(self.path, summary=True)
        modules.assert_called_once()


class PrivatePolicyTests(unittest.TestCase):
    def test_root_private_metadata_policy(self):
        def info(mode, uid=0, links=1):
            return types.SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links)
        r.private_metadata(info(stat.S_IFREG | 0o600))
        r.private_metadata(info(stat.S_IFDIR | 0o700), directory=True)
        for entry, directory in ((info(stat.S_IFREG | 0o644), False),
                                 (info(stat.S_IFREG | 0o600, uid=1000), False),
                                 (info(stat.S_IFREG | 0o600, links=2), False),
                                 (info(stat.S_IFDIR | 0o755), True),
                                 (info(stat.S_IFLNK | 0o600), False)):
            with self.subTest(mode=entry.st_mode, uid=entry.st_uid, links=entry.st_nlink), self.assertRaises(ValueError):
                r.private_metadata(entry, directory=directory)


if __name__ == '__main__':
    unittest.main()
