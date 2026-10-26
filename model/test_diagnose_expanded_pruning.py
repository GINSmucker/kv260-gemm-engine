"""Synthetic coverage/order/graph-isolation tests; not H576 quality evidence."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
from onnx import TensorProto, helper, numpy_helper

from diagnose_expanded_pruning import build_combination, fixed_order, main, make_cpu_weights
from prune_h576 import arrays
from sparse24 import quantize_weight
from test_diagnose_layer_sensitivity import low_risk_record


def model_with(tensors):
    graph = helper.make_graph([helper.make_node('Identity', ['x'], ['y'])], 'fixture',
                              [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])],
                              [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])], tensors)
    return helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], ir_version=9)


class ExpandedDiagnosisTests(unittest.TestCase):
    def test_fixed_order_preserves_prior_eligible_prefix_and_has_nested_budgets(self):
        records = [low_risk_record(i) for i in range(12)]
        records[0]['single']['w8a8']['top1_new_errors'] = 8
        records[1]['single']['w8a8']['top1_new_errors'] = 7
        order = fixed_order(list(reversed(records)), [7, 4, 9, 2])
        self.assertEqual(order[:4], [7, 4, 9, 2])
        self.assertEqual(len(order), 12)
        self.assertEqual(set(order), set(range(12)))
        self.assertTrue(set(order[:4]).issubset(order[:8]))
        self.assertEqual(order[-2:], [1, 0])

    def test_bad_order_inventory_rejected(self):
        row = low_risk_record(0)
        for records, eligible in (([row, row], [0]), ([row], [0, 0]), ([row], [1])):
            with self.assertRaises(ValueError):
                fixed_order(records, eligible)

    def test_combination_changes_exact_requested_weights_without_mutating_source(self):
        tensors = [numpy_helper.from_array(np.ones((4, 6), np.float32), f'BQL_{i}_Wf') for i in (0, 2, 3)]
        source = model_with(tensors)
        before = source.SerializeToString()
        layers = [{'layer_index': i, 'layer_id': 'layer' + str(i)} for i in (0, 2, 3)]
        weights = {i: {'Wq': np.zeros((4, 6), np.int8)} for i in (0, 2, 3)}
        modified = build_combination(source, layers, [0, 3], weights, 'w8a8')
        self.assertEqual(source.SerializeToString(), before)
        values = arrays(modified)
        self.assertTrue(np.all(values['BQL_0_Wf'] == 0))
        self.assertTrue(np.all(values['BQL_3_Wf'] == 0))
        self.assertTrue(np.all(values['BQL_2_Wf'] == 1))
        for indices in ([0, 0], [4]):
            with self.assertRaises(ValueError):
                build_combination(source, layers, indices, weights, 'w8a8')

    def test_new_cpu_masks_follow_same_rule_and_keep_quantization_scales(self):
        original = np.array([[4, -4, 8, 1, 2, 9], [3, 2, 5, 3, 4, 1],
                             [1, 1, 4, 2, 5, 2], [2, 3, 1, 4, 1, 3]], np.float32)
        q, scale = quantize_weight(original)
        fp = model_with([numpy_helper.from_array(original.T.copy(), 'head.weight')])
        quant = model_with([numpy_helper.from_array(q.astype(np.float32), 'BQL_48_Wf'),
                            numpy_helper.from_array(scale.ravel(), 'BQL_48_sf')])
        layer = {'layer_index': 48, 'layer_id': 'head', 'family': 'head', 'M': 1, 'K': 4, 'N': 6}
        weights, audit = make_cpu_weights([layer], fp, quant)
        self.assertEqual(audit[0]['groups_checked'], 6)
        self.assertEqual(audit[0]['retained_slots'], 12)
        self.assertTrue(audit[0]['weight_scale_identical'])
        self.assertEqual(weights[48]['Wq'].shape, (4, 6))
        np.testing.assert_array_equal(arrays(fp)['head.weight'], original.T)

    def test_existing_directory_guard(self):
        with tempfile.TemporaryDirectory(prefix='h576-expanded-preserve-') as folder:
            keep = Path(folder) / 'keep.txt'
            keep.write_text('preserve', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--output-dir', folder]), 1)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ['keep.txt'])


if __name__ == '__main__':
    unittest.main()
