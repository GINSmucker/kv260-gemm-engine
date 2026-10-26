"""Synthetic search-scope and ranking checks, NOT model-quality evidence."""
import copy
import unittest

import numpy as np

from diagnose_expanded_pruning import build_combination
from prune_h576 import arrays
from screen_mlp_groups import GATE, compute_coverage, group_plan, passes, selection_key
from test_diagnose_mlp_only import fixture, inventory


def records():
    result = []
    for layer in inventory():
        if layer['family'] not in {'qkv', 'fc1', 'fc2'}:
            continue
        result.append({**layer, 'single': {p: {'top1_new_errors': 0, 'top1_drop_pp': 0,
                                             'top5_drop_pp': 0, 'logit_relative_l2_vs_dense': (48-layer['layer_index'])/100}
                                          for p in ('fp32', 'w8a8')}})
    return result


class GroupScreenTests(unittest.TestCase):
    def test_plan_is_deterministic_unique_and_mlp_only(self):
        layers, previous = inventory(), records()
        snapshot = copy.deepcopy(previous)
        groups, orders = group_plan(layers, previous)
        repeated, _ = group_plan(list(reversed(layers)), list(reversed(previous)))
        self.assertEqual(groups, repeated)
        self.assertEqual(previous, snapshot)
        self.assertLessEqual(len(groups), 51)
        keys = [tuple(g['layer_indices']) for g in groups]
        self.assertEqual(len(keys), len(set(keys)))
        for g in groups:
            self.assertTrue(all(i < 48 and i % 4 in (2, 3) for i in g['layer_indices']))
        self.assertEqual(len(orders['singleton_risk_order']), 24)
        self.assertTrue(any(g['layers'] == 18 for g in groups))
        self.assertTrue(any(g['layers'] == 24 for g in groups))

    def test_missing_and_duplicate_singleton_inventory_rejected(self):
        previous = records()
        mlp_index = next(i for i, r in enumerate(previous) if r['family'] == 'fc1')
        previous.pop(mlp_index)
        with self.assertRaises(ValueError):
            group_plan(inventory(), previous)
        previous = records()
        previous.append(copy.deepcopy(previous[1]))
        with self.assertRaises(ValueError):
            group_plan(inventory(), previous)

    def test_paired_groups_never_split_a_block(self):
        groups, _ = group_plan(inventory(), records())
        for g in groups:
            if any(s.startswith(('paired_block_', 'late_block_')) for s in g['strategies']):
                ids = set(g['layer_indices'])
                self.assertTrue(all((i ^ 1) in ids for i in ids))

    def test_gate_requires_both_precisions_and_all_constraints(self):
        good = {'top1_drop_pp': 1.0, 'top5_drop_pp': 2.0, 'top1_new_errors': 2}
        metrics = {p: good.copy() for p in ('fp32', 'w8a8')}
        self.assertTrue(passes(metrics))
        self.assertFalse(passes({'w8a8': good}))
        for name, value in (('top1_drop_pp', 2), ('top5_drop_pp', 3), ('top1_new_errors', 3)):
            bad = copy.deepcopy(metrics)
            bad['fp32'][name] = value
            self.assertFalse(passes(bad))
        self.assertEqual(GATE['max_top1_drop_pp'], 1)

    def test_selection_prefers_coverage_before_better_dev_accuracy(self):
        def group(ids, macs, new):
            return {'layer_indices': ids, 'compute': {'dense_MACs_covered': macs},
                    'metrics': {p: {'top1_new_errors': new, 'top1_drop_pp': 0,
                                    'top5_drop_pp': 0, 'logit_relative_l2_vs_dense': 0.1}
                                for p in ('fp32', 'w8a8')}}
        a, b = group([2], 10, 0), group([2, 3], 20, 1)
        self.assertEqual(sorted([a, b], key=selection_key), [b, a])

    def test_mac_coverage_uses_mkn_and_all_linear_denominator(self):
        layers = [{'layer_index': 2, 'family': 'fc1', 'M': 2, 'K': 4, 'N': 3},
                  {'layer_index': 0, 'family': 'qkv', 'M': 2, 'K': 4, 'N': 3},
                  {'layer_index': 48, 'family': 'head', 'M': 1, 'K': 4, 'N': 3}]
        c = compute_coverage(layers, [2])
        self.assertEqual(c['dense_MACs_covered'], 24)
        self.assertEqual(c['all49_linear_dense_MACs'], 60)
        self.assertEqual(c['ideal_skipped_fraction_of_PL36_MACs'], 0.25)
        self.assertEqual(c['ideal_skipped_fraction_of_all49_linear_MACs'], 0.2)

    def test_partial_graphs_preserve_qkv_biases_scales_and_topology(self):
        for precision in ('fp32', 'w8a8'):
            original, layers, weights = fixture(precision)
            before = original.SerializeToString()
            candidate = build_combination(original, layers, [27, 39, 46, 47], weights, precision)
            old, new = arrays(original), arrays(candidate)
            changed = {n for n in old if not np.array_equal(old[n], new[n])}
            expected = {r['layer_id']+'.weight' if precision == 'fp32' else f"BQL_{r['layer_index']}_Wf"
                        for r in layers if r['layer_index'] in (27, 39, 46, 47)}
            self.assertEqual(changed, expected)
            self.assertEqual(before, original.SerializeToString())


if __name__ == '__main__':
    unittest.main()
