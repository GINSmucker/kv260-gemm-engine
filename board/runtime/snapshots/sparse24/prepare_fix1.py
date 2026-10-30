"""Software-only repair preparation. No owner, device, signal, or computation."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode=True
OUTER='opt50-b24-resident-v4-images46-50.service'


def prepare(authorized,verify_only=False):
    from verify_tools import verify,need,ROOT
    need(authorized or verify_only,'explicit software preparation authorization required')
    need(os.name=='posix' and os.geteuid()==0,'run software preparation on Linux as root')
    verify()
    import run_model
    import before_owner
    import build_arm
    a=run_model.api()
    if verify_only:
        refusal=before_owner.validate_refusal(a.c,before_owner.TARGET.parent,a.tools['manifest'])
    else:
        refusal=before_owner.retire(a)
    print(json.dumps({'stage':'UNSTARTED_FAILURE_RECORDED_NO_SIGNALS','review':refusal}),flush=True)
    # The old wrapper reads the new refusal and exits on its own. Only observe;
    # neither this helper nor systemd is asked to stop or signal that process.
    start=time.monotonic()
    while True:
        observed=before_owner.service(OUTER)
        if observed.get('MainPID')=='0' and observed.get('ActiveState') in ('failed','inactive','active') and observed.get('SubState') in ('failed','dead','exited'):
            break
        need(time.monotonic()-start <= 30,'old read-only wrapper has not exited; no kill/replay was performed')
        time.sleep(1)
    output=ROOT/'build_arm'
    if verify_only or (output/'build_receipt.json').is_file():
        value=build_arm.verify_build(output)
    else:
        value=build_arm.reuse_build(output)
    return {'status':'FIX1_SOFTWARE_READY_NO_NEW_COMPUTE','build_status':value['status'],
        'build_method':value['build_method'],'receipt':str(output/'build_receipt.json'),
        'old_wrapper':observed,'old_failed_plan_reused':False,'old_sources_modified':False,
        'hardware_accessed':False,'START_commands_issued':0,'signals_sent':False,
        'next':'Create a NEW plan for the same five unexecuted images; do not reuse b06c'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--authorized-software-preparation',action='store_true')
    group.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    result=prepare(args.authorized_software_preparation,args.verify_only)
    print(json.dumps(result,indent=2,allow_nan=False),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'status':'FIX1_PREPARATION_REFUSED_PRESERVE_EVIDENCE',
            'error':type(error).__name__+': '+str(error),'automatic_retry':False,'hardware_accessed':False}),flush=True)
        raise SystemExit(2)
