"""Synthetic control tests; NOT model quality evidence."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np

from eval_fp32_control import MODEL_NAMES, error_rows, main, summarize


def logits_for(predictions):
    rows = np.tile(np.arange(6, dtype=np.float32), (len(predictions), 1))
    for i, pred in enumerate(predictions):
        rows[i, pred] = 10
    return rows


class ControlTests(unittest.TestCase):
    def test_paired_counts_and_signed_interaction(self):
        labels = np.array([0, 1, 2, 3])
        data = dict(zip(MODEL_NAMES, map(logits_for, (
            [0, 1, 2, 4], [0, 4, 4, 3], [0, 1, 4, 4], [0, 4, 4, 4]))))
        summary, ranks = summarize(data, labels)
        self.assertEqual([summary['models'][n]['top1_correct'] for n in MODEL_NAMES], [3, 2, 2, 1])
        pair = summary['comparisons']['fp32_pruning']
        self.assertEqual([pair[k] for k in ('both_correct', 'new_errors', 'recovered_errors', 'both_wrong')], [1, 2, 1, 0])
        self.assertEqual(pair['top1_drop_pp'], 25)
        self.assertEqual(summary['quantization_interaction_pp']['top1'], 0)
        identities = [{'image': f'{i}.png', 'sha256': str(i), 'label': int(label)}
                      for i, label in enumerate(labels)]
        errors = error_rows(identities, data, ranks, 'dense_fp32', 'pruned_fp32')
        self.assertEqual([row['image'] for row in errors], ['1.png', '2.png'])

    def test_quantization_can_improve_counts(self):
        labels = np.array([0, 1])
        data = {name: logits_for([0, 4]) for name in MODEL_NAMES}
        data['pruned_w8a8'] = logits_for([0, 1])
        summary, _ = summarize(data, labels)
        self.assertEqual(summary['comparisons']['pruned_quantization']['top1_drop_pp'], -50)
        self.assertEqual(summary['comparisons']['w8a8_pruning']['recovered_errors'], 1)

    def test_bad_outputs_and_labels_rejected(self):
        data = {name: logits_for([0]) for name in MODEL_NAMES}
        for labels in (np.array([], dtype=int), np.array([6]), np.array([-1]), np.array([0.5])):
            with self.assertRaises(ValueError):
                summarize(data, labels)
        for value in (np.full((1, 6), np.nan, np.float32), np.zeros((1, 7), np.float32),
                      np.zeros((2, 6), np.float32)):
            invalid = dict(data, pruned_fp32=value)
            with self.assertRaises(ValueError):
                summarize(invalid, np.array([0]))

    def test_existing_output_refused_before_loading_models(self):
        with tempfile.TemporaryDirectory(prefix='h576-four-control-') as folder:
            marker = Path(folder) / 'keep.txt'
            marker.write_text('preserve', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--output-dir', folder]), 1)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')
            self.assertEqual(len(list(Path(folder).iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
