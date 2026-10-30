"""Common seeded budget; save all epochs for later W8A8-only checkpoint selection."""
import argparse, hashlib, math, random, time
import torch
from torch.nn import functional as F
from torch.utils.data import DataLoader
from finetune20 import Images, evaluate
from finetune_model import load_onnx_weights, enforce_masks, check_masks, export_original_graphs
from large24_common import *


def main(candidate_id):
    policy=check_policy(); construction=read(EXP/'construction.json');assert construction['status']=='PASS'
    item=next(x for x in construction['candidates'] if x['id']==candidate_id)
    initial=Path(item['initial_folder']); run=initial.parent/'training'
    assert not (run/'config.json').exists(),'Existing training run must not be overwritten'
    run.mkdir(parents=True,exist_ok=True)
    for name,h in item['hashes'].items():assert sha(initial/name)==h
    random.seed(SEED);np.random.seed(SEED);torch.manual_seed(SEED);torch.cuda.manual_seed_all(SEED)
    assert torch.cuda.is_available();torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    model=load_onnx_weights(initial/'model_fp32.onnx').cuda()
    teacher=load_onnx_weights(SOURCE/'h576_fp32.onnx').cuda().eval().requires_grad_(False)
    masks={}
    for l in read(initial/'layers.json'):
        if l['weights']:
            with np.load(initial/l['weights']) as z:mask=z['mask'].T.copy()
            assert np.all(mask.reshape(mask.shape[0],-1,4).sum(-1)==2)
            masks[l['layer_id']+'.weight']=torch.from_numpy(mask).cuda()
    assert len(masks)==24;check_masks(model,masks)
    mask_digest=digest(b''.join(n.encode()+masks[n].cpu().numpy().tobytes() for n in sorted(masks)))
    for name,p in model.named_parameters():
        if name in masks:p.register_hook(lambda grad,m=masks[name]:grad*m)
    training=Images(DATA/'train',train=True); tuning=Images(DATA/'tune')
    train_loader=DataLoader(training,batch_size=32,shuffle=True,num_workers=2,pin_memory=True,
        persistent_workers=True,generator=torch.Generator().manual_seed(SEED))
    tune_loader=DataLoader(tuning,batch_size=32,shuffle=False,num_workers=2,pin_memory=True,persistent_workers=True)
    optimizer=torch.optim.AdamW(model.parameters(),lr=LR_PEAK,weight_decay=.01)
    config={'status':'RUNNING','candidate':item,'seed':SEED,'epochs':EPOCHS,'optimizer_reinitialized':True,'additional_pruned_layers':[],'training_images':len(training),
        'tuning_images':len(tuning),'micro_batch':32,'gradient_accumulation':2,'optimizer':'AdamW',
        'learning_rate_peak':LR_PEAK,'learning_rate_min':LR_MIN,'warmup_updates':100,'weight_decay':.01,
        'loss':'0.5 CE + 0.5 T^2 KL(Dense_T || student_T)','temperature':2.,'precision':'BF16 train, FP32 validation/export',
        'fixed_masks':24,'mask_sha256':mask_digest,'device':torch.cuda.get_device_name(0),'torch':torch.__version__,
        'policy_sha256':sha(EXP/'policy.json'),'construction_sha256':sha(EXP/'construction.json'),
        'checkpoint_selection':'Deferred: W8A8 development Top1, Top5, CE, lower epoch across0..5',
        'acceptance_read_during_training':False,'teacher_fp32_sha256':sha(SOURCE/'h576_fp32.onnx')}
    save(run/'config.json',config)
    def lr_for(step):
        if step<100:return LR_PEAK*(step+1)/100
        frac=min(1.,(step-100)/(UPDATES-100))
        return LR_MIN+(LR_PEAK-LR_MIN)*.5*(1+math.cos(math.pi*frac))
    history=[];update=0;started=time.time()
    def checkpoint(epoch,m,z,ys):
        check_masks(model,masks); folder=run/f'epoch{epoch}';folder.mkdir(exist_ok=True)
        torch.save({'model':{n:v.detach().cpu() for n,v in model.state_dict().items()},
            'sparse_masks':{n:v.detach().cpu() for n,v in masks.items()},'epoch':epoch,'metrics_fp32':m,
            'selected_layers':item['selected_layers'],'added_layers':item['added_layers'],
            'mask_sha256':mask_digest,'stage':'FIXED24_BALANCED100K5EPOCHS','updates':update,
            'parent_fp32_sha256':EXPECTED_FP},folder/'model_trainable.pt')
        hashes=export_original_graphs(model,initial,folder)
        np.savez_compressed(folder/'tune_fp32.npz',logits=z,labels=ys)
        save(folder/'receipt.json',{'epoch':epoch,'metrics_fp32':m,'updates':update,
            'models_sha256':dict(hashes,**{'model_trainable.pt':sha(folder/'model_trainable.pt')})})
    baseline,z,ys=evaluate(model,tune_loader,'cuda');checkpoint(0,baseline,z,ys)
    history.append({'epoch':0,'tune_fp32':baseline});save(run/'history.json',history)
    print('BASELINE',candidate_id,baseline,flush=True)
    for epoch in range(1,EPOCHS+1):
        model.train();optimizer.zero_grad(set_to_none=True);sums=np.zeros(3);count=0;t0=time.time()
        for step,(x,y) in enumerate(train_loader):
            x=x.cuda(non_blocking=True);y=y.cuda(non_blocking=True)
            with torch.autocast(device_type='cuda',dtype=torch.bfloat16):
                with torch.no_grad():target=teacher(x)
                output=model(x);ce=F.cross_entropy(output.float(),y)
                kd=4*F.kl_div(F.log_softmax(output.float()/2,dim=-1),F.softmax(target.float()/2,dim=-1),reduction='batchmean')
                loss=.5*ce+.5*kd
            assert torch.isfinite(loss).item()
            divisor=1 if step==len(train_loader)-1 and len(train_loader)%2 else 2
            (loss/divisor).backward()
            if (step+1)%2==0 or step==len(train_loader)-1:
                lr=lr_for(update)
                for group in optimizer.param_groups:group['lr']=lr
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
                optimizer.step();enforce_masks(model,masks);optimizer.zero_grad(set_to_none=True);update+=1
            sums+=np.array([loss.item(),ce.item(),kd.item()])*len(y);count+=len(y)
            if (step+1)%100==0 or step==len(train_loader)-1:
                check_masks(model,masks)
                progress={'candidate':candidate_id,'epoch':epoch,'batch':step+1,'batches':len(train_loader),
                    'updates':update,'images_seen_epoch':count,'loss':float(sums[0]/count),'lr':lr,'seconds':time.time()-started}
                save(EXP/'progress.json',progress);save(run/'progress.json',progress)
                print('TRAIN',progress,flush=True)
        m,z,ys=evaluate(model,tune_loader,'cuda');checkpoint(epoch,m,z,ys)
        history.append({'epoch':epoch,'train_loss':float(sums[0]/count),'train_ce':float(sums[1]/count),
            'train_kd':float(sums[2]/count),'updates':update,'tune_fp32':m,'seconds_epoch':time.time()-t0})
        save(run/'history.json',history);print('EPOCH',candidate_id,epoch,m,flush=True)
    assert update==UPDATES
    config.update(status='ALL_EPOCHS_EXPORTED_PENDING_W8A8_SELECTION',updates=update,elapsed_seconds=time.time()-started)
    save(run/'config.json',config);print('TRAIN_COMPLETE',candidate_id,'updates',update,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('candidate');args=parser.parse_args();main(args.candidate)
