"""Two fixed historical incidents after the observed reboot; no device access.

Closed zero-graph evidence is reviewed statically. The observed8e refusal is
reviewed against its exact pre-Native source/receipt and artifact absence.
Neither case writes an old record, index entry or device-close claim.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import types

HERE=Path(__file__).absolute().parent
OLD_BOOT='2b0eaebd-2dd9-45c5-b135-26315bfdbb7f'
NEW_BOOT='cbbb80ca-8805-4971-9271-f59afbcaef81'
RECORDS=Path('/var/lib/opt50-b24-model-v1')
INTERRUPTED=RECORDS/'plan_8e7d10643b3b'
ZERO=RECORDS/'plan_c4b539de5ba7'
ZERO_BASE=Path('/var/lib/opt50-b24-zero-close-v1/plan_c4b539de5ba7')
H1={'bytes':24547,'sha256':'8f256e8eca931b80ea9513c62f0921ec0700558c5604476ea68902833411bd1e'}
FIX1={'bytes':23152,'sha256':'03d0b13bb25bc04dbffd561e08267812db8bdd6697ad474ee0b2f40294773fb1'}
ZERO_SHA='1d0a3f97781753d769cb911efb5e22cdab6fb629c1f9d33d0f72415f337da2be'
INDEX=Path('/var/lib/opt50-b24-history-v3/index.json')


def need(ok,why):
    if not ok:raise ValueError(why)
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def exists(path):return path.exists() or path.is_symlink()
def boot(c):
    value=c._proc_read(Path('/proc/sys/kernel/random/boot_id'),128).decode().strip()
    need(value==NEW_BOOT and value!=OLD_BOOT,'fixed new reboot identity required');return value
def module(c,path,sha):
    raw=c.read_file(path,private=HERE.name=='tools');need(pin(raw)['sha256']==sha,'fixed historical helper changed')
    out=types.ModuleType('_reboot_static_'+path.stem);out.__file__=str(path)
    exec(compile(raw,out.__file__,'exec'),out.__dict__);return out
def source(c,root,wanted):
    for folder in (RECORDS,root,root/'tools',root/'model'):c.private_dir(folder)
    raw=c.read_file(root/'tools/tools_manifest.json',private=True);need(pin(raw)==wanted,'fixed incident source manifest differs')
    listing=c.decode_json(raw);files={str(root/'tools/tools_manifest.json'):pin(raw)}
    for name,expected in listing['files'].items():
        parts=Path(name).parts;need(not Path(name).is_absolute() and '..' not in parts,'unsafe historical source path')
        path=root/name if name.startswith('model/') else root/'tools'/name
        value=c.read_file(path,private=True);need(pin(value)==expected,'historical source changed: '+name);files[str(path)]=expected
    raw=c.read_file(root/'plan.json',private=True);plan=c.decode_json(raw);files[str(root/'plan.json')]=pin(raw)
    need(plan.get('boot_id')==OLD_BOOT and plan.get('plan_path')==str(root/'plan.json') and
         plan.get('token')==root.name[5:] and plan.get('unit')=='opt50-b24-model-'+root.name[5:] and
         plan.get('tool_files')=={'manifest':wanted,'files':listing['files']} and
         plan.get('lifecycle_profile')=='resident-bounded-batch-v4','fixed historical plan identity differs')
    return plan,listing,files
def checked(c,path,files):
    raw=c.read_file(path,private=True);files[str(path)]=pin(raw);return c.decode_json(raw)
def journal(c,folder,files,native=False):
    c.private_dir(folder);rows=[];last=-1
    for number,path in enumerate(sorted(folder.iterdir()),1):
        need(path.name=='event_%06d.json'%number,'historical journal gap/extra entry')
        row=checked(c,path,files)
        need(row.get('schema')==('opt50_model_native_event_v1' if native else 'opt50_durable_event_v1') and
             type(row.get('sequence')) is int and row['sequence']==number and type(row.get('phase')) is str,
             'historical journal schema/chronology differs')
        if native:
            need(type(row.get('monotonic_ns')) is int and row['monotonic_ns']>last and type(row.get('details')) is str,
                 'Native chronology differs');last=row['monotonic_ns']
        rows.append(row)
    return rows


def zero_history(c,root):
    """Full original static proof; never Runtime.audit/native/guardian/group."""
    boot(c);need(Path(root)==ZERO,'only the fixed closed zero incident is admitted')
    plan,_,files=source(c,ZERO,FIX1);need(plan['sample_start']==100 and plan['sample_count']==50,'zero incident selection differs')
    z=module(c,HERE/'zero_graph_review.py',ZERO_SHA)
    intent=checked(c,ZERO_BASE/'finish_intent.json',files)
    need(intent.get('schema')=='opt50_b24_zero_graph_finish_intent_v1' and intent.get('operations')==['HELLO','STOP'],
         'original zero-close consumption intent differs')
    path=Path(intent['check_receipt'])
    need(path.parent.parent==ZERO_BASE and path.name=='check.json' and re.fullmatch('check_[a-f0-9]{12}',path.parent.name),
         'fixed zero check namespace differs')
    before=checked(c,path,files)
    need(files[str(path)]==intent['check_pin'] and before.get('schema')=='opt50_b24_zero_graph_check_v1' and
         before.get('status')=='CHECKED_ZERO_GRAPH_OWNER_NO_CONTACT' and before.get('tool_source_pin')==intent.get('tool_source_pin'),
         'original zero check/tool binding differs')
    tool=c.read_file(path.parent/'tools/zero_close.py',private=True)
    need(pin(tool)==before['tool_source_pin'] and pin(tool)['sha256']==ZERO_SHA,'original frozen zero audit tool changed')
    files[str(path.parent/'tools/zero_close.py')]=pin(tool);before=before['evidence'];after=None
    need(exists(ZERO_BASE/'receipt.json'),'completed zero-close receipt is required')
    receipt=checked(c,ZERO_BASE/'receipt.json',files)
    need(receipt.get('schema')=='opt50_b24_zero_graph_close_receipt_v1' and receipt.get('status')=='CLOSED_ZERO_GRAPHS' and
         receipt.get('before')==before and receipt.get('check_pin')==intent['check_pin'] and
         receipt.get('tool_source_pin')==pin(tool) and receipt.get('calls')==receipt.get('graphs')==0 and
         receipt.get('max_graphs')==50 and receipt.get('owner_exit_code_observed') is False and
         receipt.get('history_index_modified') is False and receipt.get('original_plan_status_completion_written_by_tool') is False,
         'zero close receipt differs')
    after=receipt['after']
    need(after.get('native_event_count')==155 and after.get('sealed_tools_manifest')==FIX1,'zero after proof differs')
    for name,expected in after['file_pins'].items():
        raw=c.read_file(Path(name),private=True);need(pin(raw)==expected,'zero historical closed file changed: '+name);files[name]=expected
    need(before.get('incident_plan')==str(ZERO/'plan.json') and before.get('boot_id')==OLD_BOOT and
         before.get('native_event_count')==141 and before.get('sealed_tools_manifest')==FIX1,'zero141 prefix binding differs')
    exempt=str(ZERO/'execution/owner.stdout')
    for name,expected in before['file_pins'].items():
        if name==exempt:continue
        raw=c.read_file(Path(name),private=True);need(pin(raw)==expected,'zero original prefix/source changed');files[name]=expected
    prefix={str(ZERO/'execution/native_events'/('event_%06d.json'%n)) for n in range(1,142)}
    need(prefix<=before['file_pins'].keys(),'zero141 prefix inventory incomplete')
    if after is not None:
        complete={str(ZERO/'execution/native_events'/('event_%06d.json'%n)) for n in range(1,156)}
        need(complete<=after['file_pins'].keys(),'zero155 closed inventory incomplete')
    rows=journal(c,ZERO/'batch_events',files);argv=rows[0].get('argv') if rows else None
    need(type(argv) is list and len(argv)==8 and argv[:6]==[str(ZERO/'native_owner'),str(ZERO/'model/weights.bin'),
         str(ZERO/'execution/runtime.sock'),str(ZERO/'metadata.xclbin'),str(ZERO/'execution'),str(ZERO/'job.kv')] and
         re.fullmatch('[0-9]{1,4}',argv[6]) and argv[7]=='--authorized-resident-batch','zero parent argv differs')
    z.validate_parent(rows,argv);native=journal(c,ZERO/'execution/native_events',files,True);z.validate_native(native,True)
    z.unused_outputs(ZERO/'execution',ZERO/'samples',True)
    out=c.read_file(ZERO/'execution/owner.stdout',private=True);err=c.read_file(ZERO/'execution/owner.stderr',private=True)
    need(out==z.CLOSED and err==b'','zero normal STOP0 output differs')
    files[str(ZERO/'execution/owner.stdout')]=pin(out);files[str(ZERO/'execution/owner.stderr')]=pin(err)
    return {'schema':'opt50_b24_static_zero_history_r1','status':'HISTORICAL_ZERO_GRAPH_NORMAL_CLOSE_VERIFIED',
        'historical_boot_id':OLD_BOOT,'current_boot_id':NEW_BOOT,'accepted_images':0,'calls':0,'graphs':0,
        'live_old_guardian_checked':False,'owner_exit_code_observed':False,'owner_contacted':False,'file_pins':files}


def static_plan(c,root,plan,listing):
    raw=c.read_file(root/'tools/run_model.py',private=True)
    need(pin(raw)==listing['files']['run_model.py'],'interrupted entry source differs')
    old=types.ModuleType('_reboot_old_h1');old.__file__=str(root/'tools/run_model.py')
    exec(compile(raw,old.__file__,'exec'),old.__dict__)
    api=old.api(private=True);need(old.load_plan(api,root/'plan.json',fresh=False)==plan,'old static sealed plan differs')
    return old,api


def refused_before_owner(c,root):
    """Review only the observed8e pre-Native guardian refusal; no writes."""
    boot(c);root=Path(root);need(root==INTERRUPTED,'only the fixed8e refusal is admitted')
    plan,listing,files=source(c,root,H1)
    need(plan.get('sample_start')==305 and plan.get('sample_count')==50 and plan.get('total_START_budget')==1800,
         'refused plan selection differs')
    old,api=static_plan(c,root,plan,listing)
    proof=checked(c,root/'batch_startup_refusal.json',files)
    started=checked(c,root/'batch_started.json',files);state=checked(c,root/'batch_status.json',files)
    phase='EXECUTION_PREFLIGHT_BEFORE_NATIVE_POPEN'
    reason='ValueError: guardian service is not guarded/running'
    need(proof.get('schema')=='opt50_b24_resident_startup_refusal_fix1' and
         proof.get('plan_path')==str(root/'plan.json') and proof.get('plan_pin')==files[str(root/'plan.json')] and
         proof.get('plan_token')==plan['token'] and proof.get('boot_id')==OLD_BOOT and proof.get('unit')==plan['unit'] and
         proof.get('source_tools_manifest')==H1 and proof.get('phase')==phase and proof.get('reason')==reason,
         'actual pre-Native refusal binding differs')
    need(all(proof.get(k) is False for k in ('native_owner_attempted','native_owner_executed','worker_attempted','hardware_accessed')) and
         type(proof.get('START_commands_issued_by_startup')) is int and proof['START_commands_issued_by_startup']==0,
         'refusal cannot exclude a Native/worker attempt')
    need(started.get('schema')=='opt50_b24_resident_batch_start_intent_fix1' and started.get('plan_token')==plan['token'] and
         started.get('boot_id')==OLD_BOOT and started.get('unit')==plan['unit'] and started.get('sample_count')==50 and
         started.get('total_START_budget')==1800 and type(started.get('pid')) is int and started['pid']>1 and
         proof.get('coordinator_pid')==started['pid'] and started.get('native_owner_attempted') is False,
         'old consumption/refusal parent differs')
    need(state.get('schema')=='opt50_b24_acceptance_batch_status_v1' and state.get('stage')=='REFUSED_BATCH_BEFORE_OWNER' and
         state.get('boot_id')==OLD_BOOT and state.get('plan_token')==plan['token'] and state.get('unit')==plan['unit'] and
         state.get('sample_start')==305 and state.get('sample_count')==50 and state.get('startup_phase')==phase and
         state.get('reason')==reason and state.get('owner_pid') is None and state.get('child_pid') is None and
         state.get('native_owner_attempted') is False and state.get('hardware_accessed') is False and
         all(type(state.get(k)) is int and state[k]==0 for k in
             ('committed_samples','verified_pending_samples','completed_PL_calls','START_commands_issued_by_startup')) and
         'completion' not in state,'old refused state differs')
    for path in (root/'batch_completion.json',root/'batch_guardian_final.json',root/'execution/native_events',
                 root/'execution/owner_closed.json',root/'execution/worker_closed.json'):
        need(not exists(path),'Native/commit/completion evidence exists; separate reconciliation required')
    rows=journal(c,root/'batch_events',files);need(not rows,'owner/worker/graph intent exists; cannot treat as unstarted')
    samples=root/'samples'
    if exists(samples):c.private_dir(samples);need(not list(samples.iterdir()),'sample output exists; cannot treat as unstarted')
    execution=root/'execution'
    if exists(execution):
        c.private_dir(execution)
        need({p.name for p in execution.iterdir()}<={'held_before.json'},'Native/worker execution artifacts exist')
        if exists(execution/'held_before.json'):checked(c,execution/'held_before.json',files)
    if exists(root/'batch_guardian_entry.json'):checked(c,root/'batch_guardian_entry.json',files)
    if exists(root/'job.kv'):
        raw=c.read_file(root/'job.kv',private=True);files[str(root/'job.kv')]=pin(raw)
        need(raw==old.job_bytes(plan,api.checked_bundle['manifest'],started['pid']),'old preflight job binding differs')
    value,index_raw=api.history_index.load(api);api.history_index.verify_known(value)
    n=value['images_count']
    need(type(n) is int and 305<=n<=1800 and set(value['samples'])=={str(i) for i in range(n)} and
         str(root/'plan.json') not in value['batches'] and
         not any(row.get('plan')==str(root/'plan.json') for row in value['samples'].values()),
         'refused attempt is already indexed; separate reconciliation required')
    files[str(INDEX)]=pin(index_raw)
    return {'schema':'opt50_b24_historical_unstarted_refusal_review_r1','status':'HISTORICAL_PRE_NATIVE_REFUSAL_VERIFIED',
        'plan_path':str(root/'plan.json'),'historical_boot_id':OLD_BOOT,'current_boot_id':NEW_BOOT,
        'accepted_images':0,'PL_START_commands_issued_by_this_attempt':0,'physical_PL_starts':'NOT_MEASURED',
        'device_closed_claimed':False,'owner_contacted':False,'old_files_modified':False,'history_index_modified':False,
        'file_pins':files}
