"""Fixed-incident reboot review, real file journals and explicit OS/source fakes.

No old process/guardian is contacted. Full source pin admission is tested with
the actual155 h1 files; chronology/negative tests inject that separate static
plan check, root metadata and flock on Windows. No hardware is accessed.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
import sys
from unittest.mock import Mock,patch

ROOT=Path(__file__).absolute().parents[1]
spec=importlib.util.spec_from_file_location('_reboot_review_test',ROOT/'reboot_review.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=value if type(value) is bytes else (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    path.write_bytes(raw);return r.pin(raw)


def access():
    return types.SimpleNamespace(read_file=lambda p,**kwargs:Path(p).read_bytes(),decode_json=json.loads,
        read_json=lambda p,**kwargs:json.loads(Path(p).read_bytes()),private_dir=lambda p:r.need(Path(p).is_dir(),'fixture directory missing'),
        no_links=lambda p:Path(p),_proc_read=lambda p,*args:(r.NEW_BOOT+'\n').encode() if str(p).endswith('boot_id') else b'locks',
        _lock_info=lambda:types.SimpleNamespace(st_dev=1,st_ino=9),parse_flock=Mock())


class StaticSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old=ROOT.parent/'b24_batch_acceptance_resident_v4_fix2h1';cls.raw=(old/'tools_manifest.json').read_bytes()
        cls.listing=json.loads(cls.raw);cls.data={name:(old/name).read_bytes() for name in cls.listing['files']}
    def test_actual155_h1_pins_bind_historical_source_before_code_exec(self):
        root=r.INTERRUPTED;data={str(root/'tools/tools_manifest.json'):self.raw}
        for name,raw in self.data.items():data[str(root/name if name.startswith('model/') else root/'tools'/name)]=raw
        plan={'boot_id':r.OLD_BOOT,'plan_path':str(root/'plan.json'),'token':root.name[5:],
              'unit':'opt50-b24-model-'+root.name[5:],'tool_files':{'manifest':r.H1,'files':self.listing['files']},
              'lifecycle_profile':'resident-bounded-batch-v4'}
        data[str(root/'plan.json')]=json.dumps(plan).encode()
        c=types.SimpleNamespace(read_file=lambda p,**kwargs:data[str(p)],decode_json=json.loads,private_dir=lambda p:None)
        actual,_,pins=r.source(c,root,r.H1)
        self.assertEqual(actual,plan);self.assertEqual(len(pins),157)
        data[str(root/'tools/source/kernel.cpp')]+=b'changed'
        with self.assertRaisesRegex(ValueError,'historical source changed'):r.source(c,root,r.H1)


class AdmissionDispatchTests(unittest.TestCase):
    def test_both_actual_zero_acceptance_cases_are_reviewed_and_excluded_from_history(self):
        out=ROOT/'.test_build';out.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=out,prefix='dispatch_') as temporary:
            base=Path(temporary);records=base/'records';records.mkdir()
            for name in ('plan_c4b539de5ba7','plan_8e7d10643b3b'):(records/name).mkdir()
            source=base/'software';source.mkdir();put(source/'tools_manifest.json',{'files':{}})
            sys.path.insert(0,str(ROOT));self.addCleanup(lambda:sys.path.remove(str(ROOT)))
            spec=importlib.util.spec_from_file_location('_reboot_dispatch_test',ROOT/'run_model.py')
            entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
            helper=types.SimpleNamespace(zero_history=Mock(),refused_before_owner=Mock())
            c=types.SimpleNamespace(private_dir=lambda p:None,read_json=Mock(side_effect=AssertionError('no fake old completion')))
            with patch.object(entry,'HERE',source),patch.object(entry,'RECORDS',records),patch.object(entry,'module_checked',return_value=helper):
                self.assertEqual(entry.previous_clear(c),[])
            helper.zero_history.assert_called_once_with(c,records/'plan_c4b539de5ba7')
            helper.refused_before_owner.assert_called_once_with(c,records/'plan_8e7d10643b3b');c.read_json.assert_not_called()


class UnstartedRefusalTests(unittest.TestCase):
    def setUp(self):
        out=ROOT/'.test_build';out.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=out,prefix='refused_');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'records/plan_8e7d10643b3b'
        (self.root/'batch_events').mkdir(parents=True);self.c=access();self.addCleanup(patch.stopall)
        patch.object(r,'RECORDS',self.root.parent).start();patch.object(r,'INTERRUPTED',self.root).start()
        self.plan={'boot_id':r.OLD_BOOT,'plan_path':str(self.root/'plan.json'),'token':'8e7d10643b3b','unit':'opt50-b24-model-8e7d10643b3b',
                   'sample_start':305,'sample_count':50,'total_START_budget':1800}
        self.planpin=put(self.root/'plan.json',self.plan)
        patch.object(r,'source',side_effect=lambda *args:(self.plan,{'files':{}},{str(self.root/'plan.json'):self.planpin})).start()
        self.n=305;self.indexed=False
        def index():
            value={'images_count':self.n,'samples':{str(i):{'plan':'other'} for i in range(self.n)},
                   'batches':{str(self.root/'plan.json'):{}} if self.indexed else {}}
            return value,json.dumps(value).encode()
        self.hist=types.SimpleNamespace(load=lambda a:index(),verify_known=Mock())
        old=types.SimpleNamespace(job_bytes=lambda *args:b'job')
        patch.object(r,'static_plan',return_value=(old,types.SimpleNamespace(checked_bundle={'manifest':{}},history_index=self.hist))).start()
        self.reason='ValueError: guardian service is not guarded/running';self.phase='EXECUTION_PREFLIGHT_BEFORE_NATIVE_POPEN'
        self.proof={'schema':'opt50_b24_resident_startup_refusal_fix1','plan_path':str(self.root/'plan.json'),'plan_pin':self.planpin,
            'plan_token':self.plan['token'],'boot_id':r.OLD_BOOT,'unit':self.plan['unit'],'source_tools_manifest':r.H1,
            'phase':self.phase,'reason':self.reason,'coordinator_pid':600,'native_owner_attempted':False,'native_owner_executed':False,
            'worker_attempted':False,'hardware_accessed':False,'START_commands_issued_by_startup':0}
        self.state={'schema':'opt50_b24_acceptance_batch_status_v1','stage':'REFUSED_BATCH_BEFORE_OWNER','boot_id':r.OLD_BOOT,
            'plan_token':self.plan['token'],'unit':self.plan['unit'],'sample_start':305,'sample_count':50,'startup_phase':self.phase,
            'reason':self.reason,'owner_pid':None,'child_pid':None,'native_owner_attempted':False,'hardware_accessed':False,
            'committed_samples':0,'verified_pending_samples':0,'completed_PL_calls':0,'START_commands_issued_by_startup':0}
        put(self.root/'batch_startup_refusal.json',self.proof);put(self.root/'batch_status.json',self.state)
        put(self.root/'batch_started.json',{'schema':'opt50_b24_resident_batch_start_intent_fix1','plan_token':self.plan['token'],
            'boot_id':r.OLD_BOOT,'unit':self.plan['unit'],'sample_count':50,'total_START_budget':1800,'pid':600,'native_owner_attempted':False})
    def test_exact_observed_refusal_is_zero_acceptance_and_never_writes_old_records(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        value=r.refused_before_owner(self.c,self.root)
        self.assertEqual(value['accepted_images'],0);self.assertEqual(value['PL_START_commands_issued_by_this_attempt'],0)
        self.assertFalse(value['device_closed_claimed']);self.assertEqual(value['physical_PL_starts'],'NOT_MEASURED')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
    def test_owner_intent_native_directory_or_commit_completion_is_never_a_no_native_refusal(self):
        for name in ('batch_completion.json','execution/owner_closed.json','samples/000305/commit.json'):
            path=self.root/name;put(path,{})
            with self.assertRaises(ValueError):r.refused_before_owner(self.c,self.root)
            path.unlink()
            for parent in (path.parent,path.parent.parent):
                if parent!=self.root and parent.exists() and not list(parent.iterdir()):parent.rmdir()
        put(self.root/'batch_events/event_000001.json',{'schema':'opt50_durable_event_v1','sequence':1,'action':'owner','phase':'INTENT'})
        with self.assertRaisesRegex(ValueError,'intent exists'):r.refused_before_owner(self.c,self.root)
    def test_same_boot_wrong_reason_or_false_attempt_flags_are_refused(self):
        self.c._proc_read=lambda *args:(r.OLD_BOOT+'\n').encode()
        with self.assertRaisesRegex(ValueError,'new reboot'):r.refused_before_owner(self.c,self.root)
        self.c=access();put(self.root/'batch_startup_refusal.json',dict(self.proof,worker_attempted=True))
        with self.assertRaisesRegex(ValueError,'exclude a Native'):r.refused_before_owner(self.c,self.root)
        put(self.root/'batch_startup_refusal.json',dict(self.proof,reason='different error'))
        with self.assertRaisesRegex(ValueError,'binding differs'):r.refused_before_owner(self.c,self.root)
    def test_new_index_progress_is_allowed_but_old_refused_plan_is_never_indexed(self):
        self.n=310;value=r.refused_before_owner(self.c,self.root);self.assertEqual(value['accepted_images'],0)
        self.indexed=True
        with self.assertRaisesRegex(ValueError,'already indexed'):r.refused_before_owner(self.c,self.root)


class ClosedZeroTests(unittest.TestCase):
    def setUp(self):
        out=ROOT/'.test_build';out.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=out,prefix='static_zero_');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'records/plan_c4b539de5ba7';self.ex=self.root/'execution'
        (self.ex/'native_events').mkdir(parents=True);(self.root/'samples').mkdir()
        self.zbase=self.base/'zero';check=self.zbase/'check_0123456789ab/check.json'
        self.c=access();self.addCleanup(patch.stopall)
        patch.object(r,'ZERO',self.root).start();patch.object(r,'ZERO_BASE',self.zbase).start()
        plan={'sample_start':100,'sample_count':50,'boot_id':r.OLD_BOOT};planpin=put(self.root/'plan.json',plan)
        patch.object(r,'source',side_effect=lambda *args:(plan,{}, {str(self.root/'plan.json'):planpin})).start()
        spec=importlib.util.spec_from_file_location('_zero_native_rows',ROOT.parent/'maintenance/b24_resident_zero_close_20261029_v1/tests/test_zero_close.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        for row in m.native_fixture(True):put(self.ex/'native_events'/('event_%06d.json'%row['sequence']),row)
        argv=[str(self.root/'native_owner'),str(self.root/'model/weights.bin'),str(self.ex/'runtime.sock'),
            str(self.root/'metadata.xclbin'),str(self.ex),str(self.root/'job.kv'),'3','--authorized-resident-batch']
        put(self.root/'batch_events/event_000001.json',{'schema':'opt50_durable_event_v1','sequence':1,'action':'owner','phase':'INTENT','argv':argv})
        put(self.root/'batch_events/event_000002.json',{'schema':'opt50_durable_event_v1','sequence':2,'action':'owner','phase':'SPAWNED','argv':argv,'pid':52117})
        put(self.ex/'owner.stdout',b'KV5_READY 52117\nKV5_STOP calls=0 graphs=0 max_graphs=50\n')
        put(self.ex/'owner.stderr',b'');put(self.ex/'held_before.json',{});put(self.ex/'runtime.sock.lock',b'lock')
        afterpins={str(p):r.pin(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}
        beforepins={name:value for name,value in afterpins.items() if '/native_events/' not in name.replace('\\','/') or
                    int(Path(name).stem.split('_')[1])<=141}
        beforepins[str(self.ex/'owner.stdout')]=r.pin(b'KV5_READY 52117\n')
        before={'incident_plan':str(self.root/'plan.json'),'boot_id':r.OLD_BOOT,'native_event_count':141,
                'sealed_tools_manifest':r.FIX1,'file_pins':beforepins}
        after={'native_event_count':155,'sealed_tools_manifest':r.FIX1,'file_pins':afterpins}
        tool=(ROOT/'zero_graph_review.py').read_bytes();put(check.parent/'tools/zero_close.py',tool)
        cp=put(check,{'schema':'opt50_b24_zero_graph_check_v1','status':'CHECKED_ZERO_GRAPH_OWNER_NO_CONTACT',
                     'tool_source_pin':r.pin(tool),'evidence':before})
        put(self.zbase/'finish_intent.json',{'schema':'opt50_b24_zero_graph_finish_intent_v1','operations':['HELLO','STOP'],
                 'check_receipt':str(check),'check_pin':cp,'tool_source_pin':r.pin(tool)})
        put(self.zbase/'receipt.json',{'schema':'opt50_b24_zero_graph_close_receipt_v1','status':'CLOSED_ZERO_GRAPHS',
            'before':before,'after':after,'check_pin':cp,'tool_source_pin':r.pin(tool),'calls':0,'graphs':0,'max_graphs':50,
            'owner_exit_code_observed':False,'history_index_modified':False,'original_plan_status_completion_written_by_tool':False})
        original=r.module
        def load(*args):
            z=original(*args);z.private_dir=self.c.private_dir
            for name in ('audit','native','guardian','group','stop'):
                setattr(z.Runtime,name,Mock(side_effect=AssertionError('old live operation forbidden')))
            self.z=z;return z
        patch.object(r,'module',side_effect=load).start()
    def test_completed155_stop0_is_static_across_boot_and_never_checks_old_live_processes(self):
        value=r.zero_history(self.c,self.root)
        self.assertEqual(value['status'],'HISTORICAL_ZERO_GRAPH_NORMAL_CLOSE_VERIFIED')
        self.assertEqual(value['accepted_images'],0);self.assertFalse(value['live_old_guardian_checked'])
        for name in ('audit','native','guardian','group','stop'):getattr(self.z.Runtime,name).assert_not_called()
    def test_missing_success_receipt_never_falls_back_to_failed_close(self):
        (self.zbase/'receipt.json').unlink();put(self.zbase/'failure.json',{'status':'RETAINED_ZERO_CLOSE_UNCERTAIN_NO_RETRY'})
        with self.assertRaisesRegex(ValueError,'completed zero-close receipt'):r.zero_history(self.c,self.root)
    def test_changed_closed_native_or_unknown_execution_artifact_is_refused(self):
        put(self.ex/'worker.stderr',b'foreign')
        with self.assertRaisesRegex(ValueError,'execution artifact'):r.zero_history(self.c,self.root)
        (self.ex/'worker.stderr').unlink();path=self.ex/'native_events/event_000155.json';row=json.loads(path.read_bytes());row['details']='changed';put(path,row)
        with self.assertRaisesRegex(ValueError,'closed file changed'):r.zero_history(self.c,self.root)


if __name__=='__main__':unittest.main()
