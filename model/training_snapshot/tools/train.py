"""Portable fixed24 100k/5-epoch rerun. Final delivery models are never overwritten.

The historical frozen scripts/receipts, rather than this portability adaptation,
are the provenance for the delivered checkpoint. Acceptance is never read here.
"""
from pathlib import Path
import argparse,math,random,sys,time
sys.dont_write_bytecode=True
import numpy as np
import torch
from torch.nn import functional as F
from torch.utils.data import Dataset,DataLoader
from torchvision import transforms as T
from torchvision.transforms import InterpolationMode
from PIL import Image
from release_common import ROOT,read,save,rows,inside,sha,preprocess
from h576_model import load_onnx_weights,load_masks,enforce_masks,check_masks,export_original_graphs
from evaluate import evaluate as evaluate_onnx

class Images(Dataset):
    def __init__(self,folder):
        self.folder=Path(folder);self.rows=rows(folder)
        self.transform=T.Compose([T.RandomResizedCrop(224,scale=(.8,1.),ratio=(.9,1.1),interpolation=InterpolationMode.BICUBIC),
            T.RandomHorizontalFlip(),T.ToTensor(),T.Normalize([.485,.456,.406],[.229,.224,.225])])
    def __len__(self):return len(self.rows)
    def __getitem__(self,i):
        r=self.rows[i]
        with Image.open(inside(self.folder,r['image'])) as im:x=self.transform(im.convert('RGB'))
        return x,int(r['label'])

def verify_data(folder,role):
    expected=read(ROOT/'datasets/archives.json')[role]
    assert sha(folder/'labels.csv')==expected['labels_sha256'],'Labels do not match the frozen role'
    rs=rows(folder);assert len(rs)==expected['images']
    for r in rs:assert sha(inside(folder,r['image']))==r['image_sha256']

def run(a):
    out=a.output.resolve()
    if out.exists():raise FileExistsError('Use a fresh run directory')
    if out.is_relative_to(ROOT) and not out.is_relative_to(ROOT/'work'):raise ValueError('Run output must be under work/ or outside release')
    assert torch.cuda.is_available(),'CUDA required for this recorded BF16 training policy'
    assert torch.cuda.is_bf16_supported(),'BF16 capable GPU required'
    verify_data(a.train,'train');verify_data(a.tune,'tune')
    policy=read(ROOT/'research_archive/policy.json');seed=policy['seed']
    assert sha(a.initial_model)==policy['source_fp32_sha256'],'Expected historical parent24 FP32'
    expected_teacher=read(ROOT/'RELEASE.json')['external_training_inputs']['teacher_model']['sha256']
    assert sha(a.teacher_model)==expected_teacher,'Expected original Dense FP32 teacher'
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed);torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    model=load_onnx_weights(a.initial_model).cuda()
    teacher=load_onnx_weights(a.teacher_model).cuda().eval().requires_grad_(False)
    masks={n:v.cuda() for n,v in load_masks(ROOT/'models/final').items()};check_masks(model,masks)
    for n,p in model.named_parameters():
        if n in masks:p.register_hook(lambda grad,m=masks[n]:grad*m)
    loader=DataLoader(Images(a.train),batch_size=32,shuffle=True,num_workers=2,pin_memory=True,persistent_workers=True,
        generator=torch.Generator().manual_seed(seed))
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-5,weight_decay=.01)
    epochs=5;total=epochs*math.ceil(len(loader)/2);assert total==7815
    out.mkdir(parents=True);save(out/'policy.json',dict(policy,portability_adaptation=True,acceptance_read=False))
    history=[];update=0
    def checkpoint(epoch):
        folder=out/f'epoch{epoch}';folder.mkdir();check_masks(model,masks)
        torch.save({'model':{n:v.detach().cpu() for n,v in model.state_dict().items()},
            'sparse_masks':{n:v.detach().cpu() for n,v in masks.items()},'epoch':epoch,'updates':update},folder/'model_trainable.pt')
        hashes=export_original_graphs(model,ROOT/'models/final',folder)
        m,z,y=evaluate_onnx(folder/'model_w8a8.onnx',a.tune)
        np.savez_compressed(folder/'tune_w8a8.npz',logits=z,labels=y)
        item={'epoch':epoch,'updates':update,'w8a8_tune':m,'models_sha256':hashes};history.append(item)
        save(folder/'receipt.json',item);save(out/'history.json',history);print('CHECKPOINT',item,flush=True)
    checkpoint(0)
    for epoch in range(1,6):
        model.train();optimizer.zero_grad(set_to_none=True)
        for step,(x,y) in enumerate(loader):
            x=x.cuda(non_blocking=True);y=y.cuda(non_blocking=True)
            with torch.autocast('cuda',dtype=torch.bfloat16):
                with torch.no_grad():target=teacher(x)
                z=model(x);ce=F.cross_entropy(z.float(),y)
                kd=4*F.kl_div(F.log_softmax(z.float()/2,-1),F.softmax(target.float()/2,-1),reduction='batchmean');loss=.5*ce+.5*kd
            assert torch.isfinite(loss).item()
            divisor=1 if step==len(loader)-1 and len(loader)%2 else 2
            (loss/divisor).backward()
            if (step+1)%2==0 or step==len(loader)-1:
                lr=1e-5*(update+1)/100 if update<100 else 1e-6+9e-6*.5*(1+math.cos(math.pi*min(1.,(update-100)/(total-100))))
                for group in optimizer.param_groups:group['lr']=lr
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
                optimizer.step();enforce_masks(model,masks);optimizer.zero_grad(set_to_none=True);update+=1
            if (step+1)%100==0:print('TRAIN',epoch,step+1,'updates',update,'loss',loss.item(),flush=True)
        checkpoint(epoch)
    assert update==total
    winner=max(history,key=lambda h:(h['w8a8_tune']['top1_correct'],h['w8a8_tune']['top5_correct'],-h['w8a8_tune']['cross_entropy'],-h['epoch']))
    save(out/'selection.json',{'selected_epoch':winner['epoch'],'basis':'development W8A8 only; epoch0..5','acceptance_read':False,
        'note':'Acceptance data is already exposed after the historical run; do not reuse it for new unbiased claims.'})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--train',type=Path,default=ROOT/'work/data/train');p.add_argument('--tune',type=Path,default=ROOT/'work/data/tune')
    p.add_argument('--initial-model',type=Path,required=True);p.add_argument('--teacher-model',type=Path,required=True)
    p.add_argument('--output',type=Path,default=ROOT/'work/training/run01');a=p.parse_args();run(a)
