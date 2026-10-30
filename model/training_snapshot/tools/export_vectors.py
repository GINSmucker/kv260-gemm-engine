"""Regenerate real propagated W8A8 layer cases and SPLIT128 payloads from this release."""
from pathlib import Path
import argparse,copy,csv,hashlib,shutil,sys
sys.dont_write_bytecode=True
import numpy as np
import onnx
from onnx import helper,numpy_helper,TensorProto
from release_common import ROOT,MODEL,read,save,sha,session,preprocess,quantize_rows
import axi_codec as codec

def export(root):
    root=Path(root).resolve();model=root/'models/final'
    assert not (root/'cases/index.json').exists(),'Do not overwrite an exported corpus'
    original=read(model/'layers.json');assert len(original)==49
    fp=onnx.load(model/'model_fp32.onnx');q=onnx.load(model/'model_w8a8.onnx')
    fa={v.name:numpy_helper.to_array(v).copy() for v in fp.graph.initializer}
    qa={v.name:numpy_helper.to_array(v).copy() for v in q.graph.initializer}
    linear_names={l['layer_id']+s for l in original for s in ['.weight','.bias']}
    np.savez_compressed(root/'parameters/all_fp32.npz',**fa)
    np.savez_compressed(root/'parameters/nonlinear_fp32.npz',**{k:v for k,v in fa.items() if k not in linear_names})
    save(root/'parameters/parameter_index.json',{'native_layout':'PyTorch/ONNX [N,K] for Linear weights',
        'logical_linear_layout':'weights/layer_XX.npz uses W[K,N]',
        'all_fp32_tensor_count':len(fa),'nonlinear_fp32_tensor_count':len(set(fa)-linear_names),
        'parameters':{k:{'shape':list(v.shape),'dtype':str(v.dtype),'bytes':v.nbytes,
            'array_sha256':hashlib.sha256(v.tobytes()).hexdigest(),'linear_tensor':k in linear_names} for k,v in fa.items()}})
    layers=[];requests={};ops={n.name:n for n in q.graph.node}
    for old in original:
        i=old['layer_index'];m,k,n=old['shape_MKN'];sparse=bool(old['weights'])
        source=old['weights'] or old['dense_weights'];z=np.load(model/source)
        w=z['Wq'];assert w.dtype==np.int8 and w.shape==(k,n)
        assert np.array_equal(w.astype(np.float32),qa[f'BQL_{i}_Wf'])
        folder=root/f'weights_axi/layer_{i:02d}';folder.mkdir(parents=True,exist_ok=True)
        (folder/'W_dense_data.bin').write_bytes(codec.pack_int8(w));(folder/'W_dense_meta.bin').write_bytes(bytes(16))
        refs={'dense_data':(folder/'W_dense_data.bin').relative_to(root).as_posix(),
              'dense_meta':(folder/'W_dense_meta.bin').relative_to(root).as_posix()}
        if sparse:
            values=z['values'];pos=z['positions'];data,meta=codec.pack_sparse(values,pos)
            assert np.array_equal(codec.unpack_sparse(data,meta,k,n)[2],w)
            (folder/'W_sparse_data.bin').write_bytes(data);(folder/'W_sparse_meta.bin').write_bytes(meta)
            refs.update(sparse_data=(folder/'W_sparse_data.bin').relative_to(root).as_posix(),
                        sparse_meta=(folder/'W_sparse_meta.bin').relative_to(root).as_posix())
        shape=[1,k] if i==48 else [1,m,k];outshape=[1,n] if i==48 else [1,m,n]
        sxshape=[1,1] if i==48 else [1,m,1]
        pre=ops[f'BQL_{i}_xd'].input[0];post=ops[f'BQL_{i}_output'].output[0]
        for name,dtype,dims in [(pre,TensorProto.FLOAT,shape),(f'BQL_{i}_xq',TensorProto.FLOAT,shape),
            (f'BQL_{i}_sx',TensorProto.FLOAT,sxshape),(f'BQL_{i}_i32',TensorProto.INT32,outshape),
            (post,TensorProto.FLOAT,outshape)]:
            requests[name]=helper.make_tensor_value_info(name,dtype,dims)
        layers.append({'index':i,'layer_id':old['layer_id'],'family':old['family'],
            'route':old['original_route'],'mode':1 if sparse else 0,'M':m,'K':k,'N':n,
            'logical_weights':'models/final/'+source,'source_weight_sha256':sha(model/source),
            'weights':refs,'activation_fp32':pre,'quantized_activation':f'BQL_{i}_xq',
            'activation_scale':f'BQL_{i}_sx','integer_output':f'BQL_{i}_i32','float_output':post})
        z.close()
    assert sum(l['mode']==1 for l in layers)==24 and sum(l['route']=='PL' for l in layers)==36
    save(root/'parameters/layers.json',layers)
    # DDR aggregate, not an on-chip residency or platform address declaration.
    blob=bytearray();segments=[]
    for l in layers:
        if l['route']!='PL':continue
        for role,key in [('W_data','sparse_data' if l['mode'] else 'dense_data'),('W_meta','sparse_meta' if l['mode'] else 'dense_meta')]:
            blob.extend(bytes((-len(blob))%64));offset=len(blob);raw=(root/l['weights'][key]).read_bytes();blob.extend(raw)
            segments.append({'layer_index':l['index'],'mode':l['mode'],'role':role,'offset':offset,
                'allocated_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'file':l['weights'][key]})
    (root/'weights_axi/pl_weights_bo.bin').write_bytes(blob)
    save(root/'weights_axi/pl_weights_bo.json',{'format_id':codec.FORMAT_ID,'base_alignment_bytes':64,
        'address_bound':False,'storage':'DDR aggregate; no on-chip residency claim','segments':segments,'bytes':len(blob)})
    traced=copy.deepcopy(q);existing={v.name for v in traced.graph.output}
    traced.graph.output.extend(v for name,v in requests.items() if name not in existing)
    onnx.checker.check_model(traced)
    import onnxruntime as ort
    opts=ort.SessionOptions();opts.intra_op_num_threads=1;opts.inter_op_num_threads=1
    opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL;opts.log_severity_level=3
    engine=ort.InferenceSession(traced.SerializeToString(),opts,providers=['CPUExecutionProvider'])
    baseline=session(model/'model_w8a8.onnx');samples=read(root/'samples/samples.json');cases=[];trace_receipts=[]
    for sample in samples:
        image=root/sample['image'];x=preprocess(image)
        assert hashlib.sha256(x.tobytes()).hexdigest()==sample['preprocessed_sha256']
        vals=engine.run(None,{engine.get_inputs()[0].name:x});out=dict(zip([v.name for v in engine.get_outputs()],vals))
        logits=baseline.run(None,{baseline.get_inputs()[0].name:x})[0]
        assert np.array_equal(out['logits'],logits),'Instrumentation changed classification'
        np.save(root/f"samples/{sample['id']}_input.npy",x);np.save(root/f"samples/{sample['id']}_logits.npy",logits)
        sample['top5']=np.argsort(-logits[0],kind='stable')[:5].tolist();sample['logits_sha256']=sha(root/f"samples/{sample['id']}_logits.npy")
        receipt=[]
        for l in layers:
            i=l['index'];k,n=l['K'],l['N'];folder=root/f"cases/{sample['id']}/layer_{i:02d}"
            folder.mkdir(parents=True,exist_ok=True)
            xf=out[l['activation_fp32']].reshape(-1,k);qf=out[l['quantized_activation']].reshape(-1,k)
            assert np.all(qf==np.rint(qf)) and np.all((qf>=-127)&(qf<=127))
            xq=qf.astype(np.int8);sx=out[l['activation_scale']].reshape(-1,1)
            own_q,own_sx=quantize_rows(xf);assert np.array_equal(xq,own_q) and np.array_equal(sx,own_sx)
            with np.load(root/l['logical_weights']) as z:w=z['Wq'];sw=z['sw'];bias=z['bias']
            golden=codec.golden_dense_int32(xq,w);actual=out[l['integer_output']].reshape(-1,n)
            assert np.array_equal(golden,actual),'Original full-K golden differs from captured ONNX integer output'
            yf=((golden.astype(np.float32)*sx)*sw)+bias
            post=out[l['float_output']].reshape(-1,n)
            assert np.allclose(yf,post,atol=1e-5,rtol=2e-6)
            np.savez_compressed(folder/'tensors.npz',Xq=xq,Wq=w,Y_ref_int32=golden,
                scale_x=sx,scale_w=sw,bias=bias,X_fp32=xf,Y_fp32=post,
                input_shape_before_flatten=np.array(out[l['quantized_activation']].shape,np.int64))
            (folder/'X.bin').write_bytes(codec.pack_int8(xq));(folder/'Y_golden.bin').write_bytes(codec.pack_int32(golden))
            cases_modes=[]
            for mode in ([0,1] if l['mode'] else [0]):
                refs={'X':(folder/'X.bin').relative_to(root).as_posix(),
                    'W_data':l['weights']['sparse_data' if mode else 'dense_data'],
                    'W_meta':l['weights']['sparse_meta' if mode else 'dense_meta'],
                    'Y':(folder/'Y_golden.bin').relative_to(root).as_posix()}
                observed=codec.emulate_axi((root/refs['X']).read_bytes(),(root/refs['W_data']).read_bytes(),
                    (root/refs['W_meta']).read_bytes() if mode else object(),xq.shape[0],k,n,mode)
                assert np.array_equal(observed,golden)
                cases_modes.append({'mode':mode,'buffers':refs,'layout':codec.buffer_layout(xq.shape[0],k,n,mode)})
            manifest={'schema_version':'B_GEMM_REAL_LAYER_CASE_V1','case_id':f"{sample['id']}_L{i:02d}",
                'format_id':codec.FORMAT_ID,'layer_index':i,'layer_id':l['layer_id'],'route':l['route'],
                'M':xq.shape[0],'K':k,'N':n,'mode':l['mode'],'model_w8a8_sha256':sha(model/'model_w8a8.onnx'),
                'sample_id':sample['id'],'sample_image_sha256':sample['image_sha256'],
                'activation_scope':'ACTUAL propagated activations from the final W8A8 ONNX',
                'integer_oracle':'Original Xq and full logical Wq, INT64 full-K dot, range-check then INT32',
                'flattening':'C order collapse leading dimensions; K is last axis; restore original leading dimensions',
                'byte_order':'little-endian','postprocess':'float32(float32(float32(Yi32)*scale_x)*scale_w)+bias',
                'dense_comparison':'Same pruned Wq; never the unpruned original Dense weights',
                'tensors':{p:{'shape':list(a.shape),'dtype':str(a.dtype)} for p,a in [('Xq',xq),('Wq',w),('Y_ref_int32',golden),('scale_x',sx),('scale_w',sw),('bias',bias)]},
                'files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()},
                'calls':cases_modes,'hardware_accessed':False}
            save(folder/'manifest.json',manifest)
            cases.append({'manifest':(folder/'manifest.json').relative_to(root).as_posix(),'case_id':manifest['case_id'],
                'layer_index':i,'route':l['route'],'calls':cases_modes})
            receipt.append({'layer_index':i,'integer_elements':golden.size,'integer_mismatches':0,
                'postprocess_max_abs_error':float(np.max(np.abs(yf-post)))})
        trace_receipts.append({'sample_id':sample['id'],'instrumented_logits_bitwise_equal':True,'layers':receipt})
        print('REAL TRACE EXPORTED',sample['id'],'49layers',flush=True)
    save(root/'samples/samples.json',samples)
    save(root/'cases/index.json',{'schema_version':'B_GEMM_CASE_INDEX_V1','real_samples':len(samples),'cases':cases})
    cols=['case_id','route','mode','M','K','N','X','W_data','W_meta','Y']
    all_rows=[]
    for c in cases:
        m=read(root/c['manifest'])
        for call in c['calls']:
            all_rows.append([c['case_id'],c['route'],call['mode'],m['M'],m['K'],m['N'],
                *[call['buffers'][key] for key in ['X','W_data','W_meta','Y']]])
    for name,vals in [('all_index.tsv',all_rows),('pl_index.tsv',[v for v in all_rows if v[1]=='PL'])]:
        with (root/'cases'/name).open('w',encoding='utf-8',newline='') as f:
            writer=csv.writer(f,delimiter='\t',lineterminator='\n');writer.writerow(cols);writer.writerows(vals)
    save(root/'validation/real_trace_export.json',{'status':'PASS_SOFTWARE_REAL_PROPAGATED_TRACES_AXI_AND_GOLDENS',
        'samples':trace_receipts,'linear_layers':49,'sparse_layers':24,'native_calls':len(all_rows),
        'same_pruned_dense_vs_sparse_verified':True,'synthetic_codec_checks':codec.synthetic_checks(),
        'hardware_accessed':False,'HLS_RTL_board_validation':'NOT_RUN'})
    return {'samples':len(samples),'layers':49,'case_directories':len(cases),'mode_calls':len(all_rows)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args();print(export(a.root))
