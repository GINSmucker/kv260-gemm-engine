"""Read-only release verification. Run with python -B; user output belongs in work/."""
from pathlib import Path
import argparse,csv,hashlib,io,json,sys,zipfile
sys.dont_write_bytecode=True
import numpy as np
import onnx
from onnx import numpy_helper
from release_common import ROOT,read,inside,sha,preprocess,quantize_rows,session
import axi_codec as c

def ensure(ok,message):
    if not ok:raise ValueError(message)
def manifest_check(root):
    ensure(sha(root/'MANIFEST.json')==(root/'MANIFEST.sha256').read_text().split()[0],'Manifest digest')
    expected=read(root/'MANIFEST.json')['files']
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).parts[0]!='work'}-{'MANIFEST.json','MANIFEST.sha256'}
    ensure(actual==set(expected),'Unexpected/missing immutable files: '+str(sorted(actual^set(expected))[:10]))
    total=0
    for rel,v in expected.items():
        p=inside(root,rel);ensure(not p.is_symlink(),'Symlink: '+rel)
        ensure(p.stat().st_size==v['bytes'] and sha(p)==v['sha256'],'File changed: '+rel);total+=v['bytes']
    return {'files':len(expected),'bytes':total,'sha256_all_pass':True}

def parameters_check(root):
    release=read(root/'RELEASE.json')
    for key,rel in release['models'].items():ensure(sha(inside(root,rel))==release['source_model_sha256'][key],'Frozen model '+key)
    fp=onnx.load(root/'models/final/model_fp32.onnx');q=onnx.load(root/'models/final/model_w8a8.onnx')
    onnx.checker.check_model(fp);onnx.checker.check_model(q)
    f={v.name:numpy_helper.to_array(v) for v in fp.graph.initializer};qs={v.name:numpy_helper.to_array(v) for v in q.graph.initializer}
    idx=read(root/'parameters/parameter_index.json');layers=read(root/'parameters/layers.json')
    ensure(len(f)==152 and len(layers)==49,'Full parameter/Linear count')
    with np.load(root/'parameters/all_fp32.npz') as a,np.load(root/'parameters/nonlinear_fp32.npz') as b:
        ensure(set(a.files)==set(f) and len(b.files)==54,'Full/nonlinear archive members')
        for name,value in f.items():
            ensure(np.array_equal(value,a[name]),'Full FP32 archive '+name)
            ensure(hashlib.sha256(value.tobytes()).hexdigest()==idx['parameters'][name]['array_sha256'],'Tensor SHA '+name)
            if not idx['parameters'][name]['linear_tensor']:ensure(np.array_equal(value,b[name]),'Nonlinear tensor '+name)
            if name in qs:ensure(np.array_equal(value,qs[name]),'Shared FP32/W8 tensor '+name)
    sparse=[]
    for l in layers:
        i=l['index'];k,n=l['K'],l['N'];name=l['layer_id'];path=inside(root,l['logical_weights'])
        ensure(sha(path)==l['source_weight_sha256'],'Logical weights identity')
        with np.load(path) as z:
            w=z['Wq'];wf=f[name+'.weight'].T;absmax=np.max(np.abs(wf),axis=0,keepdims=True)
            sw=np.where(absmax==0,np.float32(1),np.maximum(absmax/np.float32(127),np.float32(np.finfo(np.float32).tiny))).astype(np.float32)
            derived=np.clip(np.rint(wf/sw),-127,127).astype(np.int8)
            ensure(np.array_equal(w,derived) and np.array_equal(z['sw'],sw),'Requantized weights/scales')
            ensure(np.array_equal(qs[f'BQL_{i}_Wf'],w.astype(np.float32)) and np.array_equal(qs[f'BQL_{i}_sf'],sw.ravel()),'W8 graph quantized weights')
            ensure(np.array_equal(z['bias'],f[name+'.bias']) and np.array_equal(qs[f'BQL_{i}_bf'],z['bias']),'Bias')
            ensure(np.array_equal(c.unpack_int8(inside(root,l['weights']['dense_data']).read_bytes(),k,n),w),'Dense physical W')
            ensure(inside(root,l['weights']['dense_meta']).read_bytes()==bytes(16),'Dense dummy metadata')
            if l['mode']==1:
                sparse.append(i);mask=z['mask'];ensure(mask.dtype==np.bool_ and np.all(mask.reshape(k//4,4,n).sum(1)==2),'2:4 mask')
                ensure(np.all(w[~mask]==0) and np.all(wf[~mask]==0),'Pruned coordinates zero')
                vs,ps,uw=c.unpack_sparse(inside(root,l['weights']['sparse_data']).read_bytes(),inside(root,l['weights']['sparse_meta']).read_bytes(),k,n)
                ensure(np.array_equal(vs,z['values']) and np.array_equal(ps,z['positions']) and np.array_equal(uw,w),'Sparse payload/value/position consistency')
                reconstructed=np.zeros_like(mask)
                for slot in (0,1):reconstructed[4*np.arange(k//4)[:,None]+ps[...,slot],np.arange(n)[None,:]]=True
                ensure(np.array_equal(reconstructed,mask),'Mask includes kept zero slots')
    ensure(sparse==release['sparse_layer_indices'] and len(sparse)==24,'Frozen sparse layer list')
    bo=read(root/'weights_axi/pl_weights_bo.json');blob=(root/'weights_axi/pl_weights_bo.bin').read_bytes();previous=0
    ensure(len(bo['segments'])==72 and len(blob)==bo['bytes'],'PL aggregate size')
    for seg in bo['segments']:
        start=seg['offset'];end=start+seg['allocated_bytes']
        ensure(start%64==0 and start>=previous and not any(blob[previous:start]),'BO alignment/zero inter-segment padding')
        ensure(blob[start:end]==inside(root,seg['file']).read_bytes(),'BO segment bytes');previous=end
    ensure(previous==len(blob),'BO final extent')
    return {'fp32_tensors':152,'nonlinear_tensors':54,'linear_layers':49,'sparse_layers':24,'PL_layers':36,'PS_layers':13,'physical_weights_and_BO':'PASS'}

def cases_check(root):
    index=read(root/'cases/index.json');ensure(len(index['cases'])==98,'98 real cases expected');calls=0
    for item in index['cases']:
        p=inside(root,item['manifest']);m=read(p);folder=p.parent
        for f,v in m['files'].items():ensure(sha(folder/f)==v['sha256'] and (folder/f).stat().st_size==v['bytes'],'Case file '+f)
        with np.load(folder/'tensors.npz') as z:
            x,w,y=z['Xq'],z['Wq'],z['Y_ref_int32'];a,b,cshape=x.shape[0],w.shape[0],w.shape[1]
            ensure((a,b,cshape)==(m['M'],m['K'],m['N']),'Case shape')
            ensure(np.array_equal(c.golden_dense_int32(x,w),y),'Independent full-K INT64 oracle')
            qx,sx=quantize_rows(z['X_fp32']);ensure(np.array_equal(qx,x) and np.array_equal(sx,z['scale_x']),'Real activation quantization')
            ensure(np.allclose(((y.astype(np.float32)*z['scale_x'])*z['scale_w'])+z['bias'],z['Y_fp32'],atol=1e-5,rtol=2e-6),'Postprocess scale/bias order')
            for call in m['calls']:
                buffers=call['buffers'];mode=call['mode'];layout=c.buffer_layout(a,b,cshape,mode)
                ensure(call['layout']==layout,'Physical buffer layout')
                for role,info in layout.items():ensure(inside(root,buffers[role]).stat().st_size==info['allocated_bytes'],'Payload allocation')
                xb=inside(root,buffers['X']).read_bytes();wd=inside(root,buffers['W_data']).read_bytes();wm=inside(root,buffers['W_meta']).read_bytes()
                ensure(np.array_equal(c.unpack_int8(xb,a,b),x),'X bytes')
                ensure(np.array_equal(c.unpack_int32(inside(root,buffers['Y']).read_bytes(),a,cshape),y),'Y little endian bytes')
                ensure(np.array_equal(c.emulate_axi(xb,wd,wm,a,b,cshape,mode),y),'AXI selected-X MAC output');calls+=1
    ensure(calls==146,'146 mode calls expected');return {'real_images':2,'cases':98,'mode_calls':calls,'integer_exact':True}

def data_check(root,deep=False):
    archives=read(root/'datasets/archives.json');seen=set();result={}
    for role,info in archives.items():
        records=list(csv.DictReader(io.StringIO(inside(root,info['labels_path']).read_text(encoding='utf-8-sig'))))
        ensure(len(records)==info['images'],'Dataset count');ensure(sha(inside(root,info['labels_path']))==info['labels_sha256'],'Labels SHA')
        hashes={r['image_sha256'] for r in records};ensure(len(hashes)==len(records) and not(seen&hashes),'Duplicate image bytes across roles');seen|=hashes
        archive=inside(root,info['path'])
        if info.get('packaged',True) is False:
            ensure(not archive.exists(),'Lean profile unexpectedly contains image archive')
            result[role]={'images_in_historical_run':len(records),'labels_SHA256':'PASS','archive_status':'OMITTED_FROM_LEAN_EDITION','per_image_SHA256':'NOT_RUN; images are external'}
            continue
        ensure(sha(archive)==info['sha256'],'Data archive SHA')
        with zipfile.ZipFile(archive) as z:
            names=z.namelist();ensure(len(names)==len(records)+1 and len(names)==len(set(names)),'Archive member count')
            ensure(set(names)=={r['image'] for r in records}|{'labels.csv'},'Exact archive member paths')
            ensure(z.read('labels.csv')==inside(root,info['labels_path']).read_bytes(),'Archive labels bytes')
            if deep:
                for r in records:ensure(hashlib.sha256(z.read(r['image'])).hexdigest()==r['image_sha256'],'Archived JPEG SHA')
        result[role]={'images':len(records),'archive_SHA256':'PASS','per_image_SHA256':'PASS' if deep else 'Recorded creation receipt; use --deep-data to recheck'}
    return result

def runtime_check(root):
    engine=session(root/'models/final/model_w8a8.onnx')
    for s in read(root/'samples/samples.json'):
        x=preprocess(inside(root,s['image']));ensure(np.array_equal(x,np.load(root/f"samples/{s['id']}_input.npy")),'Portable preprocess')
        ensure(np.array_equal(engine.run(None,{engine.get_inputs()[0].name:x})[0],np.load(root/f"samples/{s['id']}_logits.npy")),'Classification logits')
    from demo_layer_replacement import test
    return test(root)

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--deep-data',action='store_true');p.add_argument('--runtime',action='store_true')
    a=p.parse_args();root=a.root.resolve();result={'status':'PASS','hardware_accessed':False,'board_validation':'NOT_RUN'}
    result['manifest']=manifest_check(root);print('PASS immutable file SHA256',flush=True)
    result['parameters']=parameters_check(root);print('PASS full parameters + physical weights',flush=True)
    result['cases']=cases_check(root);print('PASS 98 real cases / 146 mode calls',flush=True)
    result['datasets']=data_check(root,a.deep_data);print('PASS dataset labels and explicit external archive policy',flush=True)
    if a.runtime:result['runtime']=runtime_check(root)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
