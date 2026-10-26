"""Synthetic tests for c062 export guards, masks and graph isolation."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import onnx

import export_mlp12 as export
from prune_h576 import arrays, compare_unmodified
from sparse24 import pack, prune, quantize_weight
from test_diagnose_mlp_only import fixture, inventory


def layers():
    return [dict(r, M=2, K=4, N=3) for r in inventory()]


class ExportMlp12Tests(unittest.TestCase):
    def test_inventory_has_12_selected_37_unchanged_but_only_13_PS(self):
        records = export.selection_inventory(layers())
        self.assertEqual([r['layer_index'] for r in records if r['weight_changed']], list(export.SELECTED))
        self.assertEqual(sum(r['mode'] == 'dense_w8a8' for r in records), 37)
        self.assertEqual(sum(r['original_route'] == 'PS' for r in records), 13)
        self.assertEqual(sum(r['original_route'] == 'PL' for r in records), 36)
        self.assertEqual({r['family'] for r in records if r['weight_changed']}, {'fc1', 'fc2'})

    def test_other_groups_and_corrupt_inventory_rejected(self):
        for selected in (export.SELECTED[:-1], export.SELECTED + (0,), (0,) + export.SELECTED[1:]):
            with self.assertRaises(ValueError):
                export.selection_inventory(layers(), selected)
        for change in ('id', 'family', 'missing', 'duplicate'):
            source = layers()
            if change == 'id': source[26]['layer_id'] = 'blocks.5.mlp.fc1'
            if change == 'family': source[26]['family'] = 'qkv'
            if change == 'missing': source.pop()
            if change == 'duplicate': source[-1] = source[0]
            with self.assertRaises(ValueError):
                export.selection_inventory(source)

    def test_fp32_and_w8_graphs_change_only_selected_initializers(self):
        for precision in ('fp32', 'w8a8'):
            model, _, weights = fixture(precision)
            before = model.SerializeToString()
            new = export.build_export(model, layers(), weights, precision)
            names = {export.changed_weight_name(r, precision) for r in layers() if r['layer_index'] in export.SELECTED}
            compare_unmodified(before, new, names)
            self.assertEqual(model.SerializeToString(), before)
            actual = arrays(new)
            for r in layers():
                if r['layer_index'] in export.SELECTED:
                    expected = (weights[r['layer_index']]['W_fp32'].T if precision == 'fp32'
                                else weights[r['layer_index']]['Wq'].astype(np.float32))
                    np.testing.assert_array_equal(actual[export.changed_weight_name(r, precision)], expected)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'model.onnx'
                onnx.save(new, path)
                self.assertEqual(onnx.load(path).SerializeToString(), new.SerializeToString())

    def test_audited_mask_slots_allow_quantized_zero(self):
        original = np.array([[127, 4, 0], [0.001, 3, 0], [0.0001, 2, 0], [0, 1, 0]], np.float32)
        pruned, positions, mask = prune(original)
        wq, scale = quantize_weight(pruned)
        original_q, original_scale = quantize_weight(original)
        bias = np.zeros(3, np.float32)
        stored = dict(W_fp32=pruned, Wq=wq, sw=scale, mask=mask,
                      positions=positions, values=pack(wq, positions), bias=bias)
        result = export.audit_weight(original, original_q, original_scale.ravel(), bias, stored)
        self.assertEqual(result['retained_slots'], 6)
        self.assertLess(result['int8_nonzero_values'], 6)
        broken = copy.deepcopy(stored)
        broken['positions'][0, 0] = [0, 0]
        with self.assertRaises(AssertionError):
            export.audit_weight(original, original_q, original_scale.ravel(), bias, broken)

    def test_bitwise_check_rejects_signed_zero_and_dtype(self):
        export.bitwise_equal(np.array([0], np.float32), np.array([0], np.float32))
        for other in (np.array([-0.0], np.float32), np.array([0], np.float64)):
            with self.assertRaises(ValueError):
                export.bitwise_equal(np.array([0], np.float32), other)

    def test_known_fp32_failure_not_hidden_by_w8_pass(self):
        evaluation = {name: {p: dict(images=n, top1_lost_count=lost, top5_lost_count=2)
                            for p, lost in (('fp32', 6 if n == 100 else 20), ('w8a8', 5 if n == 100 else 17))}
                      for name, n in (('old100', 100), ('partial523', 523))}
        flags = export.quality_flags(evaluation)
        self.assertTrue(flags['w8a8_both_development_sets_within_5pp'])
        self.assertFalse(flags['fp32_both_development_sets_within_5pp'])
        self.assertEqual(flags['independent_quality_acceptance'], 'NOT_RUN')

    def test_existing_output_refused_before_reading_models(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'candidates' / 'existing'
            output.mkdir(parents=True)
            sentinel = output / 'keep.txt'
            sentinel.write_text('unchanged')
            with mock.patch.object(export, 'ROOT', root), mock.patch.object(export, 'read_source') as source:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(export.main(['--output-dir', str(output)]), 1)
                source.assert_not_called()
                self.assertEqual(sentinel.read_text(), 'unchanged')
                self.assertEqual(list(output.iterdir()), [sentinel])

    def test_output_outside_candidates_refused(self):
        with self.assertRaises(ValueError):
            export.new_output(export.ROOT / 'inputs' / 'not_authorized')


if __name__ == '__main__':
    unittest.main()
