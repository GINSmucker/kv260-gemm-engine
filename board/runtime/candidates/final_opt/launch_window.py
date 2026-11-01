"""Dispatch exactly one newly staged transient unit after the normal service is stopped."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant',choices=['baseline','diagnostic','openssl-info'],required=True)
    parser.add_argument('--count',type=int,required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('--ort-profile',action='store_true')
    parser.add_argument('--spinning',type=int,choices=[0,1],default=0)
    parser.add_argument('--force-stop-spinning',action='store_true')
    parser.add_argument('--dynamic-block',action='store_true')
    parser.add_argument('--input',action='append',required=True)
    args=parser.parse_args()
    if not args.label.replace('-','').isalnum():raise ValueError('simple new label required')
    root=Path('/var/lib/opt50-final-20261101');record=root/('window-'+args.label+'.json')
    if record.exists():raise ValueError('label already consumed; no automatic replay')
    state=subprocess.check_output(['systemctl','show','opt50-demo-hardware.service','-p','ActiveState','--value'],text=True).strip()
    if state!='inactive':raise ValueError('normal service must be inactive')
    sys.path.insert(0,'/opt/opt50-demo-v1/private')
    from loader import helpers
    board,_,_=helpers()
    observed=board.snapshot_api().device_users(Path('/proc'))
    if not (observed['coverage_complete'] and observed['error_count']==0 and observed['observed_device_fds']==0 and not observed['users']):
        raise ValueError('device ownership incomplete/busy')
    argv=['/usr/bin/python3','-B',str(Path(__file__).with_name('stage_window.py')),'--variant',args.variant,'--count',str(args.count)]
    argv.extend(['--spinning',str(args.spinning)])
    if args.force_stop_spinning:argv.append('--force-stop-spinning')
    if args.dynamic_block:argv.append('--dynamic-block')
    if args.ort_profile:argv.append('--ort-profile')
    for path in args.input:argv.extend(['--input',path])
    staged=json.loads(subprocess.check_output(argv,text=True))
    record.write_text(json.dumps(staged,indent=2)+'\n')
    command=['systemd-run','--unit',staged['unit'],'--property=KillMode=none','--property=TimeoutStopSec=infinity',
             '/usr/bin/python3','-B',str(Path(staged['root'])/'tools/run_model.py'),'run','--plan',str(Path(staged['root'])/'plan.json'),'--authorized-resident-batch']
    result=subprocess.run(command,capture_output=True,text=True)
    print(json.dumps({'root':staged['root'],'unit':staged['unit'],'dispatch_returncode':result.returncode,
                      'stdout':result.stdout,'stderr':result.stderr},indent=2))
    if result.returncode:raise RuntimeError('dispatch failed; inspect only, do not repeat')


if __name__=='__main__':main()
