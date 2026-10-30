"""Score all6 checkpoints on development only; freeze winner before acceptance."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import csv,time
from large24_common import *


def independent_metrics(z,y,m):
    truth=z[np.arange(len(y)),y]
    rank=1+(z>truth[:,None]).sum(1)+((z==truth[:,None])&(np.arange(1000)[None,:]<y[:,None])).sum(1)
    assert int((rank==1).sum())==m['top1_correct'] and int((rank<=5).sum())==m['top5_correct']
    ce=float(np.mean(np.logaddexp.reduce(z.astype(np.float64),axis=1)-truth))
    assert abs(ce-m['cross_entropy'])<1e-10


def audit_graphs(folder,selected,initial_layers):
    fp=onnx.load(folder/'model_fp32.onnx');q=onnx.load(folder/'model_w8a8.onnx')
    assert topology(fp)==topology(onnx.load(PARENT/'model_fp32.onnx'))
    assert topology(q)==topology(onnx.load(PARENT/'model_w8a8.onnx'))
    f,a=arrays(fp),arrays(q);old=arrays(onnx.load(PARENT/'model_w8a8.onnx'));handled=set()
    for l in initial_layers:
        i=l['layer_index'];name=l['layer_id'];w=f[name+'.weight'].T
        quant,scale=quantize(w)
        assert np.array_equal(a[f'BQL_{i}_Wf'],quant.astype(np.float32))
        assert np.array_equal(a[f'BQL_{i}_sf'],scale.ravel())
        assert np.array_equal(a[f'BQL_{i}_bf'],f[name+'.bias'])
        handled.update([f'BQL_{i}_Wf',f'BQL_{i}_sf',f'BQL_{i}_bf'])
        if i in selected:
            with np.load(Path(l['initial_folder'])/l['weights']) as z:mask=z['mask']
            assert not np.any(w[~mask]) and not np.any(quant[~mask])
            assert np.all(mask.reshape(-1,4,w.shape[1]).sum(1)==2)
    for name,value in a.items():
        if name not in handled:assert np.array_equal(value,f[name] if name in f else old[name]),name


def main():
    policy=check_policy(); assert not (EXP/'selection.json').exists()
    construction=read(EXP/'construction.json'); assert construction['status']=='PASS'
    x,y,rows,input_sha=inputs(DATA/'tune');jobs=[]
    for c in construction['candidates']:
        initial=Path(c['initial_folder']);run=initial.parent/'training';config=read(run/'config.json')
        assert config['status']=='ALL_EPOCHS_EXPORTED_PENDING_W8A8_SELECTION'
        assert (config['epochs'],config['updates'],config['seed'],config['training_images'],config['tuning_images'])==(EPOCHS,UPDATES,SEED,TRAIN_IMAGES,TUNE_IMAGES)
        for epoch in range(EPOCHS+1):
            folder=run/f'epoch{epoch}';receipt=read(folder/'receipt.json')
            assert receipt['updates']==epoch*UPDATES_PER_EPOCH
            for name,h in receipt['models_sha256'].items():assert sha(folder/name)==h
            layers=[dict(l,initial_folder=str(initial)) for l in read(initial/'layers.json')]
            audit_graphs(folder,c['selected_layers'],layers)
            jobs.append((c['id'],epoch,folder/'model_w8a8.onnx'))
    jobs.extend([('parent24',-1,PARENT/'model_w8a8.onnx'),('dense',-1,SOURCE/'h576_w8.onnx')])
    def run(job):
        name,epoch,path=job;folder=EXP/'development'/name/(f'epoch{epoch}' if epoch>=0 else 'control')
        folder.mkdir(parents=True,exist_ok=True)
        z=infer(path,x);m=metrics(z,y);independent_metrics(z,y,m)
        np.savez_compressed(folder/'logits.npz',logits=z,labels=y)
        result={'id':name,'epoch':epoch,'metrics':m,'model_path':str(path),'model_sha256':sha(path),
            'preprocessed_inputs_sha256':input_sha,'logits_sha256':sha(folder/'logits.npz')}
        save(folder/'result.json',result);return result
    results=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for f in as_completed([pool.submit(run,j) for j in jobs]):
            r=f.result();results.append(r)
            print('DEVELOPMENT',r['id'],'epoch',r['epoch'],r['metrics'],flush=True)
    parent_control=next(r for r in results if r['id']=='parent24')
    baseline=next(r for r in results if r['epoch']==0)
    assert baseline['metrics']==parent_control['metrics']
    with np.load(EXP/'development/fixed24/epoch0/logits.npz') as before, np.load(EXP/'development/parent24/control/logits.npz') as original:
        assert np.array_equal(before['logits'],original['logits'])
    ranking=[]
    for c in construction['candidates']:
        items=[r for r in results if r['id']==c['id']]
        assert len(items)==EPOCHS+1
        best=min(items,key=lambda r:score_key(r['metrics'],r['epoch']))
        before=next(r for r in items if r['epoch']==0)
        ranking.append(dict(best,added_layers=c['added_layers'],selected_layers=c['selected_layers'],
            before_metrics=before['metrics'],initial_folder=c['initial_folder'],all_epochs=sorted(items,key=lambda r:r['epoch'])))
    ranking.sort(key=lambda r:score_key(r['metrics'])[:3]+(tuple(r['added_layers']),))
    controls={r['id']:r for r in results if r['epoch']==-1}
    save(EXP/'ranking.json',ranking)
    with (EXP/'ranking.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['rank','combination','selected_epoch','top1_pct','top5_pct','cross_entropy','before_top1_pct','before_top5_pct'])
        w.writeheader()
        for k,r in enumerate(ranking,1):w.writerow({'rank':k,'combination':r['id'],'selected_epoch':r['epoch'],
            **{key:r['metrics'][key] for key in ['top1_pct','top5_pct','cross_entropy']},
            'before_top1_pct':r['before_metrics']['top1_pct'],'before_top5_pct':r['before_metrics']['top5_pct']})
    save(OUT/'search_verification.json',{'status':'PASS','combinations':1,'epochs_per_combination':EPOCHS,
        'total_updates':UPDATES,'common_seed_and_data':True,'checkpoint_models_audited':EPOCHS+1,
        'all49_quantization_checks_per_checkpoint':True,'all24_masks_fixed':True,
        'independent_development_rank_and_ce_checks':EPOCHS+3,'policy_sha256':sha(EXP/'policy.json'),
        'top1_tied_best_combinations':sum(r['metrics']['top1_correct']==ranking[0]['metrics']['top1_correct'] for r in ranking),
        'best_vs_second_top1_advantage_pp':None})
    save(EXP/'selection.json',{'status':'24_TRAIN100K_W8A8_CHECKPOINT_FROZEN_BEFORE_ACCEPTANCE',
        'winner':ranking[0],'controls':controls,'ranking_sha256':sha(EXP/'ranking.json'),
        'policy_sha256':sha(EXP/'policy.json'),'acceptance_used_to_select':False,
        'scope':'Balanced100k train,new4500 development; fixed24 masks; best among epochs0..5 including unchanged parent; no global or convergence guarantee'})
    print('WINNER_FROZEN',ranking[0]['id'],'epoch',ranking[0]['epoch'],flush=True)


if __name__=='__main__':main()
