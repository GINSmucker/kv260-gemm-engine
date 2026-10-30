"""Independent array and record tests; no B scripts or hardware execution."""
import builtins
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).absolute().parents[1]


def load(name='b24_accuracy_test_module'):
    spec = importlib.util.spec_from_file_location(name, ROOT/'accuracy_metrics.py')
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


m = load()


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def record(ordinal, label=0):
    return {'schema': m.RECORD_SCHEMA, 'ordinal': ordinal, 'id': 'sample_'+str(ordinal), 'label': label,
            'image_sha256': sha('encoded image '+str(ordinal)), 'input_sha256': sha('input NPY '+str(ordinal)),
            'CPU_logits_sha256': sha('raw output '+str(ordinal)), 'PL_logits_sha256': sha('raw output '+str(ordinal)),
            'CPU_top5': [0, 1, 2, 3, 4], 'PL_top5': [0, 1, 2, 3, 4],
            'bitwise_mismatches': 0, 'max_abs_difference': 0.0, 'CPU_reference_ms': 100.0,
            'inference_ms': 30.0, 'completed_PL_calls': 36, 'Dense_calls': 12, 'Sparse_calls': 24,
            'owner_exit_code': 0, 'device_closed': True, 'postrun_buffer_checks_verified': True,
            'native_END_verified': True, 'worker_runner_calls': 1, 'compared_logits': 1000}


def sample(row):
    return {'ordinal': row['ordinal'], 'id': row['id'], 'label': row['label'],
            'image': {'sha256': row['image_sha256']}, 'input': {'sha256': row['input_sha256']}}


class ArrayTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np

    def test_all_equal_ties_are_ordered_by_global_class_index(self):
        z = self.np.zeros((1, 1000), self.np.float32)
        for label in (0, 4, 5, 999):
            with self.subTest(label=label):
                result = m.compare(z, z.copy(), label)
                self.assertEqual(result['CPU_top5'], [0, 1, 2, 3, 4])
                self.assertEqual(result['CPU_true_label_rank'], label+1)
                self.assertEqual(result['CPU_top1_hit'], label == 0)
                self.assertEqual(result['CPU_top5_hit'], label < 5)
                self.assertEqual(result['status'], 'NUMERICAL_PASS')
                self.assertEqual(result['compared_logits'], 1000)
                self.assertEqual(result['CPU_logits_sha256'], hashlib.sha256(bytes(4000)).hexdigest())

    def test_rank_formula_matches_an_independent_lexicographic_python_sort(self):
        rng = self.np.random.default_rng(731)
        for label in (0, 1, 234, 999):
            z = rng.integers(-5, 6, size=(1, 1000)).astype(self.np.float32)
            order = sorted(range(1000), key=lambda i: (-float(z[0, i]), i))
            result = m.compare(z, z, label)
            self.assertEqual(result['CPU_top5'], order[:5])
            self.assertEqual(result['CPU_true_label_rank'], order.index(label)+1)

    def test_class_outside_observed_dataset_labels_still_competes(self):
        z = self.np.full((1, 1000), -2, self.np.float32)
        z[0, 999] = 10; z[0, 0] = 9
        result = m.compare(z, z, 0)
        self.assertEqual(result['CPU_top5'][0], 999)
        self.assertFalse(result['CPU_top1_hit'])
        self.assertTrue(result['CPU_top5_hit'])

    def test_non_top5_bit_difference_is_not_hidden_by_same_predictions(self):
        z = self.np.arange(1000, dtype=self.np.float32).reshape(1, 1000)
        other = z.copy(); other[0, 0] = self.np.float32(0.001)
        result = m.compare(z, other, 999)
        self.assertEqual(result['CPU_top5'], result['PL_top5'])
        self.assertEqual(result['bitwise_mismatches'], 1)
        self.assertEqual(result['status'], 'NUMERICAL_FAIL')
        self.assertEqual(result['first_differences'][0]['index'], 0)

    def test_signed_zero_has_equal_values_but_different_bits(self):
        z = self.np.zeros((1, 1000), self.np.float32)
        other = z.copy(); other[0, 500] = self.np.float32(-0.0)
        result = m.compare(z, other, 0)
        self.assertEqual(result['max_abs_difference'], 0)
        self.assertEqual(result['bitwise_mismatches'], 1)
        self.assertEqual(result['first_differences'][0]['CPU_bits'], '00000000')
        self.assertEqual(result['first_differences'][0]['PL_bits'], '80000000')

    def test_wrong_dtype_shape_nonfinite_and_label_are_rejected(self):
        good = self.np.zeros((1, 1000), self.np.float32)
        bad = [good.astype(self.np.float64), good.astype(self.np.int32), good.ravel(), good[:, :999],
               self.np.zeros((2, 1000), self.np.float32), [[0]*1000]]
        for value in (self.np.nan, self.np.inf, -self.np.inf):
            z = good.copy(); z[0, 777] = value; bad.append(z)
        for value in bad:
            with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                m.checked_logits(value)
        for label in (-1, 1000, True, 1.0, '0'):
            with self.subTest(label=label), self.assertRaises(ValueError):
                m.compare(good, good, label)

    def test_noncontiguous_fp32_input_is_copied_without_numeric_cast(self):
        source = self.np.arange(2000, dtype=self.np.float32).reshape(1, 2000)[:, ::2]
        self.assertFalse(source.flags.c_contiguous)
        result = m.checked_logits(source)
        self.assertTrue(result.flags.c_contiguous)
        self.assertEqual(result.tobytes(), source.tobytes())

    def test_first_differences_are_bounded_and_inputs_are_unchanged(self):
        z = self.np.zeros((1, 1000), self.np.float32)
        other = self.np.ones((1, 1000), self.np.float32)
        original = z.tobytes(), other.tobytes()
        result = m.compare(z, other, 0)
        self.assertEqual(result['bitwise_mismatches'], 1000)
        self.assertEqual(len(result['first_differences']), 8)
        self.assertEqual((z.tobytes(), other.tobytes()), original)
        json.dumps(result, allow_nan=False)


class RecordTests(unittest.TestCase):
    def test_frozen_nested_sample_binding_and_derived_hits(self):
        row = record(0, label=4)
        result = m.validate_record(row, sample(row))
        self.assertFalse(result['CPU_top1_hit']); self.assertTrue(result['CPU_top5_hit'])
        self.assertNotIn('CPU_top1_hit', row)  # Pure validation does not mutate the caller.

    def test_each_frozen_identity_field_is_bound(self):
        row = record(0)
        for field in ('ordinal', 'id', 'label', 'image', 'input'):
            expected = sample(row)
            if field == 'ordinal': expected[field] = 1
            elif field == 'id': expected[field] = 'another'
            elif field == 'label': expected[field] = 999
            else: expected[field]['sha256'] = 'f'*64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'frozen expected'):
                m.validate_record(row, expected)

    def test_missing_or_invalid_closed_proof_is_never_accepted(self):
        row = record(0)
        for field in m.REQUIRED_RECORD:
            missing = dict(row); missing.pop(field)
            with self.subTest(missing=field), self.assertRaisesRegex(ValueError, 'incomplete'):
                m.validate_record(missing)
        for field, value in (('owner_exit_code', 1), ('owner_exit_code', False), ('device_closed', False),
                             ('postrun_buffer_checks_verified', False), ('native_END_verified', 1),
                             ('completed_PL_calls', 35), ('Dense_calls', 24), ('Sparse_calls', 12),
                             ('worker_runner_calls', 2), ('compared_logits', 999)):
            bad = dict(row); bad[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                m.validate_record(bad)

    def test_sha_dtype_top5_time_and_rank_contradictions_are_rejected(self):
        row = record(0)
        mutations = [('image_sha256', 'not-sha'), ('label', True), ('CPU_top5', [0, 0, 1, 2, 3]),
                     ('PL_top5', [0, 1, 2, 3, 1000]), ('CPU_top5', (0, 1, 2, 3, 4)),
                     ('CPU_reference_ms', float('nan')), ('inference_ms', -1), ('inference_ms', True),
                     ('inference_ms', 10**1000),
                     ('bitwise_mismatches', 1001), ('bitwise_mismatches', False),
                     ('CPU_top1_hit', False), ('CPU_true_label_rank', 2),
                     ('PL_logits_sha256', 'f'*64), ('max_abs_difference', 0.001), ('status', 'NUMERICAL_FAIL')]
        for field, value in mutations:
            bad = copy.deepcopy(row); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.validate_record(bad)

    def test_closed_numerical_failure_remains_a_valid_record(self):
        row = record(0)
        row.update(PL_logits_sha256='f'*64, bitwise_mismatches=1, max_abs_difference=0.0,
                   status='NUMERICAL_FAIL')
        result = m.validate_record(row)
        self.assertEqual(result['bitwise_mismatches'], 1)

    def test_optional_hot_path_counters_do_not_gate_accuracy(self):
        row = record(0)
        row.update(hot_path_disk_events=7, graph_owner_sync_calls=2)
        self.assertEqual(m.validate_record(row)['hot_path_disk_events'], 7)


class AggregateTests(unittest.TestCase):
    def test_complete_bound_subset_is_numerically_passed_without_claiming_full_1800(self):
        rows = [record(i) for i in range(5, 8)]
        result = m.aggregate(rows, expected_count=3, expected_samples=[sample(r) for r in rows])
        self.assertEqual(result['status'], 'NUMERICAL_PASS')
        self.assertEqual(result['ordinal_start'], 5)
        self.assertEqual(result['ordinal_stop_exclusive'], 8)
        self.assertFalse(result['full_1800_acceptance'])
        self.assertEqual(result['completed_PL_calls'], 108)

    def test_full_1800_exact_bounds_are_required_for_full_acceptance(self):
        rows = [record(i, label=i%1000) for i in range(1800)]
        result = m.aggregate(rows, expected_samples=[sample(r) for r in rows])
        self.assertTrue(result['full_1800_acceptance'])
        self.assertEqual(result['compared_logits'], 1800000)
        self.assertEqual(result['completed_PL_calls'], 64800)
        self.assertEqual(result['Dense_calls'], 21600)
        self.assertEqual(result['Sparse_calls'], 43200)

    def test_unbound_records_never_get_acceptance_pass(self):
        result = m.aggregate([record(0)], expected_count=1)
        self.assertEqual(result['status'], 'IDENTITY_UNBOUND')
        self.assertEqual(result['numerical_status'], 'NUMERICAL_PASS')
        self.assertFalse(result['dataset_binding_verified'])
        self.assertFalse(result['full_1800_acceptance'])

    def test_cpu_pl_accuracy_is_counted_independently_even_on_numeric_failure(self):
        rows = [record(0, 0), record(1, 4), record(2, 999)]
        rows[1].update(PL_top5=[4, 0, 1, 2, 3], PL_logits_sha256='f'*64,
                       bitwise_mismatches=3, max_abs_difference=0.1)
        rows[2].update(CPU_top5=[7, 8, 9, 999, 1], PL_top5=[7, 8, 9, 10, 11],
                       PL_logits_sha256='e'*64, bitwise_mismatches=2, max_abs_difference=0.2)
        result = m.aggregate(rows, 3, [sample(r) for r in rows])
        self.assertEqual(result['status'], 'NUMERICAL_FAIL')
        self.assertEqual(result['bitwise_mismatch_images'], 2)
        self.assertEqual(result['bitwise_mismatches'], 5)
        self.assertEqual((result['accuracy']['CPU']['top1_correct'], result['accuracy']['CPU']['top5_correct']), (1, 3))
        self.assertEqual((result['accuracy']['PL']['top1_correct'], result['accuracy']['PL']['top5_correct']), (2, 2))
        self.assertFalse(result['performance_verified'])
        self.assertEqual(result['historical_B_CPU1_equivalence'], 'NOT_CLAIMED')

    def test_missing_excessive_reordered_or_duplicate_rows_are_rejected(self):
        rows = [record(0), record(1)]
        target = [sample(r) for r in rows]
        for bad in (rows[:1], rows+[record(2)], list(reversed(rows)), [rows[0], rows[0]]):
            with self.subTest(count=len(bad)), self.assertRaises(ValueError):
                m.aggregate(bad, 2, target)
        duplicated = copy.deepcopy(rows); duplicated[1]['id'] = duplicated[0]['id']
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            m.aggregate(duplicated, 2, [sample(r) for r in duplicated])
        duplicated = copy.deepcopy(rows); duplicated[1]['image_sha256'] = duplicated[0]['image_sha256']
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            m.aggregate(duplicated, 2, [sample(r) for r in duplicated])

    def test_changed_input_label_or_discontinuous_global_manifest_fails(self):
        rows = [record(5), record(6)]
        for field, value in (('input_sha256', 'f'*64), ('label', 999), ('ordinal', 8)):
            target = [sample(r) for r in rows]
            bad = copy.deepcopy(rows); bad[1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.aggregate(bad, 2, target)
        bad = [record(5), record(7)]
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            m.aggregate(bad, 2, [sample(r) for r in bad])

    def test_missing_closed_sample_cannot_be_hidden_by_numeric_matches(self):
        rows = [record(0), record(1)]
        rows[1]['device_closed'] = False
        with self.assertRaisesRegex(ValueError, 'closed proof'):
            m.aggregate(rows, 2, [sample(r) for r in rows])

    def test_module_loading_and_record_aggregation_do_not_import_numpy(self):
        original_import = builtins.__import__
        def checked_import(name, *args, **kwargs):
            if name == 'numpy' or name.startswith('numpy.'):
                raise AssertionError('stdlib aggregation imported numpy')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=checked_import):
            module = load('b24_accuracy_stdlib_only')
            rows = [record(0)]
            result = module.aggregate(rows, 1, [sample(rows[0])])
        self.assertEqual(result['status'], 'NUMERICAL_PASS')


if __name__ == '__main__':
    unittest.main()
