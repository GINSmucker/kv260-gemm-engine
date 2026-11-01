"""Review the installed service pair's fixed306..500 raw outputs independently."""
import argparse
import json
from pathlib import Path
import tarfile

from analyze_final_optimization import DATASET,MODEL,input_payload,load,need,pin


def analyze(path):
    files={}
    with tarfile.open(path,'r:gz') as archive:
        for member in archive:
            if member.isdir():continue
            need(member.isfile() and member.size<16*1024*1024 and member.name not in files
                 and '..' not in Path(member.name).parts and not member.name.startswith('/'),'unique bounded regular evidence')
            files[member.name]=archive.extractfile(member).read()
    def decoded(name):return json.loads(files[name])
    proof=load('resident_proof');dataset=load('dataset')
    need(pin(files['dataset_manifest.json'])==DATASET,'original fixed dataset')
    manifest=dataset.validate_manifest(files['dataset_manifest.json'])
    closed=decoded('service_quality/closed.json');mapping=decoded('service_quality/client_mapping.json')
    need(closed['status']=='PASS' and closed['images']==195 and len(closed['rows'])==195,'actual closed195')
    need(pin(files['service_quality/client_mapping.json'])==closed['client_mapping'],'complete client mapping')
    receipt=decoded('installed/install_receipt.json');build=decoded('service_build_receipt.json')
    need(receipt['files']==closed['installed_files'] and pin(files['installed/install_receipt.json'])==closed['installed_receipt'],'actual installed identity')
    for name,wanted in receipt['files'].items():need(pin(files['installed/'+name])==wanted,'installed bytes: '+name)
    native_changed=receipt['final_optimization_trial'].get('native_changed',True)
    if native_changed:
        for name,wanted in build['sources'].items():need(pin(files['installed/'+name])==wanted,'compiled source bytes: '+name)
    pairing=decoded('original_service/pairing.json')
    for name,wanted in pairing['original_files'].items():need(pin(files['original_service/'+name])==wanted,'complete original rollback pair')
    need(files['installed/source/native_gate.hpp']==files['original_service/source/native_gate.hpp'],'service gate unchanged')
    need(pin(files['original_service/source/kernel.cpp'])==build['baseline_kernel'],'compiled baseline kernel identity')
    owner=files['installed/build/opt50_model_owner']
    need(pin(owner)==(build['binary'] if native_changed else pairing['original_files']['build/opt50_model_owner'])
         and owner[:7]==b'\x7fELF\x02\x01\x01' and int.from_bytes(owner[18:20],'little')==183,'actual AArch64 owner identity')
    if not native_changed:
        need(files['installed/source/kernel.cpp']==files['original_service/source/kernel.cpp'],'original Native retained for model-only adoption')
    need(pin(files['model_manifest.json'])['sha256']==receipt['model_manifest_sha256']==
         '91ed1cd0a8b55cfde7e1a82403f879f1e225ae4c96cc47272f8dd92304a19f60','actual original model manifest')
    need([r['ordinal'] for r in closed['rows']]==list(range(305,500))
         and [r['request_id'] for r in closed['rows']]==[r['request_id'] for r in mapping['rows']],'once-per-image195 mapping')
    policy={'ORT_intra_spinning':1,'ORT_force_spinning_stop':1,'ORT_dynamic_block_base':receipt['final_optimization_trial']['ORT_dynamic_block_base']}
    sessions={};top1=top5=0
    for row in closed['rows']:
        sample=manifest['samples'][row['ordinal']];base='requests/'+row['request_id']+'/'
        result=decoded(base+'result.json');tensor=files[base+'input.npy'];input_payload(tensor)
        need(pin(files[base+'image.jpg'])=={k:sample['image'][k] for k in ('bytes','sha256')},'actual JPEG bytes')
        need(pin(tensor)=={k:sample['input'][k] for k in ('bytes','sha256')}
             and pin(input_payload(tensor))['sha256']==sample['input']['raw_sha256'],'actual original FP32 tensor')
        cpu,_=proof.npy_logits(files[base+'cpu_logits.npy']);actual,values=proof.npy_logits(files[base+'pl_logits.npy'])
        need(cpu==actual and pin(actual)['sha256']==row['PL_logits_sha256']==result['PL_logits_sha256']==result['CPU_logits_sha256'],'all1000 actual FP32 bits')
        need(result['source_model_sha256']==MODEL and result['boot_id']==closed['boot_id'],'same model/current boot')
        root=row['session'];plan=decoded(root+'/plan.json')
        need(all(plan.get(k)==v and result.get(k)==v for k,v in policy.items()),'actual deployed thread policy')
        need(files[root+'/native_owner']==owner,'executed owner equals installed binary')
        native=root+'/execution/performance_native_%06d.json'%row['run_id']
        need(pin(files[native])['sha256']==result['native_proof_sha256'] and proof.performance(decoded(native),plan,row['run_id'])==result['native'],'actual ordered completion and INFO')
        if root not in sessions:
            close=decoded(root+'/closed.json')
            events=[decoded(n) for n in sorted(files) if n.startswith(root+'/execution/native_events/event_') and n.endswith('.json')]
            need(proof.owner_close(events,close['completed_graphs'])==close,'real owner/resource close')
            need(files[root+'/execution/owner.stderr']==b'','empty owner stderr')
            sessions[root]=close
        order=sorted(range(1000),key=lambda i:(-values[i],i))[:5]
        top1+=int(order[0]==sample['label']);top5+=int(sample['label'] in order)
    need((top1,top5)==(closed['Top1_correct'],closed['Top5_correct']),'independent exact195 ranking')
    need(sessions==closed['sessions'],'all actual service owner closes')
    return {'status':'PASS','archive':pin(Path(path).read_bytes()),'images':195,'classes':len({manifest['samples'][r['ordinal']]['label'] for r in closed['rows']}),
            'FP32_logits_compared':195000,'bitwise_mismatches':0,'Top1_correct':top1,'Top5_correct':top5,
            'preprocessing_equivalent_images':195,'boot_id':closed['boot_id'],'installed_files':receipt['files'],
            'service_quality_receipt':pin(files['service_quality/closed.json']),'closed_service_sessions':len(sessions),
            'policy':policy,'hardware_accessed_by_this_review':False,'physical_PL_starts':'NOT INDEPENDENTLY MEASURED'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    need(not args.out.exists(),'new review destination')
    result=analyze(args.evidence);args.out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({k:v for k,v in result.items() if k!='installed_files'},indent=2))
