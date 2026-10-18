"""Bounded complete-mask reconstruction and sequential teacher compensation."""
import argparse
import shutil
import time
from pathlib import Path
import numpy as np
import onnx
from onnx import numpy_helper as NH
from .common import ROOT, read_json, write_json, sha, utc
from .runtime import verify_bundle
from .sparse24_study import BUNDLE, graph, session
from .sparse24_recovery_study import LAYERS, OLD, rebuild, candidate, copy_weight
from .sparse24_activation_study import checked_input
from .quantization import quantize
from .sparse24 import logical_pack, logical_unpack, slot_gemm
from .golden_int64 import gemm

PARENT=ROOT/'results/B/B8/sparse24_recovery_20261013_01'
NAMES=['full6','full8','sequential8','expanded12','expanded16','train_epoch1','train_epoch2','train_epoch3']


def solve_cross(weight,mask,h,cross):
    k,n=weight.shape
    assert h.shape==(k,k) and cross.shape==(k,n)
    assert np.all(mask.reshape(-1,4,n).sum(1)==2)
    damping=1e-4*max(float(np.trace(h))/k,1e-12)
    result=np.zeros_like(weight); residual=0.
    for col in range(n):
        keep=np.flatnonzero(mask[:,col]); a=h[np.ix_(keep,keep)].copy()
        a.flat[::len(keep)+1]+=damping
        b=cross[keep,col]+damping*weight[keep,col].astype(np.float64)
        value=np.linalg.solve(a,b)
        residual=max(residual,float(np.linalg.norm(a@value-b)/max(np.linalg.norm(b),1e-12)))
        result[keep,col]=value.astype(np.float32)
    assert np.isfinite(result).all() and residual<=1e-8
    return result,{'lambda':damping,'max_relative_residual':residual}


def evaluate(out,name,ids):
    assert name in NAMES
    path=out/'sensitivity.json'
    prior=read_json(path)['candidates'] if path.exists() else []
    assert len(prior)<8 and not any(r['name']==name for r in prior)
    row=candidate(out,name,ids)
    row['method']='sequential' if name=='sequential8' else 'full_teacher_input'
    for mode in ('fp32','w8a8'):
        row['results'][mode]['top5_drop_pp']=(948-row['results'][mode]['top5_correct'])/10
    saved=read_json(path); saved['candidates'][-1]=row; write_json(path,saved)
    return row


def key(row):
    return (row['max_lost'],max(948-r['top5_correct'] for r in row['results'].values()),row['results']['w8a8']['logits_mse'],row['name'])


def prepare(out):
    out.mkdir(parents=True,exist_ok=False)
    manifest=read_json(PARENT/'evidence_manifest.json')
    for f in manifest['files']: assert sha(PARENT/f['file'])==f['sha256']
    parent_protocol=read_json(PARENT/'protocol.json')
    for role,digest in parent_protocol['roles'].items():
        assert sha(PARENT/(role+'.json'))==digest
        shutil.copyfile(PARENT/(role+'.json'),out/(role+'.json'))
    shutil.copyfile(PARENT/'measured_layer_ranking.json',out/'measured_layer_ranking.json')
    snapshots=out/'source'; snapshots.mkdir()
    for f in (ROOT/'model/b').glob('*.py'): shutil.copyfile(f,snapshots/f.name)
    write_json(out/'protocol.json',{'utc':utc(),'status':'FROZEN_BEFORE_PREDICTIONS','parent_manifest_sha256':sha(PARENT/'evidence_manifest.json'),'bundle':verify_bundle(BUNDLE),
        'max_candidate_pairs':8,'max_local_candidate_inferences':16000,'candidate_names':NAMES,'selection_max_lost':5,'formal_Top1_budget_pp':1.0,
        'roles':parent_protocol['roles'],'sources':{p.name:sha(p) for p in snapshots.iterdir()},
        'mask_sources':{'full6':'parent/expanded_1','full8':'parent/expanded_2'},'ridge':'1e-4*max(trace(H)/K,1e-12); original dense weights as prior',
        'sequential_condition':'full8 not eligible OR W8 logits MSE >= parent expanded_2; one pass only',
        'same_coverage_order':['max_FP_W8_Top1_loss','max_FP_W8_Top5_loss','W8_MSE','stable_name'],
        'expansion':'eligible 8 -> 12 -> 16; append frozen measured ranking; stop first failed expansion; same restoration method',
        'training':'one highest-coverage failed candidate, best same-coverage key; CPU probe <=600s; regression20 atol/rtol1e-4; teacher ORT original 1000 logits; fixed masks retained weights only; T=2 KL*T^2, Adam 1e-5, batch8, seed20261012, <=3 epochs, total <=14400s; no parameter search',
        'fit_and_local_seconds_per_candidate_max':1800,'quality_predictions':'PROHIBITED_BY_USER','hardware':'PROHIBITED_BY_USER'})
    engines={'fp32':session((BUNDLE/'graphs/model_fp32.onnx').read_bytes()),'w8a8':session((BUNDLE/'graphs/model_w8a8_reference.onnx').read_bytes())}
    x=np.load(OLD/'selection_inputs.npy',mmap_mode='r')
    for mode,engine in engines.items():
        reference=np.load(OLD/'selection_results/baseline'/(mode+'_logits.npy'))
        for i in (0,499,999): np.testing.assert_array_equal(engine.run(None,{'image':x[i:i+1]})[0][0],reference[i])
    write_json(out/'baseline_reuse.json',{'status':'PASS','probes':6,'source':'same frozen graphs and original selection logits'})
    print('PREPARE_PASS',flush=True)


def initial(out):
    for name,parent in [('full6','expanded_1'),('full8','expanded_2')]:
        ids=next(r['indices'] for r in read_json(PARENT/'sensitivity.json')['candidates'] if r['name']==parent)
        rebuild(out,PARENT/parent,name,ids)
        evaluate(out,name,ids)


def capture(blob,layer,output=False):
    m=onnx.load_from_string(blob)
    m.graph.output.append(onnx.helper.make_tensor_value_info(layer['graph_output'] if output else layer['graph_input'],onnx.TensorProto.FLOAT,layer['output_shape'] if output else layer['input_shape']))
    return session(m.SerializeToString())


def sequential(out,source,name,ids):
    start=time.perf_counter(); dest=out/name; dest.mkdir(exist_ok=False)
    shutil.copyfile(out/'selection.json',dest/'selection.json')
    for i in ids: copy_weight(source,dest,i)
    original=(BUNDLE/'graphs/model_fp32.onnx').read_bytes()
    weights={t.name:NH.to_array(t) for t in onnx.load_from_string(original).graph.initializer}
    reports=[]
    for i in sorted(ids):
        layer=LAYERS[i]; teacher=capture(original,layer,True); student=capture(graph(dest,ids,'fp32'),layer)
        folder=dest/'weights'/f'{i:02d}'; w=weights[layer['layer_id']+'.weight'].T.copy(); bias=weights[layer['layer_id']+'.bias']
        mask=np.load(folder/'mask.npy'); pos=np.load(folder/'positions.npy'); h=np.zeros((layer['K'],layer['K']),np.float64); cross=np.zeros(w.shape,np.float64); count=0
        for row in read_json(out/'fit.json'):
            x=checked_input(row)
            a=student.run(None,{'image':x})[-1].reshape(-1,layer['K']).astype(np.float64)
            y=teacher.run(None,{'image':x})[-1].reshape(-1,layer['N']).astype(np.float64)-bias.astype(np.float64)
            h+=a.T@a; cross+=a.T@y; count+=len(a)
            if time.perf_counter()-start>1800: raise TimeoutError('Sequential compensation budget')
        restored,report=solve_cross(w,mask,h/count,cross/count)
        q,s=quantize(restored,0); values=logical_pack(q,pos)
        np.testing.assert_array_equal(logical_unpack(values,pos),q)
        xi=np.random.default_rng(20261013+i).integers(-127,128,(layer['M'],layer['K']),dtype=np.int8)
        reference=gemm(xi,q); np.testing.assert_array_equal(slot_gemm(xi,values,pos),reference)
        errors=np.zeros(3,np.float64); before=np.load(folder/'pruned_fp32.npy')
        for row in read_json(out/'local.json'):
            x=checked_input(row); a=student.run(None,{'image':x})[-1].reshape(-1,layer['K']).astype(np.float64)
            y=teacher.run(None,{'image':x})[-1].reshape(-1,layer['N']).astype(np.float64)-bias.astype(np.float64)
            errors += [np.sum((a@before-y)**2),np.sum((a@restored-y)**2),np.sum(y*y)]
        for field,a in [('pruned_fp32',restored),('Wq',q),('sw',s),('values',values),('gram',h/count),('cross',cross/count),('integer_input',xi),('integer_reference',reference)]: np.save(folder/(field+'.npy'),a)
        report.update({'index':i,'tokens':count,'local_FP32_error_before_after':(errors[:2]/max(errors[2],1e-12)).tolist(),'integer_outputs':reference.size,'integer_differences':0,'files':{p.name:sha(p) for p in folder.iterdir()}})
        reports.append(report)
        print('SEQUENTIAL_LAYER',i,report['local_FP32_error_before_after'],flush=True)
        if time.perf_counter()-start>1800: raise TimeoutError('Sequential compensation budget')
    write_json(dest/'reconstruction.json',{'status':'PASS_NUMERICS_ONLY','method':'student input to teacher output; one ordered pass','seconds':time.perf_counter()-start,'layers':reports})


def advance(out):
    rows=read_json(out/'sensitivity.json')['candidates']; full8=next(r for r in rows if r['name']=='full8')
    old8=next(r for r in read_json(PARENT/'sensitivity.json')['candidates'] if r['name']=='expanded_2')
    condition=not full8['eligible'] or full8['results']['w8a8']['logits_mse']>=old8['results']['w8a8']['logits_mse']
    write_json(out/'sequential_decision.json',{'required':condition,'frozen_condition':read_json(out/'protocol.json')['sequential_condition']})
    if condition:
        sequential(out,out/'full8','sequential8',full8['indices']); evaluate(out,'sequential8',full8['indices'])
    rows=read_json(out/'sensitivity.json')['candidates']; good=[r for r in rows if len(r['indices'])==8 and r['eligible']]
    if good:
        anchor=min(good,key=key); method=anchor['method']; ranking=read_json(out/'measured_layer_ranking.json')
        for total in (12,16):
            name='expanded'+str(total); staging=out/(name+'_inputs'); staging.mkdir()
            ids=list(anchor['indices'])
            for i in ids: copy_weight(out/anchor['name'],staging,i)
            for row in [r for r in ranking if r['index'] not in ids][:total-len(ids)]:
                copy_weight(Path(row['root']),staging,row['index']); ids.append(row['index'])
            ids.sort()
            if method=='sequential': sequential(out,staging,name,ids)
            else: rebuild(out,staging,name,ids)
            anchor=evaluate(out,name,ids); anchor['method']=method
            saved=read_json(out/'sensitivity.json'); saved['candidates'][-1]=anchor; write_json(out/'sensitivity.json',saved)
            if not anchor['eligible']: break
    rows=read_json(out/'sensitivity.json')['candidates']
    best_coverage=max([len(r['indices']) for r in rows if r['eligible']]+[0])
    failed=[r for r in rows if len(r['indices'])>=8 and len(r['indices'])>best_coverage and not r['eligible']]
    target=min(failed,key=lambda r:(-len(r['indices']),*key(r))) if failed else None
    write_json(out/'training_decision.json',{'required':bool(target),'candidate':target['name'] if target else None,'reason':'COVERAGE_GROWTH_QUALITY_LIMITED' if target else 'NO_FAILED_EXPANSION','status':'PROBE_REQUIRED' if target else 'SKIPPED'})
    print('ADVANCE_COMPLETE',{'training_target':target['name'] if target else None},flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['prepare','initial','advance']); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); out=a.output.resolve()
    if a.action!='prepare':
        for name,digest in read_json(out/'protocol.json')['sources'].items(): assert sha(ROOT/'model/b'/name)==digest
    try: {'prepare':prepare,'initial':initial,'advance':advance}[a.action](out)
    except Exception as error:
        if out.exists(): write_json(out/('failure_'+a.action+'.json'),{'utc':utc(),'error':repr(error)})
        raise
