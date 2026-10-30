"""Independently recalculate saved500-image results using only the Python standard library."""
import argparse
import ast
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import struct
import tarfile

ARCHIVE_SHA='3dd66ac7e784153ce6d479be2017f54d678870cbc02d4f02dbdc681d366f591b'
SPARSE_SHA='f14b1a7e3ec26fd7205e4b32ca57b0ae5ab55972cbcb353fbcb406003bc182fb'
REVISION='38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d'
def need(ok,why):
    if not ok:raise ValueError(why)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def archive(raw):
    t=tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz');members=t.getmembers()
    need(len(members)==len({m.name for m in members}) and all(m.isfile() and not m.issym() and not m.islnk() for m in members),'unique regular archive members required')
    # Read relevant members in physical order once. Repeated backward gzip
    # seeks per image would needlessly decompress the proof hundreds of times.
    t._paired_cache={}
    for m in members:
        if m.name.endswith(('.npy','/accuracy.json')) or m.name.startswith('report/') or m.name=='sparse_reference/evidence.tar.gz':
            need(m.size<=64*1024*1024,'bounded relevant archive member')
            t._paired_cache[m.name]=t.extractfile(m).read()
    return t,{m.name:m for m in members}
def member(t,names,name,limit=32*1024*1024):
    m=names[name];need(m.size<=limit,'bounded archive member required: '+name)
    raw=t._paired_cache[name];need(len(raw)==m.size,'short archive member');return raw
def tensor(raw):
    need(raw[:6]==b'\x93NUMPY' and len(raw)>=12,'NumPy tensor file required')
    if raw[6:8]==b'\x01\x00':offset=10;length=struct.unpack_from('<H',raw,8)[0]
    else:
        need(raw[6:8] in (b'\x02\x00',b'\x03\x00'),'NumPy header version unsupported')
        offset=12;length=struct.unpack_from('<I',raw,8)[0]
    need(length<=8192,'bounded NumPy header')
    h=ast.literal_eval(raw[offset:offset+length].decode().strip())
    need(h=={'descr':'<f4','fortran_order':False,'shape':(1,1000)},'exact C-contiguous FP32 [1,1000] tensor required')
    payload=raw[offset+length:];need(len(payload)==4000,'exact1000 FP32 values required')
    values=struct.unpack('<1000f',payload);need(all(math.isfinite(v) for v in values),'finite logits required')
    return payload,values
def top5(values):return sorted(range(1000),key=lambda i:(-values[i],i))[:5]
def distribution(values):
    xs=sorted(values);need(xs and all(math.isfinite(x) and x>=0 for x in xs),'finite nonnegative timings required')
    def quantile(p):
        x=p*(len(xs)-1);lo=math.floor(x);hi=math.ceil(x);return xs[lo]+(xs[hi]-xs[lo])*(x-lo)
    return {'samples':len(xs),'mean_ms':math.fsum(xs)/len(xs),'median_ms':statistics.median(xs),
        'p95_ms':quantile(.95),'min_ms':xs[0],'max_ms':xs[-1]}
def near(a,b):need(math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-9),'recalculated metric differs')
def verify(root):
    root=Path(root).absolute();raw=(root/'evidence.tar.gz').read_bytes()
    need(sha(raw)==ARCHIVE_SHA,'downloaded evidence differs from the exact board success SHA256')
    outer,onames=archive(raw)
    summary_raw=member(outer,onames,'report/summary.json')
    need(summary_raw==(root/'summary.json').read_bytes(),'summary is not bound to the pinned evidence archive')
    summary=json.loads(summary_raw)
    need(summary['status']=='PAIRED500_DENSE_SPARSE_SAVED_LOGITS_REVIEW_PASS' and summary['images']==500,'complete paired500 report required')
    comparison_raw=member(outer,onames,'report/per_image_comparison.csv')
    need(comparison_raw==(root/'per_image_comparison.csv').read_bytes(),'CSV is not bound to pinned evidence')
    pairs=list(csv.DictReader(io.StringIO(comparison_raw.decode())))
    sparse_raw=member(outer,onames,'sparse_reference/evidence.tar.gz',64*1024*1024)
    need(sha(sparse_raw)==SPARSE_SHA,'original Sparse evidence archive differs')
    sparse,snames=archive(sparse_raw)
    prior=list(csv.DictReader(io.StringIO(member(sparse,snames,'report/per_image.csv').decode())))
    need(len(pairs)==len(prior)==500,'exact500 records required')
    dense_folders={}
    for name in onames:
        if name.startswith('dense/') and name.endswith('/cpu_reference.npy'):
            parts=name.split('/');need(len(parts)==5 and parts[2]=='samples','canonical Dense tensor archive path')
            n=int(parts[3]);need(n not in dense_folders,'duplicate Dense ordinal');dense_folders[n]='/'.join(parts[:-1])+'/'
    need(set(dense_folders)==set(range(500)),'complete unique Dense ordinals0..499')
    ids=set();images=set();hits={d:{'top1':0,'top5':0} for d in ('Dense','Sparse')};current_dense=[];current_sparse=[];cpu_dense=[]
    for n,(pair,old) in enumerate(zip(pairs,prior)):
        need(int(pair['image_1based'])==n+1 and int(old['ordinal'])==n,'ordered paired image range')
        for key in ('id','label','image_sha256','input_sha256'):need(pair[key]==old[key],'paired image/input/label identity differs')
        need(pair['id'] not in ids and pair['image_sha256'] not in images,'duplicate image');ids.add(pair['id']);images.add(pair['image_sha256'])
        label=int(pair['label']);need(0<=label<1000,'valid true label')
        dense_path=dense_folders[n]
        old_path=old['sample_dir'];prefix='/var/lib/opt50-b24-model-v1/'
        need(old_path.startswith(prefix) and '..' not in old_path.split('/'),'canonical original Sparse sample path')
        sparse_path='original/'+old_path[len(prefix):]+'/'
        for device,t,names,path in (('Dense',outer,onames,dense_path),('Sparse',sparse,snames,sparse_path)):
            cb,cv=tensor(member(t,names,path+'cpu_reference.npy',16384))
            pb,pv=tensor(member(t,names,path+'model_logits.npy',16384))
            need(cb==pb,'saved CPU/PL raw FP32 bits differ')
            ranked=top5(pv);need(ranked==json.loads(pair[device+'_top5']),'independent full1000-class ranking differs')
            need(int(pair[device+'_top1'])==ranked[0],'CSV Top1 differs')
            need(pair[device+'_top1_hit']==str(ranked[0]==label) and pair[device+'_top5_hit']==str(label in ranked),'CSV label hit differs')
            hits[device]['top1']+=ranked[0]==label;hits[device]['top5']+=label in ranked
        accuracy=json.loads(member(outer,onames,dense_path+'accuracy.json'))
        near(float(pair['dense_inference_ms']),accuracy['inference_ms']);near(float(pair['sparse_inference_ms']),float(old['inference_ms']))
        need(accuracy['Dense_calls']==36 and accuracy['Sparse_calls']==0 and accuracy['completed_PL_calls']==36,'Dense36/Sparse0 calls required')
        cpu_dense.append(accuracy['CPU_reference_ms'])
        if old['software_revision']==REVISION:
            need(n>=305,'unexpected current Sparse scheduler range');current_dense.append(float(pair['dense_inference_ms']));current_sparse.append(float(pair['sparse_inference_ms']))
    need(len(current_dense)==len(current_sparse)==195,'same current-scheduler195 timing records required')
    for device,key in (('Dense','original_Dense_accuracy'),('Sparse','trained_Sparse24_accuracy')):
        for top in ('top1','top5'):
            need(hits[device][top]==summary[key][top+'_correct'],'recalculated labeled accuracy differs')
            near(100*hits[device][top]/500,summary[key][top+'_pct'])
    for top in ('top1','top5'):near(100*(hits['Sparse'][top]-hits['Dense'][top])/500,summary['Sparse_minus_Dense_accuracy_pp'][top+'_pct'])
    need(summary['no_accuracy_loss_observed_on_this_subset']==all(hits['Sparse'][top]>=hits['Dense'][top] for top in ('top1','top5')),'subset accuracy conclusion differs')
    timing={'Dense_PS_PL':distribution(current_dense),'Sparse_PS_PL':distribution(current_sparse)}
    for device,stats in timing.items():
        for key in ('mean_ms','median_ms','p95_ms'):near(stats[key],summary['matched_current_scheduler_latency'][device][key])
    ratio=timing['Dense_PS_PL']['mean_ms']/timing['Sparse_PS_PL']['mean_ms']
    near(ratio,summary['matched_current_scheduler_latency']['Dense_mean_over_Sparse_mean'])
    result={'status':'LOCAL_INDEPENDENT_SAVED_LOGITS_AND_PAIRED_METRICS_PASS','archive_sha256':ARCHIVE_SHA,'images':500,
        'CPU_PL_bitwise_mismatches':{'Dense':0,'Sparse':0},'FP32_values_compared_per_model':500000,
        'labeled_correct_counts':hits,'same_scheduler195_latency':timing,'Dense_mean_over_Sparse_mean':ratio,
        'Dense_full500_CPU_reference':distribution(cpu_dense),'hardware_accessed':False,
        'scope':'Independent standard-library saved-tensor ranking and metric recomputation. Native execution/closure evidence remains the original board acceptance program proof.'}
    outer.close();sparse.close()
    output=root/'local_independent_review.json'
    payload=(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()
    if output.exists():need(output.read_bytes()==payload,'existing local review differs; do not overwrite')
    else:
        with output.open('xb') as stream:stream.write(payload)
    return result
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);args=p.parse_args()
    print(json.dumps(verify(args.directory),indent=2))
if __name__=='__main__':main()
