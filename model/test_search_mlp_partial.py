"""Synthetic tests of five-point gates and partial-download handling."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from download_imagenet100 import select_rows
from search_mlp_partial import annotate, gate, nested_sets, pixel_sha, read_partial


class PartialSearchTests(unittest.TestCase):
    def test_five_point_gate_is_exact_for_523_images(self):
        self.assertTrue(gate({'top1_lost_count': 26, 'top5_lost_count': 26}, 523))
        self.assertFalse(gate({'top1_lost_count': 27, 'top5_lost_count': 0}, 523))
        self.assertFalse(gate({'top1_lost_count': 0, 'top5_lost_count': 27}, 523))
        self.assertTrue(gate({'top1_lost_count': -12, 'top5_lost_count': 26}, 523))

    def test_annotation_uses_matching_baseline_and_does_not_mutate(self):
        m = {'top1_correct': 300, 'top5_correct': 400}
        original = copy.deepcopy(m)
        a = annotate(m, {'top1_correct': 320, 'top5_correct': 398})
        self.assertEqual(a['top1_lost_count'], 20)
        self.assertEqual(a['top5_lost_count'], -2)
        self.assertEqual(m, original)

    def test_nested_sets_keep_exact_prefix_and_are_canonical(self):
        self.assertEqual(nested_sets([47, 39, 46]), [(47,), (39, 47), (39, 46, 47)])

    def test_pixel_hash_checks_decoded_content_not_compression(self):
        image = Image.new('RGB', (8, 7), (23, 45, 67))
        a, b = io.BytesIO(), io.BytesIO()
        image.save(a, format='PNG', compress_level=1)
        image.save(b, format='PNG', compress_level=9)
        self.assertEqual(pixel_sha(a.getvalue()), pixel_sha(b.getvalue()))

    def make_partial(self, root, repeated_old=False):
        source, old = root/'source', root/'old'
        for folder in (source, old):
            (folder/'images').mkdir(parents=True)
        (source/'.state').mkdir()
        def picture(color):
            stream = io.BytesIO()
            Image.new('RGB', (160, 170), color).save(stream, format='PNG')
            return stream.getvalue()
        old_data = picture((1, 2, 3))
        (old/'images/old.png').write_bytes(old_data)
        (old/'dataset_manifest.json').write_text(json.dumps({'rows': [
            {'image': 'images/old.png', 'image_sha256': hashlib.sha256(old_data).hexdigest()}]}))
        planned = select_rows(5000, 3, 20261022)
        plan = {'dataset': 'clane9/imagenet-100', 'split': 'validation',
                'revision': '0519dc2f402a3a18c6e57f7913db059215eee25b', 'count': 3, 'seed': 20261022,
                'row_indices': planned, 'label_map': [{'source_label': 0, 'label': 452, 'wnid': 'n02869837'}]}
        (source/'download_plan.json').write_text(json.dumps(plan))
        for j, index in enumerate(planned[:2]):
            data = old_data if repeated_old and j == 0 else picture((j+10, 20, 30))
            name = f'images/row_{index:07d}.png'
            (source/name).write_bytes(data)
            receipt = {'row_index': index, 'image': name, 'source_label': 0, 'label': 452,
                       'wnid': 'n02869837', 'image_sha256': hashlib.sha256(data).hexdigest(),
                       'bytes': len(data), 'width': 160, 'height': 170}
            (source/'.state'/f'row_{index:07d}.json').write_text(json.dumps(receipt))
        return source, old

    def test_partial_snapshot_requires_receipts_not_final_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            source, old = self.make_partial(Path(directory))
            protected = {}
            tensors, rows, audit = read_partial(source, 2, old, protected)
            self.assertEqual(len(rows), 2)
            self.assertEqual(tensors[0].shape, (1, 3, 224, 224))
            self.assertEqual(audit['overlap_with_old_dev100_raw_or_decoded'], 0)
            self.assertFalse((source/'labels.csv').exists())
            self.assertFalse(tensors[0].flags.writeable)
            with self.assertRaisesRegex(ValueError, 'completed receipts'):
                read_partial(source, 3, old, {})

    def test_overlap_is_rejected_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            source, old = self.make_partial(Path(directory), repeated_old=True)
            with self.assertRaisesRegex(ValueError, 'Overlap'):
                read_partial(source, 2, old, {})

    def test_receipt_label_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source, old = self.make_partial(Path(directory))
            receipt = sorted((source/'.state').glob('*.json'))[0]
            r = json.loads(receipt.read_text())
            r['label'] = 2
            receipt.write_text(json.dumps(r))
            with self.assertRaisesRegex(ValueError, 'label mapping'):
                read_partial(source, 2, old, {})


if __name__ == '__main__':
    unittest.main()
