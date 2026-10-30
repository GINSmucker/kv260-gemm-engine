"""Recompute actual saved logits and reject incomplete/repeated acceptance."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
import accuracy_metrics as m
import review_results as r
import numpy as np


def sample(i):
    return {'ordinal': i, 'id': f'val_{i:07d}', 'label': i % 1000,
            'image': {'sha256': f'{i+1:064x}'}, 'input': {'sha256': f'{i+10000:064x}'}}


def record(i, compared=None):
    s = sample(i)
    if compared is None:
        values = np.zeros((1, 1000), dtype=np.float32)
        compared = m.compare(values, values, s['label'])
    return {'ordinal': i, 'id': s['id'], 'label': s['label'], 'image_sha256': s['image']['sha256'],
            'input_sha256': s['input']['sha256'], **{k: compared[k] for k in ('CPU_logits_sha256', 'PL_logits_sha256',
            'CPU_top5', 'PL_top5', 'bitwise_mismatches', 'max_abs_difference')},
            'CPU_reference_ms': 300, 'inference_ms': 140, 'completed_PL_calls': 36, 'Dense_calls': 12,
            'Sparse_calls': 24, 'owner_exit_code': 0, 'device_closed': True,
            'postrun_buffer_checks_verified': True, 'native_END_verified': True}


class ReviewTests(unittest.TestCase):
    def test_subsets_only_progress_never_full_acceptance(self):
        rows = [record(i) for i in range(4)]; selected = [sample(i) for i in range(1800)]
        with self.assertRaises(ValueError):
            r.merge_records([rows], selected)
        report = r.merge_records([rows[2:], rows[:2]], selected, progress=True)
        self.assertEqual(report['status'], 'PARTIAL_PROGRESS_ONLY')
        self.assertEqual(report['missing_images'], 1796)
        self.assertFalse(report['accuracy_verified'])

    def test_missing_or_duplicate_image_not_silently_skipped(self):
        selected = [sample(i) for i in range(1800)]
        for rows in ([record(0), record(0)], [record(0), record(2)]):
            with self.assertRaises(ValueError):
                r.merge_records([rows], selected, progress=True)

    def test_actual_npy_recomputed_and_tampered_non_top5_element_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            folder = root/'samples/000000'; folder.mkdir(parents=True)
            cpu = np.arange(1000, dtype=np.float32)[None]
            np.save(folder/'cpu_reference.npy', cpu, allow_pickle=False)
            np.save(folder/'model_logits.npy', cpu, allow_pickle=False)
            original = record(0, m.compare(cpu, cpu, 0))
            records = r.recompute_records(root, [original])
            self.assertEqual(records[0]['CPU_true_label_rank'], 1000)
            pl = cpu.copy(); pl[0, 0] = np.float32(.5)
            np.save(folder/'model_logits.npy', pl, allow_pickle=False)
            with self.assertRaisesRegex(ValueError, 'differs from saved logits'):
                r.recompute_records(root, [original])

    def test_full_1800_exactly_once_and_zero_numerical_errors(self):
        selected = [sample(i) for i in range(1800)]
        values = np.zeros((1, 1000), dtype=np.float32)
        common = m.compare(values, values, 0)
        rows = [record(i, common) for i in range(1800)]
        report = r.merge_records([rows[:5], rows[5:]], selected)
        self.assertTrue(report['full_1800_acceptance'])
        self.assertTrue(report['accuracy_verified'])
        self.assertEqual(report['completed_PL_calls'], 64800)
        bad = copy.deepcopy(rows)
        bad[100]['device_closed'] = False
        with self.assertRaises(ValueError):
            r.merge_records([bad], selected)

    def test_five_percentage_points_requires_matched_full_cpu4(self):
        result = {'full_1800_acceptance': True, 'accuracy': {'PL': {'top1_pct': 64, 'top5_pct': 87}}}
        baseline = {'full_1800_complete': True, 'accuracy': {'top1_pct': 66, 'top5_pct': 90}}
        self.assertTrue(r.compare_dense(result, baseline)['within_5pp_both_top1_and_top5'])
        baseline['accuracy']['top5_pct'] = 93
        self.assertFalse(r.compare_dense(result, baseline)['within_5pp_both_top1_and_top5'])
        baseline['full_1800_complete'] = False
        with self.assertRaises(ValueError):
            r.compare_dense(result, baseline)


if __name__ == '__main__':
    unittest.main()
