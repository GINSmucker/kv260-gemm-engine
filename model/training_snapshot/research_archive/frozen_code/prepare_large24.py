"""Freeze model-independent100k sampling, range-fetch and verify the exact identities."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone,timedelta
import argparse,csv,random,shutil,time
import pyarrow.parquet as pq
from prepare_finetune_data import RangeReader
from large24_common import *

def known(plan):
    source=set();raw=set();rgb=set()
    for folder in plan['excluded_folders']:
        for r in read_rows(folder):
            if r.get('split') and r.get('row_index'):source.add((r['split'],int(r['row_index'])))
            raw.add(r['image_sha256']);rgb.add(r['decoded_rgb_sha256'])
    for r in read(ROOT/'references/known_used_images.json')['records']:
        raw.add(r['image_sha256']);rgb.add(r['decoded_rgb_sha256'])
    return source,raw,rgb

def plan():
    path=EXP/'sampling_plan.json'
    if path.exists():return read(path)
    assert not (EXP/'policy.json').exists()
    assert read(ROOT/'active_model.json')['models']['w8a8']['sha256']==EXPECTED_Q
    for name,h in [('model_fp32.onnx',EXPECTED_FP),('model_w8a8.onnx',EXPECTED_Q),('model_trainable.pt',EXPECTED_PT)]:assert sha(PARENT/name)==h
    for rel,r in read(PARENT/'PACKAGE_MANIFEST.json')['files'].items():assert sha(PARENT/rel)==r['sha256']
    folders=[OLD_DATA/z for z in ['train','tune','test900']]
    folders+=sorted({p.parent for p in (ROOT/'data').glob('*/labels.csv') if p.parent!=FRESH})
    p={'status':'SAMPLING_FROZEN_WITHOUT_MODEL_PREDICTIONS','dataset':DS,'revision':REV,'seed':SEED,
        'excluded_folders':[str(z) for z in folders],'training_total':100000,'old_train_kept':20000,
        'new_train_per_class':80,'new_train_candidates_per_class':96,
        'development_classes':900,'development_per_class':5,'acceptance_per_class':2,
        'sampling':'Two seeded disjoint48-row class windows for new train:40+40 primary and16 spares. Validation order separately seeded; first5 nonduplicate identities development, next2 acceptance.',
        'source_parent_fp32_sha256':EXPECTED_FP,'source_parent_w8a8_sha256':EXPECTED_Q}
    used,_,_=known(p)
    hub=read(OLD_DATA/'metadata/hub.json')
    files=sorted(z['rfilename'] for z in hub['siblings'] if z['rfilename'].startswith('data/train-'))
    labels=np.concatenate([np.load(OLD_DATA/'metadata'/(Path(z).name+'.labels.npy')) for z in files])
    assert len(labels)==1281167 and np.all(np.diff(labels)>=0)
    train={}
    for label in range(1000):
        ids=np.flatnonzero(labels==label);rng=random.Random(SEED+label);chosen=[]
        for window in range(2):
            for attempt in range(10000):
                start=rng.randrange(len(ids)-48+1);block=ids[start:start+48].tolist()
                if not set(block)&set(chosen) and all(('train',i) not in used for i in block):
                    chosen.extend(block);break
            else:raise AssertionError(('Cannot sample two unused windows',label))
        train[str(label)]=chosen[:40]+chosen[48:88]+chosen[40:48]+chosen[88:96]
    old_classes={r['label'] for r in read(ROOT/'references/source_metadata.json')['label_map']}
    val={}
    for label in sorted(set(range(1000))-old_classes):
        order=list(range(50));random.Random(SEED+10000+label).shuffle(order)
        val[str(label)]=[label*50+k for k in order if ('val',label*50+k) not in used][:20]
        assert len(val[str(label)])==20
    p.update(train_candidate_order=train,validation_candidate_order=val,
        source_label_arrays_sha256={Path(z).name+'.labels.npy':sha(OLD_DATA/'metadata'/(Path(z).name+'.labels.npy')) for z in files},
        excluded_labels_sha256={str(z/'labels.csv'):sha(z/'labels.csv') for z in folders},
        known_used_images_sha256=sha(ROOT/'references/known_used_images.json'))
    save(path,p);save(EXP/'previous_active_model.json',read(ROOT/'active_model.json'))
    print('PLAN FROZEN: keep20000 + new80000, new development4500, acceptance1800',flush=True)
    return p

def fetch():
    p=plan();started=time.time();hub=read(OLD_DATA/'metadata/hub.json')
    train_files=sorted(z['rfilename'] for z in hub['siblings'] if z['rfilename'].startswith('data/train-'))
    label_arrays=[np.load(OLD_DATA/'metadata'/(Path(z).name+'.labels.npy')) for z in train_files]
    for z in train_files:assert sha(OLD_DATA/'metadata'/(Path(z).name+'.labels.npy'))==p['source_label_arrays_sha256'][Path(z).name+'.labels.npy']
    offsets=np.cumsum([0]+[len(z) for z in label_arrays])
    requested={'train':[(i,int(c)) for c,ids in p['train_candidate_order'].items() for i in ids],
        'val':[(i,int(c)) for c,ids in p['validation_candidate_order'].items() for i in ids]}
    val_files=sorted(z['rfilename'] for z in hub['siblings'] if z['rfilename'].startswith('data/val-'))
    jobs=[('train',z,int(offsets[k]),int(offsets[k+1]),label_arrays[k]) for k,z in enumerate(train_files)]
    jobs += [('val',z,k*25000,(k+1)*25000,None) for k,z in enumerate(val_files)]
    def job(args):
        split,file,start,end,labs=args;wanted=[(i,c) for i,c in requested[split] if start<=i<end]
        missing=[];count=0
        for i,c in wanted:
            path=DATA/f'source_cache/{split}/{i:07d}.jpg';receipt=path.with_suffix('.json')
            if path.exists() and receipt.exists():
                r=read(receipt)
                if r['row_index']==i and r['label']==c and sha(path)==r['image_sha256']:
                    count+=1;continue
            missing.append((i,c))
        if not missing:return {'file':file,'cached':count,'new':0}
        print('FETCH START',Path(file).name,'needed',len(missing),flush=True)
        with RangeReader('https://huggingface.co/datasets/'+DS+'/resolve/'+REV+'/'+file) as stream:
            pqfile=pq.ParquetFile(stream);assert pqfile.metadata.num_rows==end-start
            rg=np.cumsum([0]+[pqfile.metadata.row_group(g).num_rows for g in range(pqfile.num_row_groups)])
            groups=defaultdict(list)
            for i,c in missing:groups[int(np.searchsorted(rg,i-start,side='right')-1)].append((i,c))
            ranges=[]
            for g in groups:
                for j in range(pqfile.metadata.num_columns):
                    col=pqfile.metadata.row_group(g).column(j)
                    if col.path_in_schema in ['image.bytes','label']:
                        off=col.dictionary_page_offset if col.has_dictionary_page else col.data_page_offset
                        ranges.append((off,col.total_compressed_size))
            print('RANGES',Path(file).name,len(groups),'groups',sum(n for a,n in ranges),'bytes',flush=True)
            stream.prefetch(ranges,workers=4)
            for g,items in sorted(groups.items()):
                table=pqfile.read_row_group(g,columns=['image.bytes','label'],use_threads=False)
                imgs=table.column(0).to_pylist();ys=table.column(1).to_pylist()
                for i,c in items:
                    position=i-start-int(rg[g]);assert ys[position]==c
                    if labs is not None:assert int(labs[i-start])==c
                    raw=imgs[position]['bytes'];path=DATA/f'source_cache/{split}/{i:07d}.jpg'
                    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                    save(path.with_suffix('.json'),{'split':split,'row_index':i,'label':c,
                        'image_sha256':digest(raw),'decoded_rgb_sha256':image_identity(raw),'bytes':len(raw)})
        return {'file':file,'cached':count,'new':len(missing)}
    done=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for f in as_completed([pool.submit(job,x) for x in jobs]):
            done.append(f.result());save(EXP/'download_progress.json',{'files_complete':len(done),'total_files':len(jobs),
                'cached_candidate_images':sum(x['cached']+x['new'] for x in done),'seconds':time.time()-started})
            print('FILE COMPLETE',len(done),'/',len(jobs),done[-1],flush=True)
    save(EXP/'download_complete.json',{'status':'PASS','files':done,'sampling_plan_sha256':sha(EXP/'sampling_plan.json')})
    print('ALL CANDIDATE IMAGE RANGES DOWNLOADED',flush=True)

def write_rows(folder,rows):
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'labels.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def materialize():
    p=read(EXP/'sampling_plan.json');assert read(EXP/'download_complete.json')['sampling_plan_sha256']==sha(EXP/'sampling_plan.json')
    used,raw_seen,rgb_seen=known(p);rejected=[];train=[];dev=[];test=[]
    def store(folder,row,source_role):
        split=row['split'];i=int(row['row_index']);rel=f'images/{split}_{i:07d}.jpg'
        origin=OLD_DATA/'train'/row['image'] if source_role=='original20k' else DATA/f'source_cache/{split}/{i:07d}.jpg'
        assert sha(origin)==row['image_sha256']
        dest=folder/rel;dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():shutil.copyfile(origin,dest)
        else:assert sha(dest)==row['image_sha256']
        return {'image':rel,'label':int(row['label']),'split':split,'row_index':i,
            'image_sha256':row['image_sha256'],'decoded_rgb_sha256':row['decoded_rgb_sha256'],
            'bytes':origin.stat().st_size,'source_role':source_role}
    for r in read_rows(OLD_DATA/'train'):train.append(store(DATA/'train',r,'original20k'))
    for c,ids in p['train_candidate_order'].items():
        kept=0
        for i in ids:
            r=read(DATA/f'source_cache/train/{i:07d}.json');h=r['image_sha256'];rgb=r['decoded_rgb_sha256']
            if h in raw_seen or rgb in rgb_seen:
                rejected.append({'split':'train','row_index':i,'label':int(c)});continue
            assert ('train',i) not in used
            train.append(store(DATA/'train',r,'new80k'));used.add(('train',i));raw_seen.add(h);rgb_seen.add(rgb)
            kept+=1
            if kept==80:break
        assert kept==80,('Insufficient train spares',c)
    for c,ids in p['validation_candidate_order'].items():
        kept=0
        for i in ids:
            r=read(DATA/f'source_cache/val/{i:07d}.json');h=r['image_sha256'];rgb=r['decoded_rgb_sha256']
            if h in raw_seen or rgb in rgb_seen:
                rejected.append({'split':'val','row_index':i,'label':int(c)});continue
            assert ('val',i) not in used
            if kept<5:dev.append(store(DATA/'tune',r,'new_development'))
            else:test.append(store(FRESH,r,'new_acceptance'))
            used.add(('val',i));raw_seen.add(h);rgb_seen.add(rgb);kept+=1
            if kept==7:break
        assert kept==7,('Insufficient validation spares',c)
    for role,rows,count,classes,per in [('train',train,100000,1000,100),('tune',dev,4500,900,5),('fresh',test,1800,900,2)]:
        assert len(rows)==count and len(Counter(r['label'] for r in rows))==classes
        assert set(Counter(r['label'] for r in rows).values())=={per}
        write_rows(FRESH if role=='fresh' else DATA/role,rows)
    assert len({r['image_sha256'] for r in train+dev+test})==106300
    assert len({r['decoded_rgb_sha256'] for r in train+dev+test})==106300
    save(DATA/'manifest.json',{'status':'PASS','dataset':DS,'revision':REV,'role_counts':{'train':100000,'tune':4500},
        'train_classes':1000,'train_per_class':100,'original_training_images_retained':20000,'new_training_images':80000,
        'development_origin':'Previously unused original validation split,900classes,5/class',
        'acceptance_path':str(FRESH),'csv_sha256':{r:sha(DATA/r/'labels.csv') for r in ['train','tune']},
        'sampling_plan_sha256':sha(EXP/'sampling_plan.json'),'rejected_exact_identities':rejected,
        'cross_role_source_byte_rgb_duplicates':0,'limits':'No near-duplicate or original-pretraining exposure exclusion guarantee.'})
    save(FRESH/'manifest.json',{'status':'PASS','dataset':DS,'revision':REV,'images':1800,'classes':900,'per_class':2,
        'label_sha256':sha(FRESH/'labels.csv'),'sampling_plan_sha256':sha(EXP/'sampling_plan.json'),
        'excluded_from_training_and_development':True,'limits':'Exact identities only; no pretraining exposure exclusion claim.'})
    print('MATERIALIZED100000 TRAIN,4500 FRESH DEVELOPMENT,1800 HELD OUT ACCEPTANCE',flush=True)

def freeze():
    assert not (EXP/'policy.json').exists()
    p=read(EXP/'sampling_plan.json');assert read(DATA/'manifest.json')['status']=='PASS'
    for name,h in p['excluded_labels_sha256'].items():assert sha(name)==h
    assert sha(ROOT/'references/known_used_images.json')==p['known_used_images_sha256']
    names=['large24_common.py','prepare_large24.py','build_large24.py','train_large24.py','select_large24.py',
        'package_large24.py','evaluate_large24.py','finish_large24.py','finetune20.py','finetune20_common.py',
        'finetune_model.py','exhaustive20_common.py','prepare_finetune_data.py']
    policy={'status':'FROZEN_BEFORE_TRAINING','created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'source_candidate':str(PARENT),'source_fp32_sha256':EXPECTED_FP,'source_w8a8_sha256':EXPECTED_Q,
        'selected_layers':list(SELECTED),'additional_pruned_layers':[],'fixed_mask_count':24,
        'epochs':EPOCHS,'updates':UPDATES,'micro_batch':32,'gradient_accumulation':2,
        'learning_rate_peak':LR_PEAK,'learning_rate_min':LR_MIN,'warmup_updates':100,'weight_decay':.01,
        'temperature':2.,'ce_weight':.5,'kd_weight':.5,'seed':SEED,'optimizer_reinitialized':True,
        'training_images':100000,'training_images_original':20000,'training_images_added':80000,'tuning_images':4500,
        'development':'New900class original-validation samples,not previously used for local model selection; now development',
        'checkpoint_selection':'max new development W8A8 Top1,Top5,minCE,lower epoch0..5; include unchanged parent',
        'fresh_acceptance':str(FRESH),'fresh_labels_sha256':sha(FRESH/'labels.csv'),
        'evaluation':'Finish all5epochs and freeze checkpoint before acceptance; no acceptance-based reselection/retraining',
        'promotion_rule':'Promote preselected candidate only if fresh W8A8 Top1 correct count strictly exceeds the frozen parent; otherwise retain parent active. One-shot acceptance go/no-go only.',
        'training_data_manifest_sha256':sha(DATA/'manifest.json'),'sampling_plan_sha256':sha(EXP/'sampling_plan.json'),
        'scripts_sha256':{name:sha(ROOT/'scripts'/name) for name in names}}
    (EXP/'frozen_code').mkdir(exist_ok=True)
    for name in names:shutil.copyfile(ROOT/'scripts'/name,EXP/'frozen_code'/name)
    save(EXP/'policy.json',policy);print('100K EXPERIMENT POLICY FROZEN',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['fetch','materialize','freeze']);args=parser.parse_args()
    {'fetch':fetch,'materialize':materialize,'freeze':freeze}[args.stage]()
