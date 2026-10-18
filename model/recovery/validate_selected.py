"""Same-host existing-val diagnosis and frozen ARM handoff for one selected model."""
from pathlib import Path
import hashlib,shutil,time,traceback,tarfile
import numpy as np
from b.common import ROOT,read_json,write_json,sha,utc
from b.sparse24_study import session,BUNDLE,DATA
from b.preprocess import preprocess

OUT=Path(__file__).resolve().parent
PARENT=ROOT/'results/B/B8/mlp_width_quality_20261014_01'

def main():
    decision=read_json(OUT/'decision.json');audit=read_json(OUT/'audit.json');name=decision['selected']
    assert name and audit['selected']==name
    folder=OUT/name
    for n,h in read_json(folder/'freeze.json')['files'].items():assert sha(folder/n)==h
    dest=OUT/'local_validation';dest.mkdir(exist_ok=False)
    graphs={'original_fp32':BUNDLE/'graphs/model_fp32.onnx','compact_fp32':folder/'fp32.onnx','compact_w8a8':folder/'w8a8.onnx'}
    write_json(dest/'freeze.json',{'utc':utc(),'candidate':name,'selection_closed':True,'graphs':{n:sha(p) for n,p in graphs.items()},'source_sha256':sha(__file__),'scope':'Windows diagnostic only; ARM formal quality NOT_RUN','dataset':'existing val500/full3925; no reserved2000','quality_drop_pp':1.0,'budget_seconds':900,'no_candidate_changes_after_validation':True})
    splits=read_json(BUNDLE/'splits.json')['splits'];rows=splits['validation_full'];assert len(rows)==3925
    train=read_json(ROOT/'results/B/B8/sparse24_full_recovery_20261013_01/recovery_train.json')
    valsha={r['image_sha256'] for r in rows}
    for data in [train]+[read_json(PARENT/(n+'.json')) for n in ('fit','local','selection')]:assert not valsha&{r['image_sha256'] for r in data}
    fast={r['path'] for r in splits['validation_fast']};ids=np.array([i for i,r in enumerate(rows) if r['path'] in fast]);assert len(ids)==500
    engines={n:session(p.read_bytes()) for n,p in graphs.items()};outputs={n:np.empty((3925,1000),np.float32) for n in graphs};times=np.empty((3925,3),float);input_hashes=[];start=time.perf_counter()
    for i,r in enumerate(rows):
        path=DATA/r['path'];assert sha(path)==r['image_sha256'];x=preprocess(path);input_hashes.append(hashlib.sha256(x.tobytes()).hexdigest())
        for k,(n,s) in enumerate(engines.items()):
            tick=time.perf_counter();y=s.run(None,{'image':x})[0];times[i,k]=time.perf_counter()-tick;assert y.shape==(1,1000) and np.isfinite(y).all();outputs[n][i]=y[0]
        if (i+1)%100==0:
            write_json(dest/'progress.json',{'utc':utc(),'completed':i+1,'total':3925,'seconds':time.perf_counter()-start});print('LOCAL_VAL',i+1,flush=True)
        assert time.perf_counter()-start<900,'Local diagnosis budget exceeded'
    labels=np.array([r['label'] for r in rows]);reports={}
    for label,index in [('val500',ids),('full3925',np.arange(3925))]:
        counts={}
        for n,v in outputs.items():
            rank=np.argsort(-v[index],axis=1,kind='stable')[:,:5]
            counts[n]={'top1':int(np.sum(rank[:,0]==labels[index])),'top5':int(np.any(rank==labels[index,None],axis=1).sum())}
        baseline=counts['original_fp32']['top1']
        for n in ('compact_fp32','compact_w8a8'):
            d=baseline-counts[n]['top1'];counts[n].update(drop_pp=100*d/len(index),within_budget=100*d<=len(index))
        reports[label]={'samples':len(index),'models':counts,'within_budget':all(counts[n]['within_budget'] for n in ('compact_fp32','compact_w8a8'))}
    for n,v in outputs.items():np.save(dest/(n+'_logits.npy'),v)
    np.save(dest/'times.npy',times);write_json(dest/'input_identity.json',{'splits_sha256':sha(BUNDLE/'splits.json'),'preprocess_sha256':sha(ROOT/'model/b/preprocess.py'),'input_tensor_sha256':input_hashes,'disjoint_train_fit_local_selection':True})
    write_json(dest/'quality.json',{'status':'COMPLETE_WINDOWS_DIAGNOSTIC','results':reports,'candidate':name,'seconds':time.perf_counter()-start,'inferences':11775,'missing':0,'formal_ARM':'NOT_RUN_CONNECTION_TIMEOUT','quality2000_predictions':0,'scope':'ten-class existing validation; 1000-output complete model','files':{p.name:sha(p) for p in dest.iterdir() if p.is_file()}})
    print('LOCAL_QUALITY',reports,flush=True)
    package=OUT/'arm_package';package.mkdir();candidate=package/'candidate';candidate.mkdir();(package/'b').mkdir()
    for mode in ('fp32','w8a8'):shutil.copyfile(folder/(mode+'.onnx'),candidate/('pruned_'+mode+'.onnx'))
    for n in ('__init__.py','common.py','preprocess.py','sparse24_quality.py'):shutil.copyfile(ROOT/'model/b'/n,package/'b'/n)
    shutil.copyfile(ROOT/'results/B/B8/sparse24_quality_structure_20261013_01/arm_runner.py',package/'arm_runner.py')
    write_json(candidate/'freeze.json',{'utc':utc(),'candidate':name,'files':{p.name:sha(p) for p in candidate.iterdir()},'checkpoint_freeze_sha256':sha(folder/'freeze.json'),'selection_decision_sha256':sha(OUT/'decision.json'),'pre_validation_freeze_sha256':sha(dest/'freeze.json'),'quality_runner_sha256':sha(package/'b/sparse24_quality.py'),'splits_sha256':sha(BUNDLE/'splits.json'),'quant_protocol_sha256':sha(BUNDLE/'quant_protocol.json'),'original_fp32_sha256':sha(graphs['original_fp32']),'scope':'fixed compact12 candidate; existing val500/full3925 only'})
    regression=splits['regression'];write_json(package/'regression.json',regression);local={n:[] for n in ('fp32','w8a8')};hashes=[]
    for r in regression:
        x=preprocess(DATA/r['path']);hashes.append(hashlib.sha256(x.tobytes()).hexdigest())
        for mode in local:local[mode].append(engines['compact_'+mode].run(None,{'image':x})[0][0])
    np.savez(package/'regression_local.npz',inputs=np.array(hashes),**{n:np.array(v) for n,v in local.items()})
    write_json(package/'launch_freeze.json',{'utc':utc(),'files':{p.relative_to(package).as_posix():sha(p) for p in package.rglob('*') if p.is_file()},'PL':'NOT_RUN','status':'PREPARED_NOT_EXECUTED'})
    with tarfile.open(OUT/'arm_package.tar.gz','w:gz') as archive:archive.add(package,arcname='mlp_width_recovery_20261015_01')
    write_json(OUT/'arm_handoff.json',{'status':'PREPARED_NOT_EXECUTED','candidate':name,'archive_sha256':sha(OUT/'arm_package.tar.gz'),'remote_destination':'/home/ubuntu/fpga_project','runner':'arm_runner.py','scope':'exclusive CPU four-thread regression and existing val500/full3925; no PL; no reserved2000','blocking_evidence':'board_connection.json'})

if __name__=='__main__':
    try:main()
    except BaseException:
        write_json(OUT/'validation_failure.json',{'utc':utc(),'error':traceback.format_exc()});raise
