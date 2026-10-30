"""Real durable Journal startup contract and fake read-only systemd observation.

On Windows only directory-fsync/ownership-dependent admission is mocked. The
real durable mkdir/new_json/Journal/_chain functions run on fresh temporary
paths. No compiler, Native process, device or SSH operation runs in this suite.
"""
from __future__ import annotations
from contextlib import contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(ROOT))
import run_model as entry
import run_once as front

spec=importlib.util.spec_from_file_location('fix1_real_durable_tests',ROOT/'support_py/durable.py')
durable=importlib.util.module_from_spec(spec); spec.loader.exec_module(durable)


class Reader:
    def root_linux(self): pass
    def configure(self,_): pass
    @contextmanager
    def companion_locked(self): yield 9
    def read_file(self,path,**_): return Path(path).read_bytes()


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.parent=ROOT/'.test_build'; self.parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='startup_fix1_',dir=self.parent)
        self.root=Path(self.temp.name).resolve(); self.assertEqual(self.root.parent,self.parent.resolve())
        self.addCleanup(self.cleanup)
        self.plan={'plan_path':str(self.root/'plan.json'),'token':'abcdef012345',
            'unit':'opt50-b24-model-abcdef012345','boot_id':'2b0eaebd-2dd9-45c5-b135-26315bfdbb7f',
            'guardian_plan':Path('/var/lib/fake-guardian/plan.json'),'guardian_pid':15656,
            'interpreter':{},'history_index':{'revision':2},'dataset_manifest':{'bytes':6,'sha256':'4'*64},
            'sample_start':45,'sample_count':5,'total_START_budget':180}
        (self.root/'plan.json').write_text(json.dumps({k:str(v) if isinstance(v,Path) else v for k,v in self.plan.items()}),encoding='utf-8')
        self.a=types.SimpleNamespace(c=Reader(),durable=durable,tools={'manifest':{'bytes':10,'sha256':'5'*64}},
            history_index=types.SimpleNamespace(admit=lambda *a,**kw:self.plan['history_index']))
        self.syncs=[]
        native_fsync=durable.fsync_dir
        def portable_fsync(path):
            # Explicit portability mock, never native POSIX durability proof.
            value=Path(path); self.assertTrue(value.is_dir()); self.syncs.append(value)
            if os.name == 'posix': native_fsync(path)
        patch=mock.patch.object(durable,'fsync_dir',portable_fsync); patch.start(); self.addCleanup(patch.stop)
        if os.name == 'nt':
            # Windows updates file timestamps on close; this suite tests real
            # creation/order/O_EXCL, not POSIX timestamp or power-loss proof.
            metadata=mock.patch.object(durable,'_identity',lambda st:(st.st_dev,st.st_ino,st.st_size,0,0))
            metadata.start(); self.addCleanup(metadata.stop)

    def cleanup(self):
        self.assertEqual(self.root.parent,self.parent.resolve())
        self.assertTrue(self.root.name.startswith('startup_fix1_'))
        self.temp.cleanup()

    def run_entry(self,execute):
        with mock.patch.object(entry,'api',return_value=self.a),mock.patch.object(entry,'load_plan',return_value=self.plan), \
             mock.patch.object(entry,'interpreter'),mock.patch.object(entry,'previous_clear',return_value=[]), \
             mock.patch.object(entry,'validate_parent'),mock.patch.object(entry.Coordinator,'execute',execute), \
             mock.patch('builtins.print'):
            return entry.run(self.plan['plan_path'],True)

    def test_real_journal_rejects_absent_directory_without_creating_it(self):
        path=self.root/'missing_events'
        with self.assertRaises(FileNotFoundError): durable.Journal(path)
        self.assertFalse(path.exists())

    def test_run_consumes_plan_then_creates_directory_before_real_journal(self):
        calls=[]; original=durable.Journal
        def checked_journal(path):
            self.assertTrue((self.root/'batch_started.json').is_file())
            self.assertTrue(Path(path).is_dir())
            value=original(path); calls.append(value); return value
        def execute(coordinator):
            self.assertIsInstance(coordinator.journal,original)
            self.assertEqual(coordinator.journal.folder,self.root/'batch_events')
            self.assertEqual(coordinator.journal.next_sequence,1)
            return 17
        with mock.patch.object(durable,'Journal',checked_journal):
            self.assertEqual(self.run_entry(execute),17)
        self.assertEqual(len(calls),1); self.assertTrue(self.syncs)
        receipt=json.loads((self.root/'batch_started.json').read_bytes())
        self.assertEqual(receipt['schema'],'opt50_b24_resident_batch_start_intent_fix1')
        self.assertEqual(receipt['total_START_budget'],180); self.assertFalse(receipt['native_owner_attempted'])

    def test_consumed_entry_cannot_be_run_a_second_time(self):
        self.assertEqual(self.run_entry(lambda co:0),0)
        before=(self.root/'batch_started.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'consumed'):
            self.run_entry(lambda co:self.fail('second coordinator execution'))
        self.assertEqual((self.root/'batch_started.json').read_bytes(),before)

    def test_mkdir_failure_is_known_no_owner_refusal_without_closed_completion(self):
        original=durable.mkdir_new
        def fail(path,*a,**kw):
            if Path(path).name=='batch_events': raise OSError('injected directory error')
            return original(path,*a,**kw)
        with mock.patch.object(durable,'mkdir_new',fail),mock.patch.object(entry.subprocess,'Popen') as spawn:
            self.assertEqual(self.run_entry(lambda co:self.fail('execute reached')),2)
        spawn.assert_not_called()
        state=json.loads((self.root/'batch_status.json').read_bytes())
        proof=json.loads((self.root/'batch_startup_refusal.json').read_bytes())
        self.assertEqual(state['stage'],'REFUSED_BATCH_BEFORE_OWNER')
        self.assertFalse(proof['native_owner_attempted']); self.assertFalse(proof['hardware_accessed'])
        self.assertEqual(proof['plan_pin'],entry.identity((self.root/'plan.json').read_bytes()))
        self.assertEqual(proof['source_tools_manifest'],self.a.tools['manifest'])
        self.assertNotIn('completion',state); self.assertFalse((self.root/'batch_completion.json').exists())
        self.assertFalse((self.root/'execution').exists())
        self.assertTrue((self.root/'batch_started.json').exists())

    def test_constructor_failure_preserves_consumption_and_explicit_refusal(self):
        with mock.patch.object(durable,'Journal',side_effect=ValueError('injected Journal contract error')), \
             mock.patch.object(entry.subprocess,'Popen') as spawn:
            self.assertEqual(self.run_entry(lambda co:self.fail('execute reached')),2)
        spawn.assert_not_called()
        proof=json.loads((self.root/'batch_startup_refusal.json').read_bytes())
        self.assertEqual(proof['phase'],'CONSTRUCTING_COORDINATOR_BEFORE_EXECUTION')
        with self.assertRaisesRegex(ValueError,'consumed'):
            self.run_entry(lambda co:self.fail('refused plan must not be replayed'))

    def test_early_state_is_durable_before_guardian_long_review(self):
        durable.new_json(self.root/'batch_started.json',{'consumed':True})
        durable.mkdir_new(self.root/'batch_events')
        def audit(a,p):
            state=json.loads((self.root/'batch_status.json').read_bytes())
            self.assertEqual(state['stage'],'BATCH_ADMITTED_NO_OWNER_YET')
            self.assertFalse(state['native_owner_attempted'])
            raise ValueError('injected long-review refusal')
        self.a.guardian_checkpoint=types.SimpleNamespace(bind_checkpoint=audit)
        with mock.patch.object(entry.subprocess,'Popen') as spawn,mock.patch('builtins.print'):
            coordinator=entry.Coordinator(self.a,self.plan,9)
            self.assertEqual(coordinator.execute(),2)
        spawn.assert_not_called()
        state=json.loads((self.root/'batch_status.json').read_bytes())
        self.assertEqual(state['stage'],'REFUSED_BATCH_BEFORE_OWNER')
        self.assertNotIn('completion',state)

    def test_unexpected_runtime_evidence_is_refused_without_fabricating_zero_proof(self):
        (self.root/'execution').mkdir()
        with self.assertRaisesRegex(ValueError,'runtime evidence'):
            self.run_entry(lambda co:self.fail('unknown prior state cannot run'))
        self.assertFalse((self.root/'batch_startup_refusal.json').exists())
        self.assertFalse((self.root/'batch_status.json').exists())


class WrapperTests(unittest.TestCase):
    PLAN='/var/lib/opt50-b24-model-v1/plan_abcdef012345/plan.json'
    def report(self):
        script=self.PLAN[:-len('plan.json')]+'tools/run_model.py'
        run=['sudo','systemd-run','--unit=opt50-b24-model-abcdef012345',
            *['--property='+v for v in front.PROPERTIES],'/usr/bin/python3','-B',script,'run','--plan',self.PLAN,front.AUTH]
        status=['sudo','/usr/bin/python3','-B',script,'status','--plan',self.PLAN,'--summary']
        return {'status':'B24_RESIDENT_PLAN_CREATED_NO_HARDWARE_ACCESS','hardware_executed':False,
            'expires_seconds':900,'sample_start':45,'sample_count':5,'total_START_budget':180,
            'lifecycle_profile':'resident-bounded-batch-v4','ORT_intra_threads':4,'ORT_inter_threads':1,
            'plan':self.PLAN,'next_requires_explicit_batch_authorization':shlex.join(run),'status_command':shlex.join(status)}
    def fields(self,**overrides):
        return dict({'unit':'opt50-b24-model-abcdef012345.service','query_returncode':0,
            'LoadState':'loaded','ActiveState':'failed','SubState':'failed','MainPID':'0',
            'ExecMainStatus':'2','Result':'exit-code'},**overrides)
    def passed(self):
        return {'stage':front.PASS,'sample_count':5,'committed_samples':5,'completed_PL_calls':180,
            'closed':True,'owner_pid':None,'child_pid':None,'accuracy':{'numerical_status':'NUMERICAL_PASS'}}
    def call(self,states,snapshots):
        prints=[]
        with mock.patch.object(front.os,'name','posix'),mock.patch.object(front.os,'geteuid',return_value=0,create=True), \
             mock.patch.object(front.os,'uname',return_value=types.SimpleNamespace(machine='aarch64'),create=True), \
             mock.patch.object(front,'verify'),mock.patch.object(front,'json_child',side_effect=[self.report(),*states]) as queries, \
             mock.patch.object(front,'service_snapshot',side_effect=snapshots) as observed, \
             mock.patch.object(front.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as submit, \
             mock.patch.object(front.time,'sleep'),mock.patch('builtins.print',side_effect=lambda v,*a,**kw:prints.append(v)):
            code=front.run_once('guardian','layer',True,True,previous_model_plan='origin',dataset='dataset',start=45,count=5)
        return code,prints,queries.call_count,observed.call_count,submit.call_count
    def test_failed_inner_without_saved_status_stops_wrapper_without_replay(self):
        no={'stage':'NO_SAVED_STATUS_YET_SEE_SYSTEMD_JOURNAL','saved_status_only':True}
        code,prints,queries,observed,submitted=self.call([no,no],[self.fields()])
        self.assertEqual(code,2); self.assertEqual((queries,observed,submitted),(3,1,1))
        final=json.loads(prints[-1]); self.assertEqual(final['stage'],'INNER_SERVICE_EXITED_WITHOUT_TERMINAL_STATUS')
        self.assertEqual(final['service']['ExecMainStatus'],'2'); self.assertFalse(final['resubmitted'])
        self.assertEqual(final['hardware_executed'],'NOT_DEDUCED_FROM_MISSING_SAVED_STATUS')
    def test_status_exit_race_rereads_saved_terminal_without_resubmitting(self):
        no={'stage':'NO_SAVED_STATUS_YET_SEE_SYSTEMD_JOURNAL'}
        code,_,queries,_,submitted=self.call([no,self.passed()],[self.fields(ActiveState='active',SubState='exited',ExecMainStatus='0',Result='success')])
        self.assertEqual(code,0); self.assertEqual(queries,3); self.assertEqual(submitted,1)
    def test_running_inner_can_continue_waiting_then_finish(self):
        no={'stage':'NO_SAVED_STATUS_YET_SEE_SYSTEMD_JOURNAL'}
        code,_,queries,observed,submitted=self.call([no,self.passed()],[self.fields(MainPID='123',ActiveState='active',SubState='running'),self.fields(ActiveState='active',SubState='exited')])
        self.assertEqual(code,0); self.assertEqual((queries,observed,submitted),(3,2,1))
    def test_snapshot_queries_only_exact_unit_read_only(self):
        text='\n'.join(k+'='+v for k,v in self.fields().items() if k not in ('unit','query_returncode'))+'\n'
        result=types.SimpleNamespace(returncode=0,stdout=text,stderr='')
        with mock.patch.object(front.subprocess,'run',return_value=result) as run:
            snapshot=front.service_snapshot(self.PLAN)
        command=run.call_args.args[0]
        self.assertEqual(command[:3],['/usr/bin/systemctl','show','opt50-b24-model-abcdef012345.service'])
        self.assertNotIn('start',command); self.assertTrue(front.service_ended(snapshot))
    def test_duplicate_or_missing_service_properties_are_rejected(self):
        result=types.SimpleNamespace(returncode=0,stdout='MainPID=0\nMainPID=1\n',stderr='')
        with mock.patch.object(front.subprocess,'run',return_value=result):
            with self.assertRaisesRegex(ValueError,'properties'): front.service_snapshot(self.PLAN)
    def test_unknown_plan_namespace_cannot_query_arbitrary_unit(self):
        with mock.patch.object(front.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'canonical'): front.service_snapshot('/tmp/plan_abcdef012345/plan.json')
        run.assert_not_called()


if __name__=='__main__': unittest.main(verbosity=2)
