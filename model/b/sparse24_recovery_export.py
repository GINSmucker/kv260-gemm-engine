"""Freeze selected software graphs and verify complete propagated integers."""
import argparse
import hashlib
import shutil
import tarfile
from pathlib import Path
import numpy as np
import onnx
from onnx import numpy_helper as NH
from .common import ROOT,read_json,write_json,sha,utc
from .sparse24_study import BUNDLE,graph,session
from .sparse24_recovery_study import OLD,LAYERS
from .sparse24 import logical_pack,logical_unpack,slot_gemm
from .golden_int64 import gemm


def export(out):
    decision=read_json(out/'software_decision.json')
    names=decision['arm_max2']
    assert 0<len(names)<=2
    rows=read_json(out/'sensitivity.json')['candidates']
    package=out/'arm_package'; package.mkdir(exist_ok=False)
    shutil.copyfile(out/'selection.json',package/'selection.json')
    tensors=np.load(OLD/'selection_inputs.npy',mmap_mode='r')
    maps={}
    for mode,filename in (('original_fp32','model_fp32.onnx'),('dense_w8a8','model_w8a8_reference.onnx')):
        shutil.copyfile(BUNDLE/'graphs'/filename,package/(mode+'.onnx'))
        maps[mode]=mode+'.onnx'
    reports=[]
    for name in names:
        candidate=next(r for r in rows if r['name']==name)
        indices=candidate['indices']; source=out/name
        for mode in ('fp32','w8a8'):
            blob=graph(source,indices,mode)
            target=package/(name+'_'+mode+'.onnx'); target.write_bytes(blob)
            assert sha(target)==candidate['results'][mode]['graph_sha256']
            maps[name+'_'+mode]=target.name
        modes=[]
        for layer in LAYERS:
            i=layer['layer_index']; sparse=i in indices
            entry={'index':i,'layer':layer['layer_id'],'shape':[layer[k] for k in ('M','K','N')],'mode':'sparse24_w8a8' if sparse else 'dense_w8a8','PL':'NOT_READY'}
            if sparse:
                folder=source/'weights'/f'{i:02d}'
                entry['files']={f.name:sha(f) for f in folder.glob('*.npy') if f.name in ('mask.npy','positions.npy','pruned_fp32.npy','Wq.npy','sw.npy','values.npy')}
                q=np.load(folder/'Wq.npy'); pos=np.load(folder/'positions.npy'); values=np.load(folder/'values.npy')
                np.testing.assert_array_equal(logical_pack(q,pos),values)
                np.testing.assert_array_equal(logical_unpack(values,pos),q)
            else: entry['files']={k:layer[k]['sha256'] for k in ('Wq','sw','bias')}
            modes.append(entry)
        write_json(package/(name+'_layer_modes.json'),modes)
        model=onnx.load(package/(name+'_w8a8.onnx'))
        ws={t.name:NH.to_array(t) for t in model.graph.initializer}
        reference=onnx.load(BUNDLE/'graphs/model_w8a8_reference.onnx')
        assert [n.SerializeToString() for n in model.graph.node]==[n.SerializeToString() for n in reference.graph.node]
        for layer in LAYERS:
            i=layer['layer_index']
            model.graph.output.append(onnx.helper.make_tensor_value_info(f'BQL_{i}_xq',onnx.TensorProto.FLOAT,layer['input_shape']))
            model.graph.output.append(onnx.helper.make_tensor_value_info(f'BQL_{i}_i32',onnx.TensorProto.INT32,layer['output_shape']))
        engine=session(model.SerializeToString()); plain=session((package/(name+'_w8a8.onnx')).read_bytes())
        cases=[]
        folder=out/'integer_verification'/name; folder.mkdir(parents=True)
        for image in (0,499,999):
            values=engine.run(None,{'image':tensors[image:image+1]})
            np.testing.assert_array_equal(values[0],plain.run(None,{'image':tensors[image:image+1]})[0])
            for layer in LAYERS:
                i=layer['layer_index']; xqf,yi=values[1+i*2:3+i*2]
                assert np.array_equal(xqf,np.rint(xqf)) and np.max(np.abs(xqf))<=127
                x=xqf.astype(np.int8).reshape(layer['M'],layer['K'])
                wf=ws[f'BQL_{i}_Wf']; w=wf.astype(np.int8)
                np.testing.assert_array_equal(wf,w)
                expected=(np.load(source/'weights'/f'{i:02d}'/'Wq.npy') if i in indices else np.fromfile(BUNDLE/layer['Wq']['file'],np.int8).reshape(layer['K'],layer['N']))
                np.testing.assert_array_equal(w,expected)
                ref=gemm(x,w); np.testing.assert_array_equal(yi.reshape(ref.shape),ref)
                if i in indices:
                    f=source/'weights'/f'{i:02d}'
                    np.testing.assert_array_equal(slot_gemm(x,np.load(f/'values.npy'),np.load(f/'positions.npy')),ref)
                path=folder/f'image{image:03d}_layer{i:02d}.npz'
                np.savez(path,Xq=x,actual=yi.reshape(ref.shape),reference=ref)
                cases.append({'image':image,'layer':i,'outputs':ref.size,'differences':0,'slot_checked':i in indices,'file':path.relative_to(out).as_posix(),'sha256':sha(path)})
        reports.append({'name':name,'cases':cases,'outputs':sum(c['outputs'] for c in cases),'status':'PASS','PL':'NOT_RUN'})
        print('INTEGER_FULL_GRAPH_PASS',name,sum(c['outputs'] for c in cases),flush=True)
    write_json(out/'full_integer_verification.json',{'status':'PASS','utc':utc(),'candidates':reports})
    source_names=('__init__.py','common.py','preprocess.py','sparse24_quality.py','sparse24_arm_selection.py')
    write_json(package/'freeze.json',{'status':'FROZEN_BEFORE_ARM_SELECTION','utc':utc(),'candidates':names,'graphs':maps,'files':{p.name:sha(p) for p in package.iterdir() if p.is_file()},'sources':{name:sha(ROOT/'model/b'/name) for name in source_names},'input_sha256':[hashlib.sha256(x.tobytes()).hexdigest() for x in tensors],'protocol_sha256':sha(out/'protocol.json'),'integer_verification_sha256':sha(out/'full_integer_verification.json'),'formal_quality':'NOT_RUN'})
    archive=out/'arm_selection_package.tar.gz'
    with tarfile.open(archive,'w:gz') as tf:
        tf.add(package,arcname='package')
        for name in source_names: tf.add(ROOT/'model/b'/name,arcname='b/'+name)
    write_json(out/'arm_transfer.json',{'sha256':sha(archive),'bytes':archive.stat().st_size,'freeze_sha256':sha(package/'freeze.json')})
    print('ARM_PACKAGE_READY',names,archive.stat().st_size,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    export(parser.parse_args().output.resolve())
