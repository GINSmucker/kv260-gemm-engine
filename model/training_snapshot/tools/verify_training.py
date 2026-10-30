"""Small reconstruction/export/gradient smoke test; never retrains the delivered model."""
from pathlib import Path
import argparse,sys
sys.dont_write_bytecode=True
import numpy as np
import onnx
from onnx import numpy_helper
import torch
from torch.nn import functional as F
from release_common import ROOT,read,session,save
from h576_model import load_onnx_weights,load_masks,enforce_masks,check_masks,export_original_graphs

def test(work,teacher_model):
    work=Path(work).resolve()
    if work.exists():raise FileExistsError('Use fresh scratch space')
    if work.is_relative_to(ROOT) and not work.is_relative_to(ROOT/'work'):raise ValueError('Scratch must be outside immutable release')
    work.mkdir(parents=True);torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    source=ROOT/'models/final';model=load_onnx_weights(source/'model_fp32.onnx').eval();masks=load_masks(source);check_masks(model,masks)
    checkpoint=torch.load(source/'model_trainable.pt',map_location='cpu',weights_only=True)
    for n,v in model.state_dict().items():assert torch.equal(v,checkpoint['model'][n]),n
    results=[];fp=session(source/'model_fp32.onnx');q=session(source/'model_w8a8.onnx')
    export_original_graphs(model,source,work/'roundtrip');fp2=session(work/'roundtrip/model_fp32.onnx');q2=session(work/'roundtrip/model_w8a8.onnx')
    xs=[];ys=[]
    for s in read(ROOT/'samples/samples.json'):
        x=np.load(ROOT/f"samples/{s['id']}_input.npy");xs.append(x);ys.append(s['label'])
        with torch.inference_mode():z=model(torch.from_numpy(x)).numpy()
        reference=fp.run(None,{fp.get_inputs()[0].name:x})[0]
        assert np.allclose(z,reference,atol=5e-4,rtol=5e-4)
        np.testing.assert_array_equal(np.argsort(-z,axis=1,kind='stable')[:,:5],np.argsort(-reference,axis=1,kind='stable')[:,:5])
        np.testing.assert_array_equal(fp2.run(None,{fp2.get_inputs()[0].name:x})[0],reference)
        np.testing.assert_array_equal(q2.run(None,{q2.get_inputs()[0].name:x})[0],q.run(None,{q.get_inputs()[0].name:x})[0])
        results.append({'sample_id':s['id'],'fp32_torch_vs_onnx_max_abs':float(np.max(np.abs(z-reference))),
            'fp32_w8a8_export_logits_bitwise_equal':True})
    assert torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    torch.manual_seed(2026102505);model=model.cuda().train();masks={n:v.cuda() for n,v in masks.items()}
    teacher=load_onnx_weights(teacher_model).cuda().eval().requires_grad_(False)
    for n,p in model.named_parameters():
        if n in masks:p.register_hook(lambda g,m=masks[n]:g*m)
    before=model.patch_embed.proj.weight.detach().clone();optimizer=torch.optim.AdamW(model.parameters(),lr=1e-7,weight_decay=.01)
    x=torch.from_numpy(np.concatenate(xs)).cuda();y=torch.tensor(ys,device='cuda')
    with torch.autocast('cuda',dtype=torch.bfloat16):
        with torch.no_grad():target=teacher(x)
        z=model(x);ce=F.cross_entropy(z.float(),y)
        kd=4*F.kl_div(F.log_softmax(z.float()/2,-1),F.softmax(target.float()/2,-1),reduction='batchmean');loss=.5*ce+.5*kd
    assert torch.isfinite(loss).item();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step();enforce_masks(model,masks);check_masks(model,masks)
    assert not torch.equal(before,model.patch_embed.proj.weight.detach()),'Nonlinear/full-network parameters must train'
    export_original_graphs(model,source,work/'smoke_updated')
    for p in (work/'smoke_updated').glob('*.onnx'):onnx.checker.check_model(onnx.load(p))
    return {'status':'PASS','parameter_tensors':len(checkpoint['model']),'fixed_masks':len(masks),'reconstruction':results,
        'single_bf16_gradient_step':{'finite_loss':float(loss.item()),'mask_zeros_preserved':True,'patch_weight_updated':True,'export_onnx_valid':True},
        'full_five_epoch_portable_rerun':'NOT_RUN; historical frozen run is separately documented',
        'delivered_weights_modified':False,'hardware_accessed':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--work-dir',type=Path,default=ROOT/'work/training_smoke/run01');p.add_argument('--output',type=Path)
    p.add_argument('--teacher-model',type=Path,required=True)
    a=p.parse_args();result=test(a.work_dir,a.teacher_model)
    if a.output:save(a.output,result)
    print(result)
