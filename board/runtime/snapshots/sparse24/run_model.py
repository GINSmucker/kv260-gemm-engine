"""Bounded resident CPU4/PL4 acceptance. Models and resident firmware are unchanged."""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
import types
sys.dont_write_bytecode = True
import legacy_model as legacy
from legacy_model import (need, identity, read, decode_json, ordinary, file_inode,
    verify_tools, module_checked, interpreter, system_properties, layer_proof,
    previous_model_proof, previous_clear, copy_tree, copy_dataset, selected_samples,
    new_bytes, tree_usage, read_events, Admin, PL, SPARSE, RECORDS, PYTHON, PROFILE,
    SOURCE_MODEL, METADATA, PLAN_SCHEMA, PASS, BATCH_FAIL)
HERE = Path(__file__).absolute().parent
AUTH = '--authorized-resident-batch'
LIFECYCLE = 'resident-bounded-batch-v4'
SCOPE = 'SEALED_B24_RESIDENT_BATCH_ONE_OWNER_BOUNDED_GRAPHS_36_STARTS_PER_IMAGE'
PROPERTIES = ('Type=exec','ExitType=cgroup','KillMode=process','SendSIGKILL=no',
              'TimeoutStopSec=infinity','RuntimeMaxSec=infinity','Restart=no',
              'RemainAfterExit=yes','OOMPolicy=continue')
stop_requested = False
ZERO_GRAPH_REVIEW_SHA='1d0a3f97781753d769cb911efb5e22cdab6fb629c1f9d33d0f72415f337da2be'



def module_checked(name,tools,folder=None,private=False):
    folder=HERE if folder is None else Path(folder)
    name='/'.join(legacy.safe_relative(name))
    raw=read(folder/name,private=private)
    need(identity(raw) == tools['files'][name],'source changed before module import: '+name)
    module=types.ModuleType('_b24_resident_'+Path(name).stem)
    module.__file__=str(folder/name)
    exec(compile(raw,module.__file__,'exec'),module.__dict__)
    return module


def historical_records(a, path, *, complete=False):
    """Audit the exact release closure, then use its read-only record verifier."""
    entry, root, source = a.compat.load_closed_entry(path, a.tools, access=a.c)
    rows = entry.read_completed_records(path, allow_partial=not complete)
    plan = a.c.read_json(root/'plan.json', private=True)
    need(plan.get('tool_files', {}).get('manifest') == source['tools_manifest'],
         'historical plan/release identity differs')
    return plan, rows, root, source

def review_pilot(a, pilot_path, held, dataset_pin, start, count):
    """An old release may prove a pilot; an old boot may not prove residency."""
    pilot_root = Path(pilot_path).absolute().parent
    need(pilot_root.parent == RECORDS and re.fullmatch('plan_[a-f0-9]{12}', pilot_root.name), 'pilot namespace differs')
    plan, rows, _, _ = historical_records(a, pilot_root/'plan.json', complete=True)
    need(plan.get('schema') == PLAN_SCHEMA and plan.get('measurement_profile') == PROFILE and
         plan.get('sample_count') == 5 and plan.get('dataset_manifest') == dataset_pin and
         plan.get('boot_id') == held['boot_id'], 'pilot identity/source/dataset/boot differs')
    need(all(x['bitwise_mismatches'] == 0 for x in rows), 'pilot was not bitwise exact')
    done = a.c.read_json(pilot_root/'batch_completion.json', private=True)
    need(done.get('stage') == PASS and done.get('closed') is True and done.get('committed_samples') == 5,
         'five-image pilot has not completed safely')
    need(not {x['ordinal'] for x in rows} & set(range(start, start+count)),
         'pilot images cannot be replayed in the next batch')
    return dict(tree_usage(pilot_root/'samples'), count=5)

def prevent_replay(a, history, dataset_pin, start, count):
    requested = set(range(start, start+count))
    for prior in history:
        previous_root = Path(prior['plan']).parent
        previous = a.c.read_json(previous_root/'plan.json', private=True)
        # The validated corpus has fixed labels/JPEG/input identities. A new
        # transport archive changes manifest provenance, not sample identity.
        if previous.get('schema') == PLAN_SCHEMA:
            _, rows, _, _ = historical_records(a, previous_root/'plan.json')
            need(not requested & {row['ordinal'] for row in rows},
                 'a selected image already has a closed Native execution; do not replay committed samples')

def review_incident_zero_graph(c,folder):
    """Skip one incident only after the frozen read-only closure audit.

    The original retained batch is never rewritten or presented as a completed
    image batch. The pinned auditor re-reads its full original evidence and
    fresh process/cgroup/guardian observations; this function never calls its
    main, finish or Runtime.stop paths.
    """
    need(folder==RECORDS/'plan_c4b539de5ba7','unknown zero-graph incident')
    private=HERE.name=='tools'
    raw=read(HERE/'tools_manifest.json',private=private)
    tools={'manifest':identity(raw),'files':decode_json(raw)['files']}
    helper=read(HERE/'zero_graph_review.py',private=private)
    need(identity(helper)['sha256']==ZERO_GRAPH_REVIEW_SHA and
         identity(helper)==tools['files']['zero_graph_review.py'],'fixed zero-graph reviewer differs')
    reviewer=module_checked('zero_graph_review.py',tools,HERE,private=private)
    need(reviewer.PLAN==folder/'plan.json','zero-graph reviewer incident differs')
    result=reviewer.review_closed()
    need(type(result) is dict and result.get('owner_contacted_by_review') is False and
         result.get('original_plan_status_completion_written_by_tool') is False and
         result.get('history_index_modified') is False and result.get('owner_exit_code_observed') is False and
         ((result.get('schema')=='opt50_b24_zero_graph_close_receipt_v1' and
           result.get('status')=='CLOSED_ZERO_GRAPHS_REVIEWED_READ_ONLY' and
           result.get('calls')==result.get('graphs')==0 and result.get('max_graphs')==50) or
          (result.get('schema')=='opt50_b24_zero_graph_closure_review_v1' and
           result.get('status')=='VERIFIED_ZERO_GRAPH_CLOSE_FROM_ORIGINAL_EVIDENCE' and
           result.get('finish_success_claimed') is False)), 'independent zero-graph closure review refused')
    return result


def previous_clear(c, exclude=None):
    if not RECORDS.exists():
        return []
    c.private_dir(RECORDS)
    reports = []
    folders = sorted(RECORDS.iterdir())
    need(len(folders) <= 1000, 'too many full-model plans')
    for folder in folders:
        need(re.fullmatch('plan_[a-f0-9]{12}', folder.name), 'unrecognized full-model record')
        c.private_dir(folder)
        if exclude is not None and folder == Path(exclude).parent:
            continue
        if folder.name in ('plan_c4b539de5ba7','plan_8e7d10643b3b'):
            raw=read(HERE/'tools_manifest.json',private=HERE.name=='tools');listing=decode_json(raw)
            reviewer=module_checked('reboot_review.py',{'manifest':identity(raw),'files':listing['files']},HERE,private=HERE.name=='tools')
            if folder.name=='plan_c4b539de5ba7': reviewer.zero_history(c,folder)
            else: reviewer.refused_before_owner(c,folder)
            continue
        if (folder/'batch_startup_refusal.json').exists():
            listing=decode_json(read(HERE/'tools_manifest.json',private=HERE.name=='tools'))
            pin=identity(read(HERE/'tools_manifest.json',private=HERE.name=='tools'))
            review=module_checked('before_owner.py',{'manifest':pin,'files':listing['files']},HERE,private=HERE.name=='tools')
            review.validate_refusal(c,folder,pin)
            continue
        need(folder.name != 'plan_b06c1e69f409','observed b06c attempt requires explicit before_owner review before a new plan')
        if (folder/'batch_status.json').exists():
            state = c.read_json(folder/'batch_status.json', private=True)
            need(state.get('stage') in (PASS, BATCH_FAIL, 'REFUSED_BATCH_BEFORE_OWNER',
                                       'PARTIAL_CLOSED_PLAN_EXPIRED', 'PARTIAL_CLOSED_OPERATOR_STOP') and
                 state.get('owner_pid') is None and state.get('child_pid') is None,
                 'previous batch has an unresolved image; no new hardware access')
            done = c.read_json(folder/'batch_completion.json', private=True)
            need(done.get('closed') is True and done.get('stage') == state['stage'], 'previous batch closure differs')
            reports.append({'plan': str(folder/'plan.json'), 'stage': state['stage']})
            continue
        if not (folder/'execution').exists():
            continue
        state = c.read_json(folder/'execution/status.json', private=True)
        need(state.get('stage') in (legacy.IMAGE_PASS, 'PASS_B24_ALIGNED_MODEL_36_STARTS_BITWISE_SINGLE_INPUT',
                                   'PASS_B24_FULL_MODEL_36_STARTS_REAL_INPUT_BITWISE_SINGLE_IMAGE',
                                   legacy.FAIL_NUMERICAL, legacy.REFUSED) and
             state.get('owner_pid') is None and state.get('child_pid') is None,
             'previous model execution unresolved; preserve for review')
        if state['stage'] != legacy.REFUSED:
            complete = c.read_json(folder/'execution/completion.json', private=True)
            need(complete.get('owner_exit_code') == 0 and complete.get('native', {}).get('device_closed') is True,
                 'previous model resource closure not proved')
        reports.append({'plan': str(folder/'plan.json'), 'stage': state['stage']})
    return reports

def api(folder=HERE, private=False):
    a = legacy.api(folder, private)
    a.resident_proof = module_checked('resident_proof.py',a.tools,folder,private)
    return a


def verify_build(tools, manifest):
    builder = module_checked('build_arm.py',tools)
    value = builder.verify_build(HERE/'build_arm',root=HERE)
    need(value.get('schema') in ('opt50_b24_resident_arm_build_v4','opt50_b24_resident_arm_reuse_fix1','opt50_b24_resident_arm_reuse_fix2') and
         value.get('lifecycle_profile') == LIFECYCLE and value.get('model_manifest_sha256') == manifest and
         value.get('source_release_manifest') == tools['manifest'], 'new resident ARM compilation required')
    path = Path('/usr/bin/g++-13').resolve(strict=True); info = path.stat(); compiler=value['compiler']
    need(info.st_uid == 0 and not info.st_mode & 0o022 and compiler['path'] == str(path) and
         {k:compiler[k] for k in ('bytes','sha256')} == identity(read(path)), 'compiler changed')
    return value


def check(guardian_path,layer_path,metadata,uart,*,previous_path,dataset,start=0,count=5,pilot_path=None):
    a=api(); c,d=a.c,a.durable; c.root_linux(); need(uart,'UART confirmation required')
    a.resident_proof.budget(count)
    need(type(start) is int and 0 <= start < 1800 and start+count <= 1800,'dataset range differs')
    verified=a.dataset.verify_dataset(Path(dataset),verify_payloads=True)
    bundle=a.runtime.verify_bundle(HERE/'model'); manifest=bundle['manifest']
    build=verify_build(a.tools,a.runtime.MANIFEST_SHA); python=interpreter(c)
    c.configure(guardian_path)
    with c.companion_locked():
        history=previous_clear(c); held=c.held_review()
        index=a.history_index.admit(a,history,verified['manifest_pin'],start,count)
        prior_layer=layer_proof(c,layer_path,held); prior_model=previous_model_proof(c,previous_path,held)
        if count > 5:
            need(pilot_path is not None,'five-image resident pilot required before increasing count')
            pp,rows,_,_=legacy.historical_records(a,pilot_path,complete=True)
            need(pp.get('lifecycle_profile') == LIFECYCLE and pp['sample_count'] == 5 and
                 pp['boot_id'] == held['boot_id'] and pp['dataset_manifest'] == verified['manifest_pin'] and
                 all(row['bitwise_mismatches'] == 0 for row in rows),'resident pilot differs')
        old=c.read_json(c.OLD_PLAN,private=True)
        metadata=Path(metadata or old['artifacts']['source_paths']['metadata'])
        need(identity(read(metadata)) == METADATA,'metadata identity differs')
        required=sum(x['bytes'] for x in a.tools['files'].values())+count*8*1024*1024+512*1024*1024
        fs=os.statvfs(RECORDS if RECORDS.exists() else RECORDS.parent)
        need(fs.f_bavail*fs.f_frsize >= required and fs.f_favail >= count*256+10000,'insufficient storage/inodes')
        if not RECORDS.exists(): d.mkdir_new(RECORDS)
        token=secrets.token_hex(6); root=RECORDS/('plan_'+token); d.mkdir_new(root)
        copy_tree(c,d,a.tools,root); copy_dataset(a,verified,root,start,count)
        for src,dst,mode in (('opt50_model_owner','native_owner',0o700),('libopt50_model.so','plugin.so',0o600)):
            d.copy_new(HERE/'build_arm'/src,root/dst,build['binaries'][src],mode=mode)
        d.copy_new(HERE/'build_arm/build_receipt.json',root/'build_receipt.json',identity(read(HERE/'build_arm/build_receipt.json')))
        d.copy_new(metadata,root/'metadata.xclbin',METADATA); new_bytes(c,d,root/'xrt.ini',b'')
        g=held['guardian']; ns=time.monotonic_ns()
        plan={'schema':PLAN_SCHEMA,'token':token,'unit':'opt50-b24-model-'+token,'plan_path':str(root/'plan.json'),
            'boot_id':g['boot_id'],'created_monotonic':ns/1e9,'created_monotonic_ns':ns,'expires_seconds':900,
            'expiry_scope':'before each resident graph BEGIN; never renewed','lifecycle_profile':LIFECYCLE,
            'scope':SCOPE,'resident_profile':LIFECYCLE,'measurement_profile':PROFILE,'sample_start':start,'sample_count':count,'owner_graph_count':count,
            'total_START_budget':36*count,'expected_PL_starts':36,'expected_Dense_calls':12,'expected_Sparse_calls':24,
            'run_id':1,'budget_scope':'36 per graph; bounded total for one resident owner',
            'uart_logging_operator_confirmed':True,'old_board_lock_transferred':False,
            'dataset_manifest':verified['manifest_pin'],'dataset_root':str(root/'dataset'),'model_root':str(root/'model'),
            'plugin':str(root/'plugin.so'),'socket':str(root/'execution/runtime.sock'),'execution':str(root/'execution'),
            'python':PYTHON,'interpreter':python,'guardian_plan':str(c.OLD_PLAN),
            'guardian_plan_pin':identity(c.read_file(c.OLD_PLAN,private=True)),'guardian_plan_inode':file_inode(c.OLD_PLAN),
            'guardian_pid':g['pid'],'guardian_start_ticks':g['start_ticks'],'held_review':held,
            'layer_proof':prior_layer,'previous_model_proof':prior_model,'pilot_plan':str(pilot_path) if pilot_path else None,
            'history_index':index,'tool_files':a.tools,'board_build':build,'source_model_sha256':SOURCE_MODEL,
            'model_manifest_sha256':a.runtime.MANIFEST_SHA,'model_manifest':identity(read(HERE/'model/manifest.json')),
            'logical_weights_sha256':manifest['logical_weights_sha256'],'reference_sha256':manifest['reference_sha256'],
            'custom_graph_sha256':manifest['custom_graph_sha256']}
        need(len(json.dumps(plan).encode()) <= 65536,'native plan exceeds bound'); d.new_json(root/'plan.json',plan)
    run=['sudo','systemd-run','--unit='+plan['unit'],*['--property='+x for x in PROPERTIES],
         '/usr/bin/python3','-B',str(root/'tools/run_model.py'),'run','--plan',plan['plan_path'],AUTH]
    status=['sudo','/usr/bin/python3','-B',str(root/'tools/run_model.py'),'status','--plan',plan['plan_path'],'--summary']
    return {'status':'B24_RESIDENT_PLAN_CREATED_NO_HARDWARE_ACCESS','plan':plan['plan_path'],'boot_id':plan['boot_id'],
        'sample_start':start,'sample_count':count,'total_START_budget':36*count,'expires_seconds':900,
        'lifecycle_profile':LIFECYCLE,'measurement_profile':PROFILE,'ORT_intra_threads':4,'ORT_inter_threads':1,
        'hardware_executed':False,'next_requires_explicit_batch_authorization':shlex.join(run),'status_command':shlex.join(status)}


def load_plan(a,path,fresh=False):
    path=Path(path).absolute(); root=path.parent; c=a.c
    need(path.name == 'plan.json' and root.parent == RECORDS and re.fullmatch('plan_[a-f0-9]{12}',root.name),'canonical plan required')
    for folder in (RECORDS,root,root/'tools',root/'model',root/'dataset'): c.private_dir(folder)
    p=c.read_json(path,private=True); n=p.get('sample_count'); a.resident_proof.budget(n)
    fixed={'schema':PLAN_SCHEMA,'plan_path':str(path),'token':root.name[5:],'unit':'opt50-b24-model-'+root.name[5:],
        'lifecycle_profile':LIFECYCLE,'scope':SCOPE,'resident_profile':LIFECYCLE,'measurement_profile':PROFILE,'expires_seconds':900,
        'source_model_sha256':SOURCE_MODEL,'model_manifest_sha256':a.runtime.MANIFEST_SHA,'tool_files':a.tools,
        'expected_PL_starts':36,'expected_Dense_calls':12,'expected_Sparse_calls':24,'owner_graph_count':n,
        'total_START_budget':36*n,'old_board_lock_transferred':False,'uart_logging_operator_confirmed':True,
        'model_root':str(root/'model'),'plugin':str(root/'plugin.so'),'dataset_root':str(root/'dataset'),
        'execution':str(root/'execution'),'socket':str(root/'execution/runtime.sock'),'python':PYTHON}
    need(all(type(p.get(k)) is type(v) and p[k] == v for k,v in fixed.items()),'resident sealed identity differs')
    need(type(p.get('created_monotonic_ns')) is int and p['created_monotonic_ns'] > 0 and
         type(p.get('created_monotonic')) in (int,float) and
         abs(p['created_monotonic']-p['created_monotonic_ns']/1e9) < 1e-6,'monotonic gate differs')
    for name,pin in (('native_owner',p['board_build']['binaries']['opt50_model_owner']),
                     ('plugin.so',p['board_build']['binaries']['libopt50_model.so']),('metadata.xclbin',METADATA)):
        need(identity(c.read_file(root/name,private=True)) == pin,'resident artifact changed: '+name)
    need(c.read_file(root/'xrt.ini',private=True) == b'','unexpected XRT configuration')
    need(c.read_json(root/'build_receipt.json',private=True) == p['board_build'],'private compilation receipt differs')
    bundle=a.runtime.verify_bundle(root/'model')
    for k in ('logical_weights_sha256','reference_sha256','custom_graph_sha256'):
        need(p.get(k) == bundle['manifest'][k],'resident numerical identity differs')
    a.checked_bundle=bundle; selected_samples(a,p)
    if fresh:
        a.resident_proof.within_window(p,time.monotonic_ns())
        need(c._proc_read(Path('/proc/sys/kernel/random/boot_id'),128).decode().strip() == p['boot_id'],'different boot')
    return p


def worker_context(path):
    a=api(private=True); p=load_plan(a,path,fresh=True); root=Path(path).parent
    # Compatible with compiled private modules that are not registered in sys.modules.
    entry=types.SimpleNamespace(identity=identity,new_bytes=new_bytes,need=need,PROFILE=PROFILE)
    return types.SimpleNamespace(a=a,c=a.c,durable=a.durable,runtime=a.runtime,entry=entry,plan=p,
        root=root,execution=root/'execution',samples=selected_samples(a,p),bundle=a.checked_bundle)


class StaticSeal:
    def __init__(self,files):
        self.files={}
        for path,pin in files:
            before=self.key(path); need(identity(read(path,private=True,limit=64*1024*1024)) == pin,'static pin differs')
            need(before == self.key(path),'static changed while sealing'); self.files[path]=before
    @staticmethod
    def key(path):
        s=ordinary(path).lstat()
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o077 and s.st_nlink == 1,'private static file required')
        return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid,s.st_gid,s.st_nlink)
    def boundary(self):
        need(all(self.key(p) == stamp for p,stamp in self.files.items()),'static source/model changed')


def validate_parent(c,p,lock):
    # The existing guard accepts the explicitly selected new argv only in this module.
    actual=system_properties(c,p['guardian_plan'],p['unit']+'.service',(*c.SERVICE,'MainPID','ActiveState','SubState'))
    need(all(actual.get(k) == v for k,v in c.SERVICE.items()) and actual.get('MainPID') == str(os.getpid()) and
         actual.get('ActiveState') == 'active' and actual.get('SubState') in ('start','running'),'systemd guard differs')
    command=['/usr/bin/python3','-B',str(HERE/'run_model.py'),'run','--plan',p['plan_path'],AUTH]
    need(c._proc_read(Path('/proc/self/cmdline')) == b''.join(x.encode()+bytes([0]) for x in command) and
         c._proc_read(Path('/proc/self/cgroup')) == ('0::/system.slice/'+p['unit']+'.service'+chr(10)).encode(),'parent argv/cgroup differs')
    named,opened=c._lock_info(),os.fstat(lock)
    need((named.st_dev,named.st_ino) == (opened.st_dev,opened.st_ino),'companion FD changed')
    c.parse_flock(c._proc_read(Path('/proc/locks'),4*1024*1024),os.getpid(),
        (os.major(opened.st_dev),os.minor(opened.st_dev)),opened.st_ino)


def job_bytes(p,manifest,pid):
    root=Path(p['plan_path']).parent
    fields={'schema':'opt50_b24_resident_native_job_v4','boot_id':p['boot_id'],'plan_token':p['token'],
        'expected_parent_pid':str(pid),'companion_lock':'/var/lib/opt50-first-access-fd96539d-v1/companion.lock',
        'model_root':str(root/'model'),'weights':str(root/'model/weights.bin'),'weights_sha256':manifest['logical_weights_sha256'],
        'data':str(root/'model/pl_w_data.bin'),'data_sha256':manifest['pl_data_sha256'],
        'meta':str(root/'model/pl_w_meta.bin'),'meta_sha256':manifest['pl_meta_sha256'],
        'reference':str(root/'model/reference.onnx'),'reference_sha256':manifest['reference_sha256'],
        'metadata':str(root/'metadata.xclbin'),'metadata_sha256':METADATA['sha256'],'run_id':'1','expected_PL_starts':'36',
        'source_model_sha256':SOURCE_MODEL,'sample_count':str(p['sample_count']),
        'total_START_budget':str(p['total_START_budget']),'created_monotonic_ns':str(p['created_monotonic_ns'])}
    need(len(fields) == 22 and all(not any(ord(c) in (0,10,13,61) for c in v) for v in fields.values()),'invalid native job')
    return ''.join(k+'='+v+chr(10) for k,v in fields.items()).encode()


def accuracy_record(a,p,sample,worker,closed):
    need(closed['device_closed'] is True and closed['owner_exit_code'] == 0,'real owner close missing')
    value=dict(worker['accuracy'],schema='opt50_b24_accuracy_record_v1',ordinal=sample['ordinal'],id=sample['id'],
        image_sha256=sample['image']['sha256'],input_sha256=sample['input']['sha256'],CPU_reference_ms=worker['CPU_reference_ms'],
        inference_ms=worker['runner_run_ms'],completed_PL_calls=36,Dense_calls=12,Sparse_calls=24,owner_exit_code=0,
        device_closed=True,native_END_verified=True,postrun_buffer_checks_verified=True,worker_runner_calls=1,
        hot_path_disk_events=0,graph_owner_sync_calls=0,lifecycle_profile=LIFECYCLE,per_image_device_close=False,
        owner_close_scope='Whole batch closed after all graph INFO checks',plan_token=p['token'],run_id=worker['run_id'])
    return a.metrics.validate_record(value,sample)


def initial_state(p):
    return {'schema':'opt50_b24_acceptance_batch_status_v1','boot_id':p['boot_id'],
        'plan_token':p['token'],'unit':p['unit'],'guardian_pid':p['guardian_pid'],'measurement_profile':PROFILE,
        'lifecycle_profile':LIFECYCLE,'sample_start':p['sample_start'],'sample_count':p['sample_count'],
        'total_START_budget':p['total_START_budget'],'committed_samples':0,'completed_PL_calls':0,
        'verified_pending_samples':0,'owner_pid':None,'child_pid':None}


def startup_refusal(a,p,phase,error):
    """Record a known pre-owner refusal; never manufacture resource closure."""
    root=Path(p['plan_path']).parent
    reason=type(error).__name__+': '+str(error)
    evidence={'schema':'opt50_b24_resident_startup_refusal_fix1','plan_token':p['token'],
        'boot_id':p['boot_id'],'unit':p['unit'],'phase':phase,'coordinator_pid':os.getpid(),
        'plan_path':p['plan_path'],'plan_pin':identity(a.c.read_file(root/'plan.json',private=True)),
        'source_tools_manifest':a.tools['manifest'],
        'native_owner_attempted':False,'native_owner_executed':False,'worker_attempted':False,
        'hardware_accessed':False,'START_commands_issued_by_startup':0,
        'reason':reason,'automatic_retry':False,
        'scope':'Coordinator startup rejected before any Native/worker Popen; no device-close claim'}
    a.durable.new_json(root/'batch_startup_refusal.json',evidence)
    state=initial_state(p)
    state.update(stage='REFUSED_BATCH_BEFORE_OWNER',heartbeat_monotonic=time.monotonic(),
        reason=reason,startup_phase=phase,native_owner_attempted=False,hardware_accessed=False,
        START_commands_issued_by_startup=0,saved_status_only=True)
    a.durable.replace_json(root/'batch_status.json',state)
    print(json.dumps(state,allow_nan=False),flush=True)
    return state


def prepare_coordinator(a,p,lock):
    """Consume exactly once, then create the real Journal's directory."""
    root=Path(p['plan_path']).parent
    forbidden=('batch_started.json','batch_status.json','batch_startup_refusal.json',
               'batch_events','execution','samples','job.kv')
    need(not any((root/name).exists() or (root/name).is_symlink() for name in forbidden),
         'plan already consumed or contains runtime evidence; no replay/resume')
    phase='PERSISTING_BATCH_CONSUMPTION_BEFORE_DIRECTORIES'
    try:
        a.durable.new_json(root/'batch_started.json',{
            'schema':'opt50_b24_resident_batch_start_intent_fix1','plan_token':p['token'],
            'boot_id':p['boot_id'],'unit':p['unit'],'pid':os.getpid(),
            'sample_count':p['sample_count'],'total_START_budget':p['total_START_budget'],
            'native_owner_attempted':False})
        phase='CREATING_BATCH_EVENTS_BEFORE_REAL_JOURNAL'
        a.durable.mkdir_new(root/'batch_events')
        phase='CONSTRUCTING_COORDINATOR_BEFORE_EXECUTION'
        return Coordinator(a,p,lock)
    except (OSError,ValueError,KeyError,TypeError) as error:
        # This function has no device, worker or Native spawn path. Failure of
        # the refusal write is propagated; partial evidence is never removed.
        startup_refusal(a,p,phase,error)
        return None


class Coordinator:
    def __init__(self,a,p,lock):
        self.a,self.p,self.lock=a,p,lock; self.c,self.d=a.c,a.durable
        self.root=Path(p['plan_path']).parent; self.ex=self.root/'execution'
        self.started_ns=time.perf_counter_ns(); self.timings={'samples':[]}
        self.owner=self.worker=None; self.native_owner_attempted=False; self.buffer=b''; self.pending=[]; self.logs=[]
        self.journal=self.d.Journal(self.root/'batch_events')
        self.state=initial_state(p)
    def update(self,stage,**fields):
        self.state.update(stage=stage,heartbeat_monotonic=time.monotonic(),**fields)
        self.d.replace_json(self.root/'batch_status.json',self.state)
        print(json.dumps(self.state,allow_nan=False),flush=True)
    def log(self,name):
        stream=os.fdopen(os.open(self.ex/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb',buffering=0)
        self.logs.append(stream); os.fsync(stream.fileno()); self.d.fsync_dir(self.ex); return stream
    def monitor(self):
        self.guard.monitor(self.p)
        need(self.owner is None or self.owner.poll() is None,'owner exited before normal close')
    def wait_exit(self,process,seconds):
        start=time.monotonic()
        while process.poll() is None:
            need(time.monotonic()-start <= seconds,'normal exit overdue; do not kill')
            self.guard.monitor(self.p); time.sleep(0.2)
        process.wait()
        for stream in self.logs: os.fsync(stream.fileno())
        need(process.returncode == 0,'resident child exit nonzero')
    def send(self,value):
        raw=(json.dumps(value,separators=(',',':'),allow_nan=False)+'\n').encode()
        need(len(raw) <= 32768,'worker command exceeds bound')
        self.worker.stdin.write(raw); self.worker.stdin.flush()
    def receive(self,seconds=300):
        start,last=time.monotonic(),0
        with selectors.DefaultSelector() as selector:
            selector.register(self.worker.stdout,selectors.EVENT_READ)
            while b'\n' not in self.buffer:
                need(time.monotonic()-start <= seconds,'worker response overdue; no retry')
                if time.monotonic()-last >= 5: self.monitor(); last=time.monotonic()
                if selector.select(0.2):
                    block=os.read(self.worker.stdout.fileno(),65536); need(bool(block),'worker protocol EOF')
                    self.buffer+=block; need(len(self.buffer) <= 512*1024,'worker response exceeds bound')
            line,self.buffer=self.buffer.split(b'\n',1)
        self.protocol.write(line+b'\n'); os.fsync(self.protocol.fileno())
        result=decode_json(line); need(type(result) is dict,'worker protocol must be object'); return result
    def execute(self):
        try:
            self.update('BATCH_ADMITTED_NO_OWNER_YET',native_owner_attempted=False)
            self.guard=self.a.guardian_checkpoint.bind_checkpoint(self.a,self.p)
            self.d.new_json(self.root/'batch_guardian_entry.json',self.guard.initial_review)
            self.d.mkdir_new(self.ex); self.d.mkdir_new(self.root/'samples')
            files=[(self.root/name if name.startswith('model/') else self.root/'tools'/name,pin)
                   for name,pin in self.a.tools['files'].items()]
            files.extend((self.root/name,identity(self.c.read_file(self.root/name,private=True)))
                         for name in ('native_owner','plugin.so','metadata.xclbin','plan.json','xrt.ini','build_receipt.json'))
            self.seal=StaticSeal(files); validate_parent(self.c,self.p,self.lock)
            new_bytes(self.c,self.d,self.root/'job.kv',job_bytes(self.p,self.a.checked_bundle['manifest'],os.getpid()))
            self.d.new_json(self.ex/'held_before.json',self.guard.boundary(self.p))
            self.a.resident_proof.within_window(self.p,time.monotonic_ns())
            command=[str(self.root/'native_owner'),str(self.root/'model/weights.bin'),self.p['socket'],
                str(self.root/'metadata.xclbin'),str(self.ex),str(self.root/'job.kv'),str(self.lock),AUTH]
            out,err=self.log('owner.stdout'),self.log('owner.stderr')
            self.native_owner_attempted=True  # Consume before any ambiguous Native spawn attempt.
            self.update('OWNER_INTENT_DURABLE',native_owner_attempted=True); self.journal.append('owner','INTENT',argv=command)
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':'/root','XRT_INI_PATH':str(self.root/'xrt.ini')}
            owner_started=time.perf_counter_ns()
            self.owner=subprocess.Popen(command,cwd=self.ex,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                                        pass_fds=(self.lock,),env=env)
            self.journal.append('owner','SPAWNED',argv=command,pid=self.owner.pid)
            start=time.monotonic()
            while self.c.read_file(self.ex/'owner.stdout',private=True) != ('KV5_READY %d\n'%self.owner.pid).encode():
                need(time.monotonic()-start <= 180,'owner READY overdue'); self.monitor(); time.sleep(0.2)
            self.timings['owner_initialization_ms']=(time.perf_counter_ns()-owner_started)/1e6
            worker_started=time.perf_counter_ns()
            command=[PYTHON,'-B',str(self.root/'tools/model_worker.py'),'--action','resident','--plan',self.p['plan_path'],AUTH]
            self.protocol=self.log('worker_protocol.jsonl'); werr=self.log('worker.stderr')
            self.journal.append('worker','INTENT',argv=command)
            self.worker=subprocess.Popen(command,cwd=self.ex,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                         stderr=werr,env=env,bufsize=0)
            self.journal.append('worker','SPAWNED',argv=command,pid=self.worker.pid)
            ready=self.receive(); need(ready.get('action') == 'ready' and ready.get('max_runs') == self.p['sample_count'],'worker not ready')
            self.timings['worker_initialization_ms']=(time.perf_counter_ns()-worker_started)/1e6
            self.timings['CPU_reference_setup_ms']=ready.get('CPU_reference_setup_ms')
            self.timings['Runner_setup_ms']=ready.get('Runner_setup_ms')
            self.d.new_json(self.ex/'worker_ready.json',ready)
            self.update('RESIDENT_SESSIONS_READY',owner_pid=self.owner.pid,child_pid=self.worker.pid)
            stage,reason=PASS,None
            for run_id,sample in enumerate(selected_samples(self.a,self.p),1):
                if stop_requested: stage,reason='PARTIAL_CLOSED_OPERATOR_STOP','operator stopped at verified graph boundary'; break
                try: self.a.resident_proof.within_window(self.p,time.monotonic_ns())
                except ValueError: stage,reason='PARTIAL_CLOSED_PLAN_EXPIRED','no next graph admitted'; break
                self.seal.boundary(); self.guard.monitor(self.p)
                sample_started=time.perf_counter_ns()
                self.journal.append('sample','GRAPH_INTENT',run_id=run_id,ordinal=sample['ordinal'],expected_START_commands=36)
                self.update('RESIDENT_IMAGE_RUNNING',current_ordinal=sample['ordinal'],current_id=sample['id'],run_id=run_id)
                self.send({'action':'run','run_id':run_id,'sample':sample}); response=self.receive()
                need(response.get('action') == 'result' and response.get('run_id') == run_id and
                     response.get('ordinal') == sample['ordinal'],'worker result identity differs')
                worker=self.a.resident_proof.worker(response.get('worker'),self.p,sample,run_id)
                folder=self.root/'samples'/('%06d'%sample['ordinal'])
                need(self.c.read_json(folder/'worker.json',private=True) == worker,'worker stdout/file differ')
                for name,key in (('input.npy','input_file'),('cpu_reference.npy','cpu_logits_file'),('model_logits.npy','logits_file')):
                    need(identity(self.c.read_file(folder/name,private=True)) == worker[key],'saved NPY differs')
                self.a.resident_proof.compare_saved(self.c.read_file(folder/'cpu_reference.npy',private=True),
                    self.c.read_file(folder/'model_logits.npy',private=True),worker)
                info_started=time.perf_counter_ns()
                admin=Admin(self.ex/'runtime.sock.ctl',self.owner.pid,self.p['logical_weights_sha256'])
                try: metrics=admin.metrics()
                finally: admin.close()
                path=self.ex/('performance_native_%06d.json'%run_id)
                proof=self.a.resident_proof.performance(self.c.read_json(path,private=True),self.p,run_id)
                pending={'schema':'opt50_b24_resident_pending_v4','run_id':run_id,'sample':sample,'native':proof,
                    'performance':identity(self.c.read_file(path,private=True)),'device_closed':False,'owner_metrics':metrics}
                self.d.new_json(folder/'pending.json',pending); self.pending.append((sample,worker))
                self.timings['samples'].append({'run_id':run_id,'ordinal':sample['ordinal'],
                    'whole_sample_ms':(time.perf_counter_ns()-sample_started)/1e6,
                    'INFO_and_receipt_ms':(time.perf_counter_ns()-info_started)/1e6,
                    'CPU_reference_ms':worker['CPU_reference_ms'],'Runner_run_ms':worker['runner_run_ms']})
                self.journal.append('sample','GRAPH_VERIFIED_PENDING_OWNER_CLOSE',run_id=run_id,ordinal=sample['ordinal'])
                self.update('RESIDENT_IMAGE_VERIFIED_PENDING_CLOSE',verified_pending_samples=len(self.pending))
                if worker['bitwise_mismatches']: stage,reason=BATCH_FAIL,'numerical mismatch; no next graph'; break
            closing_started=time.perf_counter_ns()
            self.send({'action':'close'}); closed=self.receive()
            need(closed.get('action') == 'closed' and closed.get('completed') == len(self.pending) and
                 closed.get('CPU_session_closed') is True and closed.get('Runner_closed') is True,'sessions not normally closed')
            pid=self.worker.pid; self.wait_exit(self.worker,120)
            self.journal.append('worker','RETURNED',pid=pid,returncode=0); self.worker=None
            need(self.c.read_file(self.ex/'worker.stderr',private=True) == b'','unexpected worker diagnostics')
            self.d.new_json(self.ex/'worker_closed.json',closed); self.journal.append('management','WORKER_CLOSED',proof=closed)
            pid=self.owner.pid; admin=Admin(self.ex/'runtime.sock.ctl',pid,self.p['logical_weights_sha256'])
            try:
                self.journal.append('management','STOP_INTENT',owner_pid=pid)
                admin.stop(); self.journal.append('management','STOP_RETURNED',owner_pid=pid)
            finally: admin.close()
            self.wait_exit(self.owner,120); self.journal.append('owner','RETURNED',pid=pid,returncode=0); self.owner=None
            need(self.c.read_file(self.ex/'owner.stdout',private=True) ==
                ('KV5_READY %d\nKV5_STOP calls=%d graphs=%d max_graphs=%d\n'%(pid,36*len(self.pending),len(self.pending),self.p['sample_count'])).encode()
                and self.c.read_file(self.ex/'owner.stderr',private=True) == b'','owner normal STOP proof differs')
            native=self.a.resident_proof.owner_close(read_events(self.c,self.ex/'native_events'),len(self.pending))
            closed={'schema':'opt50_b24_resident_owner_closed_v4','owner_pid':pid,'owner_exit_code':0,
                    'device_closed':True,'native':native,'worker_closed':closed}
            self.timings['session_and_owner_close_ms']=(time.perf_counter_ns()-closing_started)/1e6
            self.d.new_json(self.ex/'owner_closed.json',closed)
            self.seal.boundary(); verify_tools(HERE,private=True)
            self.d.new_json(self.root/'batch_guardian_final.json',self.guard.finish(self.p))
            rows=[]
            for sample,worker in self.pending:
                folder=self.root/'samples'/('%06d'%sample['ordinal'])
                row=accuracy_record(self.a,self.p,sample,worker,closed); self.d.new_json(folder/'accuracy.json',row)
                pins={name:identity(self.c.read_file(folder/name,private=True)) for name in
                    ('input.npy','cpu_reference.npy','model_logits.npy','worker.json','pending.json','accuracy.json')}
                self.d.new_json(folder/'commit.json',{'schema':'opt50_b24_batch_sample_commit_v1','closed':True,
                    'plan_token':self.p['token'],'sample':sample,'files':pins,
                    'owner_closed_pin':identity(self.c.read_file(self.ex/'owner_closed.json',private=True))})
                rows.append(row)
            accuracy=self.a.metrics.aggregate(rows,expected_count=len(rows),expected_samples=selected_samples(self.a,self.p)[:len(rows)]) if rows else None
            self.timings['coordinator_before_commit_ms']=(time.perf_counter_ns()-self.started_ns)/1e6
            self.timings['scope']='One bounded resident acceptance batch; includes initialization and checks; not pure PL or steady-state benchmark'
            done={'schema':'opt50_b24_batch_completion_v1','stage':stage,'closed':True,'plan_token':self.p['token'],
                'dataset_manifest':self.p['dataset_manifest'],'sample_start':self.p['sample_start'],'sample_count':self.p['sample_count'],
                'committed_samples':len(rows),'completed_PL_calls':36*len(rows),'Dense_calls':12*len(rows),'Sparse_calls':24*len(rows),
                'compared_logits':1000*len(rows),'total_START_budget':self.p['total_START_budget'],'owner_pid':None,'child_pid':None,
                'accuracy':accuracy,'reason':reason,'lifecycle_profile':LIFECYCLE,'footprint':tree_usage(self.root/'samples'),'timings':self.timings}
            self.d.new_json(self.root/'batch_completion.json',done)
            snapshot=self.a.history_index.append_closed(self.a,self.p,rows,done)
            self.update(stage,committed_samples=len(rows),completed_PL_calls=36*len(rows),owner_pid=None,child_pid=None,
                        completion=done,reason=reason,history_index_committed=snapshot,
                        coordinator_total_ms=(time.perf_counter_ns()-self.started_ns)/1e6)
            for stream in self.logs: stream.close()
            return 0 if stage == PASS else 1
        except BaseException as error:
            if not self.native_owner_attempted:
                startup_refusal(self.a,self.p,'EXECUTION_PREFLIGHT_BEFORE_NATIVE_POPEN',error)
                return 2
            # No signal suppression, kill, STOP, DMA-buffer release, or resubmission
            # is introduced on uncertainty. A live Native owner keeps its inherited
            # lock FD/resources; its existing state machine refuses unsafe STOP.
            self.update('RETAINED_BATCH_STATE_UNCERTAIN',reason=type(error).__name__+': '+str(error),
                owner_pid=None if self.owner is None else self.owner.pid,
                child_pid=None if self.worker is None else self.worker.pid)
            return 2


def parent_closure(a,root,p,done):
    events=[a.c.read_json(path,private=True) for path in sorted((root/'batch_events').glob('event_*.json'))]
    for i,e in enumerate(events,1): need(e.get('sequence') == i,'parent journal gap')
    positions={}; children={}
    for child in ('owner','worker'):
        rows=[(i,e) for i,e in enumerate(events) if e.get('action') == child]
        need([e['phase'] for _,e in rows] == ['INTENT','SPAWNED','RETURNED'],'child lifecycle incomplete')
        x,y,z=[e for _,e in rows]
        need(x['argv'] == y['argv'] and y['pid'] == z['pid'] and z['returncode'] == 0,'child not reaped normally')
        positions[child]=[i for i,_ in rows]; children[child]=z
    rows=[(i,e) for i,e in enumerate(events) if e.get('action') == 'management']
    need([e['phase'] for _,e in rows] == ['WORKER_CLOSED','STOP_INTENT','STOP_RETURNED'] and
         positions['owner'][0] < positions['owner'][1] < positions['worker'][0] < positions['worker'][1] <
         positions['worker'][2] < rows[0][0] < rows[1][0] < rows[2][0] < positions['owner'][2],
         'worker-close/STOP/owner-reap order differs')
    native=a.resident_proof.owner_close(read_events(a.c,root/'execution/native_events'),done['committed_samples'])
    closed=a.c.read_json(root/'execution/owner_closed.json',private=True)
    wc=a.c.read_json(root/'execution/worker_closed.json',private=True)
    need(wc == rows[0][1].get('proof') == closed.get('worker_closed') and
         wc.get('CPU_session_closed') is True and wc.get('Runner_closed') is True and
         wc.get('action') == 'closed' and wc.get('completed') == done['committed_samples'],'real worker close binding differs')
    expected=('KV5_READY %d\nKV5_STOP calls=%d graphs=%d max_graphs=%d\n' %
        (children['owner']['pid'],36*done['committed_samples'],done['committed_samples'],p['sample_count'])).encode()
    need(a.c.read_file(root/'execution/owner.stdout',private=True) == expected and
         a.c.read_file(root/'execution/owner.stderr',private=True) == b'' and
         a.c.read_file(root/'execution/worker.stderr',private=True) == b'', 'real owner/worker normal-exit logs differ')
    need(closed.get('owner_pid') == children['owner']['pid'] and
         closed.get('device_closed') is True and closed.get('owner_exit_code') == 0 and closed.get('native') == native,
         'final Native resource closure differs')
    return closed


def committed_records(a,p,root,complete=False):
    done=a.c.read_json(root/'batch_completion.json',private=True); count=done['committed_samples']
    need(0 <= count <= p['sample_count'] and (not complete or count == p['sample_count']),'resident count incomplete')
    closed=parent_closure(a,root,p,done); samples=selected_samples(a,p); rows=[]
    for i,sample in enumerate(samples[:count],1):
        folder=root/'samples'/('%06d'%sample['ordinal']); commit=a.c.read_json(folder/'commit.json',private=True)
        need(commit.get('schema') == 'opt50_b24_batch_sample_commit_v1' and commit.get('closed') is True and
             commit.get('plan_token') == p['token'] and commit.get('sample') == sample,'resident commit differs')
        need(type(commit.get('files')) is dict and set(commit['files']) ==
             {'input.npy','cpu_reference.npy','model_logits.npy','worker.json','pending.json','accuracy.json'},
             'resident commit must pin every required sample file')
        need(identity(a.c.read_file(folder/'input.npy',private=True)) ==
             {k:sample['input'][k] for k in ('bytes','sha256')},'committed input identity differs')
        for name,pin in commit['files'].items():
            legacy.safe_relative(name); need(identity(a.c.read_file(folder/name,private=True)) == pin,'resident commit changed')
        need(commit.get('owner_closed_pin') == identity(a.c.read_file(root/'execution/owner_closed.json',private=True)),
             'sample not bound to true final owner closure')
        worker=a.c.read_json(folder/'worker.json',private=True); a.resident_proof.worker(worker,p,sample,i)
        path=root/'execution'/('performance_native_%06d.json'%i)
        a.resident_proof.performance(a.c.read_json(path,private=True),p,i)
        a.resident_proof.compare_saved(a.c.read_file(folder/'cpu_reference.npy',private=True),
            a.c.read_file(folder/'model_logits.npy',private=True),worker)
        pending=a.c.read_json(folder/'pending.json',private=True)
        need(pending['performance'] == identity(a.c.read_file(path,private=True)),'native graph receipt pin changed')
        row=a.c.read_json(folder/'accuracy.json',private=True); a.metrics.validate_record(row,sample)
        need(row == accuracy_record(a,p,sample,worker,closed),'saved accuracy differs from worker/closure')
        rows.append(row)
    return rows


def read_completed_records(path,allow_partial=False):
    a=api(private=True); p=load_plan(a,path,fresh=False); root=Path(path).parent
    state=a.c.read_json(root/'batch_status.json',private=True); done=a.c.read_json(root/'batch_completion.json',private=True)
    need(state.get('stage') in (a.history_index.CLOSED if allow_partial else {PASS}) and state.get('completion') == done and
         done.get('closed') is True and state.get('owner_pid') is None and state.get('child_pid') is None,'batch not closed')
    return committed_records(a,p,root,complete=not allow_partial)


def run(path,authorized):
    need(authorized,'explicit resident authorization required')
    print('B24_RESIDENT_VALIDATING_PLAN_NO_NATIVE_ACCESS',flush=True)
    a=api(private=True); a.c.root_linux(); p=load_plan(a,path,fresh=True); a.c.configure(p['guardian_plan'])
    interpreter(a.c,p['interpreter'])
    with a.c.companion_locked() as lock:
        history=previous_clear(a.c,exclude=path)
        need(a.history_index.admit(a,history,p['dataset_manifest'],p['sample_start'],p['sample_count']) == p['history_index'],
             'history changed after check')
        validate_parent(a.c,p,lock)
        coordinator=prepare_coordinator(a,p,lock)
        return 2 if coordinator is None else coordinator.execute()


def status(path,summary=False):
    raw=read(HERE/'tools_manifest.json',private=True); listing=decode_json(raw)
    reader=module_checked('status_receipt.py',{'manifest':identity(raw),'files':listing['files']},HERE,private=True)
    return reader.read_saved(path,summary=summary,folder=HERE)


def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    item=sub.add_parser('check')
    for name in ('guardian-plan','layer-plan','previous-model-plan','dataset'): item.add_argument('--'+name,type=Path,required=True)
    item.add_argument('--start',type=int,required=True); item.add_argument('--count',type=int,default=5)
    item.add_argument('--pilot-plan',type=Path); item.add_argument('--metadata',type=Path)
    item.add_argument('--uart-log-confirmed',action='store_true')
    for name in ('run','status'):
        item=sub.add_parser(name); item.add_argument('--plan',type=Path,required=True)
        item.add_argument(AUTH if name == 'run' else '--summary',action='store_true',dest='authorized' if name == 'run' else 'summary')
    args=parser.parse_args()
    try:
        if args.action == 'run': return run(args.plan,args.authorized)
        value=status(args.plan,args.summary) if args.action == 'status' else check(args.guardian_plan,args.layer_plan,args.metadata,
            args.uart_log_confirmed,previous_path=args.previous_model_plan,dataset=args.dataset,start=args.start,
            count=args.count,pilot_path=args.pilot_plan)
        print(json.dumps(value,indent=2,allow_nan=False),flush=True); return 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        print(json.dumps({'status':'B24_RESIDENT_REFUSED_NO_RETRY','error':type(error).__name__+': '+str(error)}),flush=True); return 2


if __name__ == '__main__':
    def request_stop(*_):
        global stop_requested
        stop_requested=True
    for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP): signal.signal(number,request_stop)
    raise SystemExit(main())
