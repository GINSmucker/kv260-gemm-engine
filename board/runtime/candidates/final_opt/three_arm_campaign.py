"""Finite195-image original / Run-scoped spinning / dynamic-block comparison."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,'/opt/opt50-demo-v1/private')
from common import create,encoded,need,replace,pin
from latency_campaign import environment


def main():
    work=Path('/var/lib/opt50-final-20261101');index=json.loads((work/'quality_inputs/index.json').read_bytes())
    selected=index['samples'][305:500];need(len(selected)==195,'fixed306..500')
    os.umask(0o077);root=work/'latency_campaign_three_arm';root.mkdir(mode=0o700,exist_ok=False)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    orders=[['original','scoped','dynamic'],['scoped','dynamic','original'],['dynamic','original','scoped'],
            ['original','dynamic','scoped'],['dynamic','scoped','original']]
    create(root/'campaign.json',encoded({'schema':'opt50_final_three_arm195_v1','boot_id':boot,
        'inputs':pin((work/'quality_inputs/index.json').read_bytes()),'images_per_arm':195,'graphs':585,
        'PL_call_budget':21060,'groups':5,'images_per_owner':39,'orders':orders,
        'scope':'Every195 image once in each arm; fresh39-graph owners; dynamic differs from scoped only by session.dynamic_block_base4'}))
    results=[];windows=[]
    for group,arms in enumerate(orders):
        items=selected[group*39:(group+1)*39]
        for arm in arms:
            need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot,'same boot')
            label='three195-%02d-%s'%(group,arm)
            argv=['/home/ubuntu/fpga_project/model_b/.venv/bin/python','-B',str(work/'tools/launch_window.py'),
                  '--variant','baseline' if arm=='original' else 'openssl-info','--count','39',
                  '--spinning','0' if arm=='original' else '1','--label',label]
            if arm!='original':argv.append('--force-stop-spinning')
            if arm=='dynamic':argv.append('--dynamic-block')
            for item in items:argv.extend(['--input',item['path']])
            create(root/(label+'.intent.json'),encoded({'argv':argv,'ordinals':[s['ordinal'] for s in items],'environment':environment()}))
            ran=subprocess.run(argv,capture_output=True,text=True)
            create(root/(label+'.stdout'),ran.stdout.encode());create(root/(label+'.stderr'),ran.stderr.encode())
            need(ran.returncode==0,'dispatch failed; no retry')
            folder=Path(json.loads((work/('window-'+label+'.json')).read_bytes())['root']);began=time.monotonic()
            while not (folder/'closed.json').exists():
                need(not any(p.stat().st_size for p in (folder/'execution').glob('*.stderr'))
                     and time.monotonic()-began<880,'fault/uncertainty; retain without replay')
                time.sleep(1)
            need(json.loads((folder/'closed.json').read_bytes())['completed_graphs']==39,'exact39 closure')
            for run,item in enumerate(items,1):
                row=json.loads((folder/'execution'/('%06d_result.json'%run)).read_bytes())
                need(row['bitwise_mismatches']==0 and row['input_sha256']==item['input']['raw_sha256'],'fixed raw input/output')
                results.append({**row,'arm':arm,'group':group,'ordinal':item['ordinal'],'id':item['id'],'window':str(folder)})
            windows.append(str(folder))
            replace(root/'progress.json',{'status':'PASS','completed_graphs':len(results),'planned_graphs':585,'windows':windows,'results':results})
            print(json.dumps({'completed_graphs':len(results),'planned_graphs':585,'arm':arm,'group':group}),flush=True)
    paired={}
    for row in results:paired.setdefault(row['ordinal'],[]).append(row)
    need(len(paired)==195 and all(len(v)==3 and len({r['PL_logits_sha256'] for r in v})==1 for v in paired.values()),'all3 arms same1000 raw outputs')
    create(root/'closed.json',encoded({'status':'PASS','images_per_arm':195,'graphs':585,'compared_logits':585000,
                                      'bitwise_mismatches':0,'completed_PL_calls':21060,'windows':windows}))


if __name__=='__main__':
    try:main()
    except BaseException as error:
        print('THREE_ARM_STOPPED_NO_REPLAY '+repr(error),file=sys.stderr,flush=True)
        raise
