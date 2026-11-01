"""Read identity or normally stop and prove closure before a paired service switch."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,'/opt/opt50-demo-v1/private')
import backend
from common import need,pin,MAX_REQUESTS,VENV
from loader import helpers


def inspect(stop=False):
    config=backend.verify_install()
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if stop:subprocess.run(['systemctl','stop','opt50-demo-hardware.service'],check=True,timeout=180)
    active=subprocess.check_output(['systemctl','show','opt50-demo-hardware.service','-p','ActiveState','--value'],text=True).strip()
    board,_,_=helpers();users=board.snapshot_api().device_users(Path('/proc'))
    closed={}
    if stop:
        need(active=='inactive','service must close normally before switch/reboot')
        need(users['coverage_complete'] and users['error_count']==0 and users['observed_device_fds']==0 and not users['users'],'device users remain; preserve state')
        import resident_proof
        for folder in Path('/var/lib/opt50-demo-v1').glob('session_*'):
            plan=json.loads((folder/'plan.json').read_bytes())
            if plan['boot_id']!=boot or not (folder/'ready.json').exists():continue
            receipt=json.loads((folder/'closed.json').read_bytes())
            events=[json.loads(p.read_bytes()) for p in sorted((folder/'execution/native_events').glob('event_*.json'))]
            need(resident_proof.owner_close(events,receipt['completed_graphs'])==receipt,'real service owner close required')
            need(not (folder/'execution/owner.stderr').read_bytes(),'owner stderr must be empty')
            closed[folder.name]=receipt
        locks=[backend.lifetime_lock(p) for p in ('/home/ubuntu/fpga_project/deploy/.f8_board.lock','/var/lib/opt50-first-access-fd96539d-v1/companion.lock')]
        import os
        for fd in locks:os.close(fd)
    status=Path('/run/opt50-demo/status.json')
    return {'status':'PASS','boot_id':boot,'service_state':active,'normal_stop_requested':stop,
            'consumed_HTTP_request_ids':len(list(Path('/var/lib/opt50-demo-v1/requests').iterdir())),
            'MAX_HTTP_request_ids':MAX_REQUESTS,
            'software':json.loads(subprocess.check_output([VENV,'-B','-c',
                'import json,platform,importlib.metadata as m;print(json.dumps(dict(Python=platform.python_version(),ORT=m.version("onnxruntime"),NumPy=m.version("numpy"),Pillow=m.version("Pillow"))))'],text=True)),
            'kernel':subprocess.check_output(['uname','-a'],text=True).strip(),
            'closed_service_sessions':closed,'device_users':users,'installation':config,
            'installation_receipt':pin(Path('/opt/opt50-demo-v1/private/install_receipt.json').read_bytes()),
            'runtime_status':json.loads(status.read_bytes()) if status.exists() else None,
            'CPU_kHz':Path('/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq').read_text().strip(),
            'temperatures_mC':{p.name:p.read_text().strip() for p in Path('/sys/class/hwmon/hwmon0').glob('temp*_input')},
            'monotonic_ns':time.monotonic_ns()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stop',action='store_true');args=parser.parse_args()
    print(json.dumps(inspect(args.stop),indent=2))
