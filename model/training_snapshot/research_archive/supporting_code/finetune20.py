"""One bounded recovery experiment: fixed masks, full-network CE + dense-teacher KD.

Epoch selection uses the exposed development2000 only. Acceptance images and
regression images are never used by training or checkpoint selection.
"""
from pathlib import Path
import argparse,copy,csv,hashlib,io,json,math,random,sys,time
import numpy as np
from PIL import Image
import torch
from torch.nn import functional as F
from torch.utils.data import Dataset,DataLoader
from torchvision import transforms as T
from torchvision.transforms import InterpolationMode
from finetune_model import ROOT,SOURCE,H576,load_onnx_weights,enforce_masks,check_masks,export_original_graphs,sha,save_json

from finetune20_common import RUN, DATA, PARENT as CANDIDATE, SEED, EXPECTED_FP, EXPECTED_Q, SELECTED, read, read_rows
sys.path.insert(0,str(ROOT/'vendor'))
from eval_models import preprocess_image

class Images(Dataset):
    def __init__(self,folder,train=False):
        self.folder=Path(folder)
        with (self.folder/'labels.csv').open(encoding='utf-8-sig',newline='') as f:self.rows=list(csv.DictReader(f))
        self.transform=T.Compose([T.RandomResizedCrop(224,scale=(0.8,1.0),ratio=(0.9,1.1),interpolation=InterpolationMode.BICUBIC),T.RandomHorizontalFlip(),T.ToTensor(),T.Normalize([0.485,0.456,0.406],[0.229,0.224,0.225])]) if train else None
    def __len__(self):return len(self.rows)
    def __getitem__(self,i):
        r=self.rows[i];path=self.folder/r['image']
        if self.transform:
            with Image.open(path) as im:x=self.transform(im.convert('RGB'))
        else:x=torch.from_numpy(preprocess_image(path)[0])
        return x,int(r['label'])

@torch.inference_mode()
def evaluate(model,loader,device):
    model.eval();correct1=correct5=n=0;total_loss=0.;values=[];ys=[]
    for x,y in loader:
        x=x.to(device);y=y.to(device)
        z=model(x);r=z.argsort(dim=-1,descending=True,stable=True)[:,:5]
        correct1+=(r[:,0]==y).sum().item();correct5+=(r==y[:,None]).any(dim=1).sum().item()
        total_loss+=F.cross_entropy(z,y,reduction='sum').item();n+=len(y)
        values.append(z.cpu().numpy());ys.append(y.cpu().numpy())
    return {'images':n,'top1_correct':correct1,'top5_correct':correct5,'top1_pct':100*correct1/n,'top5_pct':100*correct5/n,'cross_entropy':total_loss/n},np.concatenate(values),np.concatenate(ys)

def load_masks(candidate):
    masks={}
    layers=read(candidate/'layers.json')
    assert tuple(l['layer_index'] for l in layers if l['weights'])==SELECTED
    for layer in layers:
        if layer['weights']:
            with np.load(candidate/layer['weights']) as z: mask=z['mask'].T.copy()
            assert np.all(mask.reshape(mask.shape[0],-1,4).sum(-1)==2)
            masks[layer['layer_id']+'.weight']=torch.from_numpy(mask)
    assert len(masks)==20
    return masks


def parity():
    import onnxruntime as ort
    torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    samples=Images(DATA/'tune')
    result={}
    for name,path in [('dense',SOURCE/'h576_fp32.onnx'),('sparse20',CANDIDATE/'model_fp32.onnx')]:
        model=load_onnx_weights(path).eval()
        opt=ort.SessionOptions();opt.intra_op_num_threads=4;opt.inter_op_num_threads=1
        opt.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        sess=ort.InferenceSession(str(path),opt,providers=['CPUExecutionProvider'])
        differences=[];rank_match=[]
        for i in [0,37,139,288,450,666,821,999]:
            x=samples[i][0][None]
            with torch.inference_mode():z=model(x).numpy()
            target=sess.run(None,{sess.get_inputs()[0].name:x.numpy()})[0]
            assert np.allclose(z,target,atol=5e-4,rtol=5e-4),(name,float(np.max(np.abs(z-target))))
            differences.append(float(np.max(np.abs(z-target))))
            rank_match.append(bool(np.array_equal(np.argsort(-z,axis=1,kind='stable')[:,:5],np.argsort(-target,axis=1,kind='stable')[:,:5])))
        result[name]={'source_sha256':sha(path),'images':8,'max_abs_logit_error':max(differences),'all_top5_equal':all(rank_match)}
        if name=='sparse20':
            masks=load_masks(CANDIDATE);check_masks(model,masks)
            assert len(masks)==20
    save_json(RUN/'parity.json',{'status':'PASS','models':result,'architecture':'12 blocks, embed192, 3 heads, MLP576, exact GELU, LayerNorm eps1e-6, no dropout','scope':'CPU FP32 original ONNX vs trainable reconstruction on eight exposed tuning images'})
    print(json.dumps(result),flush=True)

def train(epochs=3,batch=32):
    RUN.mkdir(parents=True,exist_ok=True)
    assert json.loads((RUN/'parity.json').read_text())['status']=='PASS'
    assert json.loads((DATA/'manifest.json').read_text())['role_counts']=={'train':20000,'tune':2000,'test900':1800}
    policy=read(RUN/'policy.json')
    assert sha(CANDIDATE/'model_fp32.onnx')==EXPECTED_FP and sha(CANDIDATE/'model_w8a8.onnx')==EXPECTED_Q
    for name,expected in policy['scripts_sha256'].items():assert sha(ROOT/'scripts'/name)==expected,name
    assert sha(DATA/'manifest.json')==policy['training_data_manifest_sha256']
    for role in ['train','tune']:
        assert sha(DATA/role/'labels.csv')==read(DATA/'manifest.json')['csv_sha256'][role]
        for r in read_rows(DATA/role):assert sha(DATA/role/r['image'])==r['image_sha256']
    assert torch.cuda.is_available(),'CUDA required for this bounded run'
    random.seed(SEED);np.random.seed(SEED);torch.manual_seed(SEED);torch.cuda.manual_seed_all(SEED)
    torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    device='cuda';model=load_onnx_weights(CANDIDATE/'model_fp32.onnx').to(device)
    teacher=load_onnx_weights(SOURCE/'h576_fp32.onnx').to(device).eval().requires_grad_(False)
    masks={n:v.to(device) for n,v in load_masks(CANDIDATE).items()}
    mask_digest=hashlib.sha256(b''.join(n.encode()+masks[n].cpu().numpy().tobytes() for n in sorted(masks))).hexdigest()
    for name,p in model.named_parameters():
        if name in masks:p.register_hook(lambda grad,m=masks[name]:grad*m)
    training=Images(DATA/'train',train=True);tuning=Images(DATA/'tune')
    # Windows spawn workers import this module safely under the main guard.
    workers=2
    gen=torch.Generator().manual_seed(SEED)
    train_loader=DataLoader(training,batch_size=batch,shuffle=True,num_workers=workers,pin_memory=True,persistent_workers=True,generator=gen)
    tune_loader=DataLoader(tuning,batch_size=batch,shuffle=False,num_workers=workers,pin_memory=True,persistent_workers=True)
    config={'status':'RUNNING','source_candidate':str(CANDIDATE),'source_fp32_sha256':sha(CANDIDATE/'model_fp32.onnx'),'source_w8a8_sha256':sha(CANDIDATE/'model_w8a8.onnx'),
            'teacher_sha256':sha(SOURCE/'h576_fp32.onnx'),'seed':SEED,'epochs':epochs,'micro_batch':batch,'gradient_accumulation':2,
            'optimizer':'AdamW','learning_rate_peak':3e-5,'learning_rate_min':3e-6,'weight_decay':0.01,'warmup_updates':100,
            'loss':'0.5 * CE_1000classes + 0.5 * T^2 KL(teacher_T || student_T)','temperature':2.,
            'precision':'BF16 autocast training; FP32 parameters, validation and exported ONNX','fixed_masks':20,'mask_sha256':mask_digest,
            'checkpoint_selection':'maximum tune Top1; then Top5; then lower cross entropy; includes untrained epoch0',
            'training_images':len(training),'tuning_images':len(tuning),'test_read_during_training':False,
            'device':torch.cuda.get_device_name(0),'torch':torch.__version__,'data_manifest_sha256':sha(DATA/'manifest.json'),'policy_sha256':sha(RUN/'policy.json'),'selected_layers':list(SELECTED),'tuning_role':'exposed development2000, reused from previous stages'}
    config['source_scripts_sha256']={name:sha(ROOT/'scripts'/name) for name in ['finetune20.py','finetune20_common.py','finetune_model.py','finetune16.py','exhaustive20_common.py']}
    config['environment_lock_sha256']=sha(ROOT/'requirements-finetune-lock.txt')
    config['training_runtime']=sys.executable
    config['sampling_selection_sha256']=sha(DATA/'selection.json')
    save_json(RUN/'config.json',config)
    optimizer=torch.optim.AdamW(model.parameters(),lr=3e-5,weight_decay=.01)
    updates_per_epoch=math.ceil(len(train_loader)/2);total_updates=epochs*updates_per_epoch
    def lr_for(step):
        if step<100:return 3e-5*(step+1)/100
        frac=min(1.,(step-100)/max(1,total_updates-100))
        return 3e-6+(3e-5-3e-6)*.5*(1+math.cos(math.pi*frac))
    history=[];best_key=None;best_epoch=0;update=0;started=time.time()
    def checkpoint(epoch,metrics):
        check_masks(model,masks)
        torch.save({'model':{n:v.detach().cpu() for n,v in model.state_dict().items()},'epoch':epoch,'metrics':metrics,'mask_sha256':mask_digest,'sparse_masks':{n:v.detach().cpu() for n,v in masks.items()},'selected_layers':list(SELECTED),'stage':'FINETUNED20_FIXED_MASKS','parent_fp32_sha256':EXPECTED_FP},RUN/'best.pt')
    baseline,logits,labels=evaluate(model,tune_loader,device)
    teacher_metrics,_,_=evaluate(teacher,tune_loader,device)
    np.savez_compressed(RUN/'tune_epoch0.npz',logits=logits,labels=labels)
    best_key=(baseline['top1_correct'],baseline['top5_correct'],-baseline['cross_entropy']);checkpoint(0,baseline)
    history.append({'epoch':0,'tune':baseline});save_json(RUN/'history.json',history)
    print('BASELINE',json.dumps(baseline),'TEACHER',json.dumps(teacher_metrics),flush=True)
    for epoch in range(1,epochs+1):
        model.train();optimizer.zero_grad(set_to_none=True);loss_sum=ce_sum=kd_sum=0.;count=0;epoch_start=time.time()
        for step,(x,y) in enumerate(train_loader):
            x=x.to(device,non_blocking=True);y=y.to(device,non_blocking=True)
            with torch.autocast(device_type='cuda',dtype=torch.bfloat16):
                with torch.no_grad():target=teacher(x)
                output=model(x)
                ce=F.cross_entropy(output.float(),y)
                kd=F.kl_div(F.log_softmax(output.float()/2,dim=-1),F.softmax(target.float()/2,dim=-1),reduction='batchmean')*4
                loss=.5*ce+.5*kd
            assert torch.isfinite(loss).item()
            # Correct normalization also for a last accumulation window of one.
            divisor=1 if step==len(train_loader)-1 and len(train_loader)%2 else 2
            (loss/divisor).backward()
            if (step+1)%2==0 or step==len(train_loader)-1:
                lr=lr_for(update)
                for group in optimizer.param_groups:group['lr']=lr
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
                optimizer.step();enforce_masks(model,masks);optimizer.zero_grad(set_to_none=True);update+=1
            loss_sum+=loss.item()*len(y);ce_sum+=ce.item()*len(y);kd_sum+=kd.item()*len(y);count+=len(y)
            if (step+1)%25==0 or step+1==len(train_loader):
                check_masks(model,masks)
                progress={'epoch':epoch,'batch':step+1,'batches':len(train_loader),'updates':update,'images_seen_epoch':count,'loss':loss_sum/count,'ce':ce_sum/count,'kd':kd_sum/count,'lr':lr,'seconds_epoch':time.time()-epoch_start,'elapsed_seconds':time.time()-started}
                save_json(RUN/'progress.json',progress)
                print('TRAIN',json.dumps(progress),flush=True)
        metrics,z,ys=evaluate(model,tune_loader,device)
        np.savez_compressed(RUN/f'tune_epoch{epoch}.npz',logits=z,labels=ys)
        key=(metrics['top1_correct'],metrics['top5_correct'],-metrics['cross_entropy'])
        if key>best_key:best_key=key;best_epoch=epoch;checkpoint(epoch,metrics)
        history.append({'epoch':epoch,'train_loss':loss_sum/count,'train_ce':ce_sum/count,'train_kd':kd_sum/count,'updates':update,'tune':metrics,'seconds_epoch':time.time()-epoch_start})
        save_json(RUN/'history.json',history)
        torch.save({'model':{n:v.detach().cpu() for n,v in model.state_dict().items()},'optimizer':optimizer.state_dict(),'epoch':epoch,'updates':update,'mask_sha256':mask_digest},RUN/'last.pt')
        print('EPOCH',epoch,json.dumps(metrics),'BEST',best_epoch,flush=True)
    best=torch.load(RUN/'best.pt',map_location='cpu',weights_only=True)
    model=model.cpu();model.load_state_dict(best['model']);check_masks(model,{n:m.cpu() for n,m in masks.items()})
    hashes=export_original_graphs(model,CANDIDATE,RUN/'export')
    import onnxruntime as ort
    options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1;options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess=ort.InferenceSession(str(RUN/'export/model_fp32.onnx'),options,providers=['CPUExecutionProvider'])
    differences=[]
    model.eval()
    with torch.inference_mode():
        for i in [0,113,333,555,777,999,1500,1999]:
            x=tuning[i][0][None];z=model(x).numpy();target=sess.run(None,{sess.get_inputs()[0].name:x.numpy()})[0]
            assert np.allclose(z,target,atol=5e-4,rtol=5e-4),float(np.max(np.abs(z-target)))
            differences.append(float(np.max(np.abs(z-target))))
    save_json(RUN/'reexport_parity.json',{'status':'PASS','images':8,'maximum_absolute_logit_error':max(differences),'test_set_used':False})
    config.update({'status':'TRAINING_COMPLETE_EXPORTED_EVALUATION_PENDING','best_epoch':best_epoch,'best_tune_metrics':best['metrics'],'baseline_tune_metrics':baseline,'teacher_tune_metrics':teacher_metrics,'updates':update,'elapsed_seconds':time.time()-started,'export_sha256':hashes})
    save_json(RUN/'config.json',config);print('COMPLETE',json.dumps(config),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['parity','train']);parser.add_argument('--epochs',type=int,default=3);parser.add_argument('--batch',type=int,default=32);args=parser.parse_args()
    RUN.mkdir(parents=True,exist_ok=True)
    if args.action=='parity':parity()
    else:train(args.epochs,args.batch)
