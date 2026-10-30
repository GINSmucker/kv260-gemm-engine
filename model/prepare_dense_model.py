"""Export the original H576 Dense W8A8 model for OPT50 mode0; offline only."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import numpy as np
import onnx
from onnx import numpy_helper
import onnxruntime as ort

HERE=Path(__file__).absolute().parent
PROJECT=HERE.parents[1]
BASE=PROJECT/'h576_sparse24/inputs/baseline/h576_w8.onnx'
ARCHIVE=PROJECT/'h576_sparse24/inputs/h576-runtime-public-ui.tar.gz'
SPARSE=HERE.parent/'b24_batch_acceptance_resident_v4_reboot_r1'
MODEL_SHA='f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'
ARCHIVE_SHA='c29a006fe472fd2bfb545d7bbf77f34a5090ff9315043cdd7e5830e518d61f7c'
GRAPH_SHA='0a695c9813a503e84a7efb728476b77aa4ee0c53935810ea43b9d2a52764f23a'

def need(ok,why):
    if not ok:raise ValueError(why)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def pin(raw):return {'bytes':len(raw),'sha256':sha(raw)}
def encoded(value):return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
def session(raw):
    o=ort.SessionOptions();o.intra_op_num_threads=4;o.inter_op_num_threads=1
    o.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL;o.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    o.log_severity_level=3
    for group in ('intra_op','inter_op'):o.add_session_config_entry('session.'+group+'.allow_spinning','0')
    return ort.InferenceSession(raw,o,providers=['CPUExecutionProvider'])

def header(records,canonical,data,meta):
    lines=['#pragma once','#include <array>','#include <cstddef>','#include <cstdint>','namespace opt50_model {',
        'struct Layer { uint32_t m,k,n,mode; bool pl; size_t canonical_offset,data_offset,data_bytes,meta_offset,meta_bytes; const char *w_sha,*scale_sha,*bias_sha; };',
        'inline constexpr size_t weights_bytes=4615680, data_bytes=3981312, meta_bytes=64;',
        'inline constexpr size_t max_x_bytes=113472, max_y_bytes=453888;',
        'inline constexpr const char *weights_sha="%s", *data_sha="%s", *meta_sha="%s", *reference_sha="%s";'%(sha(canonical),sha(data),sha(meta),MODEL_SHA),
        'inline constexpr const char *source_model_sha="%s";'%MODEL_SHA,'inline constexpr std::array<Layer,49> layers={{']
    for r in records:
        values=[str(r[k]) for k in ('m','k','n','mode')]+['true' if r['route']=='PL' else 'false']
        values += [str(r[k]) for k in ('canonical_offset','w_data_offset','w_data_bytes','w_meta_offset','w_meta_bytes')]
        values += ['"'+r[k]+'"' for k in ('Wq_sha256','sw_sha256','bias_sha256')]
        lines.append('    {'+','.join(values)+'},')
    return ('\n'.join(lines+['}};','} // namespace opt50_model',''])).encode('ascii')

def prepare():
    need(np.__version__=='1.26.4' and ort.__version__=='1.22.1','fixed NumPy/ORT required')
    out=HERE/'software/model';need(not out.exists(),'exclusive new Dense model directory required')
    reference=BASE.read_bytes();archive=ARCHIVE.read_bytes()
    need(len(reference)==19628633 and sha(reference)==MODEL_SHA and sha(archive)==ARCHIVE_SHA,'original Dense source identity differs')
    prefix='v5_h576_ui_20261017_01/'
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as t:
        def member(name):
            m=t.getmember(prefix+name);need(m.isfile() and not m.issym() and not m.islnk() and m.size<=32*1024*1024,'ordinary bounded public member required')
            return t.extractfile(m).read()
        graph=member('graphs/model_w8a8_pl.onnx');layers=json.loads(member('bundle/layers.json'))
        weights=[member('bundle/weights/%02d_Wq.bin'%i) for i in range(49)]
    need(sha(graph)==GRAPH_SHA and len(layers)==49,'fixed original fused Dense graph required')
    model=onnx.load_model_from_string(graph);cpu=onnx.load_model_from_string(reference)
    onnx.checker.check_model(model);onnx.checker.check_model(cpu)
    arrays={t.name:numpy_helper.to_array(t) for t in model.graph.initializer}
    cpu_arrays={t.name:numpy_helper.to_array(t) for t in cpu.graph.initializer}
    records=[];packed=bytearray();canonical=bytearray();bundle=[];files={}
    for i,(layer,w) in enumerate(zip(layers,weights)):
        m,k,n=(1,192,1000) if i==48 else ((197,192,576),(197,192,192),(197,192,576),(197,576,192))[i%4]
        name='BQL_%d_'%i;pl=i<48 and i%4!=1
        need(layer['layer_index']==i and len(w)==k*n and arrays[name+'Wq'].dtype==np.int8 and arrays[name+'Wq'].shape==(k,n),'49 fixed Dense shapes/weights required')
        need(w==arrays[name+'Wq'].tobytes() and sha(w)==layer['Wq']['sha256'],'public graph/logical weight mismatch')
        need(np.array_equal(cpu_arrays[name+'Wf'],arrays[name+'Wq'].astype(np.float32)) and
             cpu_arrays[name+'sf'].tobytes()==arrays[name+'sw'].tobytes() and
             cpu_arrays[name+'bf'].tobytes()==arrays[name+'bias'].tobytes(),'Dense fused parameters differ from original CPU graph')
        record={'layer_index':i,'layer_id':layer['layer_id'],'family':layer['family'],'m':m,'k':k,'n':n,'route':'PL' if pl else 'PS','mode':0,
            'canonical_offset':len(canonical),'w_data_offset':len(packed) if pl else 0,'w_data_bytes':len(w) if pl else 0,
            'w_meta_offset':0,'w_meta_bytes':16 if pl else 0,'Wq_sha256':sha(w),'sw_sha256':sha(arrays[name+'sw'].tobytes()),
            'bias_sha256':sha(arrays[name+'bias'].tobytes())}
        records.append(record);canonical.extend(w)
        if pl:need(len(packed)%64==0,'aligned Dense weight offset');packed.extend(w)
        row=copy.deepcopy(layer);row.update(mode='dense_w8a8',opt50_mode=0,opt50_route=record['route'])
        row['numeric_identity']={'Wq':record['Wq_sha256'],'sw':record['sw_sha256'],'bias':record['bias_sha256']}
        bundle.append(row);files['bundle/weights/%02d_Wq.bin'%i]=w
    need(len(canonical)==4615680 and len(packed)==3981312,'Dense logical and PL packed capacities')
    meta=bytes(64)
    # Reuse the existing standard ONNX expansion implementation as an independent
    # mathematical oracle. It runs no custom library, XRT, owner or device.
    oracle_path=HERE.parent/'b24_full_model_aligned_v1/prepare_model.py'
    spec=importlib.util.spec_from_file_location('_dense_standard_expansion',oracle_path)
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    expansion=oracle.integer_expansion(graph);reference_session=session(reference);expanded_session=session(expansion)
    checks=[]
    for sid in ('dev0000','dev4499','zero'):
        input_raw=(SPARSE/'model/inputs'/('zero_input.npy' if sid=='zero' else sid+'_input.npy')).read_bytes()
        image=np.load(io.BytesIO(input_raw),allow_pickle=False)
        expected=reference_session.run(None,{'image':image})[0];actual=expanded_session.run(None,{'image':image})[0]
        mismatch=int(np.count_nonzero(expected.view(np.uint32)!=actual.view(np.uint32)))
        need(expected.shape==actual.shape==(1,1000) and mismatch==0,'original Dense fused expansion differs at '+sid)
        files['inputs/'+('zero_input.npy' if sid=='zero' else sid+'_input.npy')]=input_raw
        if sid!='zero':
            stream=io.BytesIO();np.save(stream,expected,allow_pickle=False);files['inputs/'+sid+'_logits.npy']=stream.getvalue()
        checks.append({'sample_id':sid,'input_raw_sha256':sha(image.tobytes()),'logits':1000,'fused4_bitwise_mismatches':mismatch,
            'reference4_raw_logits_sha256':sha(expected.tobytes()),'top5':np.argsort(-expected[0],kind='stable')[:5].tolist()})
    certificate={'status':'PASS_DENSE_ORIGINAL_CPU4_AND_FUSED_INTEGER_EXPANSION','samples':checks,'numpy_version':np.__version__,'ORT_version':ort.__version__,
        'ORT':{'intra':4,'inter':1,'sequential':True,'optimization':'OFF','spinning':0},
        'local_affinity':'NOT_APPLIED_WINDOWS_TOPOLOGY_DIFFERS','scope':'CPU mathematical check only; no ARM plugin or PL execution'}
    files['inputs/references.json']=encoded(certificate)
    files['bundle/layers.json']=encoded(bundle)
    files['bundle/bundle_manifest.json']=encoded({'schema':'kv5_bundle_manifest_v1','source_semantic_sha256':MODEL_SHA,
        'files':{'layers.json':pin(files['bundle/layers.json'])}|{'weights/%02d_Wq.bin'%i:pin(w) for i,w in enumerate(weights)}})
    files.update({'weights.bin':bytes(canonical),'pl_w_data.bin':bytes(packed),'pl_w_meta.bin':meta,'reference.onnx':reference,
        'graphs/model_w8a8_pl.onnx':graph,'model_catalog.hpp':header(records,canonical,packed,meta)})
    files['source/DENSE_SOURCE.json']=encoded({'schema':'opt50_original_dense_provenance_v1','reference':pin(reference),'archive_sha256':ARCHIVE_SHA,
        'fused_graph':pin(graph),'fused_expansion_implementation':pin(oracle_path.read_bytes()),'fused_integer_expansion':pin(expansion),
        'original_archive_graph_modified':False,'weights_pruned':False,'hardware_accessed':False})
    manifest={'schema':'opt50_original_dense_aligned_bundle_v1','status':'SOFTWARE_PREPARED_NOT_BOARD_TESTED','format':'OPT50_DENSE36_SPLIT128_V1',
        'hardware_executed':False,'accuracy_verified':False,'performance_verified':False,'source_model_sha256':MODEL_SHA,'reference_sha256':MODEL_SHA,
        'logical_weights_sha256':sha(canonical),'pl_data_sha256':sha(packed),'pl_meta_sha256':sha(meta),'custom_graph_sha256':sha(graph),
        'routes':{'PL':36,'PS':13,'PL_dense':36,'PL_sparse24':0},'sparse_layer_indices':[],'layers':records,
        'source_pins':{'original_reference':pin(reference),'public_archive_sha256':ARCHIVE_SHA},'verification':{'full_model_CPU':certificate},
        'files':{n:pin(raw) for n,raw in sorted(files.items())}}
    out.mkdir(parents=True)
    for name,raw in files.items():
        path=out/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f:f.write(raw)
    with (out/'manifest.json').open('xb') as f:f.write(encoded(manifest))
    return {'status':'ORIGINAL_DENSE_MODEL_PREPARED_OFFLINE_ONLY','manifest':pin((out/'manifest.json').read_bytes()),'files':len(files),'checks':checks}

if __name__=='__main__':print(json.dumps(prepare(),indent=2))
