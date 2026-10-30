"""Finite, sequential continuation to exactly500 committed acceptance images.

Only the fixed reboot reentry run_once submits new hardware work. This wrapper
never retries a batch, renews an old plan, kills, signals, reloads or unloads.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import time
import types

sys.dont_write_bytecode=True
HERE=Path(__file__).absolute().parent
PUBLIC=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_resident_20261030_reboot_r1/software')
FIX2={'bytes':25141,'sha256':'38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d'}
PREVIOUS=Path('/var/lib/opt50-b24-model-v1/plan_6efa0640b22b/plan.json')
DATASET=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_ready_20261028_v1/dataset')
PYTHON='/home/ubuntu/fpga_project/model_b/.venv/bin/python'
BASE=Path('/var/lib/opt50-b24-acceptance500-v1')
EXPORTS=Path('/home/ubuntu/fpga_project/opt50_integration/b24_acceptance500_reboot_exports_20261030_v1')
SOFTWARE_UNIT='opt50-b24-reboot-r1-arm-verify.service'
PILOT_UNIT='opt50-b24-resident-v4-reboot-r1-pilot.service'
PASS='PASS_B24_BATCH_ALL_IMAGES_BITWISE_CLOSED'
TARGET=500


def need(value,reason):
    if not value: raise ValueError(reason)


def pin(raw): return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def decode(raw):
    def pairs(items):
        out={}
        for key,value in items:
            need(key not in out,'duplicate runner JSON key');out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite runner JSON')))


def read(path,limit=64*1024*1024):
    path=Path(path).absolute();need('..' not in path.parts,'parent traversal refused')
    for part in (path,*path.parents):
        info=part.lstat();need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'linked runner input')
    before=path.lstat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=limit,'bounded single-link runner input')
    raw=path.read_bytes();after=path.lstat()
    key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    need(key(before)==key(after) and len(raw)==before.st_size,'runner input changed')
    return raw


def json_records(text):
    """run_once prints a sequence of JSON objects, including its final object."""
    decoder=json.JSONDecoder();records=[];offset=0
    while offset<len(text):
        while offset<len(text) and text[offset].isspace():offset+=1
        if offset==len(text):break
        value,end=decoder.raw_decode(text,offset)
        need(type(value) is dict,'run_once emitted a non-object');records.append(value);offset=end
    need(bool(records),'run_once emitted no JSON proof');return records


def selection(prefix,pilot):
    need(type(prefix) is int and 100<=prefix<=TARGET,'verified contiguous prefix must be between100 and500')
    return 0 if prefix==TARGET else min(50 if pilot is not None else 5,TARGET-prefix)


def run(authorized,uart,manifest_sha,runtime=None,*,guardian_plan=None,layer_plan=None):
    need(authorized and uart,'explicit finite500 authorization and UART confirmation required')
    need(type(manifest_sha) is str and re.fullmatch('[a-f0-9]{64}',manifest_sha),'fixed package manifest SHA256 required')
    if runtime is None:need(guardian_plan is not None and layer_plan is not None,'explicit current-boot guardian and layer plans required')
    rt=runtime or Runtime(manifest_sha,guardian_plan,layer_plan)
    with rt.session():
        try:
            rt.wait_software();had_pilot=rt.wait_existing_pilot()
            view=rt.snapshot();initial=view['prefix'];pilot=rt.pilot(view);selection(initial,pilot)
            need(initial>=305,'reboot continuation requires the independently verified closed prefix of at least305 images')
            need(not had_pilot or (initial>=105 and pilot is not None),'existing pilot exited without its complete five-image proof')
            remaining_budget=36*(TARGET-initial);batches=0
            rt.record('ADMITTED_FINITE_500',initial_prefix=initial,target_total=TARGET,
                maximum_additional_START_commands=remaining_budget,automatic_retry=False)
            while view['prefix']<TARGET:
                count=selection(view['prefix'],pilot);start=view['prefix'];batches+=1
                need(batches<=11,'finite batch-count bound exceeded')
                before=time.monotonic()
                rt.record('BATCH_INTENT_ONCE',batch_number=batches,start=start,count=count,prefix=start,pilot_plan=pilot)
                result=rt.submit(start,count,pilot)
                rt.wait_inner(result['plan'])
                following=rt.snapshot()
                need(following['prefix']==start+count and following['dataset_pin']==view['dataset_pin'] and
                     following['boot_id']==view['boot_id'],'committed prefix/boot/dataset changed unexpectedly; no continuation')
                rt.record('BATCH_CLOSED_VERIFIED',batch_number=batches,plan=result['plan'],start=start,count=count,
                    prefix=following['prefix'],elapsed_seconds=round(time.monotonic()-before,3))
                view=following;pilot=rt.pilot(view)
                need(pilot is not None,'new five-image resident pilot is not independently verified')
            report=rt.report()
            result={'status':'PASS_FINITE_500_WITH_SAVED_REPORT','target_total':TARGET,'initial_prefix':initial,
                    'completed_prefix':view['prefix'],'submitted_batches':batches,'report':report,
                    'automatic_retry':False,'old_plans_renewed':False,'signals_sent':False}
            rt.record('PASS_FINITE_500_WITH_SAVED_REPORT',result=result)
            return result
        except Exception as error:
            rt.record('STOPPED_NO_RETRY',reason=type(error).__name__+': '+str(error),
                automatic_retry=False,signals_sent=False,old_plans_renewed=False)
            raise


class Runtime:
    def __init__(self,manifest_sha,guardian_plan=None,layer_plan=None):
        self.manifest_sha=manifest_sha;self.folder=None;self.counter=0
        self.guardian=Path(guardian_plan).absolute() if guardian_plan is not None else None
        self.layer=Path(layer_plan).absolute() if layer_plan is not None else None

    def current_proofs(self):
        need(self.guardian is not None and self.layer is not None,'explicit current-boot guardian and layer plans required')
        selected=self.a.c.configure(self.guardian)
        self.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        gp=self.a.c.read_json(self.guardian,private=True);lp=self.a.c.read_json(self.layer,private=True)
        need(gp.get('boot_id')==lp.get('boot_id')==self.boot and
             lp.get('guardian_plan')==str(self.guardian),'guardian/layer proof belongs to another boot or residency')
        self.guardian_unit=selected['unit']+'.service'

    def load(self):
        need(os.name=='posix' and os.geteuid()==0 and os.uname().machine=='aarch64','run finite500 on ARM Linux as root')
        raw=read(HERE/'files_manifest.json',1024*1024)
        need(pin(raw)['sha256']==self.manifest_sha,'fixed500 package manifest differs')
        package=decode(raw);need(package.get('schema')=='opt50_b24_acceptance500_tool_files_v1' and
            package.get('target_total')==TARGET and package.get('source_fix2')=={'path':str(PUBLIC),'tools_manifest':FIX2,'files':158},
            '500 package target/source identity differs')
        need(type(package.get('files')) is dict and {'run_to_500.py','report500.py','verify_package.py'}<=package['files'].keys() and
             len(package['files'])<=30,'500 package inventory differs')
        for name,expected in package['files'].items():
            part=PurePosixPath(name)
            need(type(name) is str and str(part)==name and not part.is_absolute() and
                 all(p not in ('','.','..') for p in name.split('/')) and not any(c in name for c in '\\:\0\r\n'),'unsafe package name')
            need(pin(read(HERE.joinpath(*part.parts)))==expected,'500 package source changed: '+name)
        manifest_raw=read(PUBLIC/'tools_manifest.json',1024*1024);need(pin(manifest_raw)==FIX2,'fixed fix2 manifest differs')
        manifest=decode(manifest_raw);need(len(manifest['files'])==158,'fixed reboot inventory differs')
        for name,expected in manifest['files'].items():need(pin(read(PUBLIC/name))==expected,'fixed fix2 source changed: '+name)
        sys.path.insert(0,str(PUBLIC))
        entry=types.ModuleType('_finite500_fixed_entry');entry.__file__=str(PUBLIC/'run_model.py')
        exec(compile(read(PUBLIC/'run_model.py'),entry.__file__,'exec'),entry.__dict__)
        self.entry=entry;self.a=entry.api();self.a.c.root_linux();self.current_proofs()
        self.package=package;self.package_raw=raw

    @contextmanager
    def session(self):
        self.load();d=self.a.durable;c=self.a.c
        if not BASE.exists():
            parent=BASE.parent.lstat();need(parent.st_uid==0 and not parent.st_mode&0o022,'safe /var/lib parent required')
            d.mkdir_new(BASE)
        c.private_dir(BASE)
        fd=os.open(BASE/'runner.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        locked=False
        try:
            import fcntl
            info=os.fstat(fd);need(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o077,'private runner lock required')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
            self.token=secrets.token_hex(6);self.folder=BASE/('action_'+self.token);d.mkdir_new(self.folder)
            d.mkdir_new(self.folder/'tools')
            for name,expected in self.package['files'].items():
                target=self.folder/'tools'/name
                for parent in reversed(target.parent.relative_to(self.folder/'tools').parents):
                    if str(parent)=='.':continue
                    path=self.folder/'tools'/parent
                    if not path.exists():d.mkdir_new(path)
                if not target.parent.exists():d.mkdir_new(target.parent)
                d.copy_new(HERE/name,target,expected,mode=0o600)
            d.copy_new(HERE/'files_manifest.json',self.folder/'tools/files_manifest.json',pin(self.package_raw),mode=0o600)
            d.new_json(self.folder/'runner_manifest.json',{'schema':'opt50_b24_finite500_runner_v1','target_total':TARGET,
                'boot_id':self.boot,'source_fix2':FIX2,'package_manifest':pin(self.package_raw),'guardian_plan':str(self.guardian),
                'layer_plan':str(self.layer),'previous_model_plan':str(PREVIOUS),'dataset':str(DATASET),
                'authorized_to_500':True,'uart_log_confirmed':True,'automatic_retry':False,'maximum_batch_images':50,
                'per_plan_expires_seconds':900,'created_monotonic':time.monotonic()})
            print(json.dumps({'runner_directory':str(self.folder),'target_total':TARGET}),flush=True)
            yield
        finally:
            if locked:fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)

    def record(self,stage,**details):
        state={'schema':'opt50_b24_finite500_runner_status_v1','stage':stage,'boot_id':self.boot,
               'heartbeat_monotonic':time.monotonic(),'target_total':TARGET,**details}
        self.a.durable.replace_json(self.folder/'runner_state.json',state)
        print(json.dumps(state,allow_nan=False),flush=True)

    def service(self,unit,*,deadline=None):
        need(re.fullmatch(r'opt50-[a-zA-Z0-9_-]+\.service',unit),'unsafe service name')
        names=('LoadState','ActiveState','SubState','MainPID','ExecMainStatus','Result')
        timeout=10 if deadline is None else min(10,deadline-time.monotonic())
        need(timeout>0,'read-only wait expired; no process was stopped: '+unit)
        result=subprocess.run(['/usr/bin/systemctl','show',unit,'--property='+','.join(names)],
            stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=timeout,check=False)
        need(len(result.stdout)<=32768 and len(result.stderr)<=8192,'oversized service observation')
        rows=[line.split('=',1) for line in result.stdout.splitlines() if '=' in line]
        need(len(rows)==len(set(k for k,_ in rows)) and set(k for k,_ in rows)==set(names),'service properties differ')
        value=dict(rows);need(result.returncode==0 or value['LoadState']=='not-found','service query refused')
        need(re.fullmatch(r'0|[1-9][0-9]*',value['MainPID']),'service PID differs');return value

    def no_other_active(self,allowed=(),*,deadline=None):
        self_group=Path('/proc/self/cgroup').read_text();own=re.fullmatch(r'0::/system.slice/(opt50-[A-Za-z0-9_-]+\.service)\n',self_group)
        excepted={self.guardian_unit,*(allowed or ())}
        if own:excepted.add(own[1])
        timeout=10 if deadline is None else min(10,deadline-time.monotonic())
        need(timeout>0,'read-only wait expired; no process was stopped')
        result=subprocess.run(['/usr/bin/systemctl','list-units','--all','--full','--plain','--no-legend','--no-pager','opt50-*'],
            stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=timeout,check=False)
        need(result.returncode==0 and len(result.stdout)<=256*1024,'active service inventory refused')
        for line in result.stdout.splitlines():
            if not line.strip():continue
            fields=line.split();need(len(fields)>=4 and re.fullmatch(r'opt50-[A-Za-z0-9_-]+\.service',fields[0]),'unknown OPT50 unit inventory')
            if fields[0] not in excepted and fields[2] in ('active','activating','deactivating'):
                observed=self.service(fields[0],deadline=deadline)
                need(observed['MainPID']=='0' and observed['SubState'] in ('exited','dead'),
                    'another OPT50 task is active: '+fields[0]+'; no concurrent submission')

    def wait_service(self,unit,optional=False,software=False):
        deadline=time.monotonic()+1200
        while True:
            need(time.monotonic()<deadline,'read-only wait expired; no process was stopped: '+unit)
            # The known pilot has its own dynamic inner unit. Observe its fixed
            # outer first; the complete busy inventory gates every later submit.
            if unit!=PILOT_UNIT:self.no_other_active((unit,),deadline=deadline)
            value=self.service(unit,deadline=deadline)
            if value['LoadState']=='not-found':
                need(optional,'required software verification service not found');return False
            if value['MainPID']=='0' and value['SubState'] in ('exited','dead','failed'):
                if not software:
                    need(value['Result']=='success' and value['ExecMainStatus']=='0' and value['SubState']=='exited',
                        'observed service did not complete successfully: '+unit)
                return True
            need(time.monotonic()<deadline,'read-only wait expired; no process was stopped: '+unit)
            self.record('WAITING_EXISTING_SERVICE_READ_ONLY',unit=unit,service=value)
            time.sleep(min(5,max(0,deadline-time.monotonic())))

    def wait_software(self):
        # Reentry changes no C++/model bytes. Prepare only a missing directory,
        # exactly once in this already-background runner; never rebuild or
        # overwrite partial output. Only the two native CPU tests execute.
        build=PUBLIC/'build_arm';receipt=build/'build_receipt.json'
        if build.exists():
            need(build.is_dir() and not build.is_symlink() and receipt.is_file() and not receipt.is_symlink(),
                 'existing ARM output is partial/linked; preserve it, no preparation retry or overwrite')
        else:
            need(not build.is_symlink(),'linked ARM output refused')
            self.no_other_active()
            argv=['/usr/bin/python3','-B',str(PUBLIC/'stage_arm.py')]
            self.a.durable.new_json(self.folder/'arm_preparation_intent.json',{
                'argv':argv,'source_tools_manifest':FIX2,'single_submission':True,
                'hardware_accessed':False,'native_owner_executed':False,'automatic_retry':False})
            self.record('ARM_SOURCE_IDENTICAL_PREPARATION_ONCE',hardware_accessed=False,native_owner_executed=False)
            result=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',check=False)
            for suffix,text in (('stdout',result.stdout),('stderr',result.stderr)):
                self.entry.new_bytes(self.a.c,self.a.durable,self.folder/('arm_preparation.'+suffix),text.encode())
            need(result.returncode==0,'ARM preparation failed or uncertain; no retry, rebuild or batch submission')
            need(len(result.stdout.encode())<=256*1024 and len(result.stderr.encode())<=65536 and
                 build.is_dir() and not build.is_symlink() and receipt.is_file() and not receipt.is_symlink(),
                 'ARM preparation did not publish a bounded complete receipt; preserve output')
        value=self.entry.verify_build(self.a.tools,self.a.runtime.MANIFEST_SHA)
        self.record('ARM_RECEIPT_VERIFIED_READ_ONLY',build_schema=value['schema'],
                    hardware_accessed=False,native_owner_executed=False)
        return value
    def wait_existing_pilot(self):return self.wait_service(PILOT_UNIT,optional=True)
    def wait_inner(self,plan):
        match=re.fullmatch(r'/var/lib/opt50-b24-model-v1/plan_([a-f0-9]{12})/plan\.json',str(plan))
        need(match is not None,'new batch plan namespace differs');return self.wait_service('opt50-b24-model-'+match[1]+'.service')

    def snapshot(self):
        self.no_other_active();a=self.a;c=a.c
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==self.boot,'boot changed')
        dataset=a.dataset.verify_dataset(DATASET,verify_payloads=False)
        with c.companion_locked():
            history=self.entry.previous_clear(c)
            with a.history_index.locked(a):
                value,raw=a.history_index.load(a);a.history_index.verify_known(value)
                n=value['images_count'];selection(n,None)
                need(set(value['samples'])=={str(i) for i in range(n)},'history is not a unique continuous committed prefix')
                need(value['dataset_manifest']==dataset['manifest_pin'],'dataset/index differs')
                need(all(b['stage']==PASS for b in value['batches'].values()),'partial/failure history blocks automatic continuation')
                need(all(s['accuracy']['bitwise_mismatches']==0 for s in value['samples'].values()),'nonzero numerical history mismatch')
                seen=set()
                for item in history:
                    p=c.read_json(item['plan'],private=True)
                    if p.get('schema')==self.entry.PLAN_SCHEMA:seen.add(str(item['plan']))
                need(seen==set(value['batches']),'history/index plan inventory differs')
                return {'prefix':n,'dataset_pin':value['dataset_manifest'],'boot_id':self.boot,
                        'index':a.history_index.snapshot(value,raw),'batches':value['batches']}

    def pilot(self,view):
        candidates=[];a=self.a
        for path in view['batches']:
            plan=a.c.read_json(path,private=True)
            if (plan.get('sample_count')==5 and plan.get('lifecycle_profile')=='resident-bounded-batch-v4' and
                plan.get('boot_id')==self.boot and plan.get('dataset_manifest')==view['dataset_pin'] and
                plan.get('tool_files',{}).get('manifest')==FIX2):candidates.append((plan['sample_start'],path))
        if not candidates:return None
        path=max(candidates)[1]
        plan,rows,_,_=self.entry.historical_records(a,Path(path),complete=True)
        need(len(rows)==5 and plan['sample_count']==5 and plan['boot_id']==self.boot and
             plan['tool_files']['manifest']==FIX2 and all(row['bitwise_mismatches']==0 for row in rows),
             'five-image fix2 pilot deep proof differs')
        return path

    def submit(self,start,count,pilot):
        need(type(start) is int and type(count) is int and 100<=start<TARGET and
             1<=count<=min(50,TARGET-start),'finite500 batch range exceeded')
        self.no_other_active();self.counter+=1
        need(self.guardian is not None and self.layer is not None,'explicit current-boot proofs required for submission')
        argv=['/usr/bin/python3','-B',str(PUBLIC/'run_once.py'),'--guardian-plan',str(self.guardian),'--layer-plan',str(self.layer),
            '--previous-model-plan',str(PREVIOUS),'--dataset',str(DATASET),'--start',str(start),'--count',str(count),
            '--uart-log-confirmed','--authorized-batch']
        if count>5:
            need(pilot is not None,'five-image current fix2 pilot required');argv+=['--pilot-plan',str(pilot)]
        self.a.durable.new_json(self.folder/('batch_%02d_intent.json'%self.counter),{'argv':argv,'start':start,'count':count,
            'boot_id':self.boot,'submitted_at_monotonic':time.monotonic(),'single_submission':True,'automatic_retry':False})
        result=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',check=False)
        for suffix,text in (('stdout',result.stdout),('stderr',result.stderr)):
            self.entry.new_bytes(self.a.c,self.a.durable,self.folder/('batch_%02d.%s'%(self.counter,suffix)),text.encode())
        need(result.returncode==0,'run_once failed or uncertain; no batch replay/renewal')
        need(len(result.stdout.encode())<=2*1024*1024 and len(result.stderr.encode())<=65536,'oversized run_once output')
        records=json_records(result.stdout);ready=[v for v in records if v.get('stage')=='NEW_MODEL_PLAN_READY_NOT_YET_SUBMITTED']
        need(len(ready)==1 and (ready[0].get('sample_start'),ready[0].get('sample_count'))==(start,count),'run_once selection proof differs')
        final=records[-1];wrapper=self.entry.module_checked('run_once.py',self.a.tools)
        need(wrapper.terminal(final)==0 and final.get('sample_start')==start and final.get('sample_count')==count,
             'run_once lacks exact PASS/closed proof')
        path=ready[0]['plan'];plan=self.a.c.read_json(path,private=True)
        need(plan.get('tool_files',{}).get('manifest')==FIX2 and plan.get('boot_id')==self.boot and
             (plan.get('sample_start'),plan.get('sample_count'))==(start,count),'new plan/source/boot differs')
        token=plan['token'];expected=re.compile(r'Running as unit: opt50-b24-model-'+token+r'\.service(?:; invocation ID: [a-f0-9]{32})?\.?')
        need(all(expected.fullmatch(line) for line in result.stderr.splitlines() if line.strip()),'unexpected run_once diagnostics')
        return {'plan':path}

    def report(self):
        path=self.folder/'tools/report500.py';expected=self.package['files']['report500.py']
        need(pin(read(path))==expected,'frozen final reporter changed')
        output=EXPORTS/('action_'+self.token)
        argv=[PYTHON,'-B',str(path),'--out',str(output),'--evidence-tar']
        result=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',check=False)
        self.entry.new_bytes(self.a.c,self.a.durable,self.folder/'report.stdout',result.stdout.encode())
        self.entry.new_bytes(self.a.c,self.a.durable,self.folder/'report.stderr',result.stderr.encode())
        need(result.returncode==0,'final independent500 report failed; saved computation evidence retained')
        return {'out':str(output),'stdout':result.stdout}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorized-to-500',action='store_true');parser.add_argument('--uart-log-confirmed',action='store_true')
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--guardian-plan',type=Path,required=True);parser.add_argument('--layer-plan',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(run(args.authorized_to_500,args.uart_log_confirmed,args.manifest_sha256,
            guardian_plan=args.guardian_plan,layer_plan=args.layer_plan),indent=2));return 0
    except Exception as error:
        print('FINITE500_STOPPED_NO_RETRY: '+type(error).__name__+': '+str(error),file=sys.stderr,flush=True);return 2


if __name__=='__main__':raise SystemExit(main())
