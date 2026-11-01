"""Remove only verified, reconstructible model/input copies from closed old-boot plans."""
import argparse
import json
from pathlib import Path
import re
import shutil
import stat
import sys

sys.path.insert(0,'/opt/opt50-demo-v1/private')
from common import create,encoded,need,pin
import resident_proof
import backend
from loader import helpers

WORK=Path('/var/lib/opt50-final-20261101')
BASE=Path('/var/lib/opt50-b24-model-v1')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verified-evidence',type=Path,required=True);args=parser.parse_args()
    need(not (WORK/'copy_compaction_intent.json').exists(),'compaction already attempted; inspect, never replay')
    evidence_raw=args.verified_evidence.read_bytes();evidence=json.loads(evidence_raw)
    need(evidence['status']=='PASS' and re.fullmatch('[0-9a-f]{64}',evidence['archive']['sha256']),'independently verified archive receipt')
    backend.verify_install();board,_,_=helpers();users=board.snapshot_api().device_users(Path('/proc'))
    need(users['coverage_complete'] and users['observed_device_fds']==0 and users['error_count']==0 and not users['users'],'no active device users')
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();targets=[];sources={};roots=[];total=0;files=0
    records=sorted(WORK.glob('window-*.json'))
    need({json.loads(p.read_bytes())['root'] for p in records}==set(evidence['window_roots']),'archive covers exactly all experiment plans')
    def source_pin(path):
        key=str(path)
        if key not in sources:sources[key]=pin(path.read_bytes())
        return sources[key]
    for record in records:
        folder=Path(json.loads(record.read_bytes())['root'])
        need(folder.parent==BASE and folder.resolve()==folder and re.fullmatch('plan_[0-9a-f]{12}',folder.name),'canonical owned plan path')
        plan=json.loads((folder/'plan.json').read_bytes())
        need(plan['root']==str(folder) and plan['token']==folder.name[5:] and plan['boot_id']!=boot,'only expired-boot plan copies')
        events=[json.loads(p.read_bytes()) for p in sorted((folder/'execution/native_events').glob('event_*.json'))]
        checked=resident_proof.owner_close(events,plan['sample_count'])
        if (folder/'closed.json').exists():need(checked==json.loads((folder/'closed.json').read_bytes()),'actual normal close')
        else:need(checked==json.loads((folder/'warning_reconciliation.json').read_bytes())['closed'],'actual reconciled resource close')
        origins={item['name']:Path(item['original_path']) for item in plan['inputs']}
        count=0;size=0
        for name in ('model','inputs'):
            target=folder/name;need(target.resolve()==target and target.is_dir(),'owned regular copy directory')
            actual={p.relative_to(folder).as_posix():p for p in target.rglob('*') if not p.is_dir()}
            wanted={n:v for n,v in plan['files'].items() if n.startswith(name+'/')}
            need(set(actual)==set(wanted),'copy file set changed; preserve it')
            for relative,path in actual.items():
                info=path.lstat();need(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1,'root-owned unlinked copy')
                need(pin(path.read_bytes())==wanted[relative],'copy bytes changed; preserve it')
                source=(WORK/'candidates'/plan['variant']/relative) if name=='model' else origins[path.name]
                need(source.resolve()==source and source_pin(source)==wanted[relative],'retained canonical reconstruction source')
                need(not source.is_relative_to(BASE),'source must survive plan copy pruning')
                count+=1;size+=info.st_size
            targets.append(target)
        roots.append({'root':str(folder),'files':count,'bytes':size});total+=size;files+=count
        print(json.dumps({'verified_copy_plans':len(roots),'total_copy_bytes':total}),flush=True)
    before=shutil.disk_usage(BASE).free
    intent={'status':'PASS','verified_evidence':pin(evidence_raw),'archive':evidence['archive'],'boot_id':boot,
            'roots':roots,'targets':[str(p) for p in targets],'files':files,'copy_bytes':total,
            'retained':'All plans, tools, binaries, raw outputs, close events, source model assets and original input paths'}
    create(WORK/'copy_compaction_intent.json',encoded(intent))
    for target in targets:shutil.rmtree(target)
    result={**intent,'free_before_bytes':before,'free_after_bytes':shutil.disk_usage(BASE).free,'hardware_modified':False}
    create(WORK/'copy_compaction_closed.json',encoded(result));print(json.dumps(result),flush=True)


if __name__=='__main__':main()
