"""Offline alignment, parameter provenance and negative conversion tests."""
import copy
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
import onnx
from onnx import helper, numpy_helper

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
spec = importlib.util.spec_from_file_location('aligned_prepare', ROOT/'prepare_model.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
V1 = ROOT.parent/'b24_full_model_v1/model'
OLD = ROOT.parent/'full_model_v1/generated_model_v1/graphs/model_w8a8_pl.onnx'
ZIP = Path('C:/Users/18016/Documents/xwechat_files/wxid_powvabtz4m4r22_e1fb/msg/file/2026-10/B_FINAL_H576_24L_100K_20261026_v1.zip')


class AlignedModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = p.load_sources(ZIP)
        cls.payloads, cls.manifest = p.assemble(cls.sources)
        cls.generic = onnx.load(V1/'graphs/model_w8a8_pl.onnx')
        cls.fused = onnx.load_model_from_string(cls.payloads['graphs/model_w8a8_pl.onnx'])
        cls.temp = ROOT/'.test_build'
        cls.temp.mkdir(exist_ok=True)

    def test_archive_complete_crc_and_sha(self):
        audit = self.sources['archive_audit']
        self.assertEqual((audit['CRC_files_checked'], audit['manifest_files_SHA_checked']), (768, 765))
        self.assertFalse(audit['package_code_executed'])
        self.assertEqual(audit['pt_handling'], 'OPAQUE_HASH_ONLY_NOT_EXTRACTED')

    def test_routes_are_24_sparse_12_dense_13_ps(self):
        self.assertEqual(self.manifest['routes'], {'PL':36,'PS':13,'PL_dense':12,'PL_sparse24':24})
        self.assertEqual(self.manifest['sparse_layer_indices'], [i for i in range(48) if i%4 in (2,3)])
        self.assertEqual([x['mode'] for x in self.manifest['layers']], [int(i<48 and i%4 in (2,3)) for i in range(49)])

    def test_66_file_closure(self):
        self.assertEqual(set(self.payloads), p.REQUIRED)
        self.assertEqual(len(self.payloads), 66)
        self.assertEqual(self.manifest['schema'], 'opt50_b24_full_model_aligned_bundle_v1')

    def test_preserves_old_versions_and_all_non_graph_payloads(self):
        changed = {'graphs/model_w8a8_pl.onnx', 'inputs/references.json', 'inputs/zero_input.npy'}
        for name, raw in self.payloads.items():
            if name not in changed:
                self.assertEqual(raw, (V1/name).read_bytes(), name)

    def test_catalog_exact_v1_bytes(self):
        self.assertEqual(p.sha(self.payloads['model_catalog.hpp']), '912b7df4ec17e9a8308f0a7e959fa26be41a8c078f95d71283e7ba8bbe3d98f1')

    def test_capacities_and_identity_unchanged(self):
        self.assertEqual([len(self.payloads[n]) for n in ('weights.bin','pl_w_data.bin','pl_w_meta.bin')],
                         [4615680,2654208,331840])
        self.assertEqual(p.sha(self.payloads['reference.onnx']), p.REFERENCE_SHA)

    def test_all_54_nonlinear_tensorprotos_preserved(self):
        before = {t.name:t.SerializeToString() for t in self.generic.graph.initializer if not t.name.startswith('BQL_')}
        after = {t.name:t.SerializeToString() for t in self.fused.graph.initializer}
        self.assertEqual(len(before),54)
        self.assertTrue(all(after[name] == raw for name,raw in before.items()))

    def test_all_147_linear_tensorprotos_preserved(self):
        before = {t.name:t.SerializeToString() for t in self.generic.graph.initializer if t.name.startswith('BQL_')}
        after = {t.name:t.SerializeToString() for t in self.fused.graph.initializer}
        self.assertEqual(len(before),147)
        self.assertTrue(all(after[name] == raw for name,raw in before.items()))

    def test_old_53_named_parameters_all_changed_and_cls_alias_new(self):
        old = p.tensors(onnx.load(OLD)); new = p.tensors(self.fused)
        learned = [t.name for t in self.generic.graph.initializer if not t.name.startswith('BQL_')]
        shared = set(learned)&set(old)
        self.assertEqual(len(shared),53)
        self.assertTrue(all(new[name].tobytes()!=old[name].tobytes() for name in shared))
        self.assertEqual(new['/Expand_output_0'].tobytes(), new['cls_token'].tobytes())
        self.assertNotEqual(new['/Expand_output_0'].tobytes(),old['/Expand_output_0'].tobytes())

    def test_complete_topology_matches_old_with_new_bindings(self):
        old = onnx.load(OLD)
        self.assertEqual(p.sha(OLD.read_bytes()),'8a462d73b5e8bb6544f65c6187dc621b560ae1329520f5556430ff42c97f0dfa')
        self.assertEqual(p.topology_signature(old),p.topology_signature(self.fused))
        self.assertEqual(p.sha(p.json_bytes(p.topology_signature(self.fused))),p.ALIGNED_TOPOLOGY_SHA)

    def test_existing_plugin_op_inventory(self):
        from collections import Counter
        counts = Counter(n.op_type for n in self.fused.graph.node if n.domain=='com.kv260')
        self.assertEqual(counts,{'QuantQKV':12,'AttentionMerge4':12,'Gelu4':12,'QuantLinearPremax':12,'QuantLinear':25})
        self.assertEqual(len(self.fused.graph.node),191)
        self.assertNotIn('QKVLayout4',counts)

    def test_every_layer_keeps_weight_hash_and_order(self):
        q = [n for n in self.fused.graph.node if n.op_type.startswith('Quant') and n.domain=='com.kv260']
        self.assertEqual([p.attributes(n)['layer_index'] for n in q],list(range(49)))
        for n in q:
            i=p.attributes(n)['layer_index']
            self.assertEqual(p.attributes(n)['W_sha256'].decode(),self.manifest['layers'][i]['Wq_sha256'])

    def test_true_qkv_bit_permutation(self):
        z=np.arange(197*576,dtype=np.uint32).reshape(197,576)
        expected=z.reshape(1,197,3,3,64).transpose(2,0,3,1,4)
        parts=np.empty((3,1,3,197,64),np.uint32)
        for part in range(3):
            for head in range(3):
                parts[part,0,head]=z[:,(part*3+head)*64:(part*3+head+1)*64]
        self.assertTrue(np.array_equal(parts,expected))

    def test_true_merge_bit_permutation(self):
        a=np.arange(197*192,dtype=np.uint32).reshape(1,3,197,64)
        expected=a.transpose(0,2,1,3).reshape(1,197,192)
        actual=np.empty((1,197,192),np.uint32)
        for head in range(3):actual[0,:,64*head:64*(head+1)]=a[0,head]
        self.assertTrue(np.array_equal(actual,expected))

    def test_onnx_expand_uses_multidirectional_broadcast(self):
        folded,arrays,_=p.constant_fold(self.generic)
        self.assertEqual(arrays['/Expand_output_0'].shape,(1,1,192))
        self.assertEqual(arrays['/Expand_output_0'].tobytes(),arrays['cls_token'].tobytes())

    def test_three_actual_four_thread_comparisons(self):
        proof=self.manifest['verification']['full_model_CPU']
        self.assertEqual(proof['ORT'],{'intra':4,'inter':1,'sequential':True,'optimization':'OFF','spinning':0})
        self.assertEqual([x['sample_id'] for x in proof['samples']],['dev0000','dev4499','zero'])
        self.assertEqual(sum(x['logits'] for x in proof['samples']),3000)
        self.assertTrue(all(x['fused4_bitwise_mismatches']==0 for x in proof['samples']))
        self.assertEqual(proof['B_saved_logits'],'THREAD1_PROVENANCE_ONLY')
        self.assertIn('not native ARM',proof['scope'])

    def test_thread_count_differences_not_hidden(self):
        proof=self.manifest['verification']['full_model_CPU']
        self.assertTrue(all('reference4_vs_reference1_bitwise_mismatches' in x and
                            'reference4_vs_reference1_max_abs' in x for x in proof['samples']))
        self.assertTrue(all(x['reference4_vs_reference1_bitwise_mismatches']>0 for x in proof['samples'][:2]))

    def test_zero_input_and_saved_b_inputs_unchanged(self):
        zero=np.load(io.BytesIO(self.payloads['inputs/zero_input.npy']),allow_pickle=False)
        self.assertEqual(zero.dtype,np.float32);self.assertEqual(zero.shape,(1,3,224,224));self.assertFalse(zero.any())
        for sid in p.SAMPLES:
            self.assertEqual(self.payloads['inputs/'+sid+'_input.npy'],(V1/'inputs'/f'{sid}_input.npy').read_bytes())

    def test_no_hardware_or_quality_claim(self):
        self.assertFalse(self.manifest['hardware_executed'])
        self.assertFalse(self.manifest['accuracy_verified'])
        self.assertFalse(self.manifest['performance_verified'])

    def altered(self,name,fn):
        graph=copy.deepcopy(self.generic)
        node=next(n for n in graph.graph.node if n.name==name)
        fn(node)
        return graph.SerializeToString()

    def test_reject_qkv_transpose_change(self):
        raw=self.altered('/blocks/blocks.0/attn/Transpose',lambda n:n.attribute[0].ints.__setitem__(0,0))
        with self.assertRaises(ValueError):p.fuse_graph(raw)

    def test_reject_merge_transpose_change(self):
        raw=self.altered('/blocks/blocks.0/attn/Transpose_2',lambda n:n.attribute[0].ints.__setitem__(0,1))
        with self.assertRaises(ValueError):p.fuse_graph(raw)

    def test_reject_gelu_constant_change(self):
        def replace(n):n.attribute[0].t.CopyFrom(numpy_helper.from_array(np.array(2,np.float32)))
        raw=self.altered('/blocks/blocks.0/mlp/act/Constant',replace)
        with self.assertRaises(ValueError):p.fuse_graph(raw)

    def test_reject_reassociated_gelu_multiplication(self):
        raw=self.altered('/blocks/blocks.0/mlp/act/Mul',lambda n:n.input.reverse())
        with self.assertRaises(ValueError):p.fuse_graph(raw)

    def test_reject_escaped_qkv_intermediate(self):
        graph=copy.deepcopy(self.generic)
        graph.graph.output.append(helper.make_tensor_value_info('/blocks/blocks.0/attn/qkv/Add_output_0',1,[1,197,576]))
        with self.assertRaises((ValueError,onnx.checker.ValidationError)):p.fuse_graph(graph.SerializeToString())

    def test_reject_unknown_fused_operator(self):
        graph=copy.deepcopy(self.fused)
        next(n for n in graph.graph.node if n.domain=='com.kv260').op_type='Unknown'
        with self.assertRaises(ValueError):p.integer_expansion(graph.SerializeToString())

    def test_reject_wrong_fused_weight_numerically(self):
        graph=copy.deepcopy(self.fused)
        t=next(t for t in graph.graph.initializer if t.name=='BQL_0_Wq')
        a=numpy_helper.to_array(t).copy();a[:]=0;t.CopyFrom(numpy_helper.from_array(a,t.name))
        with self.assertRaises(ValueError):p.cpu_equivalence(self.payloads['reference.onnx'],graph.SerializeToString(),self.sources['files'])

    def test_reject_existing_output(self):
        with self.assertRaises(ValueError):p.export_package(ZIP,ROOT/'model')

    def test_reject_wrong_archive_before_output_creation(self):
        with tempfile.TemporaryDirectory(dir=self.temp) as directory:
            r=Path(directory);(r/'bad.zip').write_bytes(b'bad')
            with self.assertRaises(ValueError):p.export_package(r/'bad.zip',r/'out')
            self.assertFalse((r/'out').exists())

    def test_exported_artifacts_match_independent_reproduction(self):
        for name,raw in self.payloads.items():self.assertEqual(raw,(ROOT/'model'/name).read_bytes(),name)
        self.assertEqual(self.manifest,p.json_read((ROOT/'model/manifest.json').read_bytes()))
        p.verify_package(ROOT/'model',self.manifest)


if __name__=='__main__':unittest.main()
