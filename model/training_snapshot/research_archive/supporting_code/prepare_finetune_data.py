"""Frozen 1000-class train/tune sample; unseen 900-class validation evaluation.

Only public Hugging Face endpoints, no credentials, no model-based sampling.
The source is sorted by class: read parquet LABEL COLUMNS only over HTTP ranges,
then request chosen images from the official dataset viewer. Full dataset is not downloaded.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import argparse,csv,hashlib,io,json,random,time,urllib.request,urllib.parse
import numpy as np
from PIL import Image
import pyarrow.parquet as pq
import fsspec

ROOT=Path(__file__).absolute().parents[1]
DATA=ROOT/'data/finetune16_imagenet1k_20261024'
DS='evanarlian/imagenet_1k_resized_256'
REV='8de107e08d1f61884d5a4ec2dea8740667264a60'
SEED=2026102401

def save(path,obj):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def get(url):
    for attempt in range(7):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'H576-fixed-mask-finetuning/1.0'}),timeout=45) as r:
                return r.read()
        except Exception:
            if attempt==6: raise
            time.sleep(min(2**attempt,16))

def jget(url): return json.loads(get(url))
def digest(raw): return hashlib.sha256(raw).hexdigest()

class RangeReader(io.RawIOBase):
    """urllib honors this Windows machine's configured HTTP proxy."""
    def __init__(self,url):
        self.url=url;self.position=0;self.cache=[]
        with urllib.request.urlopen(urllib.request.Request(url+'?h576range=footer',headers={'Range':'bytes=-65536'}),timeout=45) as r:
            assert r.status==206
            text=r.headers['Content-Range'];self.size=int(text.split('/')[-1]);raw=r.read()
            start=int(text.split()[1].split('-')[0]);self.cache.append((start,raw))
    def seekable(self):return True
    def readable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        self.position=offset if whence==0 else (self.position+offset if whence==1 else self.size+offset)
        return self.position
    def prefetch(self,ranges,workers=12):
        def fetch(pair):
            start,n=pair;end=start+n-1
            for attempt in range(6):
                try:
                    request=urllib.request.Request(self.url+f'?h576range={start}-{end}',headers={'Range':f'bytes={start}-{end}'})
                    with urllib.request.urlopen(request,timeout=60) as r:
                        assert r.status==206 and r.headers['Content-Range']==f'bytes {start}-{end}/{self.size}'
                        raw=r.read()
                    assert len(raw)==n
                    return start,raw
                except Exception:
                    if attempt==5:raise
                    time.sleep(2**attempt)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start,raw in pool.map(fetch,sorted(set(ranges))):self.cache.append((start,raw))
    def read(self,n=-1):
        if n<0:n=self.size-self.position
        n=min(n,self.size-self.position)
        if n<=0:return b''
        start=self.position;end=start+n-1
        for a,raw in self.cache:
            if a<=start and end<a+len(raw):
                self.position+=n;return raw[start-a:start-a+n]
        req=urllib.request.Request(self.url+f'?h576range={start}-{end}',headers={'Range':f'bytes={start}-{end}'})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req,timeout=45) as r:
                    assert r.status==206 and r.headers['Content-Range']==f'bytes {start}-{end}/{self.size}'
                    raw=r.read()
                assert len(raw)==n
                break
            except Exception:
                if attempt==4:raise
                time.sleep(2**attempt)
        self.cache.append((start,raw));self.position+=n
        return raw

def source_labels():
    cache=DATA/'metadata'; cache.mkdir(parents=True,exist_ok=True)
    hub=jget('https://huggingface.co/api/datasets/'+DS)
    assert hub['sha']==REV and not hub['gated'] and not hub['private']
    save(cache/'hub.json',hub)
    info=jget('https://datasets-server.huggingface.co/info?dataset='+DS+'&config=default')
    labels=json.loads((ROOT/'references/labels_imagenet1k.json').read_text())
    source_names=info['dataset_info']['features']['label']['names']
    assert len(source_names)==1000 and [i for i,(a,b) in enumerate(zip(source_names,labels)) if a!=b['name']]==[517]
    assert source_names[517]=='crane2' and labels[517]['wnid']=='n03126707'
    save(cache/'source_info.json',info)
    files=sorted(x['rfilename'] for x in hub['siblings'] if x['rfilename'].startswith('data/train-'))
    def scan(f):
        target=cache/(Path(f).name+'.labels.npy')
        if not target.exists():
            url='https://huggingface.co/datasets/'+DS+'/resolve/'+REV+'/'+f
            for attempt in range(5):
                try:
                    with RangeReader(url) as stream:
                        p=pq.ParquetFile(stream)
                        label_col=next(j for j in range(p.metadata.num_columns) if p.metadata.row_group(0).column(j).path_in_schema=='label')
                        chunks=[];mixed=0
                        for j in range(p.num_row_groups):
                            rg=p.metadata.row_group(j);st=rg.column(label_col).statistics
                            if st is not None and st.has_min_max and st.min==st.max:
                                chunks.append(np.full(rg.num_rows,st.min,dtype=np.int16))
                            else:
                                chunks.append(p.read_row_group(j,columns=['label'],use_threads=False).column(0).to_numpy().astype(np.int16));mixed+=1
                        labs=np.concatenate(chunks)
                    assert len(labs)>0
                    np.save(target,labs)
                    break
                except Exception:
                    if attempt==4: raise
                    time.sleep(2**attempt)
        a=np.load(target)
        print('Labels',Path(f).name,len(a),int(a.min()),int(a.max()),flush=True)
        return a
    with ThreadPoolExecutor(max_workers=8) as pool:
        labs=np.concatenate(list(pool.map(scan,files)))
    assert len(labs)==1281167 and np.all(np.diff(labs)>=0)
    return labs

def plan():
    path=DATA/'selection.json'
    if path.exists(): return json.loads(path.read_text())
    labs=source_labels()
    # Earlier evaluation covered these 100 original ImageNet validation classes.
    exposed={x['label'] for x in json.loads((ROOT/'references/source_metadata.json').read_text())['label_map']}
    samples=[]
    for label in range(1000):
        indices=np.flatnonzero(labs==label).tolist()
        rng=random.Random(SEED+label)
        start1=rng.randrange(0,len(indices)//2-12)
        start2=rng.randrange(len(indices)//2,len(indices)-12)
        # Two random, disjoint 12-image windows/class limit metadata traffic.
        # Each window contributes ten train, one tune and one spare image.
        for start in [start1,start2]:
            for n,index in enumerate(indices[start:start+12]):
                role='train' if n<10 else ('tune' if n==10 else 'replacement')
                samples.append({'role':role,'split':'train','row_index':index,'label':label})
        if label not in exposed:
            offsets=list(range(50));random.Random(SEED+10000+label).shuffle(offsets)
            for k in offsets[:2]:
                samples.append({'role':'test900','split':'val','row_index':label*50+k,'label':label})
    obj={'dataset':DS,'revision':REV,'seed':SEED,'train_per_class':20,'tune_per_class':2,
         'test_classes':900,'test_per_class':2,'sampling':'two seeded random disjoint class windows, no model outputs',
         'test_excludes_all_previously_evaluated_100_classes':sorted(exposed),
         'samples':samples,'note':'Train/tune are original training split. Test is unused validation classes; not ImageNet full-validation accuracy.'}
    save(path,obj); return obj

def run():
    DATA.mkdir(parents=True,exist_ok=True)
    p=plan(); wanted=[r for r in p['samples'] if r['role']!='replacement']
    cache=DATA/'metadata/pages';cache.mkdir(parents=True,exist_ok=True)
    # Collect sorted rows into <=100-row pages; one class is one small contiguous
    # page only when its random rows happen to be nearby. General grouping works.
    keys=sorted({(r['split'],r['row_index']//100*100) for r in p['samples']})
    wanted_keys={(r['split'],r['row_index']) for r in p['samples']}
    def fetch_page(key):
        split,offset=key; target=cache/f'{split}_{offset}.json'
        if target.exists(): d=json.loads(target.read_text())
        else:
            url='https://datasets-server.huggingface.co/rows?'+urllib.parse.urlencode({'dataset':DS,'config':'default','split':split,'offset':offset,'length':100})
            d=jget(url);save(target,d)
        assert d['partial'] is False
        return {(split,r['row_idx']):r['row'] for r in d['rows'] if (split,r['row_idx']) in wanted_keys}
    lookup={}
    def fetch_image(r):
        row=lookup[(r['split'],r['row_index'])]
        assert row['label']==r['label']
        folder=DATA/r['role'];(folder/'images').mkdir(parents=True,exist_ok=True)
        rel=f"images/{r['split']}_{r['row_index']:07d}.jpg"
        dest=folder/rel;receipt=folder/(rel+'.json')
        if dest.exists() and receipt.exists():
            result=json.loads(receipt.read_text());assert digest(dest.read_bytes())==result['image_sha256'];return result
        src=row['image']['src'];u=urllib.parse.urlsplit(src)
        assert u.hostname=='datasets-server.huggingface.co' and '/--/'+REV+'/--/' in u.path
        # Use the official signed asset URL. Refresh through public rows if an
        # earlier cached page's signature is about to expire.
        expiry=int(urllib.parse.parse_qs(u.query).get('Expires',['0'])[0])
        if expiry and expiry<time.time()+120:
            refresh=jget('https://datasets-server.huggingface.co/rows?'+urllib.parse.urlencode({'dataset':DS,'config':'default','split':r['split'],'offset':r['row_index'],'length':1}))
            fresh=refresh['rows'][0];assert fresh['row_idx']==r['row_index'] and fresh['row']['label']==r['label']
            src=fresh['row']['image']['src']
        raw=get(src)
        with Image.open(io.BytesIO(raw)) as im:
            im.load();rgb=im.convert('RGB'); pixels=rgb.tobytes()
            rgb_sha=digest(str(rgb.size).encode()+b'\0'+pixels)
            assert min(rgb.size)>0
        dest.write_bytes(raw)
        result={**r,'image':rel,'image_sha256':digest(raw),'decoded_rgb_sha256':rgb_sha,'bytes':len(raw)}
        save(receipt,result)
        return result
    results=[];wanted_by_key={(r['split'],r['row_index']):r for r in wanted}
    # Fetch assets as soon as metadata arrives, instead of serializing the two
    # network phases. Both phases retain a bounded concurrency and retry budget.
    with ThreadPoolExecutor(max_workers=24) as images_pool:
        futures={}
        with ThreadPoolExecutor(max_workers=16) as meta_pool:
            for n,rows in enumerate(meta_pool.map(fetch_page,keys),1):
                lookup.update(rows)
                for key in rows:
                    if key in wanted_by_key:futures[key]=images_pool.submit(fetch_image,wanted_by_key[key])
                if n%100==0:print('Metadata pages',n,'/',len(keys),flush=True)
        save(DATA/'metadata_complete.json',{'pages':len(keys),'rows':len(lookup)})
        for n,row in enumerate(wanted,1):
            r=futures[(row['split'],row['row_index'])].result();results.append(r)
            if n%200==0: print('Images',n,'/',len(wanted),flush=True)
    seen_bytes=set();seen_rgb=set();rejected=[];accepted=[];used_spares=set()
    for r in results:
        if r['image_sha256'] in seen_bytes or r['decoded_rgb_sha256'] in seen_rgb:
            rejected.append(r)
            if r['role']=='test900':
                original={s['row_index'] for s in p['samples'] if s['split']=='val'}
                spares=[{'role':'test900','split':'val','row_index':r['label']*50+k,'label':r['label']} for k in range(50) if r['label']*50+k not in original and ('val',r['label']*50+k) not in used_spares]
            else:
                spares=[s for s in p['samples'] if s['role']=='replacement' and s['label']==r['label'] and (s['split'],s['row_index']) not in used_spares]
            replaced=False
            for s in spares:
                used_spares.add((s['split'],s['row_index']))
                if (s['split'],s['row_index']) not in lookup:
                    key=(s['split'],s['row_index']//100*100)
                    page=cache/f'{key[0]}_{key[1]}.json'
                    dd=json.loads(page.read_text()) if page.exists() else jget('https://datasets-server.huggingface.co/rows?'+urllib.parse.urlencode({'dataset':DS,'config':'default','split':key[0],'offset':key[1],'length':100}))
                    lookup[(s['split'],s['row_index'])]=next(z['row'] for z in dd['rows'] if z['row_idx']==s['row_index'])
                replacement=fetch_image({**s,'role':r['role']})
                if replacement['image_sha256'] not in seen_bytes and replacement['decoded_rgb_sha256'] not in seen_rgb:
                    r=replacement;replaced=True;break
            assert replaced,('Insufficient deterministic replacements',r)
        seen_bytes.add(r['image_sha256']);seen_rgb.add(r['decoded_rgb_sha256']);accepted.append(r)
    results=accepted
    save(DATA/'duplicate_replacements.json',{'rejected':rejected,'spares_used':sorted(used_spares)})
    for role in ['train','tune','test900']:
        rows=[r for r in results if r['role']==role]
        columns=list(rows[0])
        with (DATA/role/'labels.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)
    save(DATA/'manifest.json',{'status':'DOWNLOADED_AND_EXACT_DUPLICATE_CHECKED','dataset':DS,'revision':REV,
        'role_counts':dict(Counter(r['role'] for r in results)),'bytes':sum(r['bytes'] for r in results),
        'cross_role_exact_duplicates':0,'test_classes_exclude_previous_100':True,
        'limits':'Exact bytes/RGB screening does not exclude near duplicates or pretraining exposure. Images are derived ImageNet data.',
        'csv_sha256':{role:digest((DATA/role/'labels.csv').read_bytes()) for role in ['train','tune','test900']}})
    print('DATA COMPLETE',flush=True)

if __name__=='__main__': run()
