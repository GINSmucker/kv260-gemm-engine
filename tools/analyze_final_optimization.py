"""Independently check fresh fixed500 outputs and paired195 receipts; no hardware access."""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import statistics
import tarfile
import ast

ROOT=Path(__file__).resolve().parents[1]
MODEL='ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
DATASET={'bytes':807589,'sha256':'2fd9915aa9425563869467aa0c1cf9e3aba62bd933c07cf2fdb14b4b4b84e796'}
CAMPAIGNS=('latency_campaign','latency_campaign_run_scoped','latency_campaign_dynamic','latency_campaign_three_arm')
QUALITY_CAMPAIGNS=('quality_campaign','quality_campaign_run_scoped','quality_campaign_dynamic')


def need(ok,message):
    if not ok:raise ValueError(message)


def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def distribution(values):
    values=sorted(values);position=.95*(len(values)-1);low=int(position)
    return {'n':len(values),'mean_ms':statistics.mean(values),'median_ms':statistics.median(values),
            'P95_ms':values[low]+(values[min(low+1,len(values)-1)]-values[low])*(position-low),'max_ms':max(values)}


def load(name):
    path=ROOT/'board/runtime/snapshots/sparse24'/(name+'.py')
    spec=importlib.util.spec_from_file_location('final_independent_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def input_payload(raw):
    need(raw[:6]==b'\x93NUMPY' and raw[6:8] in (b'\x01\x00',b'\x02\x00'),'fixed input NPY')
    width=2 if raw[6]==1 else 4;start=8+width;size=int.from_bytes(raw[8:start],'little')
    need(0<size<=1024 and start+size+602112==len(raw),'bounded input header/payload')
    header=ast.literal_eval(raw[start:start+size].decode('ascii').strip())
    need(header=={'descr':'<f4','fortran_order':False,'shape':(1,3,224,224)},'fixed FP32 input layout')
    return raw[start+size:]


def analyze(archive_path,campaign='latency_campaign',quality_campaign='quality_campaign',selected_arm=None):
    proof,dataset=load('resident_proof'),load('dataset')
    files={}
    with tarfile.open(archive_path,'r:gz') as archive:
        for member in archive:
            if member.isdir():continue
            need(member.isfile() and not member.issym() and not member.islnk() and member.size<16*1024*1024,'bounded regular evidence member')
            name=member.name.removeprefix('./')
            need(name not in files and '..' not in Path(name).parts and not name.startswith('/'),'unique relative evidence paths')
            files[name]=archive.extractfile(member).read()
    def read(name):
        need(name in files,'missing fresh evidence: '+name);return files[name]
    def decode(name):return json.loads(read(name))
    need(pin(read('dataset_manifest.json'))==DATASET,'fixed dataset identity')
    manifest=dataset.validate_manifest(read('dataset_manifest.json'))
    builds={}
    for variant in ('baseline','diagnostic','openssl-info'):
        receipt=decode('builds/'+variant+'/build_receipt.json')
        candidate=read('candidates/'+variant+'/candidate_manifest.json')
        need(receipt['status']=='PASS' and receipt['candidate_manifest']==pin(candidate),'native build/candidate identity')
        for name,wanted in receipt['binaries'].items():
            raw=read('builds/'+variant+'/'+name)
            need(pin(raw)==wanted and raw[:7]==b'\x7fELF\x02\x01\x01' and int.from_bytes(raw[18:20],'little')==183,'actual AArch64 binary identity')
        for name,wanted in json.loads(candidate)['files'].items():
            if name.startswith('source/'):
                need(pin(read('candidates/'+variant+'/'+name))==wanted,'compiled candidate source bytes')
        builds[variant]=receipt
    need(quality_campaign in QUALITY_CAMPAIGNS,'known quality campaign')
    quality=decode('work/'+quality_campaign+'/progress.json')
    closed=decode('work/'+quality_campaign+'/closed.json')
    need(closed['status']=='PASS' and closed['images']==500 and closed['preprocessing_equivalent_images']==500,'complete fixed500 required')
    need([r['ordinal'] for r in quality['results']]==list(range(500)),'exact first500, no duplicate or replacement image')
    checked_windows={}
    def check(row,count):
        token=Path(row['window']).name;base='plans/'+token
        plan=decode(base+'/plan.json');run=row['run_id']
        need(plan['source_model_sha256']==MODEL and plan['sample_count']==count and row['boot_id']==plan['boot_id'],'new plan/model/boot scope')
        need(plan['variant']==row['variant'] and plan['ORT_intra_spinning']==row['ORT_intra_spinning']
             and plan['ort_profile'] is False and row['ort_profile'] is False,'actual unprofiled runtime options')
        need(plan.get('ORT_force_spinning_stop',0)==row.get('ORT_force_spinning_stop',0),'plan/worker Run-scoped spinning identity')
        need(plan.get('ORT_dynamic_block_base',0)==row.get('ORT_dynamic_block_base',0),'plan/worker dynamic-block identity')
        binaries=builds[plan['variant']]['binaries']
        need(plan['files']['native_owner']==binaries['opt50_model_owner'] and
             plan['files']['libopt50_model.so']==binaries['libopt50_model.so'],'executed binary pair')
        proof.performance(decode(base+'/execution/performance_native_%06d.json'%run),plan,run)
        if token not in checked_windows:
            events=[decode(name) for name in sorted(files) if name.startswith(base+'/execution/native_events/event_') and name.endswith('.json')]
            checked=proof.owner_close(events,count)
            need(decode(base+'/closed.json')==checked,'real owner close receipt')
            checked_windows[token]=checked
        cpu,cv=proof.npy_logits(read(base+'/execution/%06d_cpu.npy'%run))
        pl,pv=proof.npy_logits(read(base+'/execution/%06d_pl.npy'%run))
        need(cpu==pl,'new1000 FP32 raw output mismatch')
        need(pin(cpu)['sha256']==row['CPU_logits_sha256']==row['PL_logits_sha256'],'raw output identity')
        original=manifest['samples'][row['ordinal']]
        need((row['id'],row['input_sha256'])==(original['id'],original['input']['raw_sha256']),'fixed image/tensor identity')
        return pv
    top1=top5=0
    for row in quality['results']:
        need(row['variant']=='openssl-info' and row['ORT_intra_spinning']==1,'quality candidate profile')
        need(row.get('ORT_force_spinning_stop',0)==int(quality_campaign!='quality_campaign'),'quality Run-scoped spinning profile')
        need(row.get('ORT_dynamic_block_base',0)==(4 if quality_campaign.endswith('_dynamic') else 0),'quality dynamic-block profile')
        sample=manifest['samples'][row['ordinal']]
        image=read('dataset/'+sample['image']['file'])
        tensor=read('inputs/%04d.npy'%row['ordinal'])
        need(pin(image)=={k:sample['image'][k] for k in ('bytes','sha256')},'actual original JPEG bytes')
        need(pin(tensor)=={k:sample['input'][k] for k in ('bytes','sha256')} and
             pin(input_payload(tensor))['sha256']==sample['input']['raw_sha256'],'actual reconstructed input bytes')
        values=check(row,50);label=manifest['samples'][row['ordinal']]['label']
        need(row['label']==label,'1000-class original label ID')
        order=sorted(range(1000),key=lambda i:(-values[i],i))[:5]
        top1+=int(order[0]==label);top5+=int(label in order)
    need((top1,top5)==(357,461),'same-model fixed500 quality differs; do not replace the original reference')
    need(campaign in CAMPAIGNS,'known finite campaign')
    paired=decode('work/'+campaign+'/progress.json')
    pclosed=decode('work/'+campaign+'/closed.json')
    three=campaign.endswith('_three_arm');graph_count=585 if three else 390
    need(pclosed['status']=='PASS' and pclosed['images_per_arm']==195 and pclosed['graphs']==graph_count,'complete paired195 required')
    by_image={};arms={k:[] for k in (('original','scoped','dynamic') if three else ('original','candidate'))}
    for row in paired['results']:
        need((row['variant'],row['ORT_intra_spinning'])==(('baseline',0) if row['arm']=='original' else ('openssl-info',1)),'comparison arm profile')
        need(row.get('ORT_force_spinning_stop',0)==int(row['arm']!='original' and campaign!='latency_campaign'),'Run-scoped spinning profile')
        need(row.get('ORT_dynamic_block_base',0)==(4 if row['arm']=='dynamic' or row['arm']=='candidate' and campaign.endswith('_dynamic') else 0),'dynamic-block profile')
        check(row,39);need(row['ordinal'] in range(305,500) and row['arm'] in arms,'paired195 selection')
        key=row['ordinal'];need(row['arm'] not in by_image.setdefault(key,{}),'duplicate arm/image')
        by_image[key][row['arm']]=row;arms[row['arm']].append(row)
    need(set(by_image)==set(range(305,500)) and all(set(p)==set(arms) for p in by_image.values()),'exact195 matched images')
    need(len({r['boot_id'] for r in paired['results']})==1,'paired comparison same boot')
    need(all(len({row['PL_logits_sha256'] for row in p.values()})==1 for p in by_image.values()),'cross-arm raw output mismatch')
    metrics=['Runner_run_ms','CPU_reference_ms','INFO_ms']
    metrics.extend(key for key in ('Runner_main_CPU_ms','Runner_process_CPU_ms') if all(key in row for rows in arms.values() for row in rows))
    summary={arm:{key:distribution([r[key] for r in rows]) for key in metrics} for arm,rows in arms.items()}
    candidate_arm=selected_arm or ('dynamic' if three else 'candidate')
    need(candidate_arm in arms and candidate_arm!='original','actual candidate arm')
    old,new=summary['original']['Runner_run_ms'],summary[candidate_arm]['Runner_run_ms']
    gain=old['median_ms']-new['median_ms'];relative=100*gain/old['median_ms']
    groups=[]
    for group in range(5):
        stats={arm:distribution([r['Runner_run_ms'] for r in rows if r['group']==group]) for arm,rows in arms.items()}
        order=list(dict.fromkeys(row['arm'] for row in paired['results'] if row['group']==group))
        groups.append({'group':group,'order':order,
                       'Runner_run_ms':stats,'median_gain_ms':stats['original']['median_ms']-stats[candidate_arm]['median_ms']})
    result={'schema':'opt50_final_independent_review_v1','status':'PASS','archive':pin(Path(archive_path).read_bytes()),
            'quality':{'campaign':quality_campaign,'images':500,'classes':len({r['label'] for r in quality['results']}),'FP32_logits_compared':500000,
                       'bitwise_mismatches':0,'Top1_correct':top1,'Top5_correct':top5,'parallel_preprocessing_equivalent_images':500},
            'paired195':{'images_per_arm':195,'graphs':graph_count,'FP32_logits_compared':graph_count*1000,'bitwise_mismatches':0,
                         'campaign':campaign,
                         'selected_candidate_arm':candidate_arm,
                         'groups':groups,
                         'same_boot':True,'source_model_sha256':MODEL,'statistics':summary,
                         'median_Runner_gain_ms':gain,'median_Runner_reduction_percent':relative,
                         'threshold_2ms_and_3percent':gain>2 and relative>3,
                         'paired_gain':distribution([p['original']['Runner_run_ms']-p[candidate_arm]['Runner_run_ms'] for p in by_image.values()]),
                         'scope':'Each image measured once per arm in balanced39-image owner groups; not long-term tail guarantees'},
            'closed_owner_windows_verified':len(checked_windows),'hardware_accessed_by_this_review':False,
            'physical_PL_starts':'NOT INDEPENDENTLY MEASURED'}
    csv_rows=[{'image_1based':i+1,'id':p['original']['id'],'input_raw_sha256':p['original']['input_sha256'],
               'original_Runner_ms':p['original']['Runner_run_ms'],'candidate_Runner_ms':p[candidate_arm]['Runner_run_ms'],
               'original_CPU_ms':p['original']['CPU_reference_ms'],'candidate_CPU_ms':p[candidate_arm]['CPU_reference_ms'],
               'original_INFO_ms':p['original']['INFO_ms'],'candidate_INFO_ms':p[candidate_arm]['INFO_ms'],
               **({'scoped_Runner_ms':p['scoped']['Runner_run_ms'],'dynamic_Runner_ms':p['dynamic']['Runner_run_ms']} if three else {})}
              for i,p in sorted(by_image.items())]
    return result,csv_rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--campaign',choices=CAMPAIGNS,default='latency_campaign')
    parser.add_argument('--candidate-arm',choices=['candidate','scoped','dynamic'])
    parser.add_argument('--quality-campaign',choices=QUALITY_CAMPAIGNS,default='quality_campaign');args=parser.parse_args()
    need(not args.out.exists(),'NEW review directory required')
    result,rows=analyze(args.evidence,args.campaign,args.quality_campaign,args.candidate_arm);args.out.mkdir(parents=True)
    (args.out/'review.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
    with (args.out/'paired195.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    print(json.dumps(result,indent=2))
