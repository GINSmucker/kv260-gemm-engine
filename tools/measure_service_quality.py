"""Submit fixed306..500 original JPEGs once through the current service; quality only."""
import argparse
import json
from pathlib import Path
import tarfile
import time
import uuid
from urllib.error import HTTPError

from analyze_final_optimization import DATASET,need,pin
from latency_diagnostics.measure_requests import fetch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-evidence',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--base',default='http://127.0.0.1:4179/api/sparse24/');args=parser.parse_args()
    args.out.mkdir(exist_ok=False)
    with tarfile.open(args.dataset_evidence,'r:gz') as archive:
        raw=archive.extractfile('dataset_manifest.json').read();need(pin(raw)==DATASET,'fixed500 dataset manifest')
        manifest=json.loads(raw);photos={};samples=manifest['samples'][305:500]
        need(len(samples)==195 and [r['ordinal'] for r in samples]==list(range(305,500)),'exact fixed306..500 order')
        for row in samples:
            image=archive.extractfile('dataset/'+row['image']['file']).read()
            need(pin(image)=={k:row['image'][k] for k in ('bytes','sha256')},'actual original JPEG')
            photos[row['ordinal']]=image
    rows=[];boot=None
    for sample in samples:
        admission_started=time.monotonic()
        while True:
            status=fetch(args.base+'status')['status']
            if boot is None:boot=status['boot_id']
            need(status['boot_id']==boot and status['stage']=='READY','same boot READY; no submission after uncertainty')
            if not status['busy']:break
            need(time.monotonic()-admission_started<30,'service remained busy; no submission')
            time.sleep(.1)
        token=uuid.uuid4().hex;raw=photos[sample['ordinal']]
        intent={'ordinal':sample['ordinal'],'id':sample['id'],'label':sample['label'],'request_id':token,
                'boot_id':boot,'image':pin(raw),'poll_period_ms':100}
        (args.out/(token+'.intent.json')).write_text(json.dumps(intent,indent=2)+'\n',encoding='utf-8')
        began=time.perf_counter_ns()
        fetch(args.base+'infer/'+token,raw,{'Content-Type':'image/jpeg','X-Sparse24-Boot':boot})
        while True:
            try:result=fetch(args.base+'result/'+token)['result']
            except HTTPError as error:
                if error.code not in (409,502,503):raise
                with (args.out/(token+'.query-errors.jsonl')).open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps({'code':error.code,'body':error.read(4096).decode(errors='replace')})+'\n')
                need(time.perf_counter_ns()-began<150_000_000_000,'query unavailable; no request replay')
                time.sleep(.1);continue
            if result['stage']=='PASS':break
            need(result['stage'] in ('SUBMITTED','QUEUED') and time.perf_counter_ns()-began<150_000_000_000,'uncertain request; query original ID only, never resubmit')
            time.sleep(.1)
        (args.out/(token+'.result.json')).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        need(result['boot_id']==boot and result['image_sha256']==sample['image']['sha256']
             and result['input_sha256']==sample['input']['sha256'] and result['decoded_RGB_sha256']==sample['image']['decoded_rgb_sha256']
             and result['bitwise_mismatches']==0 and result['CPU_logits_sha256']==result['PL_logits_sha256'],'fixed original input and reported output check')
        rows.append({**intent,'session':result['session'],'run_id':result['run_id'],
                     'client_complete_ms':(time.perf_counter_ns()-began)/1e6,'result_file':token+'.result.json'})
        (args.out/'progress.json').write_text(json.dumps({'status':'PASS','completed_images':len(rows),'rows':rows},indent=2)+'\n',encoding='utf-8')
        if len(rows)%39==0:print(json.dumps({'completed_service_images':len(rows),'planned_images':195}),flush=True)
    report={'schema':'opt50_final_service_quality_client_v1','status':'PASS','boot_id':boot,'images':195,
            'dataset_manifest':DATASET,'rows':rows,'scope':'New actual installed-service requests; quality only, not the formal195 latency score; raw board outputs require independent verification'}
    (args.out/'client_closed.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
