"""One finite 500-image campaign: ten fresh <=50-graph plans, never retrying a dispatched graph."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import argparse

sys.path.insert(0,'/opt/opt50-demo-v1/private')
from common import create,encoded,need,replace,pin
import dataset
sys.path.insert(0,str(Path(__file__).parent))
from parallel_cpu import preprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-scoped-spinning',action='store_true');parser.add_argument('--dynamic-block',action='store_true');args=parser.parse_args()
    need(not args.dynamic_block or args.run_scoped_spinning,'dynamic block requires Run-scoped candidate')
    work=Path('/var/lib/opt50-final-20261101');index=json.loads((work/'quality_inputs/index.json').read_bytes())
    need(index['count']==500 and len(index['samples'])==500,'fixed500 index')
    os.umask(0o077);root=work/('quality_campaign_dynamic' if args.dynamic_block else 'quality_campaign_run_scoped' if args.run_scoped_spinning else 'quality_campaign');root.mkdir(mode=0o700,exist_ok=False)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    campaign={'schema':'opt50_final_fixed500_campaign_v1','boot_id':boot,'source_model_sha256':'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43',
              'max_graphs':500,'max_PL_calls':18000,'batches':10,'max_graphs_per_owner':50,
              'variant':'openssl-info','ORT_intra_spinning':1,'ORT_force_spinning_stop':int(args.run_scoped_spinning),'ORT_dynamic_block_base':4 if args.dynamic_block else 0,'inputs':pin((work/'quality_inputs/index.json').read_bytes())}
    create(root/'campaign.json',encoded(campaign));results=[];windows=[];preprocessing_checked=0
    original=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_ready_20261028_v1/dataset')
    os.sched_setaffinity(0,{0})
    for batch in range(10):
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot,'campaign boot changed')
        selected=index['samples'][batch*50:(batch+1)*50]
        for item in selected:
            jpeg=(original/item['image']['file']).read_bytes()
            need(pin(jpeg)=={k:item['image'][k] for k in ('bytes','sha256')},'fixed original JPEG')
            tensor,pixels,rgb_sha=preprocess(jpeg,dataset)
            need(pin(tensor)=={k:item['input'][k] for k in ('bytes','sha256')}
                 and hashlib.sha256(pixels).hexdigest()==item['input']['raw_sha256']
                 and rgb_sha==item['image']['decoded_rgb_sha256'],'parallel preprocessing differs from fixed input')
            preprocessing_checked+=1
        label=('quality-dynamic-' if args.dynamic_block else 'quality-runscoped-' if args.run_scoped_spinning else 'quality-')+'%02d'%batch
        argv=['/home/ubuntu/fpga_project/model_b/.venv/bin/python','-B',str(work/'tools/launch_window.py'),
              '--variant','openssl-info','--count','50','--spinning','1','--label',label]
        if args.run_scoped_spinning:argv.append('--force-stop-spinning')
        if args.dynamic_block:argv.append('--dynamic-block')
        for item in selected:argv.extend(['--input',item['path']])
        create(root/('dispatch_%02d.json'%batch),encoded({'argv':argv,'batch':batch,'input_ordinals':[s['ordinal'] for s in selected]}))
        dispatched=subprocess.run(argv,capture_output=True,text=True)
        create(root/('dispatch_%02d.stdout'%batch),dispatched.stdout.encode());create(root/('dispatch_%02d.stderr'%batch),dispatched.stderr.encode())
        need(dispatched.returncode==0,'dispatch failed; campaign stops, no replay')
        window=json.loads((work/('window-'+label+'.json')).read_bytes());folder=Path(window['root']);started=time.monotonic()
        while not (folder/'closed.json').exists():
            errors=[p for p in (folder/'execution').glob('*.stderr') if p.stat().st_size]
            need(not errors and time.monotonic()-started<880,'window fault/uncertainty; campaign stops without signals or retries')
            time.sleep(1)
        need(json.loads((folder/'closed.json').read_bytes())['completed_graphs']==50,'exact closed batch')
        for run,item in enumerate(selected,1):
            row=json.loads((folder/'execution'/('%06d_result.json'%run)).read_bytes())
            need(row['bitwise_mismatches']==0 and row['input_sha256']==item['input']['raw_sha256'],'fixed input/raw logits mismatch')
            results.append({**row,'ordinal':item['ordinal'],'id':item['id'],'label':item['label'],'window':str(folder)})
        windows.append(str(folder))
        replace(root/'progress.json',{'status':'PASS','completed_images':len(results),'planned_images':500,
                                     'completed_PL_calls':len(results)*36,'preprocessing_equivalent_images':preprocessing_checked,
                                     'windows':windows,'results':results})
        print(json.dumps({'completed_images':len(results),'planned_images':500,'completed_PL_calls':len(results)*36}),flush=True)
    create(root/'closed.json',encoded({'status':'PASS','images':500,'FP32_logits_compared':500000,
                                      'bitwise_mismatches':0,'completed_PL_calls':18000,
                                      'preprocessing_equivalent_images':preprocessing_checked,'windows':windows}))


if __name__=='__main__':
    try:main()
    except BaseException as error:
        print('CAMPAIGN_STOPPED_NO_REPLAY '+repr(error),file=sys.stderr,flush=True)
        raise
