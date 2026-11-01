"""Current-boot coordinator retaining the original Native gate, locks and INFO contract."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,'/opt/opt50-demo-v1/private')
from common import create,encoded,need,pin
from backend import lifetime_lock,verify_install
import resident_proof


def run(plan_path):
    root=Path(plan_path).parent;plan=json.loads(Path(plan_path).read_bytes())
    need(sys.argv==[str(root/'tools/run_model.py'),'run','--plan',str(root/'plan.json'),'--authorized-resident-batch'],'coordinator exact argv')
    need(os.geteuid()==0 and plan['root']==str(root) and plan['sample_count']<=50,'root bounded plan')
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==plan['boot_id'],'current boot required')
    need(Path('/proc/self/cgroup').read_text()=='0::/system.slice/opt50-b24-model-'+plan['token']+'.service\n','current token unit')
    verify_install()
    for name,expected in plan['files'].items():
        path=root/name
        need(not path.is_symlink() and pin(path.read_bytes())==expected,'window file changed: '+name)
    board_lock=lifetime_lock('/home/ubuntu/fpga_project/deploy/.f8_board.lock')
    companion=lifetime_lock('/var/lib/opt50-first-access-fd96539d-v1/companion.lock')
    # Match the deployed supervisor/owner CPU placement before creating children.
    os.sched_setaffinity(0,{0})
    started=time.monotonic_ns()
    need(0<=started-plan['created_monotonic_ns']<60_000_000_000,'fresh window required')
    fields={'schema':'opt50_b24_resident_native_job_v4','boot_id':plan['boot_id'],'plan_token':plan['token'],
            'expected_parent_pid':str(os.getpid()),'companion_lock':'/var/lib/opt50-first-access-fd96539d-v1/companion.lock',
            'model_root':str(root/'model'),'weights':str(root/'model/weights.bin'),
            'data':str(root/'model/pl_w_data.bin'),'meta':str(root/'model/pl_w_meta.bin'),
            'reference':str(root/'model/reference.onnx'),'metadata':str(root/'metadata.xclbin'),
            'run_id':'1','expected_PL_starts':'36','source_model_sha256':plan['source_model_sha256'],
            'sample_count':str(plan['sample_count']),'total_START_budget':str(plan['sample_count']*36),
            'created_monotonic_ns':str(plan['created_monotonic_ns'])}
    manifest=json.loads((root/'model/manifest.json').read_bytes())
    for key,field in (('weights','logical_weights_sha256'),('data','pl_data_sha256'),('meta','pl_meta_sha256'),('reference','reference_sha256')):
        fields[key+'_sha256']=manifest[field]
    fields['metadata_sha256']=pin((root/'metadata.xclbin').read_bytes())['sha256']
    need(len(fields)==22,'exact native job')
    create(root/'job.kv',''.join(k+'='+v+'\n' for k,v in fields.items()).encode())
    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':'/root','XRT_INI_PATH':str(root/'xrt.ini')}
    argv=[str(root/'native_owner'),str(root/'model/weights.bin'),str(root/'execution/runtime.sock'),
          str(root/'metadata.xclbin'),str(root/'execution'),str(root/'job.kv'),str(companion),'--authorized-resident-batch']
    create(root/'owner_intent.json',encoded({'argv':argv,'plan_sha256':pin(Path(plan_path).read_bytes())}))
    out=open(root/'execution/owner.stdout','xb',buffering=0);err=open(root/'execution/owner.stderr','xb',buffering=0)
    owner=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=out,stderr=err,pass_fds=(companion,),env=env,cwd=root/'execution')
    while (root/'execution/owner.stdout').read_bytes()!=('KV5_READY %d\n'%owner.pid).encode():
        need(owner.poll() is None and time.monotonic_ns()-started<180_000_000_000,'owner readiness failed; retain')
        time.sleep(.05)
    worker_plan=dict(plan,owner_pid=owner.pid)
    create(root/'worker_plan.json',encoded(worker_plan))
    with open(root/'execution/worker.stdout','xb',buffering=0) as worker_out,open(root/'execution/worker.stderr','xb',buffering=0) as worker_err:
        worker=subprocess.Popen(['/home/ubuntu/fpga_project/model_b/.venv/bin/python','-B',str(root/'tools/worker.py'),str(root/'worker_plan.json')],
                                stdin=subprocess.DEVNULL,stdout=worker_out,stderr=worker_err,env=env,cwd=root)
        while worker.poll() is None:
            owner_code=owner.poll()
            need((owner_code is None or (owner_code==0 and (root/'execution/worker_closed.json').exists()))
                 and time.monotonic_ns()-started<880_000_000_000,'window/owner uncertain; retain')
            time.sleep(.1)
    need(worker.returncode==0 and (root/'execution/worker.stderr').read_bytes()==b'','worker failed')
    need(json.loads((root/'execution/worker_closed.json').read_bytes())['graphs']==plan['sample_count'],'worker closed')
    need(owner.wait(timeout=150)==0,'owner close exit')
    expected=('KV5_READY %d\nKV5_STOP calls=%d graphs=%d max_graphs=%d\n'%(owner.pid,36*plan['sample_count'],plan['sample_count'],plan['sample_count'])).encode()
    need((root/'execution/owner.stdout').read_bytes()==expected and (root/'execution/owner.stderr').read_bytes()==b'','owner close logs')
    events=[json.loads(p.read_bytes()) for p in sorted((root/'execution/native_events').glob('event_*.json'))]
    closed=resident_proof.owner_close(events,plan['sample_count'])
    create(root/'closed.json',encoded(closed));os.close(companion);os.close(board_lock)
    print(json.dumps({'status':'PASS','root':str(root),'graphs':plan['sample_count'],'device_closed':True}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['run']);parser.add_argument('--plan',required=True);parser.add_argument('--authorized-resident-batch',action='store_true',required=True);args=parser.parse_args()
    try:run(args.plan)
    except BaseException as error:
        print('RETAINED_COORDINATOR '+repr(error),file=sys.stderr,flush=True)
        while True:time.sleep(5)
