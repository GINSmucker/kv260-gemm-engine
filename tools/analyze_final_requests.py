"""Review complete-request reports against downloaded raw tensors, logits and normal closes."""
import argparse
import json
from pathlib import Path
import tarfile

from analyze_final_optimization import distribution,input_payload,load,need,pin


def review(folder):
    report=json.loads((folder/'requests/report.json').read_bytes())
    with tarfile.open(folder/'request-evidence.tar.gz','r:gz') as archive:
        files={m.name:archive.extractfile(m).read() for m in archive if m.isfile()}
    proof=load('resident_proof');sessions={};fingerprints=set()
    def decoded(name):return json.loads(files[name])
    install=decoded('installed/install_receipt.json')
    for name,wanted in install['files'].items():
        need(pin(files['installed/'+name])==wanted,'installed pair identity: '+name)
    for row in report['samples']:
        base='requests/'+row['request_id']+'/'
        result=decoded(base+'result.json')
        need(all(result.get(k)==v for k,v in row.items() if k in result),'downloaded/server result differs')
        tensor=files[base+'input.npy'];input_payload(tensor)
        need(pin(tensor)['sha256']==row['input_sha256'],'actual input identity')
        cpu,_=proof.npy_logits(files[base+'cpu_logits.npy']);pl,_=proof.npy_logits(files[base+'pl_logits.npy'])
        need(cpu==pl and pin(cpu)['sha256']==row['CPU_logits_sha256']==row['PL_logits_sha256'],'all1000 raw FP32 bits')
        fingerprints.add((pin(tensor)['sha256'],pin(pl)['sha256'],row['decoded_RGB_sha256'],row['image_sha256']))
        session='session_'+row['session'];root=session+'/execution/'
        if session not in sessions:
            closed=decoded(session+'/closed.json')
            events=[decoded(n) for n in sorted(files) if n.startswith(root+'native_events/event_') and n.endswith('.json')]
            need(proof.owner_close(events,closed['completed_graphs'])==closed,'actual normal service close')
            need(files[root+'owner.stderr']==b'','empty owner stderr')
            sessions[session]=closed
        plan=decoded(session+'/plan.json')
        need(plan['boot_id']==row['boot_id'] and files[session+'/native_owner']==files['installed/build/opt50_model_owner'],'executed owner/boot pair')
        native_path=root+'performance_native_%06d.json'%row['run_id']
        need(pin(files[native_path])['sha256']==result['native_proof_sha256'],'downloaded per-graph proof identity')
        need(proof.performance(decoded(native_path),plan,row['run_id'])==result['native'],'ordered graph completion/INFO proof')
    need(len(fingerprints)==1 and len(report['samples'])==6,'exact6 same-input requests')
    need(report['samples'][0]['run_id']==1 and [r['run_id'] for r in report['samples']]==list(range(1,7)),'one cold session then5warm')
    startup={'status':'NOT RUN'}
    journal=folder/'service-journal.jsonl'
    if journal.exists():
        stages=[]
        for line in journal.read_text(encoding='utf-8').splitlines():
            try:value=json.loads(json.loads(line)['MESSAGE'])
            except (ValueError,KeyError,TypeError):continue
            if isinstance(value,dict) and value.get('schema')=='opt50_live_demo_status_v1':stages.append(value)
        starts=[v for v in stages if v['stage']=='VERIFYING_FILES'];ready=[v for v in stages if v['stage']=='READY']
        if starts and ready:
            startup={'status':'PASS','service_initialize_through_selftest_ms':1000*(ready[0]['heartbeat_monotonic']-starts[0]['heartbeat_monotonic']),
                     'stages':stages,'scope':'Service VERIFYING_FILES to READY with current-boot selftest; excludes earlier Linux boot and later HTTP request'}
    return {'status':'PASS','folder':str(folder),'archive':pin((folder/'request-evidence.tar.gz').read_bytes()),
            'requests':6,'raw_FP32_logits_compared':6000,'bitwise_mismatches':0,
            'fingerprint':list(fingerprints.pop()),'boot_id':report['samples'][0]['boot_id'],
            'normal_owner_closes':sessions,'cold_session_request':report['samples'][0],
            'warm5':{k:distribution([r[k] for r in report['samples'][1:]]) for k in report['subsequent_samples']},
            'installation_receipt':pin(files['installed/install_receipt.json']),'startup':startup}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--round',type=Path,action='append',required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    reviews=[review(p) for p in args.round]
    need(len({tuple(r['fingerprint']) for r in reviews})==1,'same input/output across all rounds')
    need(not args.out.exists(),'new review destination required')
    args.out.write_text(json.dumps({'status':'PASS','rounds':reviews,'scope':'Separate boots and cold session initialization; no summed stage means or long-term tail claims'},indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'status':'PASS','rounds':len(reviews),'requests':6*len(reviews),'bitwise_mismatches':0}))
