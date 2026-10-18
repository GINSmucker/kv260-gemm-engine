"""Bounded compact-MLP recovery; existing ORT graphs remain the evaluator."""
from pathlib import Path
import copy, math, time, traceback, hashlib
import numpy as np
import torch
import torch.nn.functional as F
import onnx
from onnx import numpy_helper as NH, helper as H, TensorProto as T
from b.common import ROOT, read_json, write_json, sha, utc
from b.reference_model import original
from b.sparse24_study import session, BUNDLE, DATA
from b.preprocess import preprocess
from b.quantization import quantize
from b.golden_int64 import gemm
from b.sparse24_mask_training import memory

OUT=Path(__file__).resolve().parent
PARENT=ROOT/'results/B/B8/mlp_width_quality_20261014_01'
REC=ROOT/'results/B/B8/sparse24_full_recovery_20261013_01'
OLD=ROOT/'results/B/B8/sparse24_20261012_01'
TRAIN_NAMES={f'blocks.{i}.mlp.fc{j}.weight' for i in range(12) for j in (1,2)}
SEED=20261015

def build():
    m=original()
    arrays={v.name:NH.to_array(v).copy() for v in onnx.load(PARENT/'compact12/fp32.onnx').graph.initializer}
    for b in m.blocks:
        b.attn.fused_attn=False
        b.mlp.fc1=torch.nn.Linear(192,576)
        b.mlp.fc2=torch.nn.Linear(576,192)
    with torch.no_grad():
        for n,p in m.named_parameters():
            if n in arrays: p.copy_(torch.from_numpy(arrays[n]))
            p.requires_grad_(n in TRAIN_NAMES)
    assert {n for n,p in m.named_parameters() if p.requires_grad}==TRAIN_NAMES
    return m.eval()

def step(m,opt,x,y):
    opt.zero_grad(set_to_none=True)
    pred=m(torch.from_numpy(x))
    loss=F.kl_div(F.log_softmax(pred/2,dim=-1),F.softmax(torch.from_numpy(y)/2,dim=-1),reduction='batchmean')*4
    assert torch.isfinite(loss)
    loss.backward()
    for p in m.parameters():
        if p.requires_grad: assert p.grad is not None and torch.isfinite(p.grad).all()
    opt.step()
    return float(loss.detach())

def checked(r):
    assert sha(DATA/r['path'])==r['image_sha256']
    return preprocess(DATA/r['path'])

def parity(m,path,rows,dest):
    s=session(Path(path).read_bytes()); actual=[]; refs=[]
    for r in rows:
        x=checked(r)
        with torch.inference_mode(): actual.append(m(torch.from_numpy(x)).numpy()[0])
        refs.append(s.run(None,{'image':x})[0][0])
    a=np.array(actual); b=np.array(refs); np.savez(dest,actual=a,reference=b)
    np.testing.assert_allclose(a,b,atol=1e-4,rtol=1e-4)
    return {'samples':len(rows),'max_abs':float(np.max(np.abs(a-b))),'argmax_equal':bool(np.array_equal(a.argmax(1),b.argmax(1))),'status':'PASS','tolerance':'atol=rtol=1e-4','raw_sha256':sha(dest)}

def export(m,epoch):
    folder=OUT/f'epoch{epoch}';folder.mkdir(exist_ok=False)
    fp=onnx.load(PARENT/'compact12/fp32.onnx')
    values={n:p.detach().numpy().copy() for n,p in m.named_parameters() if n in TRAIN_NAMES}
    np.savez(folder/'weights.npz',**values)
    for t in fp.graph.initializer:
        if t.name in values: t.CopyFrom(NH.from_array(values[t.name],t.name))
    onnx.checker.check_model(fp);onnx.save(fp,folder/'fp32.onnx')
    arrays={v.name:NH.to_array(v) for v in fp.graph.initializer}
    ref=onnx.load(PARENT/'compact12/w8a8.onnx');updates={};layers=copy.deepcopy(read_json(PARENT/'compact12/layers.json'))
    for l in layers:
        i=l['layer_index']; w=arrays[l['layer_id']+'.weight'].T.copy();bias=arrays[l['layer_id']+'.bias'];q,s=quantize(w,0)
        updates[f'BQL_{i}_Wf']=q.astype(np.float32);updates[f'BQL_{i}_sf']=s.reshape(-1);updates[f'BQL_{i}_bf']=bias
        l['numeric_identity']={n:hashlib.sha256(a.tobytes()).hexdigest() for n,a in [('Wq',q),('sw',s.reshape(-1)),('bias',bias)]}
        l['original_weight_sha256']=hashlib.sha256(w.T.copy().tobytes()).hexdigest()
    for t in ref.graph.initializer:
        if t.name in updates:t.CopyFrom(NH.from_array(updates[t.name],t.name))
    onnx.checker.check_model(ref);onnx.save(ref,folder/'w8a8.onnx');write_json(folder/'layers.json',layers)
    for mode,g in [('fp32',fp),('w8a8',ref)]:
        parent=onnx.load(PARENT/f'compact12/{mode}.onnx')
        assert [n.SerializeToString() for n in g.graph.node]==[n.SerializeToString() for n in parent.graph.node]
    write_json(folder/'freeze.json',{'utc':utc(),'epoch':epoch,'before_selection':True,'files':{p.name:sha(p) for p in folder.iterdir()},'structure':'all12 MLP H576','PL':'NOT_READY'})
    return folder

def evaluate(folder,x,labels,base):
    result={}
    for mode in ('fp32','w8a8'):
        s=session((folder/(mode+'.onnx')).read_bytes()); logits=np.empty((len(x),1000),np.float32);times=[]
        for i in range(len(x)):
            start=time.perf_counter();logits[i]=s.run(None,{'image':x[i:i+1]})[0][0];times.append(time.perf_counter()-start)
            if (i+1)%250==0:print('SELECT',folder.name,mode,i+1,flush=True)
        assert np.isfinite(logits).all()
        np.save(folder/(mode+'_logits.npy'),logits);np.save(folder/(mode+'_times.npy'),np.array(times))
        rank=np.argsort(-logits,axis=1,kind='stable')[:,:5]
        result[mode]={'top1':int(np.sum(rank[:,0]==labels)),'top5':int(np.sum(np.any(rank==labels[:,None],axis=1))),'samples':len(x),'seconds':sum(times),'logits_sha256':sha(folder/(mode+'_logits.npy')),'MSE':float(np.mean((logits.astype(float)-base)**2))}
        print('RESULT',folder.name,mode,result[mode]['top1'],result[mode]['top5'],flush=True)
    write_json(folder/'selection.json',result)
    return result

def integer(folder):
    layers=read_json(folder/'layers.json');g=onnx.load(folder/'w8a8.onnx');ws={t.name:NH.to_array(t) for t in g.graph.initializer};plain=session(g.SerializeToString())
    for l in layers:
        i=l['layer_index'];g.graph.output.append(H.make_tensor_value_info(f'BQL_{i}_xq',T.FLOAT,l['input_shape']));g.graph.output.append(H.make_tensor_value_info(f'BQL_{i}_i32',T.INT32,l['output_shape']))
    s=session(g.SerializeToString());rows=read_json(BUNDLE/'splits.json')['splits']['regression'];records=[];dest=folder/'integer';dest.mkdir()
    for j in (0,7,19):
        x=checked(rows[j]);v=s.run(None,{'image':x});np.testing.assert_array_equal(v[0],plain.run(None,{'image':x})[0])
        for l in layers:
            i=l['layer_index'];a,y=v[1+2*i:3+2*i];np.testing.assert_array_equal(a,np.rint(a));assert np.max(np.abs(a))<=127
            xq=np.ascontiguousarray(a.reshape(l['M'],l['K']),dtype=np.int8);wf=ws[f'BQL_{i}_Wf'];q=wf.astype(np.int8);np.testing.assert_array_equal(q,wf)
            expected=gemm(xq,q);np.testing.assert_array_equal(y.reshape(expected.shape),expected)
            p=dest/f'image{j:02d}_layer{i:02d}.npz';np.savez_compressed(p,Xq=xq,actual=y.reshape(expected.shape),reference=expected)
            records.append({'image':j,'layer':i,'outputs':expected.size,'file':p.relative_to(OUT).as_posix(),'sha256':sha(p)})
    write_json(OUT/'integer_verification.json',{'status':'PASS','candidate':folder.name,'cases':records,'outputs':sum(r['outputs'] for r in records),'differences':0})

def run():
    assert not (OUT/'protocol.json').exists(),'No restart or repeated selection'
    start=time.perf_counter();deadline=start+3600
    protocol={'utc':utc(),'experiment_date':'2026-10-15','structure':'compact12 H576','trainable':sorted(TRAIN_NAMES),'fixed':'all other parameters including biases; no new architecture','seed':SEED,'threads':4,'optimizer':'Adam lr1e-5 foreach=False','loss':'original ORT FP32 teacher, T2 KL times4','batch':8,'epochs_max':3,'checkpoints_max':3,'budget_seconds':3600,'probe_batches':12,'probe_seconds_max':180,'selection':'both FP32 and W8 top1>=759; rank max(min(top1),sum(top1),-epoch); no extra candidates','formal_drop_pp':1.0,'reserved2000':'NO_READ_NO_PREDICTIONS','hardware':'NONE','source_sha256':sha(__file__),'parent_freeze_sha256':sha(PARENT/'compact12/freeze.json'),'teacher_sha256':sha(REC/'training/teacher_logits.npy'),'train_manifest_sha256':sha(REC/'recovery_train.json'),'selection_sha256':sha(PARENT/'selection.json')}
    write_json(OUT/'protocol.json',protocol)
    for p,h in read_json(PARENT/'compact12/freeze.json')['files'].items():assert sha(PARENT/'compact12'/p)==h
    manifest={r['file']:r['sha256'] for r in read_json(REC/'evidence_manifest.json')['files']}
    for p in ('recovery_train.json','training/teacher_logits.npy','training/freeze.json'):assert sha(REC/p)==manifest[p]
    assert protocol['teacher_sha256']==read_json(REC/'training/decision.json')['teacher_sha256']
    assert protocol['train_manifest_sha256']==read_json(REC/'training/freeze.json')['train_sha256']
    rows=read_json(REC/'recovery_train.json');assert len(rows)==6193
    roles=[{r['image_sha256'] for r in rows}]+[{r['image_sha256'] for r in read_json(PARENT/(role+'.json'))} for role in ('fit','local','selection')]
    assert all(not roles[i]&roles[j] for i in range(4) for j in range(i))
    for r in rows:assert sha(DATA/r['path'])==r['image_sha256']
    targets=np.load(REC/'training/teacher_logits.npy');assert targets.shape==(6193,1000) and np.isfinite(targets).all()
    teacher=session((BUNDLE/'graphs/model_fp32.onnx').read_bytes())
    for i in (0,3096,6192):np.testing.assert_array_equal(teacher.run(None,{'image':checked(rows[i])})[0][0],targets[i])
    del teacher
    write_json(OUT/'data_verification.json',{'status':'PASS','train_images_verified':6193,'disjoint_roles':['train6193','fit128','local128','selection1000'],'teacher_reuse':'historical manifest + ordered training manifest + three bitwise probes','reserved2000':'NOT_READ','ten_class_scope':True})
    torch.manual_seed(SEED);torch.use_deterministic_algorithms(True);torch.set_num_threads(4)
    m=build();reg=read_json(BUNDLE/'splits.json')['splits']['regression']
    write_json(OUT/'initial_parity.json',parity(m,PARENT/'compact12/fp32.onnx',reg,OUT/'initial_parity.npz'))
    opt=torch.optim.Adam([p for p in m.parameters() if p.requires_grad],lr=1e-5,foreach=False)
    timings=[];probe=time.perf_counter()
    for b in range(12):
        tick=time.perf_counter();x=np.concatenate([checked(r) for r in rows[b*8:(b+1)*8]]);loss=step(m,opt,x,targets[b*8:(b+1)*8]);timings.append(time.perf_counter()-tick)
        assert time.perf_counter()-probe<180,'Probe exceeded fixed budget'
    estimated=float(np.mean(timings[4:]))*math.ceil(len(rows)/8)*3+240
    write_json(OUT/'probe.json',{'status':'PASS' if estimated<deadline-time.perf_counter() else 'OVER_BUDGET','batch_seconds':timings,'estimated_training_export_selection_seconds':estimated,'memory':memory(),'probe_weights_discarded':True})
    print('PROBE',estimated,flush=True)
    if estimated>=deadline-time.perf_counter():write_json(OUT/'decision.json',{'status':'NOT_RUN_COST_BUDGET'});return
    del m,opt
    torch.manual_seed(SEED);m=build();opt=torch.optim.Adam([p for p in m.parameters() if p.requires_grad],lr=1e-5,foreach=False)
    fixed={n:p.detach().clone() for n,p in m.named_parameters() if not p.requires_grad}
    x=np.load(OLD/'selection_inputs.npy',mmap_mode='r');pp=read_json(PARENT/'protocol.json');assert sha(OLD/'selection_inputs.npy')==pp['selection_inputs_sha256']
    base=np.load(OLD/'selection_results/baseline/fp32_logits.npy');assert sha(OLD/'selection_results/baseline/fp32_logits.npy')==pp['reference_logits_sha256']
    labels=np.array([r['label'] for r in read_json(PARENT/'selection.json')]);assert int(np.sum(base.argmax(1)==labels))==764
    rng=np.random.default_rng(SEED);completed=[];losses=[]
    for epoch in range(1,4):
        if time.perf_counter()+estimated/3>deadline:break
        order=rng.permutation(len(rows));np.save(OUT/f'epoch{epoch}_order.npy',order)
        for batch,offset in enumerate(range(0,len(rows),8)):
            idx=order[offset:offset+8];inputs=np.concatenate([preprocess(DATA/rows[j]['path']) for j in idx]);tick=time.perf_counter();loss=step(m,opt,inputs,targets[idx]);losses.append({'epoch':epoch,'batch':batch,'samples':len(idx),'loss':loss,'step_seconds':time.perf_counter()-tick})
            if (batch+1)%50==0:
                write_json(OUT/'progress.json',{'utc':utc(),'epoch':epoch,'batch':batch+1,'batches':math.ceil(len(rows)/8),'seconds':time.perf_counter()-start,'loss':loss,'memory':memory()});print('TRAIN',epoch,batch+1,round(loss,6),flush=True)
            if time.perf_counter()+90>deadline:
                write_json(OUT/'losses.json',losses);write_json(OUT/'decision.json',{'status':'STOPPED_BUDGET_PARTIAL_EPOCH','completed':completed});return
        for n,p in m.named_parameters():
            if n in fixed:assert torch.equal(p.detach(),fixed[n]),n
        folder=export(m,epoch);write_json(folder/'parity.json',parity(m,folder/'fp32.onnx',reg,folder/'parity.npz'))
        result=evaluate(folder,x,labels,base);completed.append({'epoch':epoch,'name':folder.name,'results':result,'eligible':all(v['top1']>=759 for v in result.values())});write_json(OUT/'selection_progress.json',completed);write_json(OUT/'losses.json',losses)
    eligible=[r for r in completed if r['eligible']]
    best=max(eligible or completed,key=lambda r:(min(v['top1'] for v in r['results'].values()),sum(v['top1'] for v in r['results'].values()),-r['epoch'])) if completed else None
    write_json(OUT/'decision.json',{'status':'COMPLETE' if len(completed)==3 else 'STOPPED_BUDGET','completed':completed,'selected':best['name'] if eligible else None,'best_diagnostic':best['name'] if best else None,'seconds':time.perf_counter()-start,'frozen_parameters_unchanged':True,'formal_quality':'NOT_RUN','ARM':'NOT_RUN','reserved2000_predictions':0})
    if best:integer(OUT/best['name'])

if __name__=='__main__':
    try:run()
    except BaseException:
        write_json(OUT/'failure.json',{'utc':utc(),'status':'FAIL','error':traceback.format_exc()});raise
