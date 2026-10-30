"""Retire the exact proven pre-Native v4 constructor failure, without signals.

No device access, owner invocation, source/plan edits, index update, or replay.
The old read-only wrapper observes the refusal and exits by itself.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
RECORDS = Path('/var/lib/opt50-dense-model-v1')
TARGET = RECORDS/'plan_b06c1e69f409/plan.json'
V4 = {'bytes':21974,'sha256':'58580186e4540eeb33c97c411a2d517793e9f752d3fa5e58302cfab80e7a1407'}
SCHEMA = 'opt50_dense_resident_startup_refusal_fix1'
STAGE = 'REFUSED_BATCH_BEFORE_OWNER'
ABSENT = ('execution','samples','job.kv','input.npy','current_sample.json')


def need(value,message):
    if not value: raise ValueError(message)


def identity(raw):
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def lexists(path):
    try: path.lstat()
    except FileNotFoundError: return False
    return True


def absent_native(root):
    need(all(not lexists(root/name) for name in ABSENT),
         'Native/worker execution artifacts exist; no unstarted retirement is allowed')


def binary(name):
    path = Path('/usr/bin')/name
    resolved = path.resolve(strict=True); info = resolved.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
         'root-owned read-only service diagnostic binary required')
    return str(path)


def service(unit):
    result = subprocess.run([binary('systemctl'),'show',unit,'--property=Id,LoadState,ActiveState,SubState,MainPID,ExecMainStatus,Result'],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20,check=False)
    need(result.returncode == 0 and len(result.stdout) <= 32768 and not result.stderr,
         'service observation failed')
    pairs = [line.split('=',1) for line in result.stdout.splitlines() if '=' in line]
    need(len(pairs) == len(dict(pairs)),'duplicate service fields')
    return dict(pairs)


def failure_event(unit,boot,root):
    result = subprocess.run([binary('journalctl'),'-u',unit,'-n','100','--no-pager','-o','json'],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20,check=False)
    need(result.returncode == 0 and len(result.stdout.encode()) <= 1024*1024 and
         len(result.stderr.encode()) <= 65536,'bounded journal observation failed')
    # An archived truncated-journal warning may be on stderr. Only the exact
    # boot-bound Python refusal message below is admitted as failure evidence.
    expected = "FileNotFoundError: [Errno 2] No such file or directory: "+repr(str(root/'batch_events'))
    matches = []
    for line in result.stdout.splitlines():
        event = json.loads(line)
        if event.get('_BOOT_ID') != boot.replace('-',''): continue
        if event.get('_SYSTEMD_UNIT') != unit: continue
        try: message = json.loads(event.get('MESSAGE',''))
        except (TypeError,ValueError): continue
        if message == {'status':'B24_RESIDENT_REFUSED_NO_RETRY','error':expected}:
            matches.append({'boot_id':boot,'unit':unit,'message':message,'event_sha256':identity(line.encode())['sha256'],
                            'pid':event.get('_PID'),'cursor':event.get('__CURSOR')})
    need(len(matches) == 1,'exact boot-bound constructor failure was not observed once')
    return matches[0]


def source(c,root):
    raw = c.read_file(root/'tools/tools_manifest.json',private=True)
    need(identity(raw) == V4,'only the fixed original v4 release may be retired here')
    tools = c.decode_json(raw)
    need(len(tools['files']) == 140,'original v4 inventory differs')
    for name,pin in tools['files'].items():
        parts = name.split('/')
        need(name and not any(p in ('','.','..') for p in parts) and not any(ch in name for ch in '\\:\0\r\n'),
             'unsafe original inventory path')
        path = root/name if parts[0] == 'model' else root/'tools'/name
        need(identity(c.read_file(path,private=True,limit=64*1024*1024)) == pin,
             'original root-copied source changed: '+name)
    return identity(raw)


def current_boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def snapshot(c,path):
    path = Path(path).absolute(); need(path == TARGET,'this recovery is restricted to the observed b06c plan')
    root = path.parent
    for folder in (RECORDS,root,root/'tools',root/'model'): c.private_dir(folder)
    raw = c.read_file(path,private=True,limit=65536); p = c.decode_json(raw)
    need(p.get('token') == root.name[5:] and p.get('plan_path') == str(path) and
         p.get('unit') == 'opt50-dense-model-'+p['token'] and p.get('tool_files',{}).get('manifest') == V4 and
         p.get('lifecycle_profile') == 'resident-bounded-batch-v4' and
         p.get('sample_start') == 45 and p.get('sample_count') == 5 and p.get('total_START_budget') == 180,
         'observed failed plan identity differs')
    boot = current_boot()
    need(boot == p['boot_id'],'recovery requires the same observed boot')
    absent_native(root)
    need(not lexists(root/'batch_events') and not lexists(root/'batch_started.json') and
         not lexists(root/'batch_completion.json'),'constructor failure directory/consumption evidence differs')
    src = source(c,root); observed = service(p['unit']+'.service')
    fixed = {'Id':p['unit']+'.service','LoadState':'loaded','ActiveState':'failed','SubState':'failed',
             'MainPID':'0','ExecMainStatus':'2','Result':'exit-code'}
    need(observed == fixed,'inner service is not the exact exited-before-constructor instance')
    procs = Path('/sys/fs/cgroup/system.slice')/(p['unit']+'.service')/'cgroup.procs'
    need(not procs.exists() or procs.read_text().strip() == '','inner service still has live tasks')
    event = failure_event(p['unit']+'.service',p['boot_id'],root)
    return p,{'plan_path':str(path),'plan_pin':identity(raw),'source_tools_manifest':src,
              'unit_properties':observed,'journal_failure':event,'native_artifacts_absent':list(ABSENT),
              'no_batch_events_directory':True,'no_job_or_execution_directory':True}


def validate_refusal(c,root,current_manifest):
    """Admission-only check of a documented no-Native attempt; no image commit."""
    root = Path(root); c.private_dir(root)
    plan_raw = c.read_file(root/'plan.json',private=True,limit=65536); p = c.decode_json(plan_raw)
    need(p.get('schema') == 'opt50_dense_acceptance_batch_plan_v1' and
         p.get('plan_path') == str(root/'plan.json') and p.get('token') == root.name[5:] and
         p.get('unit') == 'opt50-dense-model-'+p['token'] and
         p.get('lifecycle_profile') == 'resident-bounded-batch-v4', 'refused plan namespace differs')
    proof = c.read_json(root/'batch_startup_refusal.json',private=True)
    need(proof.get('schema') == SCHEMA and proof.get('plan_path') == str(root/'plan.json') and
         proof.get('plan_pin') == identity(plan_raw) and proof.get('plan_token') == p.get('token') and
         proof.get('boot_id') == p.get('boot_id') and proof.get('unit') == p.get('unit') and
         proof.get('source_tools_manifest') == p.get('tool_files',{}).get('manifest') and
         proof.get('source_tools_manifest') in (V4,current_manifest), 'startup refusal source/plan binding differs')
    for key in ('native_owner_attempted','native_owner_executed','worker_attempted','hardware_accessed'):
        need(proof.get(key) is False,'startup refusal cannot exclude a Native attempt')
    need(type(proof.get('START_commands_issued_by_startup')) is int and
         proof['START_commands_issued_by_startup'] == 0,'startup refusal START evidence differs')
    need(proof.get('phase') in {'ORIGINAL_V4_JOURNAL_CONSTRUCTOR','PERSISTING_BATCH_CONSUMPTION_BEFORE_DIRECTORIES',
         'CREATING_BATCH_EVENTS_BEFORE_REAL_JOURNAL','CONSTRUCTING_COORDINATOR_BEFORE_EXECUTION'},
         'unknown startup refusal phase; preserve for review')
    absent_native(root)
    state = c.read_json(root/'batch_status.json',private=True)
    need(state.get('stage') == STAGE and state.get('owner_pid') is None and state.get('child_pid') is None and
         state.get('boot_id') == p['boot_id'] and state.get('plan_token') == p['token'] and
         state.get('unit') == p['unit'] and state.get('committed_samples') == 0 and
         type(state.get('committed_samples')) is int and type(state.get('completed_PL_calls')) is int and
         state.get('completed_PL_calls') == 0 and 'completion' not in state and
         not lexists(root/'batch_completion.json'), 'startup refusal is not an uncommitted no-owner result')
    need(identity(c.read_file(root/'tools/tools_manifest.json',private=True)) == proof['source_tools_manifest'],
         'refused source inventory changed')
    # This index does not accept zero-image pseudo-closed batches. A failed
    # attempt is excluded only while its bound refusal and absence evidence hold.
    return {'plan':str(root/'plan.json'),'scope':'Confirmed no Native attempt; zero images, no device-close claim'}


def retire(a,check_only=False):
    c,d = a.c,a.durable; c.root_linux(); root = TARGET.parent
    with c.companion_locked():
        if lexists(root/'batch_startup_refusal.json'):
            result = validate_refusal(c,root,a.tools['manifest'])
            return {'status':'UNSTARTED_ATTEMPT_ALREADY_RECORDED_NO_CHANGES',**result,'hardware_accessed':False}
        need(not lexists(root/'batch_status.json'),'saved status already exists; preserve and review')
        p,evidence = snapshot(c,TARGET)
        if check_only:
            return {'status':'UNSTARTED_REVIEW_PASS_NO_STATUS_WRITTEN','evidence':evidence,'hardware_accessed':False}
        reason = evidence['journal_failure']['message']['error']
        proof = {'schema':SCHEMA,'plan_path':str(TARGET),'plan_pin':evidence['plan_pin'],'source_tools_manifest':V4,
            'plan_token':p['token'],'boot_id':p['boot_id'],'unit':p['unit'],'phase':'ORIGINAL_V4_JOURNAL_CONSTRUCTOR',
            'native_owner_attempted':False,'native_owner_executed':False,'worker_attempted':False,'hardware_accessed':False,
            'START_commands_issued_by_startup':0,'reason':reason,'automatic_retry':False,'evidence':evidence}
        d.new_json(root/'batch_startup_refusal.json',proof)
        state = {'schema':'opt50_dense_acceptance_batch_status_v1','stage':STAGE,'reason':reason,
            'boot_id':p['boot_id'],'plan_token':p['token'],'unit':p['unit'],'guardian_pid':p['guardian_pid'],
            'measurement_profile':p['measurement_profile'],'lifecycle_profile':p['lifecycle_profile'],
            'sample_start':45,'sample_count':5,'total_START_budget':180,'committed_samples':0,'completed_PL_calls':0,
            'verified_pending_samples':0,'owner_pid':None,'child_pid':None,'saved_status_only':True,
            'no_native_attempt_review':identity(c.read_file(root/'batch_startup_refusal.json',private=True))}
        d.new_json(root/'batch_status.json',state)
        result = validate_refusal(c,root,a.tools['manifest'])
    return {'status':'CONFIRMED_UNSTARTED_ATTEMPT_RECORDED_NO_COMPUTE_NO_SIGNALS',**result,
            'source_plan_modified':False,'index_modified':False,'hardware_accessed':False,
            'next':'Old wrapper reads REFUSED and exits itself; do not reuse this plan'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check-only',action='store_true')
    group.add_argument('--retire-confirmed-unstarted',action='store_true')
    args = parser.parse_args()
    import run_model
    result = retire(run_model.api(),check_only=args.check_only)
    print(json.dumps(result,indent=2,allow_nan=False),flush=True)


if __name__ == '__main__':
    try: main()
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        raise SystemExit('UNSTARTED_REVIEW_REFUSED_NO_CHANGES_OR_REPLAY: '+type(error).__name__+': '+str(error))
