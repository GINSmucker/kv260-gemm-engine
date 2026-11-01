"""Switch reviewed original/optimized service pairs only while devices are closed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import statistics

from prepare import pin,replace_once

APP=Path('/opt/opt50-demo-v1/private')
WORK=Path('/var/lib/opt50-final-20261101')
BACKUP=WORK/'original_service'
DYNAMIC_BLOCK=False


def publish(path,raw,mode=0o600):
    temporary=path.with_name(path.name+'.final-'+secrets.token_hex(4))
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def optimized_backend(raw,*,dynamic_block=False,parallel_rgb=True):
    source=raw.decode()
    source=replace_once(source,'from cpu_optimizations import ExactBundleAudit, preprocess as exact_preprocess',
                        ('from cpu_optimizations import ExactBundleAudit\nfrom parallel_cpu import preprocess as exact_preprocess' if parallel_rgb else
                         'from cpu_optimizations import ExactBundleAudit, preprocess as exact_preprocess')+'\nfrom runtime_options import pl_options')
    source=replace_once(source,'max_runs=50, checked_bundle=s.bundle)',
                        'max_runs=50, checked_bundle=s.bundle, options=pl_options(s.runtime.dependencies()[1],1,force_stop=True'+(',dynamic_block=True' if dynamic_block else '')+'))')
    profile='demo-live-exact-runscoped'+('-dynamic4' if dynamic_block else '')+'-v1'
    metadata="'measurement_profile': "+repr(profile)+", 'ORT_intra_spinning': 1, 'ORT_force_spinning_stop': 1, 'ORT_dynamic_block_base': "+str(4 if dynamic_block else 0)
    source=replace_once(source,"'measurement_profile': 'demo-live-cpu4-pl4-v1'",metadata)
    marker="'python_profile': 'exact-rgb-path-audit-v1'"
    if source.count(marker)!=2:raise ValueError('backend profile anchors differ')
    source=source.replace(marker,"'python_profile': "+repr('exact-parallel-rgb-sha-v2' if parallel_rgb else 'exact-rgb-path-audit-v1')+", "+metadata)
    source=replace_once(source,'        self.admission()\n        run = self.completed + 1',
                        '        infer_started = time.perf_counter_ns()\n        admission_started = infer_started\n        self.admission()\n        admission_ms = (time.perf_counter_ns()-admission_started)/1e6\n        run = self.completed + 1')
    source=replace_once(source,"        self.admission()\n        create(folder / 'graph_intent.json'",
                        "        admission_started = time.perf_counter_ns()\n        self.admission()\n        admission_ms += (time.perf_counter_ns()-admission_started)/1e6\n        intent_started = time.perf_counter_ns()\n        create(folder / 'graph_intent.json'")
    source=replace_once(source,'        began = time.perf_counter_ns()\n        actual = self.runner.run',
                        '        intent_ms = (time.perf_counter_ns()-intent_started)/1e6\n        began = time.perf_counter_ns()\n        actual = self.runner.run')
    source=replace_once(source,'        self.admin.call(8)\n        proof_path',
                        '        info_started = time.perf_counter_ns()\n        self.admin.call(8)\n        info_ms = (time.perf_counter_ns()-info_started)/1e6\n        proof_path')
    source=replace_once(source,"        for name, value in [('cpu_logits.npy', cpu), ('pl_logits.npy', actual)]:",
                        "        persistence_started = time.perf_counter_ns()\n        for name, value in [('cpu_logits.npy', cpu), ('pl_logits.npy', actual)]:")
    source=replace_once(source,'        self.completed = run\n        self.last_activity',
                        '        persistence_ms = (time.perf_counter_ns()-persistence_started)/1e6\n        self.completed = run\n        self.last_activity')
    source=replace_once(source,'        return result\n\n    def close(self):',
                        '''        infer_total_ms = (time.perf_counter_ns()-infer_started)/1e6
        result.update(INFO_ms=info_ms, bundle_admission_ms=admission_ms,
                      graph_intent_persistence_ms=intent_ms, logit_persistence_ms=persistence_ms,
                      Session_infer_ms=infer_total_ms,
                      inference_other_ms=infer_total_ms-admission_ms-cpu_ms-intent_ms-latency-info_ms-persistence_ms)
        return result

    def close(self):''')
    source=replace_once(source,"                create(folder / 'input.npy', tensor)\n                if self.session",
                        "                input_persistence_started = time.perf_counter_ns()\n                create(folder / 'input.npy', tensor)\n                input_persistence_ms = (time.perf_counter_ns()-input_persistence_started)/1e6\n                lifecycle_started = time.perf_counter_ns()\n                if self.session")
    source=replace_once(source,'                result = self.session.infer(image, folder)',
                        '                lifecycle_ms = (time.perf_counter_ns()-lifecycle_started)/1e6\n                result = self.session.infer(image, folder)')
    source=replace_once(source,"                              request_work_ms=(time.perf_counter_ns()-began)/1e6)\n                create(folder / 'result.json'",
                        "                              request_work_ms=(time.perf_counter_ns()-began)/1e6)\n                result.update(input_persistence_ms=input_persistence_ms, session_lifecycle_ms=lifecycle_ms,\n                              request_other_ms=result['request_work_ms']-preprocessing-input_persistence_ms-lifecycle_ms-result['Session_infer_ms'])\n                create(folder / 'result.json'")
    compile(source,'optimized_backend.py','exec')
    return source.encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['original','optimized','model-only']);args=parser.parse_args()
    if os.geteuid()!=0:raise ValueError('root required')
    sys.path.insert(0,str(APP))
    import backend
    from loader import helpers
    from common import need
    current=backend.verify_install()
    need(subprocess.check_output(['systemctl','show','opt50-demo-hardware.service','-p','ActiveState','--value'],text=True).strip()=='inactive','normal service stop required')
    board,_,_=helpers();users=board.snapshot_api().device_users(Path('/proc'))
    need(users['coverage_complete'] and users['error_count']==0 and users['observed_device_fds']==0 and not users['users'],'all device users must be closed')
    board_lock=backend.lifetime_lock('/home/ubuntu/fpga_project/deploy/.f8_board.lock')
    companion=backend.lifetime_lock('/var/lib/opt50-first-access-fd96539d-v1/companion.lock')
    if not BACKUP.exists():
        os.umask(0o077);BACKUP.mkdir(mode=0o700)
        need(hashlib.sha256((APP/'backend.py').read_bytes()).hexdigest()=='2ab0d2080cf0e501f8e3f6eff63c0d051c64560eb6395c28ca6de088d5e51ce5','expected original backend')
        names=('backend.py','install_receipt.json','source/kernel.cpp','source/native_gate.hpp',
               'build/opt50_model_owner','build/libopt50_model.so','cpu_optimizations.py')
        for name in names:
            destination=BACKUP/name;destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            publish(destination,(APP/name).read_bytes())
        pairing={'original_files':{name:pin((BACKUP/name).read_bytes()) for name in names},
                 'preserved_model_root':'/var/lib/opt50-demo-v1/model',
                 'model_manifest':pin(Path('/var/lib/opt50-demo-v1/model/manifest.json').read_bytes()),
                 'firmware_path':'/usr/lib/firmware/xilinx/kv260_opt50_fd96539d_4cbcf732',
                 'firmware_and_model_modified':False}
        publish(BACKUP/'pairing.json',(json.dumps(pairing,indent=2)+'\n').encode())
    pairing=json.loads((BACKUP/'pairing.json').read_bytes())
    need(all(pin((BACKUP/name).read_bytes())==wanted for name,wanted in pairing['original_files'].items()),'original rollback bytes changed')
    original_raw=(BACKUP/'install_receipt.json').read_bytes();receipt=json.loads(original_raw)
    payload={name:(BACKUP/name).read_bytes() for name in ('backend.py','source/kernel.cpp','build/opt50_model_owner')}
    if args.mode in ('optimized','model-only'):
        cpu=json.loads((WORK/'parallel-cpu-checks.json').read_bytes())
        need(cpu['status']=='PASS' and cpu['equivalent_cases']==31 and cpu['invalid_cases']==3,'CPU preprocessing validation')
        provenance=json.loads((WORK/'parallel-cpu-provenance.json').read_bytes())
        need(provenance['status']=='PASS' and provenance['artifact']==pin((WORK/'parallel-cpu-checks.json').read_bytes())
             and provenance['candidate_source']==pin(Path(__file__).with_name('parallel_cpu.py').read_bytes()),'validated CPU candidate source')
        quality_path=WORK/'quality_campaign_run_scoped/closed.json'
        quality=json.loads(quality_path.read_bytes())
        need(quality['status']=='PASS' and quality['images']==500 and quality['FP32_logits_compared']==500000
             and quality['bitwise_mismatches']==0,'fixed500 numerical validation')
        performance_path=WORK/'latency_campaign_three_arm/closed.json'
        performance=json.loads(performance_path.read_bytes())
        need(performance['status']=='PASS' and performance['graphs']==585 and performance['bitwise_mismatches']==0,'closed three-arm195 validation')
        measured=json.loads((performance_path.parent/'progress.json').read_bytes())['results']
        selected_arm='dynamic' if DYNAMIC_BLOCK else 'scoped'
        original=[r['Runner_run_ms'] for r in measured if r['arm']=='original']
        selected=[r['Runner_run_ms'] for r in measured if r['arm']==selected_arm]
        need(len(original)==len(selected)==195,'exact195 comparison')
        gain=statistics.median(original)-statistics.median(selected)
        need(gain>2 and 100*gain/statistics.median(original)>3,'fixed model adoption thresholds')
        if DYNAMIC_BLOCK:
            dynamic_cpu=json.loads((WORK/'dynamic-cpu-check.json').read_bytes())
            need(dynamic_cpu['status']=='PASS' and dynamic_cpu['compared_logits']==50000 and dynamic_cpu['bitwise_mismatches']==0,'dynamic CPU50 validation')
        build=WORK/'service-openssl-info';built=json.loads((build/'build_receipt.json').read_bytes())
        need(built['status']=='PASS' and built['installed_receipt']==pin(original_raw),'paired service-gate build')
        need(pin((build/'opt50_model_owner').read_bytes())==built['binary'],'candidate owner changed')
        need((build/'source/native_gate.hpp').read_bytes()==(BACKUP/'source/native_gate.hpp').read_bytes(),'service gate unchanged')
        if args.mode=='optimized':
            payload['source/kernel.cpp']=(build/'source/kernel.cpp').read_bytes()
            payload['build/opt50_model_owner']=(build/'opt50_model_owner').read_bytes()
        payload['backend.py']=optimized_backend(payload['backend.py'],dynamic_block=DYNAMIC_BLOCK,parallel_rgb=args.mode=='optimized')
        for name in (('parallel_cpu.py','runtime_options.py') if args.mode=='optimized' else ('runtime_options.py',)):
            payload[name]=Path(__file__).with_name(name).read_bytes()
        for name,raw in payload.items():receipt['files'][name]=pin(raw)
        compiled=[entry for entry in receipt['compiled'] if entry['name']=='opt50_model_owner']
        need(len(compiled)==1,'one current owner build record')
        if args.mode=='optimized':
            compiled[0].update(command=built['command'],returncode=built['compile_returncode'],
                               build_receipt=pin((build/'build_receipt.json').read_bytes()))
        receipt['final_optimization_trial']={'profile':('exact-run-scoped-spin-model-only-v1' if args.mode=='model-only' else 'exact-run-scoped-spin'+('-dynamic4' if DYNAMIC_BLOCK else '')+'-sha-parallel-v1'),'final_adoption':False,
                                            'native_changed':args.mode=='optimized','parallel_preprocessing':args.mode=='optimized',
                                            'ORT_dynamic_block_base':4 if DYNAMIC_BLOCK else 0,
                                            'baseline_install_receipt':pin(original_raw),'installation_recipe':pin(Path(__file__).read_bytes()),
                                            'quality_receipt':pin(quality_path.read_bytes()),
                                            'performance_receipt':pin(performance_path.read_bytes()),
                                            'native_build_receipt':pin((build/'build_receipt.json').read_bytes()) if args.mode=='optimized' else None,
                                            'native_binary':pin(payload['build/opt50_model_owner']),
                                            'plugin_binary':pairing['original_files']['build/libopt50_model.so']}
    previous={name:(APP/name).read_bytes() if (APP/name).exists() else None for name in payload}
    previous_receipt=(APP/'install_receipt.json').read_bytes()
    try:
        for name,raw in payload.items():publish(APP/name,raw,0o700 if name=='build/opt50_model_owner' else 0o600)
        publish(APP/'install_receipt.json',original_raw if args.mode=='original' else (json.dumps(receipt,sort_keys=True)+'\n').encode())
        backend.verify_install()
        need(pin((APP/'build/libopt50_model.so').read_bytes())==pairing['original_files']['build/libopt50_model.so'],'plugin pair changed')
        need(pin(Path('/var/lib/opt50-demo-v1/model/manifest.json').read_bytes())==pairing['model_manifest'],'model unchanged')
    except BaseException:
        for name,raw in previous.items():
            if raw is not None:publish(APP/name,raw,0o700 if name=='build/opt50_model_owner' else 0o600)
        publish(APP/'install_receipt.json',previous_receipt)
        raise
    result={'status':'PASS','mode':args.mode,'trial_only':True,'requires_new_boot':True,'service_started':False,
            'changed_files':{name:pin(raw) for name,raw in payload.items()},'rollback_pairing':str(BACKUP/'pairing.json')}
    publish(WORK/('installation-'+args.mode+'-'+secrets.token_hex(4)+'.json'),(json.dumps(result,indent=2)+'\n').encode())
    os.close(companion);os.close(board_lock)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
