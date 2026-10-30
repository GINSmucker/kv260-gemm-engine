"""Labeled Dense W8A8 CPU4 baseline, isolated from any PL execution.

Use the externally supplied fixed Dense model; never copy it into chunk tools.
One ORT session, original CSV order, all 1000 classes, exclusive durable output.
Existing global companion lock remains held until the CPU session is closed.
This script never creates an owner, opens XRT/PL CSR, sends START, resumes or
overwrites output. A subset is not a complete 1800-image accuracy result.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
DENSE_PIN = {'bytes':19628633,'sha256':'f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'}
SCHEMA = 'opt50_dense_cpu4_accuracy_v1'
ENTRY_SCHEMA = 'opt50_dense_cpu4_accuracy_entry_v1'
OPTIONS = {'intra_op_threads':4,'inter_op_threads':1,'sequential':True,'optimization':'OFF','spinning':0,
           'log_severity':3,'main_CPU':0,'worker_affinities':'2;3;4'}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def identity(raw):
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def verification_apis():
    # Release verification happens before importing any released runtime helper.
    import verify_tools
    tools = verify_tools.verify()
    need(tools['files'].get('cpu_dense_baseline.py') == identity(verify_tools.read(HERE/'cpu_dense_baseline.py')),
         'Dense CPU script must be frozen in the audited tools manifest')
    import dataset
    import model_runtime
    import accuracy_metrics
    return SimpleNamespace(dataset=dataset,runtime=model_runtime,metrics=accuracy_metrics,
        tools_pin=identity(verify_tools.read(HERE/'tools_manifest.json')),tools=tools)


def apis():
    value=verification_apis()
    from support_py import resident, durable
    value.resident,value.durable=resident,durable
    return value


def boot_id():
    text=Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    need(re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',text), 'current boot UUID required')
    return text


def new_bytes(durable,path,raw):
    # Exact npy bytes with the audited new_json exclusive/fsync sequence.
    import os
    with durable._parent(path) as (path,descriptor):
        fd=durable._open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,descriptor)
        written=durable._write(fd,raw)
        durable._publish_check(path,descriptor,durable._identity(written))
        durable.fsync_dir(path.parent)


def historical_cpu1(a):
    name='data/B_cpu1_summary.json'
    if name not in a.tools['files']:
        return {'status':'NOT_INCLUDED_IN_RELEASE','used_for_current_counts':False}
    raw=a.dataset.read(HERE/name,128*1024)
    need(identity(raw)==a.tools['files'][name],'historical B summary changed')
    value=a.dataset.decode(raw)
    need(value.get('images')==1800 and value.get('model_sha256',{}).get('dense_w8a8')==DENSE_PIN['sha256'],
         'historical Dense CPU1 provenance differs')
    return {'status':'HISTORICAL_B_CPU1_ONLY','used_for_current_counts':False,'model_sha256':DENSE_PIN['sha256'],
            'metrics':value['metrics']['dense_w8a8'],'note':'Not the current board CPU4 baseline; equality is not assumed'}


def validate_range(start,count,total):
    need(type(start) is int and type(count) is int and 0 <= start < total and 1 <= count <= total-start,
         'nonempty bounded range within frozen manifest required')


def run(model,dataset_root,out,start=0,count=1800):
    a=apis();a.resident.root_linux()
    model,dataset_root,out=map(lambda p:Path(p).absolute(),(model,dataset_root,out))
    a.dataset.ordinary(out.parent)
    need(not out.exists() and not out.is_symlink(),'NEW output required; partial/existing results are never resumed or overwritten')
    session=None
    with a.resident.companion_locked():
        current_boot=boot_id()
        model_raw=a.dataset.read(model,64*1024*1024)
        need(identity(model_raw)==DENSE_PIN,'external model must be the exact Dense W8A8 f2f20336 baseline')
        checked=a.dataset.verify_dataset(dataset_root,verify_payloads=True)
        manifest=checked['manifest'];samples=manifest['samples']
        validate_range(start,count,len(samples))
        need(len(samples)==1800,'complete frozen1800 dataset required even for a selected CPU subset')
        selected=samples[start:start+count]
        np,ort=a.runtime.dependencies()
        # Also verify the Pillow contract before allocating an inference session.
        a.dataset.dependencies()
        old_B=historical_cpu1(a)
        a.durable.mkdir_new(out);a.durable.mkdir_new(out/'entries');a.durable.mkdir_new(out/'logits')
        a.durable.new_json(out/'run.json',{'schema':SCHEMA,'status':'CPU_ONLY_PREPARED_NOT_COMPLETE',
            'kind':'DENSE_W8A8_CPU4','model':DENSE_PIN,'model_external_path':str(model),
            'dataset_root':str(dataset_root),'dataset_manifest':checked['manifest_pin'],'source_tools':a.tools_pin,
            'boot_id':current_boot,'ordinal_start':start,'ordinal_stop_exclusive':start+count,
            'ORT_options':OPTIONS,'issued_PL_START_commands':0,'CPU_only':True,
            'no_XRT':True,'no_PL_CSR':True,'no_START':True})
        run_pin=identity(a.dataset.read(out/'run.json'))
        entries=[];correct1=correct5=0;aggregate=hashlib.sha256();latency_ns=0
        try:
            options=a.runtime.session_options(ort,bind_affinity=True)
            setup_started=time.perf_counter_ns()
            session=ort.InferenceSession(model_raw,options,providers=['CPUExecutionProvider'])
            setup_ns=time.perf_counter_ns()-setup_started
            inputs=session.get_inputs();outputs=session.get_outputs()
            need(len(inputs)==1 and inputs[0].name=='image' and len(outputs)==1,'fixed single-image Dense graph interface required')
            for offset,sample in enumerate(selected):
                need(boot_id()==current_boot,'boot identity changed during CPU baseline')
                # Re-read exact JPEG, decoded RGB, NPY file SHA and raw input SHA
                # immediately before this image. Never reuse an arbitrary npy.
                npy=a.dataset.materialize_input(dataset_root,sample)
                need(identity(npy)=={k:sample['input'][k] for k in ('bytes','sha256')},'materialized NPY file pin differs')
                image=np.load(io.BytesIO(npy),allow_pickle=False)
                need(image.dtype==np.dtype('float32') and image.shape==(1,3,224,224) and
                     image.flags.c_contiguous and bool(np.isfinite(image).all()),'finite C-contiguous FP32 image required')
                raw_input=image.tobytes(order='C')
                need(identity(raw_input)['sha256']==sample['input']['raw_sha256'],'raw FP32 input pin differs')
                aggregate.update(raw_input)
                began=time.perf_counter_ns()
                values=session.run(None,{'image':image})
                elapsed=time.perf_counter_ns()-began
                need(type(values) is list and len(values)==1,'one Dense logits result required')
                logits=a.metrics.checked_logits(values[0])
                order=np.argsort(-logits[0],kind='stable');top5=[int(x) for x in order[:5]]
                label=sample['label'];need(type(label) is int and 0 <= label < 1000,'original1000-class label required')
                rank=1+int(np.count_nonzero(logits[0]>logits[0,label]))+int(np.count_nonzero(
                    (logits[0]==logits[0,label]) & (np.arange(1000)<label)))
                need((top5[0]==label)==(rank==1) and (label in top5)==(rank<=5),'independent stable-rank check failed')
                top1,top5_hit=rank==1,rank<=5;correct1+=top1;correct5+=top5_hit;latency_ns+=elapsed
                stream=io.BytesIO();np.save(stream,logits,allow_pickle=False);saved=stream.getvalue()
                stem=f'{sample["ordinal"]:04d}_{sample["id"]}'
                need(re.fullmatch(r'[0-9]{4}_val_[0-9]{7}',stem),'frozen safe sample filename required')
                logits_name='logits/'+stem+'.npy';entry_name='entries/'+stem+'.json'
                new_bytes(a.durable,out/logits_name,saved)
                row={'schema':ENTRY_SCHEMA,'ordinal':sample['ordinal'],'id':sample['id'],'label':label,
                    'model_sha256':DENSE_PIN['sha256'],'dataset_manifest':checked['manifest_pin'],
                    'image_sha256':sample['image']['sha256'],'input_sha256':sample['input']['sha256'],
                    'CPU_logits_sha256':identity(logits.tobytes(order='C'))['sha256'],
                    'CPU_logits_file':dict(identity(saved),file=logits_name),'CPU_top5':top5,
                    'CPU_true_label_rank':rank,'CPU_top1_hit':top1,'CPU_top5_hit':top5_hit,
                    'CPU_reference_ns':elapsed,'CPU_reference_ms':elapsed/1e6,'first_session_call':offset==0,
                    'CPU_only':True,'issued_PL_START_commands':0}
                a.durable.new_json(out/entry_name,row)
                entries.append({'ordinal':sample['ordinal'],'id':sample['id'],'entry':dict(identity(a.dataset.read(out/entry_name)),file=entry_name)})
                if (offset+1)%100==0:
                    print(json.dumps({'Dense_CPU4_completed':offset+1,'selected':count,'issued_PL_START_commands':0}),flush=True)
        finally:
            # Release the ORT pool before relinquishing the common PL/CPU lock.
            session=None;gc.collect()
        full=start==0 and count==1800 and len(entries)==1800
        if full:need(aggregate.hexdigest()==a.dataset.INPUTS_SHA,'full1800 actual preprocessing aggregate differs from B')
        after=a.dataset.verify_dataset(dataset_root,verify_payloads=True)
        need(after['manifest_pin']==checked['manifest_pin'] and boot_id()==current_boot,'dataset/boot changed during CPU pass')
        result={'schema':SCHEMA,'kind':'DENSE_W8A8_CPU4',
            'status':'CPU_DENSE_FULL_1800_COMPLETE_NOT_PL_TEST' if full else 'CPU_DENSE_SUBSET_COMPLETE_NOT_FULL_ACCEPTANCE',
            'model':DENSE_PIN,'source_model_sha256':DENSE_PIN['sha256'],'model_external_path':str(model),
            'dataset_manifest':checked['manifest_pin'],'source_tools':a.tools_pin,'boot_id':current_boot,
            'run_metadata':run_pin,
            'full_1800_complete':full,'ordinal_start':start,'ordinal_stop_exclusive':start+count,'images':len(entries),
            'classes_ranked':1000,'label_classes':len({s['label'] for s in selected}),
            'accuracy':{'images':count,'top1_correct':int(correct1),'top5_correct':int(correct5),
                        'top1_pct':100*correct1/count,'top5_pct':100*correct5/count},
            'ORT_options':OPTIONS,'CPU_session_closed':True,'companion_lock_held_entire_CPU_run':True,
            'CPU_only':True,'no_XRT':True,'no_PL_CSR':True,'no_START':True,'issued_PL_START_commands':0,
            'hardware_numerical_parity_verified':False,'B_CPU1_equivalence':'NOT_CLAIMED','B_CPU1_historical':old_B,
            'session_setup_ns_excluded':setup_ns,'CPU_reference_ns_sum':latency_ns,
            'selected_inputs_raw_sha256':aggregate.hexdigest(),'entries':entries,
            'timing_scope':'one persistent CPU ORT session; per-image Session.run only, first call included; not a PSPL or steady-state benchmark',
            'logits_sha256_scope':'CPU_logits_sha256 = 4000 raw FP32 bytes; CPU_logits_file = complete NPY file',
            'performance_verified':False}
        filename='summary.json' if full else 'subset_summary.json'
        a.durable.new_json(out/filename,result)
        return {'status':result['status'],'summary':str(out/filename),'summary_pin':identity(a.dataset.read(out/filename)),
                'images':count,'full_1800_complete':full,'accuracy':result['accuracy'],'issued_PL_START_commands':0}


def read_baseline(root,expected_manifest,expected_samples):
    """Read-only full1800 review; recompute every saved CPU logits rank and SHA.

    expected_manifest is the exact prepared dataset manifest {bytes,sha256}
    pin. expected_samples is its full ordered1800 samples, not a selected slice.
    No lock, CPU inference, resident import, XRT or file write occurs here.
    """
    a=verification_apis();root=Path(root).absolute();a.dataset.ordinary(root)
    need(type(expected_manifest) is dict and set(expected_manifest)=={'bytes','sha256'},'exact dataset manifest pin required')
    need(type(expected_samples) is list and len(expected_samples)==1800,'full ordered1800 expected samples required')
    raw=a.dataset.read(root/'summary.json',4*1024*1024);value=a.dataset.decode(raw)
    source = 'current'
    if value.get('source_tools') != a.tools_pin:
        import release_compat
        source = release_compat.dense_source(value.get('source_tools'),
            {'manifest': a.tools_pin, 'files': a.tools['files']}, folder=HERE)
    need(type(value) is dict and value.get('schema')==SCHEMA and value.get('kind')=='DENSE_W8A8_CPU4' and
         value.get('status')=='CPU_DENSE_FULL_1800_COMPLETE_NOT_PL_TEST' and value.get('full_1800_complete') is True and
         value.get('ordinal_start')==0 and value.get('ordinal_stop_exclusive')==value.get('images')==1800 and
         value.get('classes_ranked')==1000 and value.get('model')==DENSE_PIN and
         value.get('source_model_sha256')==DENSE_PIN['sha256'] and value.get('dataset_manifest')==expected_manifest and
         value.get('ORT_options')==OPTIONS and value.get('CPU_session_closed') is True and
         value.get('CPU_only') is True and value.get('issued_PL_START_commands')==0 and
         all(value.get(k) is True for k in ['no_XRT','no_PL_CSR','no_START','companion_lock_held_entire_CPU_run']) and
         value.get('hardware_numerical_parity_verified') is False and
         value.get('B_CPU1_equivalence')=='NOT_CLAIMED','complete current Dense CPU4 identity/scope differs')
    run_raw=a.dataset.read(root/'run.json',65536);run=a.dataset.decode(run_raw)
    need(identity(run_raw)==value.get('run_metadata') and run.get('status')=='CPU_ONLY_PREPARED_NOT_COMPLETE' and
         all(run.get(k)==value.get(k) for k in
        ['schema','kind','model','model_external_path','dataset_manifest','source_tools','boot_id','ORT_options',
         'ordinal_start','ordinal_stop_exclusive','CPU_only','issued_PL_START_commands']), 'CPU run metadata differs')
    need(value.get('selected_inputs_raw_sha256')==a.dataset.INPUTS_SHA,'full input aggregate differs')
    entries=value.get('entries');need(type(entries) is list and len(entries)==1800,'1800 indexed CPU records required')
    np,_=a.runtime.dependencies();c1=c5=elapsed=0;files={'run.json','summary.json'}
    for ordinal,(indexed,sample) in enumerate(zip(entries,expected_samples)):
        need(type(indexed) is dict and indexed.get('ordinal')==sample.get('ordinal')==ordinal and
             indexed.get('id')==sample.get('id'),'CPU index/order differs from selected CSV')
        stem=f'{ordinal:04d}_{sample["id"]}'
        need(re.fullmatch(r'[0-9]{4}_val_[0-9]{7}',stem),'unsafe saved sample name')
        name='entries/'+stem+'.json';wanted=dict(indexed.get('entry',{}));file=wanted.pop('file',None)
        need(file==name,'CPU entry path differs')
        row_raw=a.dataset.read(root/name,65536);need(identity(row_raw)==wanted,'CPU entry file pin differs')
        row=a.dataset.decode(row_raw)
        need(row.get('schema')==ENTRY_SCHEMA and row.get('ordinal')==ordinal and row.get('id')==sample['id'] and
             row.get('label')==sample['label'] and row.get('image_sha256')==sample['image']['sha256'] and
             row.get('input_sha256')==sample['input']['sha256'] and row.get('model_sha256')==DENSE_PIN['sha256'] and
             row.get('dataset_manifest')==expected_manifest and row.get('CPU_only') is True and
             row.get('issued_PL_START_commands')==0 and row.get('first_session_call') is (ordinal==0),
             'CPU record/model/image/input/scope differs')
        logits_name='logits/'+stem+'.npy';npypin=dict(row.get('CPU_logits_file',{}));file=npypin.pop('file',None)
        need(file==logits_name,'CPU saved logits path differs')
        npy=a.dataset.read(root/logits_name,16384);need(identity(npy)==npypin,'CPU logits NPY file pin differs')
        logits=a.metrics.checked_logits(np.load(io.BytesIO(npy),allow_pickle=False))
        need(identity(logits.tobytes(order='C'))['sha256']==row.get('CPU_logits_sha256'),'CPU raw logits SHA differs')
        top=[int(x) for x in np.argsort(-logits[0],kind='stable')[:5]];label=sample['label']
        rank=1+int(np.count_nonzero(logits[0]>logits[0,label]))+int(np.count_nonzero((logits[0]==logits[0,label]) & (np.arange(1000)<label)))
        need(top==row.get('CPU_top5') and rank==row.get('CPU_true_label_rank') and
             row.get('CPU_top1_hit') is (rank==1) and row.get('CPU_top5_hit') is (rank<=5),'CPU saved Top1/5 differs from actual logits')
        ns=row.get('CPU_reference_ns');need(type(ns) is int and ns>=0 and row.get('CPU_reference_ms')==ns/1e6,'CPU timing units differ')
        elapsed+=ns;c1+=rank==1;c5+=rank<=5;files.update((name,logits_name))
    accuracy={'images':1800,'top1_correct':int(c1),'top5_correct':int(c5),'top1_pct':100*c1/1800,'top5_pct':100*c5/1800}
    need(value.get('accuracy')==accuracy and value.get('CPU_reference_ns_sum')==elapsed,'CPU summary disagrees with saved logits/records')
    actual=set()
    for path in root.rglob('*'):
        a.dataset.ordinary(path)
        if path.is_file():actual.add(path.relative_to(root).as_posix())
        else:need(path.is_dir(),'special output member')
    need(actual==files,'extra/missing Dense output file')
    need(a.dataset.read(root/'summary.json',4*1024*1024)==raw,'Dense summary changed during review')
    return {k:value[k] for k in ['schema','kind','status','source_model_sha256','dataset_manifest','full_1800_complete',
        'images','classes_ranked','accuracy','ORT_options','boot_id','CPU_only','CPU_session_closed','issued_PL_START_commands',
        'B_CPU1_equivalence','B_CPU1_historical']} | {'summary_pin':identity(raw),'all_saved_CPU_logits_recomputed':True,
        'verified_entries':1800,'hardware_numerical_parity_verified':False,
        'source_release_review':source,'source_tools':value['source_tools']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--count',type=int,default=1800)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(run(args.model,args.dataset,args.out,args.start,args.count),indent=2,allow_nan=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError,ImportError) as error:
        print('DENSE_CPU4_BASELINE_REFUSED_PRESERVE_PARTIAL_OUTPUT: '+str(error),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
