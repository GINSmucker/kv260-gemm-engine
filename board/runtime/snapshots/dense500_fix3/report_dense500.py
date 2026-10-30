"""Recompute paired Dense/Sparse500 labeled results from saved logits only."""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tarfile
sys.dont_write_bytecode=True
HERE=Path(__file__).absolute().parent
PUBLIC=Path('/home/ubuntu/fpga_project/opt50_integration/dense500_same_pl_20261030_v1/software')
SOFTWARE_PIN = {'bytes': 17233, 'sha256': 'e60866fca203ee366c9c9717cdbc55256cdde6ced7c25f007faf923854ad7b46'}
EXPORTS=Path('/home/ubuntu/fpga_project/opt50_integration/dense500_same_pl_exports_20261030_v1')
SPARSE_REVISION='38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d'
COUNT=500
def need(ok,why):
    if not ok:raise ValueError(why)
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def encoded(v):return (json.dumps(v,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
def distribution(values):
    ordered=sorted(values);need(ordered and all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in ordered),'finite timings')
    def quantile(q):
        n=q*(len(ordered)-1);a=math.floor(n);b=math.ceil(n)
        return ordered[a]+(ordered[b]-ordered[a])*(n-a)
    return {'samples':len(ordered),'mean_ms':statistics.mean(ordered),'median_ms':statistics.median(ordered),
        'p90_ms':quantile(.9),'p95_ms':quantile(.95),'min_ms':ordered[0],'max_ms':ordered[-1],'sum_ms':math.fsum(ordered)}
def rank(np,value,label):
    need(value.shape==(1,1000) and value.dtype==np.float32 and bool(np.isfinite(value).all()),'finite FP32 [1,1000] logits')
    ids=np.argsort(-value[0],kind='stable');top=[int(v) for v in ids[:5]]
    return {'top5':top,'top1_hit':top[0]==label,'top5_hit':label in top}
def paired_counts(rows,device):
    first=sum(r[device]['top1_hit'] for r in rows);fifth=sum(r[device]['top5_hit'] for r in rows)
    return {'images':len(rows),'top1_correct':first,'top5_correct':fifth,'top1_pct':100*first/len(rows),'top5_pct':100*fifth/len(rows)}
def summarize(rows):
    need(len(rows)==500 and [r['ordinal'] for r in rows]==list(range(500)),'exact same ordered500 required')
    need(len({r['id'] for r in rows})==len({r['image_sha256'] for r in rows})==500,'duplicate images')
    dense=paired_counts(rows,'Dense');sparse=paired_counts(rows,'Sparse')
    matched=[r for r in rows if r['sparse_software_revision']==SPARSE_REVISION]
    need([r['ordinal'] for r in matched]==list(range(305,500)),'fixed same-scheduler195 comparison differs')
    dl=distribution([r['dense_inference_ms'] for r in matched]);sl=distribution([r['sparse_inference_ms'] for r in matched])
    transitions={}
    for metric in ('top1_hit','top5_hit'):
        transitions[metric]={'both_correct':sum(r['Dense'][metric] and r['Sparse'][metric] for r in rows),
            'dense_only_correct':sum(r['Dense'][metric] and not r['Sparse'][metric] for r in rows),
            'sparse_only_correct':sum(not r['Dense'][metric] and r['Sparse'][metric] for r in rows),
            'both_incorrect':sum(not r['Dense'][metric] and not r['Sparse'][metric] for r in rows)}
    return {'schema':'opt50_original_dense_sparse_paired500_v1','status':'PAIRED500_DENSE_SPARSE_SAVED_LOGITS_REVIEW_PASS',
        'images':500,'label_classes':len({r['label'] for r in rows}),'classes_ranked_per_image':1000,
        'original_Dense_accuracy':dense,'trained_Sparse24_accuracy':sparse,'paired_classification':transitions,
        'Sparse_minus_Dense_accuracy_pp':{k:sparse[k]-dense[k] for k in ('top1_pct','top5_pct')},
        'no_accuracy_loss_observed_on_this_subset':sparse['top1_correct']>=dense['top1_correct'] and sparse['top5_correct']>=dense['top5_correct'],
        'Dense_CPU_PL_bitwise_mismatches':0,'Sparse_CPU_PL_bitwise_mismatches':0,'compared_logits_per_model':500000,
        'Dense_completed_PL_calls':18000,'Dense_modes':{'Dense':18000,'Sparse':0},
        'matched_current_scheduler_latency':{'images':195,'ordinal_range_1based':[306,500],'Dense_PS_PL':dl,'Sparse_PS_PL':sl,
            'Dense_mean_over_Sparse_mean':dl['mean_ms']/sl['mean_ms'],'Sparse_mean_latency_reduction_pct':100*(1-sl['mean_ms']/dl['mean_ms']),
            'scope':'Same195 inputs and current PS scheduler; model weights and Dense/Sparse modes differ. Not isolated PL timing.'},
        'full500_recorded_Dense_PS_PL':distribution([r['dense_inference_ms'] for r in rows]),
        'full500_recorded_Dense_CPU_reference':distribution([r['dense_CPU_reference_ms'] for r in rows]),
        'full500_recorded_Sparse_PS_PL_mixed_revisions':distribution([r['sparse_inference_ms'] for r in rows]),
        'full500_uniform_version_latency_comparison_verified':False,'isolated_sparse_kernel_speedup_claimed':False,
        'full_1800_acceptance':False,'ImageNet_full_validation':False,'hardware_accessed_by_report':False,
        'accuracy_scope':'First500 images/250 classes of the fixed acceptance corpus; all1000 outputs ranked.',
        'latency_scope':'Recorded Runner.run excludes setup and CPU reference; includes BEGIN/END and client sync. First500 Sparse records include several software revisions.'}

def collect(public=PUBLIC):
    import numpy as np
    sys.path.insert(0,str(public))
    need(pin((public/'tools_manifest.json').read_bytes())==SOFTWARE_PIN,'fixed Dense report source changed')
    import verify_tools
    release=verify_tools.verify(public)
    entry_path=public/'run_model.py';spec=importlib.util.spec_from_file_location('_dense_final_entry',entry_path)
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    from bounded_sparse_reference import install
    install(entry)
    a=entry.api();index,_=a.history_index.load(a);a.history_index.verify_known(index)
    need(index['images_count']==500 and set(index['samples'])=={str(i) for i in range(500)},'Dense500 not all formally committed')
    reference=entry.paired_reference_proof(a,entry.Path('/home/ubuntu/fpga_project/opt50_integration/b24_acceptance500_reboot_exports_20261030_v1/action_a77e3fa15328'))
    sparse_root=Path(reference['report_root']);archive_raw=a.c.read_file(sparse_root/'evidence.tar.gz',limit=64*1024*1024)
    archive=tarfile.open(fileobj=io.BytesIO(archive_raw),mode='r:gz')
    members=archive.getmembers();names={m.name:m for m in members}
    need(len(names)==len(members) and all(m.isfile() and not m.issym() and not m.islnk() for m in members),'ordinary unique Sparse archive')
    csv_raw=archive.extractfile(names['report/per_image.csv']).read()
    sparse_rows=list(csv.DictReader(io.StringIO(csv_raw.decode())))
    need(len(sparse_rows)==500 and [int(r['ordinal']) for r in sparse_rows]==list(range(500)),'Sparse CSV exact500 order')
    dense={};evidence={};plans=[]
    def add(path):
        path=Path(path);raw=a.c.read_file(path,private=True,limit=64*1024*1024)
        need(path.is_relative_to(entry.RECORDS),'Dense proof outside namespace')
        evidence[str(path)]={'path':str(path),'archive_name':'dense/'+path.relative_to(entry.RECORDS).as_posix(),**pin(raw)}
        return raw
    for path in sorted(index['batches'],key=lambda p:index['batches'][p]['ordinals'][0]):
        plan_path=Path(path);root=plan_path.parent
        historical,_,_=a.compat.load_closed_entry(plan_path,a.tools,access=a.c)
        rows=historical.read_completed_records(plan_path,allow_partial=False)
        plan=a.c.read_json(plan_path,private=True)
        need(plan['expected_Dense_calls']==36 and plan['expected_Sparse_calls']==0 and plan['source_model_sha256']==verify_tools.MODEL_SHA,'original Dense36 source/call policy')
        plans.append(path)
        for f in ('plan.json','batch_completion.json','batch_status.json','build_receipt.json','tools/tools_manifest.json','model/manifest.json'):add(root/f)
        for folder in (root/'batch_events',root/'execution'):
            for f in folder.rglob('*'):
                if f.is_file():add(f)
        for row in rows:
            ordinal=row['ordinal'];need(ordinal not in dense,'duplicate Dense image')
            folder=root/'samples'/('%06d'%ordinal);commit_raw=add(folder/'commit.json');commit=a.c.decode_json(commit_raw)
            for n,p in commit['files'].items():
                raw=a.c.read_file(folder/n,private=True);need(pin(raw)==p,'Dense committed file changed')
                if n!='input.npy':add(folder/n)
            cpu=np.load(io.BytesIO(add(folder/'cpu_reference.npy')),allow_pickle=False)
            pl=np.load(io.BytesIO(add(folder/'model_logits.npy')),allow_pickle=False)
            need(cpu.dtype==pl.dtype==np.float32 and cpu.shape==pl.shape==(1,1000) and
                 bool(np.array_equal(cpu.view(np.uint32),pl.view(np.uint32))),'Dense CPU/PL bit mismatch')
            checked=a.metrics.compare(cpu,pl,row['label'])
            need(checked['bitwise_mismatches']==0 and checked['PL_top5']==row['PL_top5'],'Dense saved rank mismatch')
            dense[ordinal]=(row,rank(np,pl,row['label']))
    need(set(dense)==set(range(500)),'Dense500 proof incomplete')
    output=[]
    for ordinal,s in enumerate(sparse_rows):
        d,dr=dense[ordinal];label=int(s['label'])
        need(d['id']==s['id'] and d['label']==label and d['image_sha256']==s['image_sha256'] and d['input_sha256']==s['input_sha256'],'models did not use identical image/label/input')
        folder=Path(s['sample_dir']);need(folder.is_relative_to(Path('/var/lib/opt50-b24-model-v1')),'Sparse archive source namespace')
        stem='original/'+folder.relative_to(Path('/var/lib/opt50-b24-model-v1')).as_posix()+'/'
        def sparse_logits(name):
            m=names[stem+name];need(m.size<16384,'bounded Sparse logits');return np.load(io.BytesIO(archive.extractfile(m).read()),allow_pickle=False)
        sc,sp=sparse_logits('cpu_reference.npy'),sparse_logits('model_logits.npy')
        need(sc.dtype==sp.dtype==np.float32 and sc.shape==sp.shape==(1,1000) and
             np.array_equal(sc.view(np.uint32),sp.view(np.uint32)),'original Sparse CPU/PL bits differ')
        sr=rank(np,sp,label);need(sr['top5']==json.loads(s['PL_top5']),'Sparse saved rank differs')
        output.append({'ordinal':ordinal,'image_1based':ordinal+1,'id':s['id'],'label':label,'image_sha256':s['image_sha256'],
            'input_sha256':s['input_sha256'],'Dense':dr,'Sparse':sr,'dense_inference_ms':d['inference_ms'],
            'dense_CPU_reference_ms':d['CPU_reference_ms'],'sparse_inference_ms':float(s['inference_ms']),
            'sparse_software_revision':s['software_revision']})
    archive.close()
    return output,list(evidence.values()),reference,plans

def write(path,raw):
    with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    path.chmod(0o644)
def export(out,public=PUBLIC):
    rows,evidence,reference,plans=collect(public);summary=summarize(rows)
    summary['Sparse_reference']=reference;summary['Dense_plans']=plans
    out=Path(out).absolute();need(out.parent==EXPORTS and not out.exists() and not out.is_symlink(),'exclusive fixed export path')
    for parent in (out.parent,*out.parent.parents):
        need(not parent.is_symlink(),'linked export ancestor')
    out.parent.mkdir(exist_ok=True);out.mkdir(mode=0o755)
    text=io.StringIO(newline='');fields=('image_1based','id','label','Dense_top1','Sparse_top1','Dense_top5','Sparse_top5','Dense_top1_hit','Sparse_top1_hit','Dense_top5_hit','Sparse_top5_hit','dense_inference_ms','sparse_inference_ms','image_sha256','input_sha256')
    writer=csv.DictWriter(text,fieldnames=fields,lineterminator='\n');writer.writeheader()
    for r in rows:
        row={k:r[k] for k in fields if k in r}
        for device in ('Dense','Sparse'):
            row.update({device+'_top1':r[device]['top5'][0],device+'_top5':json.dumps(r[device]['top5']),
                device+'_top1_hit':r[device]['top1_hit'],device+'_top5_hit':r[device]['top5_hit']})
        writer.writerow(row)
    summary['Dense_source_model_sha256']='f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'
    summary['Sparse_source_model_sha256']='ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
    summary['Dense_tools_manifest']=pin((public/'tools_manifest.json').read_bytes())
    files={'summary.json':encoded(summary),'per_image_comparison.csv':text.getvalue().encode(),
        'evidence_files.json':encoded({'files':evidence,'original_records_modified':False}),
        'README.txt':b'Paired original Dense and trained Sparse24 on the same500 images. Accuracy is paired across500; primary timing is the same-scheduler195 subset. No isolated sparse-kernel or full ImageNet claim. Original records retained.\n'}
    for name,raw in files.items():write(out/name,raw)
    target=out/'evidence.tar.gz'
    with tarfile.open(target,mode='w:gz',format=tarfile.PAX_FORMAT) as tar:
        def member(name,raw):
            m=tarfile.TarInfo(name);m.size=len(raw);m.mode=0o644;m.mtime=0;tar.addfile(m,io.BytesIO(raw))
        for name,raw in files.items():member('report/'+name,raw)
        for item in evidence:
            raw=Path(item['path']).read_bytes();need(pin(raw)=={k:item[k] for k in ('bytes','sha256')},'Dense evidence changed during export')
            member(item['archive_name'],raw)
        original=Path(reference['report_root'])/'evidence.tar.gz';raw=original.read_bytes();need(pin(raw)==reference['archive'],'Sparse archive changed during export')
        member('sparse_reference/evidence.tar.gz',raw)
    target.chmod(0o644)
    with target.open('rb') as stream:os.fsync(stream.fileno())
    digest=pin(target.read_bytes())
    write(out/'evidence.tar.gz.sha256',(digest['sha256']+'  evidence.tar.gz\n').encode())
    write(out/'SHA256SUMS',''.join(pin(p.read_bytes())['sha256']+'  '+p.name+'\n' for p in sorted(out.iterdir())).encode())
    for directory in (out,out.parent):
        descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    return {'status':summary['status'],'export_dir':str(out),'archive':digest,'summary':str(out/'summary.json'),'hardware_accessed':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--evidence-tar',action='store_true');args=p.parse_args()
    need(os.name=='posix' and os.geteuid()==0,'review as Linux root')
    print(json.dumps(export(args.out),indent=2,allow_nan=False))
if __name__=='__main__':main()
