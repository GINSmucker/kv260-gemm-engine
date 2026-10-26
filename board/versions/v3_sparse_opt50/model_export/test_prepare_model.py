"""Fixed-model export tests; no device, training or classification inference."""
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import onnx
from onnx import numpy_helper
import prepare_model as p


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = p.load_sources()
        cls.artifacts, cls.manifest = p.assemble(cls.sources)

    def setUp(self):
        base = p.HERE / '.test_build'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='model_', dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_package(self):
        folder = self.root / 'package'
        folder.mkdir()
        for name, raw in self.artifacts.items():
            path = folder / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(raw)
        (folder / 'manifest.json').write_bytes(p.json_bytes(self.manifest))
        return folder

    def test49_routes_shapes_and_exact12_sparse(self):
        rows = self.manifest['layers']
        self.assertEqual(len(rows), 49)
        self.assertEqual([r['layer_index'] for r in rows], list(range(49)))
        self.assertEqual([r['layer_index'] for r in rows if r['mode']], list(p.SELECTED))
        self.assertEqual(sum(r['route'] == 'PL' for r in rows), 36)
        self.assertEqual(sum(r['route'] == 'PS' for r in rows), 13)
        self.assertEqual((rows[48]['m'], rows[48]['k'], rows[48]['n'], rows[48]['route']), (1, 192, 1000, 'PS'))
        self.assertFalse(self.manifest['hardware_executed'])
        self.assertFalse(self.manifest['accuracy_verified'])
        self.assertFalse(self.manifest['performance_verified'])

    def test_exact_canonical_and_physical_sizes_and_no_guards(self):
        self.assertEqual(len(self.artifacts['weights.bin']), 4615680)
        self.assertEqual(len(self.artifacts['pl_w_data.bin']), 3317760)
        self.assertEqual(len(self.artifacts['pl_w_meta.bin']), 165952)
        self.assertEqual(self.artifacts['pl_w_meta.bin'][:64], bytes(64))
        canonical_offset = data_offset = 0
        meta_offset = 64
        for row in self.manifest['layers']:
            nbytes = row['k']*row['n']
            self.assertEqual(row['canonical_offset'], canonical_offset)
            canonical = self.artifacts['weights.bin'][canonical_offset:canonical_offset+nbytes]
            self.assertEqual(p.sha(canonical), row['Wq_sha256'])
            canonical_offset += nbytes
            if row['route'] == 'PS':
                self.assertEqual([row[key] for key in ('w_data_offset','w_data_bytes','w_meta_offset','w_meta_bytes')], [0]*4)
                continue
            self.assertEqual(row['w_data_offset'], data_offset)
            self.assertEqual(data_offset % 64, 0)
            self.assertEqual(row['w_meta_offset'] % 64, 0)
            data_offset += row['w_data_bytes']
            if row['mode']:
                self.assertEqual((row['w_meta_offset'], row['w_meta_bytes']), (meta_offset, 13824))
                meta_offset += 13824
            else:
                self.assertEqual((row['w_meta_offset'], row['w_meta_bytes']), (0, 16))
                self.assertEqual(self.artifacts['pl_w_data.bin'][row['w_data_offset']:data_offset], canonical)
        self.assertEqual((canonical_offset,data_offset,meta_offset), (4615680,3317760,165952))

    def test_c_runner_bundle_uses_new49_weights_identity(self):
        bm = json.loads(self.artifacts['bundle/bundle_manifest.json'])
        layers = json.loads(self.artifacts['bundle/layers.json'])
        self.assertEqual(bm['source_semantic_sha256'], p.REFERENCE_SHA)
        self.assertEqual(len(bm['files']), 50)
        joined = b''.join(self.artifacts['bundle/' + layer['Wq']['file']] for layer in layers)
        self.assertEqual(joined, self.artifacts['weights.bin'])
        self.assertEqual(p.sha(joined), p.EXPECTED_WEIGHTS_SHA)
        self.assertNotEqual(p.sha(joined), '3a35c6d461d655cb218b655f21944fc88270619473962af42738374678faf811')
        for i, layer in enumerate(layers):
            self.assertEqual(layer['numeric_identity']['Wq'], self.manifest['layers'][i]['Wq_sha256'])
            self.assertEqual(layer['numeric_identity']['sw'], self.manifest['layers'][i]['sw_sha256'])
            self.assertEqual(layer['numeric_identity']['bias'], self.manifest['layers'][i]['bias_sha256'])
            self.assertEqual(p.identity(self.artifacts['bundle/' + layer['Wq']['file']]),
                             {key: layer['Wq'][key] for key in ('bytes','sha256')})

    def test_exact_graph12_weights_and12_identity_attributes_only(self):
        original = onnx.load_model_from_string(self.sources['archive']['graphs/model_w8a8_pl.onnx'])
        actual = onnx.load_model_from_string(self.artifacts['graphs/model_w8a8_pl.onnx'])
        self.assertEqual(len(original.graph.node), len(actual.graph.node))
        changed_weights = []
        for before, after in zip(original.graph.initializer, actual.graph.initializer):
            self.assertEqual(before.name, after.name)
            if before.SerializeToString() != after.SerializeToString():
                changed_weights.append(before.name)
                after.CopyFrom(before)
        changed_attributes = []
        for before, after in zip(original.graph.node, actual.graph.node):
            for x, y in zip(before.attribute, after.attribute):
                if x.SerializeToString() != y.SerializeToString():
                    self.assertEqual(x.name, 'W_sha256')
                    changed_attributes.append(next(a.i for a in before.attribute if a.name == 'layer_index'))
                    y.CopyFrom(x)
        self.assertEqual(changed_weights, ['BQL_%d_Wq' % i for i in p.SELECTED])
        self.assertEqual(changed_attributes, list(p.SELECTED))
        self.assertEqual(actual.SerializeToString(), original.SerializeToString())

    def test_all49_scale_bias_are_original_bytes(self):
        original = onnx.load_model_from_string(self.sources['archive']['graphs/model_w8a8_pl.onnx'])
        changed = onnx.load_model_from_string(self.artifacts['graphs/model_w8a8_pl.onnx'])
        a = {i.name:i.SerializeToString() for i in original.graph.initializer}
        b = {i.name:i.SerializeToString() for i in changed.graph.initializer}
        for i in range(49):
            for suffix in ('sw', 'bias'):
                self.assertEqual(a['BQL_%d_%s' % (i,suffix)], b['BQL_%d_%s' % (i,suffix)])

    def test12_sparse_physical_segments_equal_tested_axi_bytes(self):
        for i in p.SELECTED:
            row = self.manifest['layers'][i]
            self.assertEqual(self.artifacts['pl_w_data.bin'][row['w_data_offset']:row['w_data_offset']+row['w_data_bytes']],
                             self.sources['axi']['layers/layer_%02d/weights/W_sparse_data.bin' % i])
            self.assertEqual(self.artifacts['pl_w_meta.bin'][row['w_meta_offset']:row['w_meta_offset']+row['w_meta_bytes']],
                             self.sources['axi']['layers/layer_%02d/weights/W_sparse_meta.bin' % i])
        self.assertEqual(self.manifest['verification']['independent_INT64_vs_slot_outputs_compared'], 27648)

    def test_header_contract_and_fixed_digests(self):
        text = self.artifacts['model_catalog.hpp'].decode()
        for value in ('namespace opt50_model', 'std::array<Layer,49>', 'weights_bytes=4615680',
                      'data_bytes=3317760', 'meta_bytes=165952', 'max_x_bytes=113472', 'max_y_bytes=453888',
                      p.EXPECTED_WEIGHTS_SHA, p.REFERENCE_SHA, self.manifest['pl_data_sha256'], self.manifest['pl_meta_sha256']):
            self.assertIn(value, text)

    def test_changed_reference_or_candidate_is_rejected(self):
        for key in ('reference_raw','candidate_raw','base_raw','axi_manifest_raw'):
            source = self.sources.copy()
            source[key] = source[key] + b'changed'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'source identity'):
                p.assemble(source)

    def test_changed_npz_or_axi_input_is_rejected(self):
        source = self.sources.copy()
        source['npzs'] = self.sources['npzs'].copy()
        source['npzs'][26] = b'not a weight npz'
        with self.assertRaisesRegex(ValueError, 'NPZ source identity'):
            p.assemble(source)
        source = self.sources.copy()
        source['axi'] = self.sources['axi'].copy()
        source['axi']['layers/layer_26/weights/W_sparse_meta.bin'] = bytes(13824)
        with self.assertRaisesRegex(ValueError, 'tested AXI'):
            p.assemble(source)

    def test_legacy_graph_change_and_identity_staleness_rejected(self):
        source = self.sources.copy()
        source['archive'] = self.sources['archive'].copy()
        source['archive']['graphs/model_w8a8_pl.onnx'] += b'changed'
        with self.assertRaisesRegex(ValueError, 'C fused graph'):
            p.assemble(source)

    def test_verify_written_package_and_detect_tamper(self):
        folder = self.write_package()
        self.assertEqual(p.verify_package(folder, self.manifest), self.manifest)
        path = folder / 'pl_w_meta.bin'
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'file changed'):
            p.verify_package(folder, self.manifest)

    def test_self_consistent_manifest_tampering_still_rejected(self):
        folder = self.write_package()
        changed = copy.deepcopy(self.manifest)
        raw = bytes(len(self.artifacts['pl_w_data.bin']))
        (folder/'pl_w_data.bin').write_bytes(raw)
        changed['files']['pl_w_data.bin'] = p.identity(raw)
        (folder/'manifest.json').write_bytes(p.json_bytes(changed))
        with self.assertRaisesRegex(ValueError, 'source-derived expected'):
            p.verify_package(folder, self.manifest)

    def test_missing_extra_and_unsafe_files_rejected(self):
        folder = self.write_package()
        (folder/'extra').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            p.verify_package(folder, self.manifest)
        (folder/'extra').unlink()
        (folder/'weights.bin').unlink()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            p.verify_package(folder, self.manifest)
        for name in ('../x','/x','C:/x','a\\b','a//b','a/./b',''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.safe_relative(name)

    def test_export_refuses_existing_directory_before_reading_inputs(self):
        with patch.object(p,'load_sources') as load:
            with self.assertRaisesRegex(ValueError, 'NEW'):
                p.export_package(self.root)
            load.assert_not_called()

    def test_export_new_files_and_final_marker_reread(self):
        folder = self.root / 'new_export'
        with patch.object(p,'load_sources',return_value=self.sources):
            manifest = p.export_package(folder)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(len(list(folder.rglob('*.onnx'))), 2)
        self.assertEqual(p.verify_package(folder, self.manifest), self.manifest)


if __name__ == '__main__':
    unittest.main()
