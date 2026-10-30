"""Resident parent integration with fake workers/owner; no device or compiler access.

Filesystem ownership/directory-fsync and XRT/processes are explicit portability
mocks. Saved numerical arrays, parent journal order, INFO receipts, closed pins
and index-admission ordering exercise the real Python proof functions.
"""
from __future__ import annotations
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import time
import types
import sys
import unittest
from unittest import mock
import numpy as np

HERE = Path(__file__).absolute().parent.parent
sys.path.insert(0,str(HERE))
import run_model as entry
import resident_proof
import accuracy_metrics
import model_worker


def encoded(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False)+'\n').encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def npy(value):
    stream=io.BytesIO(); np.save(stream,value,allow_pickle=False); return stream.getvalue()


def performance(plan, run_id):
    def allocation(n): return ((n+128+4095)//4096)*4096
    events=[{'phase':'MODEL_BEGIN','layer':49,'command':0,'mode':0,'monotonic_ns':1}]
    for call,layer in enumerate(entry.PL,1):
        for phase in resident_proof.PHASES:
            events.append({'phase':phase,'layer':layer,'command':call,
                           'mode':int(layer in entry.SPARSE),'monotonic_ns':len(events)+1})
    events.append({'phase':'MODEL_END_COMPLETED','layer':49,'command':36,'mode':0,'monotonic_ns':182})
    return {'schema':'opt50_performance_native_resident_v4','profile':'b24-resident-low-overhead-v4',
        'boot_id':plan['boot_id'],'source_model_sha256':plan['source_model_sha256'],'run_id':run_id,
        'max_graphs':plan['sample_count'],'total_START_budget':36*plan['sample_count'],
        'START_commands_issued':36,'completed_PL_calls':36,'PL_dense':12,'PL_sparse':24,
        'device_closed':False,'native_END_completed':True,'guards_verified_after_graph':True,
        'static_weights_verified_after_graph':True,'postrun_buffer_checks_verified':True,
        'graph_owner_sync_calls':0,'graph_owner_sync_bytes':0,'per_layer_input_sha_checks':0,
        'per_layer_guard_checks':0,'per_layer_y_poison_writes':0,'initial_weight_to_device_sync_calls':2,
        'initial_weight_to_device_sync_bytes':allocation(2654208)+allocation(331840),
        'initial_weight_sync_scope':'owner_lifetime','postrun_owner_sync_calls':4,
        'postrun_owner_sync_bytes':sum(allocation(n) for n in (113472,2654208,331840,453888)),
        'postrun_guard_checks':4,'postrun_static_weight_sha_checks':2,'postrun_checks_started_ns':183,
        'postrun_checks_completed_ns':184,'hot_path_fsync_count':0,'hot_path_disk_events':0,
        'lifetime_START_commands':36*run_id,'lifetime_completed_graphs':run_id,
        'last_verified_run':run_id,'prior_verified_run':run_id-1,'ram_events':events}


class Reader:
    @staticmethod
    def read_file(path,**_): return Path(path).read_bytes()
    @staticmethod
    def read_json(path,**_): return json.loads(Path(path).read_bytes())
    @staticmethod
    def private_dir(path):
        if not Path(path).is_dir(): raise ValueError('test directory missing')


class Journal:
    def __init__(self,folder): self.folder=folder; self.folder.mkdir(parents=True,exist_ok=True); self.sequence=0
    def append(self,action,phase,**details):
        self.sequence+=1
        value={'schema':'opt50_durable_event_v1','sequence':self.sequence,'action':action,'phase':phase,**details}
        save(self.folder/('event_%06d.json'%self.sequence),value)


class Guard:
    initial_review={'guardian':'fake guardian already reviewed'}
    def monitor(self,_): return {'live':True}
    def boundary(self,_): return {'boundary':'fake OS proof'}
    def finish(self,_): return {'finished':True}


class FakeProcess:
    def __init__(self,pid): self.pid=pid; self.returncode=None
    def poll(self): return self.returncode
    def wait(self): return self.returncode


class ParentHarness:
    def __init__(self,root,count=3,*,mismatch=False,missing_info=False,eof=False,expire_after_one=False,index_error=False,static_after_one=False):
        self.root=root; self.count=count; self.mismatch=mismatch; self.missing_info=missing_info
        self.eof=eof; self.expire_after_one=expire_after_one
        self.index_error=index_error; self.static_after_one=static_after_one
        self.sent=[]; self.spawns=[]; self.waited=[]; self.index_calls=0; self.info_calls=0; self.stop_calls=0
        self.natives=[]; self.run=0; self.ready=False; self.current=None; self.window_calls=0
        self.samples=[]
        for ordinal in range(45,45+count):
            raw=('frozen input %d'%ordinal).encode()
            self.samples.append({'ordinal':ordinal,'id':'val_%07d'%ordinal,'label':999,
                'image':{'bytes':9,'sha256':('%064x'%(ordinal+100))},'input':entry.identity(raw)})
        self.plan={'sample_start':45,'sample_count':count,'plan_path':str(root/'plan.json'),
            'boot_id':'2b0eaebd-2dd9-45c5-b135-26315bfdbb7f','token':'abcdef012345',
            'unit':'opt50-b24-model-abcdef012345','guardian_pid':15656,'measurement_profile':entry.PROFILE,
            'lifecycle_profile':entry.LIFECYCLE,'resident_profile':entry.LIFECYCLE,'owner_graph_count':count,
            'scope':entry.SCOPE,'total_START_budget':36*count,'created_monotonic_ns':time.monotonic_ns(),
            'socket':str(root/'execution/runtime.sock'),'source_model_sha256':entry.SOURCE_MODEL,
            'logical_weights_sha256':'1'*64,'reference_sha256':'2'*64,'custom_graph_sha256':'3'*64,
            'model_manifest_sha256':'4'*64,'dataset_manifest':{'bytes':6,'sha256':'5'*64},'tool_files':{'files':{}}}
        root.mkdir(); save(root/'plan.json',self.plan)
        for name in ('native_owner','plugin.so','metadata.xclbin'): (root/name).write_bytes(name.encode())
        (root/'xrt.ini').write_bytes(b''); save(root/'build_receipt.json',{})
        self.owner=FakeProcess(701); self.worker=FakeProcess(702)
        self.guard=Guard(); self.c=Reader()
        self.d=types.SimpleNamespace(Journal=Journal,new_json=lambda p,v: save(Path(p),v),
            replace_json=lambda p,v: save(Path(p),v),mkdir_new=lambda p: Path(p).mkdir(),fsync_dir=lambda p:None)
        self.a=types.SimpleNamespace(c=self.c,durable=self.d,tools={'files':{}},resident_proof=resident_proof,
            checked_bundle={'manifest':{'logical_weights_sha256':'1'*64,'pl_data_sha256':'6'*64,'pl_meta_sha256':'7'*64,
                'reference_sha256':'2'*64}},metrics=accuracy_metrics,
            guardian_checkpoint=types.SimpleNamespace(bind_checkpoint=lambda a,p:self.guard),
            history_index=types.SimpleNamespace(append_closed=self.append_index))

    def native_event(self,phase):
        i=len(self.natives)+1; value={'schema':'opt50_model_native_event_v1','sequence':i,'phase':phase,
                                   'details':({'RESOURCE_CLOSE_INTENT':'allocated_BO_count=4; fake fixture',
                                               'RESOURCES_CLOSED':'released_BO_count=4; fake fixture'}.get(phase,'fake fixture '+phase)),
                                   'monotonic_ns':i}
        self.natives.append(value)
        save(self.root/'execution/native_events'/('event_%06d.json'%i),value)

    def popen(self,command,**kwargs):
        self.spawns.append(command)
        if str(command[0]).endswith('native_owner'):
            kwargs['stdout'].write(b'KV5_READY 701\n')
            for phase in ('NATIVE_ENTERED','RAW_PREFLIGHT_PASS','NATIVE_READY'): self.native_event(phase)
            return self.owner
        return self.worker

    def send(self,coordinator,value): self.sent.append(copy.deepcopy(value)); self.current=value

    def receive(self,coordinator,seconds=300):
        if not self.ready:
            self.ready=True; return {'action':'ready','max_runs':self.count}
        if self.current['action']=='close':
            return {'action':'closed','completed':self.run,'CPU_session_closed':True,'Runner_closed':True}
        if self.eof: raise ValueError('worker protocol EOF')
        command=self.current; self.run=command['run_id']; sample=command['sample']
        worker=self.make_worker(sample,self.run)
        return {'action':'result','run_id':self.run,'ordinal':sample['ordinal'],'worker':worker}

    def make_worker(self,sample,run_id):
        folder=self.root/'samples'/('%06d'%sample['ordinal']); folder.mkdir()
        raw=('frozen input %d'%sample['ordinal']).encode(); (folder/'input.npy').write_bytes(raw)
        cpu=np.arange(1000,dtype=np.float32).reshape(1,1000); pl=cpu.copy()
        if self.mismatch and run_id==1: pl[0,0]=0.25
        cpu_raw,pl_raw=npy(cpu),npy(pl)
        (folder/'cpu_reference.npy').write_bytes(cpu_raw); (folder/'model_logits.npy').write_bytes(pl_raw)
        compared=accuracy_metrics.compare(cpu,pl,sample['label'])
        worker={'schema':'opt50_b24_resident_worker_result_v4','run_id':run_id,'sample_ordinal':sample['ordinal'],
            'sample_id':sample['id'],'label':sample['label'],'Runner_closed':False,'CPU_session_closed':False,
            'expected_PL_starts':36,'compared_logits':1000,'boot_id':self.plan['boot_id'],'plan_token':self.plan['token'],
            'source_model_sha256':self.plan['source_model_sha256'],'reference_sha256':self.plan['reference_sha256'],
            'custom_graph_sha256':self.plan['custom_graph_sha256'],'logical_weights_sha256':self.plan['logical_weights_sha256'],
            'model_manifest_sha256':self.plan['model_manifest_sha256'],'measurement_profile':entry.PROFILE,
            'resident_profile':entry.LIFECYCLE,'completed_graphs_in_session':run_id,'runner_calls':1,
            'image_sha256':sample['image']['sha256'],'input_sha256':sample['input']['sha256'],
            'input_file':entry.identity(raw),'CPU_reference_ms':1.0,'runner_run_ms':0.5,
            'cpu_logits_file':entry.identity(cpu_raw),'logits_file':entry.identity(pl_raw),
            'cpu_logits_raw_sha256':compared['CPU_logits_sha256'],'logits_raw_sha256':compared['PL_logits_sha256'],
            'accuracy':compared,'ORT_options':model_worker.OPTIONS,
            **{k:compared[k] for k in ('bitwise_mismatches','max_abs_difference','CPU_top5','PL_top5')}}
        save(folder/'worker.json',worker)
        return worker

    def admin_factory(self,*_):
        harness=self
        class Admin:
            def metrics(self):
                harness.info_calls+=1
                if not harness.missing_info:
                    save(harness.root/'execution'/('performance_native_%06d.json'%harness.run),performance(harness.plan,harness.run))
                    harness.native_event('PERFORMANCE_COMPACT_PUBLISHED')
                return {'completed_PL_calls':36}
            def stop(self):
                harness.stop_calls+=1
                harness.native_event('RESOURCE_CLOSE_INTENT'); harness.native_event('RESOURCES_CLOSED')
                with (harness.root/'execution/owner.stdout').open('ab') as out:
                    out.write(('KV5_STOP calls=%d graphs=%d max_graphs=%d\n'%(36*harness.run,harness.run,harness.count)).encode())
            def close(self): pass
        return Admin()

    def wait_exit(self,coordinator,process,seconds): process.returncode=0; self.waited.append(process.pid)

    def within_window(self,plan,now):
        self.window_calls+=1
        if self.expire_after_one and self.window_calls>=3: raise ValueError('resident graph admission expired')
        # Explicit non-device stub; the exact helper is tested separately below.
        return resident_proof.budget(plan['sample_count'])

    def append_index(self,a,p,rows,done):
        self.index_calls+=1
        if self.index_error: raise ValueError('index publication failed')
        if self.waited != [702,701]: raise AssertionError('index before real child closure')
        if not (self.root/'execution/owner_closed.json').exists(): raise AssertionError('missing owner closure')
        observed=entry.committed_records(a,p,self.root,complete=False)
        if rows != observed: raise AssertionError('index cannot commit unaudited records')
        return {'revision':99,'images_count':45+len(rows)}

    def execute(self):
        harness=self
        class Seal:
            def __init__(self,_): pass
            def boundary(self):
                if harness.static_after_one and harness.run >= 1: raise ValueError('static source/model changed')
        with mock.patch.object(entry,'StaticSeal',Seal),mock.patch.object(entry,'validate_parent',lambda *_:None), \
             mock.patch.object(entry,'new_bytes',lambda c,d,p,b:Path(p).write_bytes(b)), \
             mock.patch.object(entry,'selected_samples',lambda a,p:self.samples), \
             mock.patch.object(entry,'verify_tools',lambda *a,**kw:{'files':{}}), \
             mock.patch.object(entry.os,'O_NOFOLLOW',getattr(os,'O_NOFOLLOW',0),create=True), \
             mock.patch.object(entry.subprocess,'Popen',self.popen),mock.patch.object(entry,'Admin',self.admin_factory), \
             mock.patch.object(entry.Coordinator,'send',lambda co,v:harness.send(co,v)), \
             mock.patch.object(entry.Coordinator,'receive',lambda co,*a,**kw:harness.receive(co,*a,**kw)), \
             mock.patch.object(entry.Coordinator,'wait_exit',lambda co,p,t:harness.wait_exit(co,p,t)), \
             mock.patch.object(resident_proof,'within_window',self.within_window),mock.patch.object(entry,'stop_requested',False), \
             mock.patch('builtins.print'):
            coordinator=entry.Coordinator(self.a,self.plan,9)
            try:
                self.result=coordinator.execute()
            finally:
                # Only fake processes exist; close this fixture's log handles.
                for stream in coordinator.logs:
                    if not stream.closed: stream.close()
        return self.result

    def records(self):
        with mock.patch.object(entry,'selected_samples',lambda a,p:self.samples):
            return entry.committed_records(self.a,self.plan,self.root,complete=True)


class ResidentParentTests(unittest.TestCase):
    def setUp(self):
        self.parent=HERE/'.test_build'; self.parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='resident_parent_',dir=self.parent)
        self.folder=Path(self.temp.name).resolve(); self.assertEqual(self.folder.parent,self.parent.resolve())
        self.addCleanup(self.cleanup)
    def cleanup(self):
        self.assertEqual(self.folder.parent,self.parent.resolve())
        self.assertTrue(self.folder.name.startswith('resident_parent_'))
        self.temp.cleanup()
    def harness(self,**kwargs): return ParentHarness(self.folder/'plan',**kwargs)

    def test_three_graphs_share_one_owner_and_worker_then_commit_after_stop(self):
        h=self.harness(); self.assertEqual(h.execute(),0)
        self.assertEqual(len(h.spawns),2); self.assertEqual(h.waited,[702,701]); self.assertEqual(h.index_calls,1)
        self.assertEqual([x['run_id'] for x in h.sent if x['action']=='run'],[1,2,3])
        rows=h.records(); self.assertEqual(len(rows),3)
        self.assertTrue(all(x['device_closed'] and x['owner_exit_code']==0 for x in rows))

    def test_mismatch_closes_prefix_and_never_admits_next_image(self):
        h=self.harness(mismatch=True); self.assertEqual(h.execute(),1)
        self.assertEqual([x['action'] for x in h.sent],['run','close']); self.assertEqual(h.info_calls,1)
        self.assertEqual(h.stop_calls,1); self.assertEqual(h.index_calls,1)
        done=json.loads((h.root/'batch_completion.json').read_bytes())
        self.assertEqual(done['stage'],entry.BATCH_FAIL); self.assertEqual(done['committed_samples'],1)

    def test_missing_info_keeps_uncommitted_state_and_no_next_graph(self):
        h=self.harness(missing_info=True); self.assertEqual(h.execute(),2)
        self.assertEqual(h.stop_calls,0); self.assertEqual(h.index_calls,0)
        self.assertFalse((h.root/'batch_completion.json').exists())
        self.assertEqual([x['action'] for x in h.sent],['run'])
        self.assertFalse((h.root/'samples/000045/commit.json').exists())

    def test_worker_eof_never_commits_or_starts_another_graph(self):
        h=self.harness(eof=True); self.assertEqual(h.execute(),2)
        self.assertEqual(h.index_calls,0); self.assertEqual(h.stop_calls,0)
        state=json.loads((h.root/'batch_status.json').read_bytes())
        self.assertEqual(state['stage'],'RETAINED_BATCH_STATE_UNCERTAIN')

    def test_expiry_after_first_info_closes_only_verified_prefix(self):
        h=self.harness(expire_after_one=True); self.assertEqual(h.execute(),1)
        self.assertEqual([x['action'] for x in h.sent],['run','close'])
        done=json.loads((h.root/'batch_completion.json').read_bytes())
        self.assertEqual(done['stage'],'PARTIAL_CLOSED_PLAN_EXPIRED'); self.assertEqual(done['committed_samples'],1)

    def test_index_failure_after_normal_owner_close_never_publishes_pass(self):
        h=self.harness(index_error=True); self.assertEqual(h.execute(),2)
        self.assertEqual(h.waited,[702,701]); self.assertEqual(h.index_calls,1)
        state=json.loads((h.root/'batch_status.json').read_bytes())
        self.assertEqual(state['stage'],'RETAINED_BATCH_STATE_UNCERTAIN')
        self.assertTrue((h.root/'execution/owner_closed.json').exists())
        self.assertNotIn('history_index_committed',state)

    def test_static_file_change_stops_before_next_begin_and_keeps_uncommitted(self):
        h=self.harness(static_after_one=True); self.assertEqual(h.execute(),2)
        self.assertEqual([x['action'] for x in h.sent],['run'])
        self.assertEqual(h.index_calls,0); self.assertEqual(h.stop_calls,0)
        self.assertFalse((h.root/'batch_completion.json').exists())

    def test_saved_logits_are_recomputed_even_if_file_pin_is_resealed(self):
        h=self.harness(); self.assertEqual(h.execute(),0)
        folder=h.root/'samples/000045'; data=np.arange(1000,dtype=np.float32).reshape(1,1000)
        data[0,0]=0.25; (folder/'model_logits.npy').write_bytes(npy(data))
        commit=json.loads((folder/'commit.json').read_bytes())
        commit['files']['model_logits.npy']=entry.identity((folder/'model_logits.npy').read_bytes())
        save(folder/'commit.json',commit)
        with self.assertRaisesRegex(ValueError,'1000-logit'): h.records()

    def test_per_image_commit_requires_actual_batch_owner_closed_pin(self):
        h=self.harness(); self.assertEqual(h.execute(),0)
        path=h.root/'samples/000045/commit.json'; value=json.loads(path.read_bytes())
        value['owner_closed_pin']={'bytes':0,'sha256':'0'*64}; save(path,value)
        with self.assertRaisesRegex(ValueError,'owner closure'): h.records()

    def test_missing_commit_inventory_cannot_hide_changed_saved_input(self):
        h=self.harness(); self.assertEqual(h.execute(),0)
        path=h.root/'samples/000045/commit.json'; value=json.loads(path.read_bytes())
        value['files']={}; save(path,value); (path.parent/'input.npy').write_bytes(b'changed input')
        with self.assertRaises(ValueError): h.records()

    def test_duplicate_run_id_in_saved_second_graph_is_rejected(self):
        h=self.harness(); self.assertEqual(h.execute(),0)
        folder=h.root/'samples/000046'; worker=json.loads((folder/'worker.json').read_bytes()); worker['run_id']=1
        save(folder/'worker.json',worker)
        commit=json.loads((folder/'commit.json').read_bytes()); commit['files']['worker.json']=entry.identity((folder/'worker.json').read_bytes()); save(folder/'commit.json',commit)
        with self.assertRaisesRegex(ValueError,'worker'): h.records()

    def test_foreign_owner_pid_is_not_a_closure_proof(self):
        h=self.harness(); self.assertEqual(h.execute(),0)
        path=h.root/'execution/owner_closed.json'; closed=json.loads(path.read_bytes()); closed['owner_pid']=999; save(path,closed)
        for sample in h.samples:
            cpath=h.root/'samples'/('%06d'%sample['ordinal'])/'commit.json'; commit=json.loads(cpath.read_bytes())
            commit['owner_closed_pin']=entry.identity(path.read_bytes()); save(cpath,commit)
        with self.assertRaises(ValueError): h.records()

    def test_stop_before_worker_reap_is_rejected(self):
        h=self.harness(); self.assertEqual(h.execute(),0)
        paths=sorted((h.root/'batch_events').glob('event_*.json')); events=[json.loads(p.read_bytes()) for p in paths]
        wi=next(i for i,e in enumerate(events) if e['action']=='worker' and e['phase']=='RETURNED')
        si=next(i for i,e in enumerate(events) if e['phase']=='STOP_INTENT'); events[wi],events[si]=events[si],events[wi]
        for i,(path,event) in enumerate(zip(paths,events),1): event['sequence']=i; save(path,event)
        with self.assertRaisesRegex(ValueError,'order'): h.records()

    def test_monotonic_window_and_count_limits_are_enforced(self):
        plan={'sample_count':5,'created_monotonic_ns':100}
        resident_proof.within_window(plan,100+900_000_000_000)
        for now in (99,100+900_000_000_001):
            with self.subTest(now=now),self.assertRaises(ValueError): resident_proof.within_window(plan,now)
        for count in (0,51,True):
            with self.subTest(count=count),self.assertRaises(ValueError): resident_proof.budget(count)


if __name__=='__main__': unittest.main(verbosity=2)
