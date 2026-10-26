"""Synthetic MLP-whitelist/graph-isolation tests; not model-quality evidence."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from onnx import TensorProto, helper, numpy_helper

from diagnose_mlp_only import build_mlp_variant, main, select_mlp_indices
from prune_h576 import arrays


def inventory():
    layers = []
    for index in range(49):
        family = 'head' if index == 48 else ('qkv', 'proj', 'fc1', 'fc2')[index % 4]
        if index == 48:
            layer_id = 'head'
        elif family in {'qkv', 'proj'}:
            layer_id = f'blocks.{index // 4}.attn.{family}'
        else:
            layer_id = f'blocks.{index // 4}.mlp.{family}'
        layers.append({'layer_index': index, 'family': family, 'layer_id': layer_id})
    return layers


def fixture(precision):
    layers = inventory()
    tensors, weights = [], {}
    for layer in layers:
        index, layer_id = layer['layer_index'], layer['layer_id']
        # Non-square K x N arrays make an incorrect FP32 transpose observable.
        original = np.full((4, 3), index + 1, dtype=np.float32)
        pruned = original.copy()
        pruned[1:3] = 0
        weights[index] = {'W_fp32': pruned, 'Wq': pruned.astype(np.int8)}
        name = layer_id + '.weight' if precision == 'fp32' else f'BQL_{index}_Wf'
        stored = original.T.copy() if precision == 'fp32' else original
        tensors.extend([
            numpy_helper.from_array(stored, name),
            numpy_helper.from_array(np.arange(3, dtype=np.float32), layer_id + '.bias'),
            numpy_helper.from_array(np.full(3, 0.25, np.float32), f'BQL_{index}_sf'),
        ])
    tensors.append(numpy_helper.from_array(np.array([17], np.int64), 'unrelated_constant'))
    graph = helper.make_graph(
        [helper.make_node('Identity', ['x'], ['y'])], 'mlp_whitelist_fixture',
        [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])],
        [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])], tensors)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], ir_version=9)
    model.doc_string = 'Synthetic graph metadata must survive unchanged.'
    return model, layers, weights


class MlpOnlyDiagnosisTests(unittest.TestCase):
    def test_exact_49_inventory_selects_only_24_mlp_layers(self):
        layers = inventory()
        before = copy.deepcopy(layers)
        selected = select_mlp_indices(list(reversed(layers)))
        expected = [i for i in range(48) if i % 4 in (2, 3)]
        self.assertEqual(selected, expected)
        self.assertEqual(len(selected), 24)
        self.assertEqual(layers, before)

    def test_duplicate_missing_and_wrong_inventory_rejected(self):
        duplicate = inventory()
        duplicate[-1] = copy.deepcopy(duplicate[0])
        outside = inventory()
        outside[-1]['layer_index'] = 49
        for layers in (duplicate, inventory()[:-1], inventory() + [inventory()[0]], outside):
            with self.subTest(indices=[r['layer_index'] for r in layers]):
                with self.assertRaises(ValueError):
                    select_mlp_indices(layers)

    def test_mislabeled_families_rejected_including_head(self):
        for index, wrong in ((0, 'fc1'), (1, 'qkv'), (2, 'proj'), (3, 'fc1'), (48, 'fc2')):
            layers = inventory()
            layers[index]['family'] = wrong
            with self.subTest(index=index, wrong=wrong):
                with self.assertRaises(ValueError):
                    select_mlp_indices(layers)

    def check_graph_isolation(self, precision):
        source, layers, weights = fixture(precision)
        source_before = source.SerializeToString()
        layers_before = copy.deepcopy(layers)
        weights_before = copy.deepcopy(weights)
        modified, audit = build_mlp_variant(source, layers, weights, precision)
        original_arrays, modified_arrays = arrays(source), arrays(modified)
        selected = [i for i in range(48) if i % 4 in (2, 3)]
        expected_names = {
            layer['layer_id'] + '.weight' if precision == 'fp32' else f"BQL_{layer['layer_index']}_Wf"
            for layer in layers if layer['layer_index'] in selected
        }
        actual_changed = {
            name for name in original_arrays
            if not np.array_equal(original_arrays[name], modified_arrays[name])
        }
        self.assertEqual(actual_changed, expected_names)
        self.assertEqual(audit['pruned_layer_indices'], selected)
        self.assertEqual(audit['kept_dense_layer_indices'], sorted(set(range(49)) - set(selected)))
        self.assertEqual(set(audit['changed_initializer_names']), expected_names)
        self.assertEqual(len(audit['changed_initializer_names']), 24)
        for layer in layers:
            index = layer['layer_index']
            name = layer['layer_id'] + '.weight' if precision == 'fp32' else f'BQL_{index}_Wf'
            if index in selected:
                expected = weights[index]['W_fp32'].T if precision == 'fp32' else weights[index]['Wq'].astype(np.float32)
                np.testing.assert_array_equal(modified_arrays[name], expected)
            else:
                np.testing.assert_array_equal(modified_arrays[name], original_arrays[name])
        # Compare graph/metadata bytes after removing initializer data.
        original_skeleton, modified_skeleton = copy.deepcopy(source), copy.deepcopy(modified)
        del original_skeleton.graph.initializer[:]
        del modified_skeleton.graph.initializer[:]
        self.assertEqual(original_skeleton.SerializeToString(), modified_skeleton.SerializeToString())
        self.assertEqual(source.SerializeToString(), source_before)
        self.assertEqual(layers, layers_before)
        for index in weights:
            for name in weights[index]:
                np.testing.assert_array_equal(weights[index][name], weights_before[index][name])

    def test_fp32_variant_only_changes_mlp_weights_and_transposes(self):
        self.check_graph_isolation('fp32')

    def test_w8a8_variant_only_changes_mlp_weights_and_casts(self):
        self.check_graph_isolation('w8a8')

    def test_invalid_precision_rejected_without_source_mutation(self):
        source, layers, weights = fixture('fp32')
        before = source.SerializeToString()
        with self.assertRaises(ValueError):
            build_mlp_variant(source, layers, weights, 'int4')
        self.assertEqual(source.SerializeToString(), before)

    def test_existing_directory_guard_precedes_source_reads_and_never_overwrites(self):
        with tempfile.TemporaryDirectory(prefix='h576-mlp-preserve-') as directory:
            keep = Path(directory) / 'keep.txt'
            keep.write_text('preserve', encoding='utf-8')
            stderr = io.StringIO()
            with mock.patch('diagnose_mlp_only.read_source') as read_source, contextlib.redirect_stderr(stderr):
                status = main(['--output-dir', directory, '--archive', str(Path(directory) / 'missing.tar.gz')])
            self.assertEqual(status, 1)
            self.assertIn('Output exists', stderr.getvalue())
            read_source.assert_not_called()
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['keep.txt'])
            self.assertEqual(keep.read_text(encoding='utf-8'), 'preserve')


if __name__ == '__main__':
    unittest.main()
