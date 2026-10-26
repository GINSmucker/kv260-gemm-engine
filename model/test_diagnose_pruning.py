"""Synthetic fixtures for bounded family-only diagnostics, not quality results."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
from onnx import TensorProto, helper, numpy_helper

from diagnose_pruning import (FAMILIES, create_family_models, independent_positions,
                              main, mask_from_positions, metric_counts)
from prune_h576 import arrays
from sparse24 import prune


class PruningDiagnosisTests(unittest.TestCase):
    def test_six_choice_audit_matches_python_ranking(self):
        rng = np.random.default_rng(93)
        weight = rng.integers(-3, 4, size=(12, 17)).astype(np.float32)
        weight[:4, 0] = [1, -1, 1, -1]
        weight[:4, 1] = 0
        positions = independent_positions(weight)
        for g in range(3):
            for n in range(17):
                expected = sorted(sorted(range(4), key=lambda i: (-abs(float(weight[4*g+i, n])), i))[:2])
                np.testing.assert_array_equal(positions[g, n], expected)
        _, production_positions, production_mask = prune(weight)
        np.testing.assert_array_equal(positions, production_positions)
        np.testing.assert_array_equal(mask_from_positions(positions, weight.shape), production_mask)

    def test_invalid_group_inputs(self):
        for value in (np.ones((3, 4), np.float32), np.ones((4, 4), np.float64),
                      np.full((4, 2), np.inf, np.float32), np.empty((4, 0), np.float32)):
            with self.assertRaises(ValueError):
                independent_positions(value)

    def test_metrics_paired_reconcile_and_quantized_improvement(self):
        labels = np.array([0, 1, 2, 3])
        dense = np.array([[9, 8, 7, 6, 5, 0], [0, 9, 8, 7, 6, 5],
                          [0, 8, 9, 7, 6, 5], [0, 9, 8, 7, 6, 5]], np.float32)
        pruned = dense.copy()
        pruned[1] = [0, 8, 9, 7, 6, 5]
        pruned[3] = [0, 8, 7, 9, 6, 5]
        metric, _ = metric_counts(pruned, labels, dense)
        self.assertEqual(metric['top1_correct'], 3)
        self.assertEqual(metric['top1_new_errors'], 1)
        self.assertEqual(metric['top1_recovered_errors'], 1)
        self.assertEqual(metric['top1_drop_pp'], 0)
        self.assertEqual(sum(metric['top1_' + suffix] for suffix in
                             ('new_errors', 'recovered_errors', 'both_correct', 'both_wrong')), 4)
        with self.assertRaises(ValueError):
            metric_counts(pruned[:2], labels, dense)

    def test_family_graphs_only_change_the_requested_family(self):
        layers, weights, fp_tensors, q_tensors = [], {}, [], []
        for family_number, family in enumerate(FAMILIES):
            for block in range(12):
                i = 12 * family_number + block
                layer_id = f'block.{block}.{family}'
                layers.append({'layer_index': i, 'layer_id': layer_id, 'family': family})
                original = np.ones((4, 6), np.float32)
                pruned = original.copy()
                pruned[2:] = 0
                weights[i] = {'W_fp32': pruned, 'Wq': pruned.astype(np.int8)}
                fp_tensors.append(numpy_helper.from_array(original.T.copy(), layer_id + '.weight'))
                q_tensors.append(numpy_helper.from_array(original, f'BQL_{i}_Wf'))
        def make(tensors):
            graph = helper.make_graph([helper.make_node('Identity', ['x'], ['y'])], 'fixture',
                                      [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])],
                                      [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])], tensors)
            return helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], ir_version=9)
        fp, q = make(fp_tensors), make(q_tensors)
        payload = {'assets/h576_fp32.onnx': fp.SerializeToString(), 'assets/h576_w8.onnx': q.SerializeToString()}
        with tempfile.TemporaryDirectory(prefix='h576-family-models-') as folder:
            specs = create_family_models(payload, layers, fp, q, weights, Path(folder))
            self.assertEqual(len(specs), 6)
            self.assertTrue(all(record['path'].is_file() for record in specs.values()))
            # The builder must not mutate its in-memory source models either.
            self.assertTrue(all(np.all(value == 1) for value in arrays(fp).values()))
            self.assertTrue(all(np.all(value == 1) for value in arrays(q).values()))

    def test_output_guard_preserves_existing_directory(self):
        with tempfile.TemporaryDirectory(prefix='h576-family-preserve-') as folder:
            root = Path(folder)
            marker = root / 'preserve.txt'
            marker.write_text('unchanged', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(['--model-output', str(root), '--result-output', str(root / 'new')])
            self.assertEqual(code, 1)
            self.assertEqual([p.name for p in root.iterdir()], ['preserve.txt'])
            self.assertEqual(marker.read_text(encoding='utf-8'), 'unchanged')


if __name__ == '__main__':
    unittest.main()
