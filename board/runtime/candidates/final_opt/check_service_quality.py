"""Verify the installed pair's fixed195 HTTP requests and real session closes."""
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,'/opt/opt50-demo-v1/private')
import backend
import resident_proof
from common import create,encoded,need,pin,MODEL_SHA

WORK=Path('/var/lib/opt50-final-20261101')
STATE=Path('/var/lib/opt50-demo-v1')
APP=Path('/opt/opt50-demo-v1/private')


def main():
    root=WORK/'service_quality';root.mkdir(mode=0o700,exist_ok=False)
    receipt=backend.verify_install();request_map=Path(sys.argv[1]);request_raw=request_map.read_bytes();client=json.loads(request_raw)
    policy={'ORT_intra_spinning':1,'ORT_force_spinning_stop':1,
            'ORT_dynamic_block_base':receipt['final_optimization_trial']['ORT_dynamic_block_base']}
    need(client['status']=='PASS' and client['images']==195 and len(client['rows'])==195,'complete client195')
    manifest_raw=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_ready_20261028_v1/dataset/manifest.json').read_bytes()
    expected={'bytes':807589,'sha256':'2fd9915aa9425563869467aa0c1cf9e3aba62bd933c07cf2fdb14b4b4b84e796'}
    need(pin(manifest_raw)==expected==client['dataset_manifest'],'fixed dataset identity')
    samples=json.loads(manifest_raw)['samples'][305:500]
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();need(client['boot_id']==boot,'actual current boot')
    need([r['ordinal'] for r in client['rows']]==list(range(305,500)) and len({r['request_id'] for r in client['rows']})==195,'unique once-per-image requests')
    sessions={};top1=top5=0;rows=[]
    for item,sample in zip(client['rows'],samples):
        token=item['request_id'];need(re.fullmatch('[0-9a-f]{32}',token),'request ID')
        folder=STATE/'requests'/token;result=json.loads((folder/'result.json').read_bytes())
        need((item['id'],item['label'])==(sample['id'],sample['label']),'fixed1000-class labels')
        need(result['stage']=='PASS' and result['boot_id']==boot and result['source_model_sha256']==MODEL_SHA,'actual service result')
        need(pin((folder/'image.jpg').read_bytes())=={k:sample['image'][k] for k in ('bytes','sha256')},'actual uploaded JPEG bytes')
        need(pin((folder/'input.npy').read_bytes())=={k:sample['input'][k] for k in ('bytes','sha256')}
             and result['input_sha256']==sample['input']['sha256'] and result['decoded_RGB_sha256']==sample['image']['decoded_rgb_sha256'],'actual preprocessing/input tensor')
        cpu,_=resident_proof.npy_logits((folder/'cpu_logits.npy').read_bytes())
        actual,values=resident_proof.npy_logits((folder/'pl_logits.npy').read_bytes())
        need(cpu==actual and hashlib.sha256(cpu).hexdigest()==result['CPU_logits_sha256']==result['PL_logits_sha256'],'all1000 actual FP32 output bits')
        session=STATE/('session_'+result['session']);plan=json.loads((session/'plan.json').read_bytes());run=result['run_id']
        need(all(result.get(k)==v and plan.get(k)==v for k,v in policy.items())
             and result['measurement_profile']==plan['measurement_profile'],'actual service policy identity')
        native_path=session/'execution'/('performance_native_%06d.json'%run)
        need(pin(native_path.read_bytes())['sha256']==result['native_proof_sha256']
             and resident_proof.performance(json.loads(native_path.read_bytes()),plan,run)==result['native'],'per-graph completion/INFO')
        if session.name not in sessions:
            need(pin((session/'native_owner').read_bytes())==receipt['files']['build/opt50_model_owner'],'actual installed owner binary')
            closed=json.loads((session/'closed.json').read_bytes())
            events=[json.loads(p.read_bytes()) for p in sorted((session/'execution/native_events').glob('event_*.json'))]
            need(resident_proof.owner_close(events,closed['completed_graphs'])==closed,'real owner/resource close')
            need((session/'execution/owner.stderr').read_bytes()==b'','empty owner stderr')
            sessions[session.name]=closed
        order=sorted(range(1000),key=lambda i:(-values[i],i))[:5]
        top1+=int(order[0]==sample['label']);top5+=int(sample['label'] in order)
        rows.append({'ordinal':item['ordinal'],'request_id':token,'session':session.name,'run_id':run,
                     'PL_logits_sha256':result['PL_logits_sha256'],'bitwise_mismatches':0})
    need(backend.verify_install()==receipt,'installation changed during quality review')
    create(root/'client_mapping.json',request_raw)
    create(root/'closed.json',encoded({'status':'PASS','images':195,'classes':len({s['label'] for s in samples}),'FP32_logits_compared':195000,
        'bitwise_mismatches':0,'preprocessing_equivalent_images':195,'Top1_correct':top1,'Top5_correct':top5,
        'boot_id':boot,'installed_files':receipt['files'],'installed_receipt':pin((APP/'install_receipt.json').read_bytes()),
        'client_mapping':pin(request_raw),'sessions':sessions,'rows':rows,
        'scope':'Actual installed service pair, original JPEGs and raw outputs; no saved historical logits used'}))
    print('ACTUAL_SERVICE_FIXED195_RAW_BITS_AND_CLOSE_PASS',flush=True)


if __name__=='__main__':main()
