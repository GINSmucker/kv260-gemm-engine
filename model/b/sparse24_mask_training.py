"""One bounded CPU fixed-mask recovery experiment; ORT remains the evaluator."""
import argparse
import copy
import math
import shutil
import time
from pathlib import Path
import numpy as np
import torch
import torch.nn.functional as F
from .common import ROOT,read_json,write_json,sha,utc
from .reference_model import original
from .sparse24_study import BUNDLE,DATA,graph,session
from .sparse24_activation_study import checked_input
from .sparse24_full_recovery import LAYERS,evaluate
from .quantization import quantize
from .preprocess import preprocess
from .sparse24 import logical_pack,logical_unpack,slot_gemm
from .golden_int64 import gemm


def student(source,ids):
    model=original()
    for block in model.blocks: block.attn.fused_attn=False
    for parameter in model.parameters(): parameter.requires_grad_(False)
    masks=[]
    for i in ids:
        module=model.get_submodule(LAYERS[i]['layer_id']); folder=source/'weights'/f'{i:02d}'
        with torch.no_grad(): module.weight.copy_(torch.from_numpy(np.load(folder/'pruned_fp32.npy').T.copy()))
        module.weight.requires_grad_(True)
        masks.append((module.weight,torch.from_numpy(np.load(folder/'mask.npy').T.copy())))
    return model.eval(),masks


def step(model,masks,optimizer,x,target):
    optimizer.zero_grad(set_to_none=True)
    prediction=model(torch.from_numpy(x))
    loss=F.kl_div(F.log_softmax(prediction/2,dim=-1),F.softmax(torch.from_numpy(target)/2,dim=-1),reduction='batchmean')*4
    if not torch.isfinite(loss): raise ValueError('Nonfinite recovery loss')
    loss.backward()
    with torch.no_grad():
        for parameter,mask in masks:
            if parameter.grad is None or not torch.isfinite(parameter.grad).all(): raise ValueError('Missing/nonfinite gradient')
            parameter.grad.mul_(mask)
    optimizer.step()
    with torch.no_grad():
        for parameter,mask in masks: parameter.mul_(mask)
    return float(loss.detach())


def memory():
    import ctypes as c
    from ctypes import wintypes as w
    class ProcessMemory(c.Structure):
        _fields_=[('cb',w.DWORD),('faults',w.DWORD)]+[(n,c.c_size_t) for n in ('peak_rss','rss','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
    class SystemMemory(c.Structure):
        _fields_=[('length',w.DWORD),('load',w.DWORD)]+[(n,c.c_ulonglong) for n in ('total','available','total_page','available_page','total_virtual','available_virtual','extended')]
    p=ProcessMemory(); p.cb=c.sizeof(p); system=SystemMemory(); system.length=c.sizeof(system)
    c.windll.kernel32.GetCurrentProcess.restype=w.HANDLE
    fn=c.windll.psapi.GetProcessMemoryInfo; fn.argtypes=[w.HANDLE,c.POINTER(ProcessMemory),w.DWORD]
    if not fn(c.windll.kernel32.GetCurrentProcess(),c.byref(p),p.cb): raise OSError('GetProcessMemoryInfo failed')
    if not c.windll.kernel32.GlobalMemoryStatusEx(c.byref(system)): raise OSError('GlobalMemoryStatusEx failed')
    return {'rss':p.rss,'peak_rss':p.peak_rss,'private':p.private,'system_available':system.available}


def save_checkpoint(out,source,model,ids,epoch):
    name='train_epoch'+str(epoch); dest=out/name; dest.mkdir(exist_ok=False)
    shutil.copyfile(out/'selection.json',dest/'selection.json')
    checks=[]
    for i in ids:
        folder=dest/'weights'/f'{i:02d}'; folder.mkdir(parents=True)
        for field in ('mask','positions'): shutil.copyfile(source/'weights'/f'{i:02d}'/(field+'.npy'),folder/(field+'.npy'))
        mask=np.load(folder/'mask.npy'); pos=np.load(folder/'positions.npy')
        w=model.get_submodule(LAYERS[i]['layer_id']).weight.detach().numpy().T.copy()
        assert np.isfinite(w).all() and np.all(w[~mask]==0)
        q,s=quantize(w,0); values=logical_pack(q,pos)
        np.testing.assert_array_equal(logical_unpack(values,pos),q)
        x=np.random.default_rng(20261013+i).integers(-127,128,(LAYERS[i]['M'],LAYERS[i]['K']),dtype=np.int8)
        ref=gemm(x,q); np.testing.assert_array_equal(slot_gemm(x,values,pos),ref)
        for field,a in [('pruned_fp32',w),('Wq',q),('sw',s),('values',values),('integer_input',x),('integer_reference',ref)]: np.save(folder/(field+'.npy'),a)
        checks.append({'index':i,'integer_outputs':ref.size,'differences':0,'mask_sha256':sha(folder/'mask.npy'),'files':{p.name:sha(p) for p in folder.iterdir()}})
    write_json(dest/'training_weights.json',{'status':'PASS_NUMERICS_ONLY','epoch':epoch,'layers':checks})
    return name


def run(out):
    decision=read_json(out/'training_decision.json')
    if not decision['required']: return
    target=decision['candidate']; source=out/target
    row=next(r for r in read_json(out/'sensitivity.json')['candidates'] if r['name']==target); ids=row['indices']
    evidence=out/'training'; evidence.mkdir(exist_ok=False)
    shutil.copyfile(__file__,evidence/'runner.py')
    write_json(evidence/'freeze.json',{'utc':utc(),'target':target,'indices':ids,'source_sha256':sha(__file__),'protocol_sha256':sha(out/'protocol.json'),'target_weights':{str(i):{p.name:sha(p) for p in (source/'weights'/f'{i:02d}').glob('*.npy')} for i in ids},'train_sha256':sha(out/'recovery_train.json'),'torch':torch.__version__,'budget_seconds':14400,'seed':20261012,'epochs':3,'batch':8,'lr':1e-5,'temperature':2,'teacher':'original ORT FP32','quality':'PROHIBITED'})
    torch.manual_seed(20261012); torch.set_num_threads(4); torch.use_deterministic_algorithms(True)
    model,masks=student(source,ids); oracle=session(graph(source,ids,'fp32'))
    regression=read_json(out/'regression.json'); records=[]
    for start in range(0,len(regression),8):
        x=np.concatenate([checked_input(r) for r in regression[start:start+8]])
        reference=np.concatenate([oracle.run(None,{'image':z[None]})[0] for z in x])
        with torch.inference_mode(): actual=model(torch.from_numpy(x)).numpy()
        np.savez(evidence/f'regression_{start:02d}.npz',actual=actual,reference=reference)
        ok=bool(np.allclose(actual,reference,atol=1e-4,rtol=1e-4))
        records.append({'start':start,'samples':len(x),'max_abs':float(np.max(np.abs(actual-reference))),'pass':ok})
    write_json(evidence/'regression.json',{'status':'PASS' if all(r['pass'] for r in records) else 'FAIL','records':records})
    if not all(r['pass'] for r in records):
        write_json(evidence/'decision.json',{'status':'NOT_RUN_NUMERICAL_PARITY_FAILED'}); return
    del oracle
    rows=read_json(out/'recovery_train.json'); teacher=session((BUNDLE/'graphs/model_fp32.onnx').read_bytes())
    optimizer=torch.optim.Adam([p for p,m in masks],lr=1e-5,foreach=False)
    timings=[]; probe_start=time.perf_counter()
    for batch in range(30):
        tick=time.perf_counter(); x=np.concatenate([checked_input(r) for r in rows[batch*8:(batch+1)*8]]); prep=time.perf_counter()-tick
        tick=time.perf_counter(); y=np.concatenate([teacher.run(None,{'image':z[None]})[0] for z in x]); teaching=time.perf_counter()-tick
        tick=time.perf_counter(); loss=step(model,masks,optimizer,x,y); training=time.perf_counter()-tick
        timings.append({'batch':batch,'warmup':batch<10,'preprocess_seconds':prep,'teacher_seconds':teaching,'train_seconds':training,'loss':loss,'memory':memory()})
        if time.perf_counter()-probe_start>600:
            write_json(evidence/'probe.json',{'status':'BUDGET_EXCEEDED','batches':timings})
            write_json(evidence/'decision.json',{'status':'NOT_RUN_PROBE_BUDGET'}); return
    measured=timings[10:]
    prep=float(np.mean([r['preprocess_seconds'] for r in measured])); teach=float(np.mean([r['teacher_seconds'] for r in measured])); train=float(np.mean([r['train_seconds'] for r in measured]))
    batches=math.ceil(len(rows)/8)
    selection_seconds=sum(row['results'][m]['seconds'] for m in ('fp32','w8a8'))
    estimated=(prep+teach)*batches+3*(prep+train)*batches+3*selection_seconds+60
    write_json(evidence/'probe.json',{'status':'PASS','seconds':time.perf_counter()-probe_start,'batches':timings,'estimated_total_seconds':estimated,'estimate_includes':'teacher cache, three epochs including preprocessing, three FP/W8 evaluations, 60s export allowance'})
    print('TRAIN_PROBE',round(estimated,2),memory(),flush=True)
    if estimated>14400:
        write_json(evidence/'decision.json',{'status':'NOT_RUN_COST_EXCEEDS_BUDGET','estimated_seconds':estimated}); return
    del model,masks,optimizer
    torch.manual_seed(20261012); model,masks=student(source,ids)
    optimizer=torch.optim.Adam([p for p,m in masks],lr=1e-5,foreach=False)
    fixed={n:p.detach().clone() for n,p in model.named_parameters() if not p.requires_grad}
    start=time.perf_counter(); deadline=start+14400
    targets=np.empty((len(rows),1000),np.float32)
    for i,r in enumerate(rows):
        targets[i]=teacher.run(None,{'image':checked_input(r)})[0][0]
        if (i+1)%500==0: print('TEACHER_CACHE',i+1,len(rows),flush=True)
        if time.perf_counter()>deadline:
            write_json(evidence/'decision.json',{'status':'STOPPED_TIME_BUDGET_DURING_CACHE'}); return
    np.save(evidence/'teacher_logits.npy',targets); del teacher
    rng=np.random.default_rng(20261012); completed=[]; losses=[]
    for epoch in range(1,4):
        if time.perf_counter()+(prep+train)*batches+selection_seconds+20>deadline: break
        order=rng.permutation(len(rows)); np.save(evidence/f'epoch{epoch}_order.npy',order)
        for b,offset in enumerate(range(0,len(rows),8)):
            chosen=order[offset:offset+8]
            x=np.concatenate([preprocess(DATA/rows[j]['path']) for j in chosen])
            tick=time.perf_counter(); loss=step(model,masks,optimizer,x,targets[chosen]); elapsed=time.perf_counter()-tick
            losses.append({'epoch':epoch,'batch':b,'samples':len(chosen),'loss':loss,'step_seconds':elapsed})
            if (b+1)%50==0:
                write_json(evidence/'progress.json',{'utc':utc(),'epoch':epoch,'batch':b+1,'total_batches':batches,'seconds':time.perf_counter()-start,'loss':loss,'memory':memory()})
                print('TRAIN_PROGRESS',epoch,b+1,batches,round(loss,6),flush=True)
            if time.perf_counter()+selection_seconds+20>deadline:
                write_json(evidence/'losses.json',losses); write_json(evidence/'decision.json',{'status':'STOPPED_TIME_BUDGET_PARTIAL_EPOCH','completed':completed,'partial_epoch':epoch,'seconds':time.perf_counter()-start}); return
        for n,p in model.named_parameters():
            if n in fixed: assert torch.equal(p.detach(),fixed[n]),'Frozen parameter changed: '+n
        name=save_checkpoint(out,source,model,ids,epoch)
        # Candidate graphs are the existing ORT graphs with only weight initializers replaced.
        check=session(graph(out/name,ids,'fp32'))
        for r in regression:
            x=checked_input(r)
            with torch.inference_mode(): y=model(torch.from_numpy(x)).numpy()
            np.testing.assert_allclose(check.run(None,{'image':x})[0],y,atol=1e-4,rtol=1e-4)
        del check
        evaluation=evaluate(out,name,ids)
        saved=read_json(out/'sensitivity.json'); saved['candidates'][-1]['method']='fixed_mask_distillation'; write_json(out/'sensitivity.json',saved)
        completed.append(name)
        write_json(evidence/'losses.json',losses)
        print('TRAIN_EPOCH_COMPLETE',epoch,evaluation['results']['fp32']['top1_correct'],evaluation['results']['w8a8']['top1_correct'],flush=True)
    write_json(evidence/'decision.json',{'status':'COMPLETE' if len(completed)==3 else 'STOPPED_TIME_BUDGET','completed':completed,'seconds':time.perf_counter()-start,'teacher_sha256':sha(evidence/'teacher_logits.npy'),'frozen_parameters_unchanged':True,'formal_quality':'NOT_RUN'})


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    try: run(a.output.resolve())
    except Exception as e:
        write_json(a.output/'training/failure.json',{'utc':utc(),'error':repr(e)}); raise
