"""Device-free compatibility tests using the actual released v1 source bytes.

The virtual reader substitutes only the Linux private filesystem. No owner,
CPU inference, device, systemd call, or START is executed by these tests.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT.parent/'b24_batch_acceptance_v1'
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    out = importlib.util.module_from_spec(spec); spec.loader.exec_module(out)
    return out


compat = module('compat_tested', ROOT/'release_compat.py')
entry = module('compat_current_entry', ROOT/'run_model.py')
review = module('compat_review', ROOT/'review_results.py')
dense = module('compat_dense', ROOT/'cpu_dense_baseline.py')


class VirtualPrivate:
    """Strict path/pin calls remain real; only POSIX metadata/storage is fake."""
    def __init__(self, source_files, manifest_raw, token='0123456789ab'):
        self.root = compat.RECORDS.absolute()/('plan_'+token)
        self.path = self.root/'plan.json'
        self.files = {self.root/'tools/tools_manifest.json': manifest_raw}
        self.reads = []
        self.dirs = {compat.RECORDS.absolute(), self.root, self.root/'tools', self.root/'model'}
        for name, raw in source_files.items():
            parts = compat.relative(name)
            path = self.root.joinpath(*parts) if parts[0] == 'model' else self.root/'tools'/Path(*parts)
            self.files[path] = raw
            for parent in path.parents:
                if parent == self.root: break
                self.dirs.add(parent)
    def read_file(self, path, *, private=False, limit=64*1024*1024):
        if not private: raise AssertionError('private read disabled')
        path = Path(path); self.reads.append(path)
        raw = self.files[path]
        if len(raw) > limit: raise ValueError('fixture exceeds bound')
        return raw
    def read_json(self, path, *, private=False):
        return json.loads(self.read_file(path, private=private))
    def private_dir(self, path):
        if Path(path) not in self.dirs: raise ValueError('nonprivate fixture directory')
        return Path(path)


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_manifest_raw = (OLD/'tools_manifest.json').read_bytes()
        cls.old_manifest = json.loads(cls.old_manifest_raw)
        cls.old_files = {name: (OLD/name).read_bytes() for name in cls.old_manifest['files']}
        cls.current_tools = {'manifest': {'bytes': 7, 'sha256': 'a'*64}, 'files': {}}

    def fixture(self):
        return VirtualPrivate(self.old_files, self.old_manifest_raw)

    def test_actual_predecessor_pin_and_all119_source_files_audited_before_dispatch(self):
        self.assertEqual(compat.identity(self.old_manifest_raw), compat.PREDECESSOR)
        f = self.fixture()
        old, root, info = compat.load_closed_entry(f.path, self.current_tools, access=f)
        self.assertEqual(info['release'], 'predecessor_v1')
        self.assertEqual(root, f.root)
        self.assertEqual(old.HERE, f.root/'tools')
        self.assertEqual(len(self.old_files), 119)
        self.assertEqual(set(f.reads), set(f.files))
        self.assertEqual(old.read_completed_records.__code__.co_filename, str(f.root/'tools/run_model.py'))
        # The old implementation and profile are used, never a new verifier
        # monkeypatched into a differently pinned old tool inventory.
        self.assertEqual(old.PROFILE, entry.PROFILE)
        self.assertIsNot(old.read_completed_records, entry.read_completed_records)

    def test_current_source_is_also_fully_audited(self):
        f = self.fixture()
        current = {'manifest': compat.identity(self.old_manifest_raw), 'files': self.old_manifest['files']}
        _, _, info = compat.load_closed_entry(f.path, current, access=f)
        self.assertEqual(info['release'], 'current')
        self.assertEqual(set(f.reads), set(f.files))

    def test_unknown_self_consistent_manifest_rejected_before_any_source_exec(self):
        f = self.fixture()
        value = copy.deepcopy(self.old_manifest); value['unknown_release'] = True
        f.files[f.root/'tools/tools_manifest.json'] = json.dumps(value).encode()
        with self.assertRaisesRegex(ValueError, 'unknown coordinator release'):
            compat.load_closed_entry(f.path, self.current_tools, access=f)
        self.assertEqual(f.reads, [f.root/'tools/tools_manifest.json'])

    def test_changed_predecessor_code_rejected_before_exec(self):
        f = self.fixture()
        f.files[f.root/'tools/run_model.py'] += b'\nraise AssertionError("untrusted code ran")\n'
        with self.assertRaisesRegex(ValueError, 'root-copied release changed: run_model.py'):
            compat.load_closed_entry(f.path, self.current_tools, access=f)

    def test_changed_model_and_nonruntime_source_each_rejected(self):
        for name in ('model/reference.onnx', 'source/kernel.cpp', 'README.md'):
            with self.subTest(name=name):
                f = self.fixture()
                path = f.root/name if name.startswith('model/') else f.root/'tools'/name
                f.files[path] += b'changed'
                with self.assertRaisesRegex(ValueError, 'root-copied release changed'):
                    compat.load_closed_entry(f.path, self.current_tools, access=f)

    def test_predecessor_root_private_directory_contract_not_skipped(self):
        f = self.fixture(); f.dirs.remove(f.root/'model/inputs')
        with self.assertRaisesRegex(ValueError, 'nonprivate'):
            compat.load_closed_entry(f.path, self.current_tools, access=f)

    def test_canonical_namespace_and_no_traversal(self):
        f = self.fixture()
        for path in (f.root/'other.json', f.root/'../plan_0123456789ab/plan.json',
                     f.root.parent/'plan_ABCDEF012345/plan.json', Path('/tmp/plan_0123456789ab/plan.json')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                compat.load_closed_entry(path, self.current_tools, access=f)

    def test_old_readonly_entry_rejects_unclosed_state_even_if_status_says_pass(self):
        f = self.fixture(); old, _, _ = compat.load_closed_entry(f.path, self.current_tools, access=f)
        done = {'stage': old.PASS, 'closed': False}
        state = {'stage': old.PASS, 'owner_pid': None, 'child_pid': None, 'completion': done}
        fake = types.SimpleNamespace(c=types.SimpleNamespace(read_json=lambda path, **_: state if Path(path).name == 'batch_status.json' else done))
        with patch.object(old, 'api', return_value=fake), patch.object(old, 'load_plan', return_value={}), \
             patch.object(old, 'committed_records') as records:
            with self.assertRaisesRegex(ValueError, 'not normally closed'):
                old.read_completed_records(f.path, allow_partial=True)
            records.assert_not_called()

    def test_old_readonly_entry_still_rechecks_native_counters_and_accuracy(self):
        f = self.fixture(); old, _, _ = compat.load_closed_entry(f.path, self.current_tools, access=f)
        done = {'stage': old.PASS, 'closed': True, 'committed_samples': 1, 'completed_PL_calls': 35,
                'compared_logits': 1000, 'accuracy': {'bitwise_mismatches': 0}}
        state = {'stage': old.PASS, 'owner_pid': None, 'child_pid': None, 'completion': done}
        fake = types.SimpleNamespace(c=types.SimpleNamespace(read_json=lambda path, **_: state if Path(path).name == 'batch_status.json' else done))
        with patch.object(old, 'api', return_value=fake), patch.object(old, 'load_plan', return_value={}), \
             patch.object(old, 'committed_records', return_value=[{'ordinal': 0}]) as records:
            with self.assertRaisesRegex(ValueError, 'completion counters/metrics'):
                old.read_completed_records(f.path, allow_partial=True)
            records.assert_called_once_with(fake, {}, f.root, complete=False)

    def test_historical_dispatch_keeps_closed_prefix_verification_and_plan_identity(self):
        path = compat.RECORDS.absolute()/'plan_0123456789ab/plan.json'
        root = path.parent
        plan = {'tool_files': {'manifest': compat.PREDECESSOR}}
        old = types.SimpleNamespace(read_completed_records=Mock(return_value=[{'ordinal': 0}]))
        a = types.SimpleNamespace(tools=self.current_tools,
            compat=types.SimpleNamespace(load_closed_entry=Mock(return_value=(old, root, {'tools_manifest': compat.PREDECESSOR}))),
            c=types.SimpleNamespace(read_json=lambda *_, **__: plan))
        result = entry.historical_records(a, path)
        self.assertEqual(result[1], [{'ordinal': 0}])
        old.read_completed_records.assert_called_once_with(path, allow_partial=True)
        old.read_completed_records.reset_mock()
        entry.historical_records(a, path, complete=True)
        old.read_completed_records.assert_called_once_with(path, allow_partial=False)
        plan['tool_files']['manifest'] = {'bytes': 1, 'sha256': 'f'*64}
        with self.assertRaisesRegex(ValueError, 'plan/release identity'):
            entry.historical_records(a, path)

    def test_v1_commits_block_replay_even_with_repacked_dataset_and_allow_next_unseen_images(self):
        path = compat.RECORDS.absolute()/'plan_0123456789ab/plan.json'
        prior = {'schema': entry.PLAN_SCHEMA, 'dataset_manifest': {'sha256': 'b'*64}}
        a = types.SimpleNamespace(c=types.SimpleNamespace(read_json=lambda *_, **__: prior))
        rows = [{'ordinal': n} for n in range(15)]
        with patch.object(entry, 'historical_records', return_value=(prior, rows, path.parent, {})) as dispatch:
            with self.assertRaisesRegex(ValueError, 'do not replay'):
                entry.prevent_replay(a, [{'plan': str(path)}], {'sha256': 'different_transport'}, 10, 5)
            entry.prevent_replay(a, [{'plan': str(path)}], {'sha256': 'different_transport'}, 15, 5)
            self.assertEqual(dispatch.call_count, 2)

    def test_unknown_release_is_not_silently_skipped_during_replay_check(self):
        path = compat.RECORDS.absolute()/'plan_0123456789ab/plan.json'
        a = types.SimpleNamespace(c=types.SimpleNamespace(read_json=lambda *_, **__: {'schema': entry.PLAN_SCHEMA}))
        with patch.object(entry, 'historical_records', side_effect=ValueError('unknown coordinator release')):
            with self.assertRaisesRegex(ValueError, 'unknown coordinator release'):
                entry.prevent_replay(a, [{'plan': str(path)}], {}, 15, 5)

    def test_v1_pilot_must_match_current_boot_dataset_and_bitwise_closed_proof(self):
        path = compat.RECORDS.absolute()/'plan_0123456789ab/plan.json'
        pin = {'bytes': 55, 'sha256': 'b'*64}
        plan = {'schema': entry.PLAN_SCHEMA, 'measurement_profile': entry.PROFILE, 'sample_count': 5,
                'dataset_manifest': pin, 'boot_id': 'current-boot'}
        rows = [{'ordinal': i, 'bitwise_mismatches': 0} for i in range(5)]
        done = {'stage': entry.PASS, 'closed': True, 'committed_samples': 5}
        a = types.SimpleNamespace(c=types.SimpleNamespace(read_json=lambda *_, **__: done))
        with patch.object(entry, 'RECORDS', compat.RECORDS.absolute()), \
             patch.object(entry, 'historical_records', return_value=(plan, rows, path.parent, {'release': 'predecessor_v1'})) as dispatch, \
             patch.object(entry, 'tree_usage', return_value={'allocated_bytes': 100}):
            self.assertEqual(entry.review_pilot(a, path, {'boot_id': 'current-boot'}, pin, 15, 10)['count'], 5)
            dispatch.assert_called_once_with(a, path, complete=True)
            with self.assertRaisesRegex(ValueError, 'boot differs'):
                entry.review_pilot(a, path, {'boot_id': 'new-boot'}, pin, 15, 10)
            with self.assertRaisesRegex(ValueError, 'dataset/boot differs'):
                entry.review_pilot(a, path, {'boot_id': 'current-boot'}, {'bytes': 2}, 15, 10)
            rows[0]['bitwise_mismatches'] = 1
            with self.assertRaisesRegex(ValueError, 'not bitwise exact'):
                entry.review_pilot(a, path, {'boot_id': 'current-boot'}, pin, 15, 10)
            rows[0]['bitwise_mismatches'] = 0; done['closed'] = False
            with self.assertRaisesRegex(ValueError, 'not completed safely'):
                entry.review_pilot(a, path, {'boot_id': 'current-boot'}, pin, 15, 10)

    def test_reviewer_uses_same_pinned_adapter(self):
        path = compat.RECORDS.absolute()/'plan_0123456789ab/plan.json'
        raw = self.old_manifest_raw
        with patch.object(review.dataset, 'read', return_value=raw), \
             patch.object(review.release_compat, 'load_closed_entry', return_value=('ENTRY', path.parent, {})) as call:
            self.assertEqual(review.load_closed_entry(path, self.old_manifest), ('ENTRY', path.parent))
            call.assert_called_once_with(path, {'manifest': compat.identity(raw), 'files': self.old_manifest['files']})

    def test_dense_old_result_requires_same_numerical_files_and_unchanged_run_threads(self):
        files = {name: compat.identity((ROOT/name).read_bytes()) for name in self.old_manifest['files']}
        tools = {'manifest': self.current_tools['manifest'], 'files': files}
        self.assertEqual(compat.dense_source(compat.PREDECESSOR, tools, folder=ROOT), 'predecessor_v1')
        bad = copy.deepcopy(tools); bad['files']['source/quant_neon.h']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'numerical model/source/preprocessing'):
            compat.dense_source(compat.PREDECESSOR, bad, folder=ROOT)

    def test_dense_current_result_and_unknown_source(self):
        self.assertEqual(compat.dense_source(self.current_tools['manifest'], self.current_tools), 'current')
        with self.assertRaisesRegex(ValueError, 'unknown coordinator release'):
            compat.dense_source({'bytes': 1, 'sha256': '0'*64}, self.current_tools)

    def test_dense_changed_ort_thread_contract_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent, prefix='.compat-test-') as name:
            folder = Path(name)
            (folder/'predecessor_tools_manifest.json').write_bytes(self.old_manifest_raw)
            (folder/'cpu_dense_baseline.py').write_bytes((ROOT/'cpu_dense_baseline.py').read_bytes())
            raw = (ROOT/'model_runtime.py').read_bytes()
            changed = raw.replace(b'options.intra_op_num_threads, options.inter_op_num_threads = 4, 1',
                                  b'options.intra_op_num_threads, options.inter_op_num_threads = 2, 1')
            self.assertTrue(changed != raw, 'thread fixture must actually change source')
            (folder/'model_runtime.py').write_bytes(changed)
            files = dict(self.old_manifest['files'])
            files['cpu_dense_baseline.py'] = compat.identity((folder/'cpu_dense_baseline.py').read_bytes())
            files['model_runtime.py'] = compat.identity(changed)
            with self.assertRaisesRegex(ValueError, 'inference/thread contract differs'):
                compat.dense_source(compat.PREDECESSOR, {'manifest': self.current_tools['manifest'], 'files': files}, folder=folder)

    def test_dense_current_baseline_reader_still_requires_every1800_logits(self):
        # This small fixture stops at the original full-dataset requirement,
        # before any output summary can be accepted or inference can run.
        fake = types.SimpleNamespace(dataset=types.SimpleNamespace(ordinary=lambda p: p))
        with patch.object(dense, 'verification_apis', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'full ordered1800'):
                dense.read_baseline('/unused', {'bytes': 1, 'sha256': 'a'*64}, [])


if __name__ == '__main__': unittest.main()
