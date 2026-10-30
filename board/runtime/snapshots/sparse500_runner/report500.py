"""Export exactly the first 500 closed real-image records. No device/owner calls."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import statistics
import sys
import tarfile
import types

sys.dont_write_bytecode = True
SOFTWARE = Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_resident_20261030_reboot_r1/software')
SOFTWARE_PIN = {'bytes':25141,'sha256':'38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d'}
RECORDS = Path('/var/lib/opt50-b24-model-v1')
INDEX = Path('/var/lib/opt50-b24-history-v3')
EXPORT_ROOT = Path('/home/ubuntu/fpga_project/opt50_integration/b24_acceptance500_reboot_exports_20261030_v1')
COUNT = 500
PROFILE = 'b24-batch-acceptance-cpu4-pl4-v1'


def need(value,reason):
    if not value: raise ValueError(reason)


def pin(raw): return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def encoded(value): return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def safe_relative(name):
    need(type(name) is str and name and not any(c in name for c in '\\:\0\r\n'),'unsafe relative evidence name')
    path=Path(name)
    need(not path.is_absolute() and '..' not in path.parts and '.' not in path.parts,'unsafe relative evidence path')
    return path


def ordinary(path):
    path=Path(path).absolute(); need('..' not in path.parts,'parent traversal refused')
    for part in (path,*path.parents):
        info=part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'linked evidence/export path refused')
    return path


def read(path,limit=64*1024*1024):
    path=ordinary(path); before=path.stat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=limit,'bounded single-link input required')
    key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_birthtime_ns if os.name=='nt' else s.st_ctime_ns)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    with os.fdopen(fd,'rb') as stream:
        need(key(os.fstat(stream.fileno()))==key(before),'input replaced before read')
        raw=stream.read(limit+1); need(key(os.fstat(stream.fileno()))==key(before),'input changed during read')
    need(key(path.stat())==key(before) and len(raw)==before.st_size,'input replaced after read')
    return raw


def discover_paths(index):
    """The verified index discovers explicit plans; it never proves their data."""
    wanted=set(range(COUNT)); groups=[]; found=[]
    for name,batch in index['batches'].items():
        values=batch['ordinals']; selected=set(values)&wanted
        if not selected: continue
        need(set(values)<=wanted,'a committed batch straddles image500; do not silently include/drop images')
        groups.append((min(values),Path(name))); found.extend(values)
    need(len(found)==COUNT and len(set(found))==COUNT and set(found)==wanted,'need exactly closed ordinals0..499, no duplicates/gaps')
    return [path for _,path in sorted(groups)]


def distribution(values):
    values=list(values)
    need(values and all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in values),'finite nonnegative timing required')
    ordered=sorted(float(x) for x in values)
    def percentile(q):
        at=q*(len(ordered)-1); low=math.floor(at); high=math.ceil(at)
        return ordered[low]+(ordered[high]-ordered[low])*(at-low)
    return {'samples':len(values),'mean_ms':statistics.mean(values),'median_ms':statistics.median(values),
            'p90_ms':percentile(.9),'p95_ms':percentile(.95),'min_ms':min(values),'max_ms':max(values),
            'sum_ms':math.fsum(values),'percentile_method':'linear interpolation at p*(n-1)'}


def ordinal_ranges(items):
    ranges=[]
    for ordinal in sorted(row['ordinal'] for row in items):
        if ranges and ranges[-1][1]==ordinal:ranges[-1][1]=ordinal+1
        else:ranges.append([ordinal+1,ordinal+1])
    return ranges


def summarize(rows,review):
    rows=sorted(rows,key=lambda row:row['ordinal'])
    need(len(rows)==COUNT and [row['ordinal'] for row in rows]==list(range(COUNT)),
         'report requires exactly ordinals0..499')
    need(len({row['id'] for row in rows})==COUNT and len({row['image_sha256'] for row in rows})==COUNT,
         'duplicate image ID or JPEG hash')
    result=review['result']
    exact={'images':500,'compared_logits':500000,'completed_PL_calls':18000,'Dense_calls':6000,'Sparse_calls':12000,
           'ordinal_start':0,'ordinal_stop_exclusive':500,'all_devices_closed':True,'all_native_END_verified':True,
           'all_postrun_buffer_checks_verified':True,'dataset_binding_verified':True}
    need(all(type(result.get(k)) is type(v) and result[k]==v for k,v in exact.items()),'full closed500 review counters differ')
    need(review.get('all_saved_logits_recomputed') is True and review.get('hardware_accessed_by_review') is False,
         'independent saved-logit review required')
    need(all(row['measurement_profile']==PROFILE and row['ORT_intra_threads']==4 and row['ORT_inter_threads']==1 for row in rows),
         'numerical/thread profile differs')
    need(sum(row['bitwise_mismatches'] for row in rows)==result['bitwise_mismatches'] and
         max(row['max_abs_difference'] for row in rows)==result['max_abs_difference'],'per-image numerical sum differs')
    need(result['numerical_status']=='NUMERICAL_PASS' and result['bitwise_mismatches']==0 and
         result['max_abs_difference']==0,'500-image numerical comparison did not pass')
    label_count=len({row['label'] for row in rows})
    need(result.get('true_label_classes')==label_count and result.get('full_1800_acceptance') is False,
         'actual label class count or subset scope differs')
    for device in ('CPU','PL'):
        first=sum(row[device+'_top5'][0]==row['label'] for row in rows)
        fifth=sum(row['label'] in row[device+'_top5'] for row in rows)
        need(result['accuracy'][device]=={'images':500,'top1_correct':first,'top5_correct':fifth,
             'top1_pct':100*first/500,'top5_pct':100*fifth/500},'labeled accuracy differs from independent per-image Top5')
    grouped={}
    for row in rows:
        key=(row['measurement_profile'],row['lifecycle_profile'],row['software_revision'])
        grouped.setdefault(key,[]).append(row)
    groups=[]
    for (numeric,lifecycle,revision),items in sorted(grouped.items()):
        groups.append({'measurement_profile':numeric,'lifecycle_profile':lifecycle,'software_revision':revision,
            'images':len(items),'ordinal_ranges_1based':ordinal_ranges(items),
            'Runner_run':distribution(x['inference_ms'] for x in items),
            'CPU_reference':distribution(x['CPU_reference_ms'] for x in items)})
    return {'schema':'opt50_b24_subset500_report_v1','status':'SUBSET500_NUMERICAL_AND_LABELED_ACCURACY_VERIFIED',
        'images':500,'target_500_complete':True,'subset500_accuracy_computed':True,
        'ordinal_start':0,'ordinal_stop_exclusive':500,'classes_ranked_per_image':1000,'actual_true_label_classes':label_count,
        'compared_FP32_values_bitwise':500000,
        'bitwise_mismatch_images':result['bitwise_mismatch_images'],'bitwise_mismatches':result['bitwise_mismatches'],
        'max_abs_difference':result['max_abs_difference'],'accuracy':result['accuracy'],
        'PL_calls':18000,'Dense_calls':6000,'Sparse_calls':12000,'linear_routes_per_image':{'PL':36,'PS':13,'PL_dense':12,'PL_sparse':24},
        'overall_recorded_Runner_run':distribution(row['inference_ms'] for row in rows),
        'overall_recorded_CPU_reference':distribution(row['CPU_reference_ms'] for row in rows),'latency_groups':groups,
        'latency_scope':'Recorded Runner.run includes graph BEGIN/END and required client synchronization; excludes setup and CPU reference. Overall500 mixes software revisions/lifecycles; not a steady-state or pure-PL benchmark.',
        'wall_time_scope':'No overall wall latency inferred from summed Runner.run; initialization, checks, pauses and export are separate.',
        'source_model_sha256':review['source_model_sha256'],'dataset_manifest':review['dataset_manifest'],
        'review_software_manifest':SOFTWARE_PIN,'full_1800_acceptance':False,'ImageNet_full_validation':False,
        'all_images_classified_correctly_claimed':False,'performance_verified':False,
        'hardware_accessed_by_export':False,'original_records_modified':False,
        'scope':'First500 real images of the exposed B1800 regression corpus. CPU4 mathematical equivalence and true-label classification accuracy are separate results.'}


@contextmanager
def checked_modules():
    raw=read(SOFTWARE/'tools_manifest.json'); need(pin(raw)==SOFTWARE_PIN,'fixed fix2 release manifest differs')
    manifest=json.loads(raw)
    for name,expected in manifest['files'].items():
        need(pin(read(SOFTWARE/safe_relative(name)))==expected,'fixed fix2 source changed: '+name)
    names=('verify_tools','accuracy_metrics','dataset','release_compat','history_index','legacy_model','review_results')
    previous={name:sys.modules.get(name) for name in names}; old_path=list(sys.path)
    try:
        sys.path.insert(0,str(SOFTWARE))
        for name in names:
            module=types.ModuleType(name);module.__file__=str(SOFTWARE/(name+'.py'));sys.modules[name]=module
            exec(compile(read(module.__file__),module.__file__,'exec'),module.__dict__)
        yield sys.modules['review_results'],sys.modules['history_index']
    finally:
        sys.path[:]=old_path
        for name,value in previous.items():
            if value is None:sys.modules.pop(name,None)
            else:sys.modules[name]=value


def collect(review,index_api):
    index,raw=index_api.load(types.SimpleNamespace(metrics=review.metrics))
    paths=discover_paths(index); captured={}; commit_pins={}; original=review.recompute_records
    def capture(root,records):
        before={row['ordinal']:pin(read(Path(root)/'samples'/('%06d'%row['ordinal'])/'commit.json')) for row in records}
        checked=original(root,records)  # Preserve the released full1000-logit calculation.
        need(str(root) not in captured,'same plan recomputed twice')
        need(before=={row['ordinal']:pin(read(Path(root)/'samples'/('%06d'%row['ordinal'])/'commit.json')) for row in records},
             'commits changed during independent logit review')
        captured[str(root)]=checked
        commit_pins[str(root)]=before
        return checked
    review.recompute_records=capture
    try: report=review.review(paths,progress=True)
    finally:review.recompute_records=original
    rows=[]; evidence={}; archive_names={}
    def add(path,expected=None,shared=None,include=True):
        path=Path(path).absolute(); need(path.is_relative_to(RECORDS),'evidence outside fixed original record namespace')
        ordinary(path)
        expected=pin(read(path)) if expected is None else expected
        name=('shared/'+shared+'/'+expected['sha256']+'.json' if shared else 'original/'+path.relative_to(RECORDS).as_posix())
        item={'path':str(path),'archive_name':name,'bytes':expected['bytes'],'sha256':expected['sha256'],'included_in_optional_archive':include}
        if str(path) in evidence:need(evidence[str(path)]==item,'conflicting evidence path pin')
        evidence[str(path)]=item
        if include:
            if name in archive_names:need(archive_names[name]==expected,'archive path collision')
            archive_names[name]=expected
    chunk_pins={item['plan']:item for item in report['chunks']}
    for path in paths:
        root=path.parent; raw_plan=read(path);need(pin(raw_plan)==chunk_pins[str(path)]['plan_pin'],'plan changed after deep review')
        plan=json.loads(raw_plan); frozen=json.loads(read(root/'dataset/manifest.json'))
        need(plan['dataset_manifest']==report['dataset_manifest'] and plan.get('source_model_sha256')==report['source_model_sha256'],
             'chunk model/dataset differs')
        for name in ('plan.json','batch_completion.json','batch_status.json','tools/tools_manifest.json','build_receipt.json'):
            if (root/name).is_file():add(root/name)
        add(root/'model/manifest.json',shared='model_manifest');add(root/'dataset/manifest.json',shared='dataset_manifest')
        for directory in (root/'batch_events',root/'execution'):
            if directory.is_dir():
                for file in sorted(directory.rglob('*')):
                    need(not file.is_symlink(),'linked original audit member')
                    if file.is_file():add(file)
        revision=plan['tool_files']['manifest']['sha256'];lifecycle=plan.get('lifecycle_profile','fresh-owner-per-image')
        for record in captured[str(root)]:
            ordinal=record['ordinal'];folder=root/'samples'/('%06d'%ordinal);sample=frozen['samples'][ordinal]
            commit_path=folder/'commit.json';commit_raw=read(commit_path);commit=json.loads(commit_raw)
            need(pin(commit_raw)==commit_pins[str(root)][ordinal],'commit changed after independent original-record review')
            add(commit_path)
            for name,expected in commit['files'].items():
                add(folder/safe_relative(name),expected,include=name!='input.npy')
            image=root/'dataset'/safe_relative(sample['image']['file']);add(image,{k:sample['image'][k] for k in ('bytes','sha256')})
            rows.append(dict(record,measurement_profile=plan['measurement_profile'],lifecycle_profile=lifecycle,
                software_revision=revision,ORT_intra_threads=plan.get('ORT_intra_threads',4),
                ORT_inter_threads=plan.get('ORT_inter_threads',1),plan_path=str(path),sample_dir=str(folder),
                image_path=str(image),input_path=str(folder/'input.npy'),cpu_logits_path=str(folder/'cpu_reference.npy'),
                pl_logits_path=str(folder/'model_logits.npy')))
    rows.sort(key=lambda row:row['ordinal'])
    return rows,report,sorted(evidence.values(),key=lambda row:row['path']),{'history_index_pin':pin(raw),'explicit_plans':[str(path) for path in paths]}


def csv_bytes(rows):
    fields=('ordinal','image_1based','id','label','CPU_top1','PL_top1','CPU_top5','PL_top5','CPU_top1_hit','PL_top1_hit',
        'CPU_top5_hit','PL_top5_hit','bitwise_mismatches','max_abs_difference','inference_ms','CPU_reference_ms',
        'measurement_profile','lifecycle_profile','software_revision','image_sha256','input_sha256',
        'CPU_logits_sha256','PL_logits_sha256','completed_PL_calls','Dense_calls','Sparse_calls','plan_path',
        'sample_dir','image_path','input_path','cpu_logits_path','pl_logits_path')
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader()
    for row in rows:
        value={key:row[key] for key in fields if key in row}
        value.update(image_1based=row['ordinal']+1,CPU_top1=row['CPU_top5'][0],PL_top1=row['PL_top5'][0],
                     CPU_top5=json.dumps(row['CPU_top5']),PL_top5=json.dumps(row['PL_top5']))
        writer.writerow(value)
    return stream.getvalue().encode('utf-8')


README = '''This report covers exactly images1..500 of the exposed B1800 regression subset, not all1800 or full ImageNet validation.
CPU4/PS+PL1000-logit bit equality proves execution equivalence; ground-truth Top1/Top5 measures classification quality. Some images can be classified incorrectly while numerical equivalence passes.
Each image has49 linear layers:36 PL(12 Dense+24 Sparse),13 PS. Native receipts and true final owner closure were independently checked by the fixed released reader.
Runner.run excludes setup/CPU reference; includes BEGIN/END and client synchronization. First45 images used old one-shot lifecycles; later resident revisions differ. Groups are separated; combined500 statistics are descriptive, not a steady-state/pure-PL benchmark. Runner sums are not wall time.
evidence_files.json lists original immutable source paths and required SHA256s. Optional tar verifies copied bytes against these pins and includes CPU/PL NPYs, JPEGs, commit/Native/close receipts, and deduplicated model/dataset manifests. Large input.npy files are listed by SHA but excluded from tar; full model weights/ELFs are excluded. Recheck output arithmetic/classification offline from the logits and labels; full inference replay still needs the original model/environment. No original record is edited or chmodded.
SHA256SUMS authenticates new export files. evidence.tar.gz.sha256 checks the optional archive alone. Keep the original root-private records as the authoritative source.
'''


def write_new(path,raw):
    with Path(path).open('xb') as stream:
        need(stream.write(raw)==len(raw),'short export write');stream.flush();os.fsync(stream.fileno())
    Path(path).chmod(0o644)


def export(out,rows,review,evidence,provenance,with_tar=False):
    summary=summarize(rows,review);out=Path(out).absolute()
    need('..' not in out.parts and not out.exists() and not out.is_symlink(),'new export directory required; never overwrite')
    if not out.parent.exists():
        need(out.parent==EXPORT_ROOT,'only the fixed new export parent may be created')
        ordinary(out.parent.parent);out.parent.mkdir(mode=0o755);out.parent.chmod(0o755)
    ordinary(out.parent)
    need(not any(out.is_relative_to(path) for path in (RECORDS,INDEX,SOFTWARE)),'export must not be inside original records/index/sealed software')
    out.mkdir(mode=0o755);out.chmod(0o755)
    summary['provenance']=provenance;summary['report_tool_sha256']=pin(Path(__file__).read_bytes())['sha256']
    files={'summary.json':encoded(summary),'per_image.csv':csv_bytes(rows),'review.json':encoded(review),
        'evidence_files.json':encoded({'schema':'opt50_subset500_evidence_files_v1','files':evidence,
            'original_records_modified':False,'input_npy_archive_policy':'SHA listed; not copied in optional tar'}),
        'README.txt':README.encode('utf-8')}
    for name,raw in files.items():write_new(out/name,raw)
    archive=None
    if with_tar:
        target=out/'evidence.tar.gz';seen=set()
        with target.open('xb') as destination:
            with gzip.GzipFile(fileobj=destination,filename='',mode='wb',mtime=0) as compressed:
                with tarfile.open(fileobj=compressed,mode='w|',format=tarfile.PAX_FORMAT) as tar:
                    def member(name,raw):
                        need(name not in seen,'duplicate evidence archive member');seen.add(name)
                        info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;info.mtime=0;info.uid=info.gid=0;info.uname=info.gname=''
                        tar.addfile(info,io.BytesIO(raw))
                    for name,raw in sorted(files.items()):member('report/'+name,raw)
                    for item in evidence:
                        if not item['included_in_optional_archive'] or item['archive_name'] in seen:continue
                        raw=read(item['path']);need(pin(raw)=={k:item[k] for k in ('bytes','sha256')},'source evidence changed before archive')
                        member(item['archive_name'],raw)
            destination.flush();os.fsync(destination.fileno())
        target.chmod(0o644);archive=pin(read(target,limit=2*1024*1024*1024))
        write_new(out/'evidence.tar.gz.sha256',(archive['sha256']+'  evidence.tar.gz\n').encode('ascii'))
    sums=[]
    for path in sorted(out.iterdir()):sums.append(pin(read(path,limit=2*1024*1024*1024))['sha256']+'  '+path.name+'\n')
    write_new(out/'SHA256SUMS',''.join(sums).encode('ascii'))
    return {'status':summary['status'],'export_dir':str(out),'images':500,'summary':str(out/'summary.json'),
            'archive':str(out/'evidence.tar.gz') if archive else None,'archive_sha256':archive['sha256'] if archive else None,
            'archive_checksum':str(out/'evidence.tar.gz.sha256') if archive else None,'checksums':str(out/'SHA256SUMS'),
            'hardware_accessed':False,'original_records_modified':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--evidence-tar',action='store_true');args=parser.parse_args(argv)
    try:
        need(os.name=='posix' and os.geteuid()==0,'review private source records as Linux root')
        with checked_modules() as (review,index):rows,report,evidence,provenance=collect(review,index)
        result=export(args.out,rows,report,evidence,provenance,args.evidence_tar)
        print(json.dumps(result,indent=2,allow_nan=False));return 0
    except (OSError,ValueError,KeyError,TypeError,ImportError) as error:
        print('SUBSET500_REPORT_REFUSED_NO_HARDWARE_ACCESS: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
