"""Paired fixed195 comparison in five balanced 39-image owner pairs on the same boot."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import argparse

sys.path.insert(0,'/opt/opt50-demo-v1/private')
from common import create,encoded,need,replace,pin


def environment():
    paths={'governor':'/sys/devices/system/cpu/cpufreq/policy0/scaling_governor',
           'CPU_kHz':'/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq','loadavg':'/proc/loadavg'}
    result={key:Path(path).read_text().strip() if Path(path).exists() else 'NOT RUN' for key,path in paths.items()}
    result['temperatures_mC']={p.name:p.read_text().strip() for p in Path('/sys/class/hwmon/hwmon0').glob('temp*_input')}
    result['monotonic_ns']=time.monotonic_ns()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-scoped-spinning',action='store_true');parser.add_argument('--dynamic-block',action='store_true');args=parser.parse_args()
    need(not args.dynamic_block or args.run_scoped_spinning,'dynamic block comparison requires Run-scoped candidate')
    work=Path('/var/lib/opt50-final-20261101');index=json.loads((work/'quality_inputs/index.json').read_bytes())
    selected=index['samples'][305:500]
    need(len(selected)==195 and [s['ordinal'] for s in selected]==list(range(305,500)),'fixed306..500')
    suffix='_dynamic' if args.dynamic_block else '_run_scoped' if args.run_scoped_spinning else ''
    os.umask(0o077);root=work/('latency_campaign'+suffix);root.mkdir(mode=0o700,exist_ok=False)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    create(root/'campaign.json',encoded({'schema':'opt50_final_paired195_campaign_v1','boot_id':boot,
        'inputs':pin((work/'quality_inputs/index.json').read_bytes()),'images_per_arm':195,'graphs':390,'PL_call_budget':14040,
        'groups':5,'images_per_owner':39,'order':['original,candidate' if i%2==0 else 'candidate,original' for i in range(5)],
        'candidate_force_spinning_stop':int(args.run_scoped_spinning),
        'candidate_dynamic_block_base':4 if args.dynamic_block else 0,
        'scope':'Fresh owner/session per arm/group; current same-boot exact inputs; Runner excludes CPU reference and INFO; all valid samples retained'}))
    results=[];windows=[]
    for batch in range(5):
        items=selected[batch*39:(batch+1)*39]
        arms=('original','candidate') if batch%2==0 else ('candidate','original')
        for arm in arms:
            need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot,'same boot required')
            label='paired195%s-%02d-%s'%('-dynamic' if args.dynamic_block else '-runscoped' if args.run_scoped_spinning else '',batch,arm)
            argv=['/home/ubuntu/fpga_project/model_b/.venv/bin/python','-B',str(work/'tools/launch_window.py'),
                  '--variant','baseline' if arm=='original' else 'openssl-info','--count','39',
                  '--spinning','0' if arm=='original' else '1','--label',label]
            if arm=='candidate' and args.run_scoped_spinning:argv.append('--force-stop-spinning')
            if arm=='candidate' and args.dynamic_block:argv.append('--dynamic-block')
            for item in items:argv.extend(['--input',item['path']])
            create(root/(label+'.intent.json'),encoded({'argv':argv,'ordinals':[s['ordinal'] for s in items],'environment':environment()}))
            dispatched=subprocess.run(argv,capture_output=True,text=True)
            create(root/(label+'.stdout'),dispatched.stdout.encode());create(root/(label+'.stderr'),dispatched.stderr.encode())
            need(dispatched.returncode==0,'dispatch failed; no retry')
            window=json.loads((work/('window-'+label+'.json')).read_bytes());folder=Path(window['root']);began=time.monotonic()
            while not (folder/'closed.json').exists():
                need(not any(p.stat().st_size for p in (folder/'execution').glob('*.stderr'))
                     and time.monotonic()-began<880,'uncertain/failed window; no signals or replay')
                time.sleep(1)
            need(json.loads((folder/'closed.json').read_bytes())['completed_graphs']==39,'exact closed39')
            for run,item in enumerate(items,1):
                row=json.loads((folder/'execution'/('%06d_result.json'%run)).read_bytes())
                need(row['bitwise_mismatches']==0 and row['input_sha256']==item['input']['raw_sha256'],'paired input/numerical identity')
                results.append({**row,'arm':arm,'group':batch,'ordinal':item['ordinal'],'id':item['id'],'window':str(folder)})
            windows.append(str(folder))
            replace(root/'progress.json',{'status':'PASS','completed_graphs':len(results),'planned_graphs':390,
                                         'windows':windows,'results':results})
            print(json.dumps({'completed_graphs':len(results),'planned_graphs':390,'arm':arm,'group':batch}),flush=True)
    by_image={}
    for row in results:by_image.setdefault(row['ordinal'],{})[row['arm']]=row
    need(all(set(pair)=={'original','candidate'} and pair['original']['PL_logits_sha256']==pair['candidate']['PL_logits_sha256']
             for pair in by_image.values()),'same-model paired1000 outputs')
    create(root/'closed.json',encoded({'status':'PASS','images_per_arm':195,'graphs':390,'compared_logits':390000,
                                      'bitwise_mismatches':0,'completed_PL_calls':14040,'windows':windows}))


if __name__=='__main__':
    try:main()
    except BaseException as error:
        print('PAIRED_CAMPAIGN_STOPPED_NO_REPLAY '+repr(error),file=sys.stderr,flush=True)
        raise
