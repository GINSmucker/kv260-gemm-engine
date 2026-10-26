"""Synthetic layer-intervention/selection tests, not quality acceptance."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
from onnx import TensorProto, helper, numpy_helper

from diagnose_layer_sensitivity import (PRECISIONS, changed_weight_name, combined_passes,
                                        main, measure, select_shortlist, variant_model)
from prune_h576 import arrays


def low_risk_record(i):
    return {'layer_index': i,
            'single': {p: {'top1_drop_pp': 0, 'top5_drop_pp': 0,
                           'top1_new_errors': 1, 'logit_relative_l2_vs_dense': 0.05 + i * 0.001}
                       for p in PRECISIONS},
            'restore': {p: {'top1_gain_vs_pl36_pp': 0, 'top5_gain_vs_pl36_pp': 0}
                        for p in PRECISIONS}}


class LayerSensitivityTests(unittest.TestCase):
    def test_bidirectional_and_both_precision_gates(self):
        a, b, c = [low_risk_record(i) for i in (0, 2, 3)]
        b['single']['w8a8']['top1_drop_pp'] = 4
        c['restore']['fp32']['top1_gain_vs_pl36_pp'] = 3
        selected = select_shortlist([a, b, c])
        self.assertEqual(selected['eligible_in_risk_order'], [0])
        self.assertEqual(selected['dense_priority_indices'], [2, 3])

    def test_new_errors_limit_even_if_net_accuracy_is_unchanged(self):
        row = low_risk_record(0)
        row['single']['fp32']['top1_new_errors'] = 5
        self.assertEqual(select_shortlist([row])['initial_pilot_indices'], [])

    def test_bounded_deterministic_shortlist(self):
        records = [low_risk_record(i) for i in range(9)]
        self.assertEqual(select_shortlist(list(reversed(records)))['initial_pilot_indices'], [0, 1, 2, 3])
        before = copy.deepcopy(records)
        select_shortlist(records)
        self.assertEqual(records, before)

    def test_combined_requires_both_precisions_and_no_subset_search(self):
        metric = {'top1_drop_pp': 1, 'top5_drop_pp': 2, 'top1_new_errors': 2}
        self.assertFalse(combined_passes({}))
        self.assertFalse(combined_passes({'fp32': metric}))
        self.assertTrue(combined_passes({p: metric for p in PRECISIONS}))
        self.assertFalse(combined_passes({'fp32': metric, 'w8a8': dict(metric, top1_drop_pp=2)}))

    def test_variant_only_changes_one_initializer_and_leaves_source_intact(self):
        graph = helper.make_graph([helper.make_node('Identity', ['x'], ['y'])], 'fixture',
                                  [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])],
                                  [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])],
                                  [numpy_helper.from_array(np.ones((4, 6), np.float32), 'BQL_2_Wf'),
                                   numpy_helper.from_array(np.ones((4, 6), np.float32), 'untouched')])
        source = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], ir_version=9)
        initial = source.SerializeToString()
        layer = {'layer_index': 2, 'layer_id': 'blocks.0.mlp.fc1'}
        modified = variant_model(source, layer, 'w8a8', np.zeros((4, 6), np.float32))
        self.assertEqual(source.SerializeToString(), initial)
        self.assertTrue(np.all(arrays(modified)['BQL_2_Wf'] == 0))
        self.assertTrue(np.all(arrays(modified)['untouched'] == 1))
        self.assertEqual(changed_weight_name(layer, 'fp32'), 'blocks.0.mlp.fc1.weight')
        with self.assertRaises(ValueError):
            changed_weight_name(layer, 'invalid')

    def test_restore_gain_is_relative_to_full_pruned_not_dense(self):
        labels = np.array([0, 1, 2, 3])
        dense = np.array([[9, 8, 7, 6, 5, 0], [0, 9, 8, 7, 6, 5],
                          [0, 8, 9, 7, 6, 5], [0, 8, 7, 9, 6, 5]], np.float32)
        pruned = dense.copy()
        pruned[1] = pruned[2] = pruned[3] = [9, 8, 7, 6, 5, 0]
        restored = pruned.copy()
        restored[1] = dense[1]
        metric, _ = measure(restored, labels, dense, pruned, 'restore')
        self.assertEqual(metric['top1_drop_pp'], 50)
        self.assertEqual(metric['top1_gain_vs_pl36_pp'], 25)

    def test_existing_directory_is_not_modified(self):
        with tempfile.TemporaryDirectory(prefix='h576-layer-preserve-') as folder:
            path = Path(folder) / 'keep.txt'
            path.write_text('preserve', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--output-dir', folder]), 1)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ['keep.txt'])
            self.assertEqual(path.read_text(encoding='utf-8'), 'preserve')


if __name__ == '__main__':
    unittest.main()
