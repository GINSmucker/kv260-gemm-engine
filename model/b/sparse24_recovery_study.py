"""Small measured-layer combinations and one fixed-mask reconstruction."""
import argparse
import hashlib
import shutil
import time
from pathlib import Path
import numpy as np
import onnx
from onnx import numpy_helper as NH
from .common import ROOT, read_json, write_json, sha, utc
from .runtime import verify_bundle
from .sparse24_study import BUNDLE, graph, session, evaluate
from .sparse24_activation_study import checked_input
from .sparse24_reconstruct import reconstruct
from .sparse24 import logical_pack, logical_unpack, slot_gemm
from .quantization import quantize, postprocess
from .golden_int64 import gemm

OLD=ROOT/'results/B/B8/sparse24_20261012_01'
ACT=ROOT/'results/B/B8/sparse24_activation_20261012_01'
LAYERS=read_json(BUNDLE/'layers.json')
GROUPS=[[39,15],[39,12],[39,40],[39,15,12],[39,15,40],[39,15,12,40]]


def quality_key(r):
    return r['max_lost'],-r['removed_MAC'],r['results']['w8a8']['logits_mse'],r['name']


def adopt_key(r):
    return -r['removed_MAC'],-r['results']['w8a8']['top1_correct'],r['results']['w8a8']['logits_mse'],r['name']


def measured_layers():
    rows=[]
    for run in (OLD,ACT):
        for r in read_json(run/'sensitivity.json')['candidates']:
            if len(r['indices'])!=1: continue
            i=r['indices'][0]
            if LAYERS[i]['family'] not in ('qkv','fc1','fc2'): continue
            method='magnitude' if run==OLD else r['root']
            q=r['results']['w8a8']; f=r['results']['fp32']
            rows.append({'index':i,'method':method,'root':str(run if run==OLD else run/method),'max_lost':max(764-f['top1_correct'],764-q['top1_correct']),'w8_correct':q['top1_correct'],'mse':q['logits_mse']})
    key=lambda r:(r['max_lost'],-r['w8_correct'],r['mse'],r['method'])
    best=[min([r for r in rows if r['index']==i],key=key) for i in sorted({r['index'] for r in rows})]
    return sorted(best,key=lambda r:(r['max_lost'],-r['w8_correct'],r['mse'],r['index']))


def copy_weight(source,dest,i):
    folder=dest/'weights'/f'{i:02d}'; folder.mkdir(parents=True,exist_ok=False)
    for name in ('pruned_fp32','mask','positions','Wq','sw','values'):
        shutil.copyfile(source/'weights'/f'{i:02d}'/(name+'.npy'),folder/(name+'.npy'))


def prepare(out):
    out.mkdir(parents=True,exist_ok=False)
    verified=verify_bundle(BUNDLE)
    for role in ('selection','fit','local','quality','recovery_train','regression','validation'):
        expected=read_json(ACT/'protocol.json')['roles'][role]
        assert sha(ACT/(role+'.json'))==expected
        shutil.copyfile(ACT/(role+'.json'),out/(role+'.json'))
    ranking=measured_layers()
    assert [r['index'] for r in ranking[:6]]==[39,15,12,40,10,44]
    write_json(out/'measured_layer_ranking.json',ranking)
    provenance=[]
    specs=[('mixed_'+str(n+1),ids,'mixed') for n,ids in enumerate(GROUPS)]+[('magnitude_four',GROUPS[-1],'magnitude'),('gram_four',GROUPS[-1],'gram')]
    for name,ids,method in specs:
        dest=out/name; dest.mkdir()
        shutil.copyfile(out/'selection.json',dest/'selection.json')
        for i in sorted(ids):
            source=Path(next(r['root'] for r in ranking if r['index']==i)) if method=='mixed' else (OLD if method=='magnitude' else ACT/'gram')
            report=read_json(source/('pruning_verification.json' if source==OLD else 'weight_verification.json'))
            entry=next(r for r in report['layers'] if (r['layer_id']==LAYERS[i]['layer_id'] if source==OLD else r['index']==i))
            for f in ('pruned_fp32.npy','mask.npy','positions.npy','Wq.npy','sw.npy','values.npy'):
                assert sha(source/'weights'/f'{i:02d}'/f)==entry['files'][f]
            copy_weight(source,dest,i)
            provenance.append({'candidate':name,'index':i,'source':str(source),'files':{p.name:sha(p) for p in (dest/'weights'/f'{i:02d}').iterdir()}})
    rules=ROOT/'docs/model/KV260_2比4稀疏W8A8开发与验收计划.md'
    shutil.copyfile(rules,out/'approved_plan.md')
    source=out/'source'; source.mkdir()
    for p in (ROOT/'model/b').glob('*.py'): shutil.copyfile(p,source/p.name)
    write_json(out/'protocol.json',{'utc':utc(),'experiment_date':'2026-10-13','status':'FROZEN_BEFORE_NEW_PREDICTIONS','bundle':verified,'plan_sha256':sha(out/'approved_plan.md'),'max_candidates':14,'max_new_candidate_inferences':28000,'selection_max_lost':5,'quality_max_drop_pp':1.0,'initial':[{'name':n,'indices':sorted(i)} for n,i,m in specs],'ranking_sha256':sha(out/'measured_layer_ranking.json'),'roles':{r:sha(out/(r+'.json')) for r in ('selection','fit','local','quality','recovery_train','regression','validation')},'source_sha256':{p.name:sha(p) for p in source.iterdir()},'weight_provenance':provenance,'selection_inputs_sha256':sha(OLD/'selection_inputs.npy'),'baseline_logits_sha256':sha(OLD/'selection_results/baseline/fp32_logits.npy'),'reconstruction_seconds_max':1800,'training_seconds_max':14400,'probe_seconds_max':600,'formal_candidates_max':1,'hardware':'NOT_RUN'})
    x=np.load(OLD/'selection_inputs.npy',mmap_mode='r')
    probes=[]
    for mode,filename in (('fp32','model_fp32.onnx'),('w8a8','model_w8a8_reference.onnx')):
        engine=session((BUNDLE/'graphs'/filename).read_bytes()); expected=np.load(OLD/'selection_results/baseline'/(mode+'_logits.npy'))
        for i in (0,499,999):
            np.testing.assert_array_equal(engine.run(None,{'image':x[i:i+1]})[0][0],expected[i])
            probes.append({'mode':mode,'index':i,'bitwise_equal':True})
    write_json(out/'baseline_reuse.json',{'status':'PASS','probes':probes})
    print('PREPARE_PASS',len(specs),flush=True)


def candidate(out,name,ids):
    dest=out/name
    tensors=np.load(OLD/'selection_inputs.npy',mmap_mode='r')
    labels=np.array([r['label'] for r in read_json(out/'selection.json')])
    baseline=np.load(OLD/'selection_results/baseline/fp32_logits.npy')
    results={mode:evaluate(dest,name,sorted(ids),mode,tensors,labels,baseline)[0] for mode in ('fp32','w8a8')}
    row={'name':name,'indices':sorted(ids),'results':results,'max_lost':max(764-r['top1_correct'] for r in results.values()),'eligible':all(r['top1_correct']>=759 for r in results.values()),'removed_MAC':sum(LAYERS[i]['M']*LAYERS[i]['K']*LAYERS[i]['N']//2 for i in ids)}
    path=out/'sensitivity.json'; saved=read_json(path)['candidates'] if path.exists() else []
    saved=[r for r in saved if r['name']!=name]+[row]
    assert len(saved)<=14
    write_json(path,{'baseline_fp32_correct':764,'candidates':saved,'formal_quality':'NOT_RUN'})
    return row


def input_engine(ids):
    model=onnx.load(BUNDLE/'graphs/model_fp32.onnx')
    for i in ids:
        l=LAYERS[i]
        model.graph.output.append(onnx.helper.make_tensor_value_info(l['graph_input'],onnx.TensorProto.FLOAT,l['input_shape']))
    return session(model.SerializeToString())


def rebuild(out,source,dest_name,ids):
    start=time.perf_counter(); deadline=start+1800
    engine=input_engine(ids); plain=session((BUNDLE/'graphs/model_fp32.onnx').read_bytes())
    h={i:np.zeros((LAYERS[i]['K'],LAYERS[i]['K']),np.float64) for i in ids}; counts={i:0 for i in ids}
    initializers={t.name:NH.to_array(t) for t in onnx.load(BUNDLE/'graphs/model_fp32.onnx').graph.initializer}
    orig={i:np.ascontiguousarray(initializers[LAYERS[i]['layer_id']+'.weight'].T) for i in ids}
    for n,row in enumerate(read_json(out/'fit.json')):
        x=checked_input(row); values=engine.run(None,{'image':x})
        if n<3: np.testing.assert_array_equal(values[0],plain.run(None,{'image':x})[0])
        for i,v in zip(ids,values[1:]):
            a=v.reshape(-1,LAYERS[i]['K']).astype(np.float64)
            h[i]+=a.T@a; counts[i]+=len(a)
        if time.perf_counter()>deadline: raise TimeoutError('Reconstruction budget exceeded')
    dest=out/dest_name; dest.mkdir(exist_ok=False)
    shutil.copyfile(out/'selection.json',dest/'selection.json')
    reports=[]; alternatives={}
    rng=np.random.default_rng(20261013)
    for i in ids:
        folder=dest/'weights'/f'{i:02d}'; folder.mkdir(parents=True)
        sf=source/'weights'/f'{i:02d}'
        mask=np.load(sf/'mask.npy'); pos=np.load(sf/'positions.npy')
        gram=h[i]/counts[i]
        w,report=reconstruct(orig[i],mask,gram)
        q,s=quantize(w,0); values=logical_pack(q,pos)
        np.testing.assert_array_equal(logical_unpack(values,pos),q)
        x=rng.integers(-127,128,(LAYERS[i]['M'],LAYERS[i]['K']),dtype=np.int8)
        reference=gemm(x,q); np.testing.assert_array_equal(slot_gemm(x,values,pos),reference)
        for name,a in (('pruned_fp32',w),('Wq',q),('sw',s),('values',values),('mask',mask),('positions',pos),('gram',gram),('integer_input',x),('integer_reference',reference)):
            np.save(folder/(name+'.npy'),a)
        report.update({'index':i,'tokens':counts[i],'outputs':reference.size,'differences':0,'mask_unchanged':True,'scale_changed_channels':int(np.sum(s!=np.load(sf/'sw.npy'))),'files':{p.name:sha(p) for p in folder.iterdir()}})
        reports.append(report)
        alternatives[i]=[(np.load(sf/'pruned_fp32.npy'),np.load(sf/'Wq.npy'),np.load(sf/'sw.npy')),(w,q,s)]
        if time.perf_counter()>deadline: raise TimeoutError('Reconstruction budget exceeded')
    sums={i:np.zeros((2,4),np.float64) for i in ids}
    for row in read_json(out/'local.json'):
        inputs=engine.run(None,{'image':checked_input(row)})[1:]
        for i,x in zip(ids,inputs):
            x=x.reshape(-1,LAYERS[i]['K']); w=orig[i]; bias=initializers[LAYERS[i]['layer_id']+'.bias']
            q,s=quantize(w,0); xq,sx=quantize(x,1); xf=xq.astype(np.float32)
            yf=x@w+bias; yq=postprocess((xf@q.astype(np.float32)).astype(np.int32),sx,s,bias)
            for a,(aw,aq,asc) in enumerate(alternatives[i]):
                af=x@aw+bias; ay=postprocess((xf@aq.astype(np.float32)).astype(np.int32),sx,asc,bias)
                sums[i][a]+=[np.sum((af.astype(float)-yf)**2),np.sum(yf.astype(float)**2),np.sum((ay.astype(float)-yq)**2),np.sum(yq.astype(float)**2)]
        if time.perf_counter()>deadline: raise TimeoutError('Reconstruction budget exceeded')
    write_json(dest/'reconstruction.json',{'status':'PASS_NUMERICS_ONLY','utc':utc(),'source':str(source),'seconds':time.perf_counter()-start,'layers':reports,'local_errors':{i:[{'fp32':float(v[0]/max(v[1],1e-12)),'w8a8':float(v[2]/max(v[3],1e-12))} for v in sums[i]] for i in ids},'local_order':['unrestored','restored'],'quality':'NOT_YET_EVALUATED'})
    print('RECONSTRUCTION_PASS',dest_name,round(time.perf_counter()-start,2),flush=True)
    return dest


def expand(out):
    rows=read_json(out/'sensitivity.json')['candidates']
    eligible=[r for r in rows if r['eligible']]
    if not eligible: return
    anchor=min(eligible,key=adopt_key)
    ranking=read_json(out/'measured_layer_ranking.json')
    initial=read_json(out/'reconstruction_anchor.json')['candidate']
    restored=next((r for r in rows if r['name']=='reconstructed'),None)
    use_reconstruction=bool(restored and restored['max_lost']<initial['max_lost'] and restored['results']['w8a8']['top1_correct']>initial['results']['w8a8']['top1_correct'])
    for step in (1,2):
        name='expanded_'+str(step)
        existing=next((r for r in rows if r['name']==name),None)
        if existing:
            if not existing['eligible']: break
            anchor=existing; continue
        added=[r for r in ranking if r['index'] not in anchor['indices']][:2]
        ids=sorted(anchor['indices']+[r['index'] for r in added])
        dest=out/name
        if use_reconstruction:
            staging=out/('expansion_inputs_'+str(step)); staging.mkdir()
            for r in added: copy_weight(Path(r['root']),staging,r['index'])
            rebuild(out,staging,name,[r['index'] for r in added])
        else:
            dest.mkdir(); shutil.copyfile(out/'selection.json',dest/'selection.json')
            for r in added: copy_weight(Path(r['root']),dest,r['index'])
        for i in anchor['indices']: copy_weight(out/anchor['name'],dest,i)
        anchor=candidate(out,name,ids)
        if not anchor['eligible']: break


def run(out):
    protocol=read_json(out/'protocol.json')
    for name,digest in protocol['source_sha256'].items():
        assert sha(ROOT/'model/b'/name)==digest,'Source changed: '+name
    for r in protocol['initial']: candidate(out,r['name'],r['indices'])
    rows=read_json(out/'sensitivity.json')['candidates']
    anchor=min(rows,key=quality_key)
    write_json(out/'reconstruction_anchor.json',{'candidate':anchor,'utc':utc()})
    rebuild(out,out/anchor['name'],'reconstructed',anchor['indices'])
    recovered=candidate(out,'reconstructed',anchor['indices'])
    rows=read_json(out/'sensitivity.json')['candidates']
    training=not any(r['eligible'] for r in rows)
    origin=min([anchor,recovered],key=lambda r:(r['max_lost'],r['results']['w8a8']['logits_mse'],r['name']))
    write_json(out/'training_decision.json',{'required':training,'origin':origin['name'] if training else None,'reason':'NO_INITIAL_OR_RECONSTRUCTED_ELIGIBLE' if training else 'SELECTION_ELIGIBLE_EXISTS','utc':utc()})
    if not training: expand(out)
    finish(out)


def finish(out):
    rows=read_json(out/'sensitivity.json')['candidates']
    good=sorted([r for r in rows if r['eligible']],key=adopt_key)
    write_json(out/'software_decision.json',{'utc':utc(),'candidate_count':len(rows),'eligible':[r['name'] for r in good],'arm_max2':[r['name'] for r in good[:2]],'training':read_json(out/'training_decision.json'),'formal_quality':'NOT_RUN','hardware':'NOT_RUN'})
    print('SOFTWARE_EVALUATION_COMPLETE',len(rows),[r['name'] for r in good],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('prepare','run','expand'))
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        {'prepare':prepare,'run':run,'expand':expand}[args.action](args.output.resolve())
    except Exception as exc:
        if args.output.exists(): write_json(args.output/'failure.json',{'utc':utc(),'error':repr(exc)})
        raise
