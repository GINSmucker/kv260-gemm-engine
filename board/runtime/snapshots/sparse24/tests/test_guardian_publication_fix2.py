"""Publisher ordering tests with real files and explicit simulated OS policy.

Threads write a partial event, file-fsync, then delay the status rename. Windows
fixtures simulate only root ownership/mode and live process/lock observations;
these are software tests, not board, Linux permissions or power-loss proof.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_fix2_checkpoint_fixture',ROOT/'tests/test_guardian_checkpoint.py')
fixtures=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
g=fixtures.g


def regular(info):
    g.need(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'fixture regular/single-link policy')


def atomic_status(path,state):
    temporary=path.with_name('status.next')
    raw=json.dumps(state,sort_keys=True).encode()
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)


class CheckpointPublicationTests(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'.test_build'; folder.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=folder,prefix='publisher_')
        self.addCleanup(self.temp.cleanup)
        self.f=fixtures.Fixture(Path(self.temp.name))
        policy=patch.object(g,'private_file',side_effect=regular)
        policy.start(); self.addCleanup(policy.stop)
        self.guard=g.bind_checkpoint(self.f.a,self.f.plan)

    def new_state(self):
        self.f.now+=1
        return dict(self.f.state,heartbeat_monotonic=self.f.now)

    def test_partial_event_then_delayed_status_publish_waits_without_early_cache_commit(self):
        state=self.new_state(); visible=threading.Event(); complete=threading.Event(); errors=[]
        path=self.f.root/'execution/events/event_000004.json'
        raw=json.dumps({'schema':'opt50_durable_event_v1','sequence':4,'action':'state',
                        'phase':state['stage'],'state':state},sort_keys=True).encode()
        def writer():
            try:
                descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(descriptor,'wb') as stream:
                    stream.write(raw[:len(raw)//2]); stream.flush(); visible.set()
                    time.sleep(.07)
                    stream.write(raw[len(raw)//2:]); stream.flush(); os.fsync(stream.fileno())
                complete.set()
                # Models the writer's slow directory fsync before status replace.
                time.sleep(.25)
                self.assertEqual(self.guard.sequence,3)
                atomic_status(self.f.root/'execution/status.json',state)
            except BaseException as error: errors.append(error); visible.set()
        thread=threading.Thread(target=writer); thread.start(); self.addCleanup(thread.join)
        self.assertTrue(visible.wait(2))
        started=time.monotonic(); result=self.guard.monitor(self.f.plan)
        thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors,[])
        self.assertTrue(complete.is_set()); self.assertGreaterEqual(time.monotonic()-started,.25)
        self.assertEqual(result['journal']['last_sequence'],4)
        self.assertEqual(self.guard.sequence,4)

    def test_progress_never_renews_absolute_ten_second_deadline(self):
        clock=[0.]; number=[4]; state=[self.new_state()]
        self.f.event(4,state[0])
        def advance(delay):
            clock[0]+=delay
            atomic_status(self.f.root/'execution/status.json',state[0])
            number[0]+=1; state[0]=self.new_state(); self.f.event(number[0],state[0])
        with patch.object(g.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(g.time,'sleep',side_effect=advance):
            with self.assertRaisesRegex(ValueError,'fixed 10 seconds'):
                self.guard.monitor(self.f.plan)
        self.assertAlmostEqual(clock[0],10.); self.assertGreater(number[0],50)
        self.assertEqual(self.guard.sequence,3)
        self.assertEqual(set(self.guard.events),{'event_%06d.json'%n for n in range(1,4)})

    def test_live_pid_ticks_boot_and_flock_changes_are_refused_during_pending_window(self):
        original=copy.deepcopy(self.f.live)
        for field,value in [('pid',74),('start_ticks',406),('boot_id','foreign'),('flock',{})]:
            with self.subTest(field=field):
                self.f.live=copy.deepcopy(original)
                path=self.f.root/'execution/events/event_000004.json'
                if path.exists(): path.unlink()
                state=self.new_state(); self.f.event(4,state); clock=[0.]
                def advance(delay):
                    clock[0]+=delay; self.f.live[field]=value
                with patch.object(g.time,'monotonic',side_effect=lambda:clock[0]), \
                     patch.object(g.time,'sleep',side_effect=advance):
                    with self.assertRaisesRegex(ValueError,'live identity/flock'):
                        self.guard.monitor(self.f.plan)
                self.assertLessEqual(clock[0],.11); self.assertEqual(self.guard.sequence,3)

    def test_complete_invalid_events_and_unrepresented_status_never_wait(self):
        cases=[('action',{'action':'command'}),('phase',{'phase':'UNKNOWN'}),
               ('immutable',{'state':dict(self.new_state(),START='CHANGED')}),
               ('heartbeat',{'state':dict(self.f.state,heartbeat_monotonic=999.)})]
        for label,changes in cases:
            with self.subTest(case=label):
                event={'schema':'opt50_durable_event_v1','sequence':4,'action':'state',
                       'phase':self.f.state['stage'],'state':self.new_state()}
                event.update(changes); fixtures.write(self.f.root/'execution/events/event_000004.json',event)
                with patch.object(g.time,'sleep') as sleep:
                    with self.assertRaises(ValueError): self.guard.monitor(self.f.plan)
                    sleep.assert_not_called()
                self.assertEqual(self.guard.sequence,3)
        self.f.event(4,self.new_state())
        atomic_status(self.f.root/'execution/status.json',dict(self.new_state(),heartbeat_monotonic=1030.))
        self.f.now=1030.
        with patch.object(g.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'absent from validated'):
                self.guard.monitor(self.f.plan)
            sleep.assert_not_called()

    def test_wrong_action_visible_in_incomplete_event_is_immediate_refusal(self):
        path=self.f.root/'execution/events/event_000004.json'
        path.write_bytes(b'{"action":"command","schema":"opt50_durable_event_v1","state":')
        path.chmod(0o600)
        with patch.object(g.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'unexpected in-progress guardian event action'):
                self.guard.monitor(self.f.plan)
            sleep.assert_not_called()

    def test_new_validated_prefix_changed_while_waiting_is_fatal(self):
        self.f.event(4,self.new_state()); clock=[0.]
        def advance(delay):
            clock[0]+=delay
            path=self.f.root/'execution/events/event_000004.json'
            value=json.loads(path.read_bytes()); value['extra']='changed'; fixtures.write(path,value)
        with patch.object(g.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(g.time,'sleep',side_effect=advance):
            with self.assertRaisesRegex(ValueError,'validated new event prefix changed'):
                self.guard.monitor(self.f.plan)
        self.assertEqual(self.guard.sequence,3)

    def test_initial_binding_accepts_a_legal_heartbeat_published_after_full_review(self):
        original=self.f.c._journal_and_state; errors=[]; threads=[]
        def reviewed(plan):
            saved,records=original(plan); state=self.new_state(); self.f.event(4,state)
            def finish():
                try: time.sleep(.22); atomic_status(self.f.root/'execution/status.json',state)
                except BaseException as error: errors.append(error)
            thread=threading.Thread(target=finish); threads.append(thread); thread.start()
            return saved,records
        self.f.c._journal_and_state=reviewed
        guarded=g.bind_checkpoint(self.f.a,self.f.plan)
        for thread in threads: thread.join(2)
        self.assertEqual(errors,[]); self.assertEqual(guarded.sequence,4)
        self.assertEqual(guarded.last_status['heartbeat_monotonic'],self.f.now)

    def test_atomic_status_reader_can_finish_an_opened_old_inode_after_replace(self):
        pub=self.guard.publisher; path=self.f.root/'execution/status.json'
        state=self.new_state(); original_fdopen=pub.os.fdopen; changed=[False]
        def opening(descriptor,*args,**kwargs):
            stream=original_fdopen(descriptor,*args,**kwargs)
            if not changed[0]:
                changed[0]=True
                try: atomic_status(path,state)
                except PermissionError:
                    stream.close()
                    self.skipTest('Windows open-handle sharing cannot model POSIX atomic unlink; covered by metadata policy tests')
            return stream
        with patch.object(pub.os,'fdopen',side_effect=opening):
            previous=pub.read_status(path,self.f.c.decode_json,g.metadata,regular)
        self.assertEqual(previous,self.f.state)
        self.assertEqual(json.loads(path.read_bytes()),state)

    def test_opened_unlinked_inode_policy_is_explicitly_simulated_on_windows(self):
        pub=self.guard.publisher; path=self.f.root/'execution/status.json'
        old=self.f.root/'execution/opened.previous'; fixtures.write(old,self.f.state)
        atomic_status(path,self.new_state()); original_open=pub.os.open; original_stat=pub.os.fstat
        def open_old(*args,**kwargs): return original_open(old,os.O_RDONLY|getattr(os,'O_BINARY',0))
        def unlinked(descriptor):
            info=original_stat(descriptor)
            fields={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
            fields['st_nlink']=0
            return types.SimpleNamespace(**fields)
        with patch.object(pub.os,'open',side_effect=open_old),patch.object(pub.os,'fstat',side_effect=unlinked):
            previous=pub.read_status(path,self.f.c.decode_json,g.metadata,regular)
        self.assertEqual(previous,self.f.state)
        # The named current status may never use that nlink=0 exception.
        fields={name:getattr(path.lstat(),name) for name in dir(path.lstat()) if name.startswith('st_')}
        fields['st_nlink']=0
        with self.assertRaises(ValueError): regular(types.SimpleNamespace(**fields))


class FullJournalPublicationTests(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'.test_build'; folder.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=folder,prefix='full_publisher_')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); (self.root/'execution/events').mkdir(parents=True)
        spec=importlib.util.spec_from_file_location('_full_guardian_fix2',ROOT/'support_py/resident.py')
        self.c=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.c)
        c=self.c; c.OLD_PLAN=self.root/'plan.json'; c.BOOT='fixture-boot'; c.GUARDIAN_UNIT='fixture-unit'
        c.LOAD_STATUS_SCHEMA='fixture-status'; self.now=1000.; self.live_calls=0
        self.live={'pid':73,'start_ticks':405,'boot_id':c.BOOT,'flock':{'pid':73,'inode':999}}
        self.state={'schema':c.LOAD_STATUS_SCHEMA,'stage':'LOAD_ONLY_OBSERVED_AWAITING_REVIEW',
                    'boot_id':c.BOOT,'unit':c.GUARDIAN_UNIT,'run_dir':str(self.root/'execution'),
                    'guardian_pid':73,'child_pid':None,'CSR':'BLOCKED','START':'BLOCKED',
                    'PL_START_commands_issued':0,'physical_PL_starts':'NOT_MEASURED_NO_CSR_ACCESS',
                    'source_package_modified':False,'automatic_restoration':'DISABLED',
                    'saved_status_only':True,'heartbeat_monotonic':1000.}
        for number in range(1,4): self.event(number,dict(self.state,heartbeat_monotonic=997.+number))
        fixtures.write(self.root/'execution/status.json',self.state)
        def observe(plan,state,expected=None,**kwargs):
            self.live_calls+=1
            if expected is not None:
                c.require(expected['guardian_pid']==self.live['pid'] and
                          expected['guardian_start_ticks']==self.live['start_ticks'] and
                          expected['boot_id']==self.live['boot_id'],'fixture live identity changed')
            return copy.deepcopy(self.live)
        validate=c._validate_state
        overrides={'private_dir':lambda path: c.require(Path(path).is_dir(),'missing fixture directory'),
                   '_publication_private':regular,'_observe_guardian':observe,
                   'read_file':lambda path,**kwargs:Path(path).read_bytes(),
                   '_validate_state':lambda state,plan,now=None:validate(state,plan,now=self.now if now is None else now)}
        for name,value in overrides.items(): setattr(c,name,value)

    def event(self,number,state,action='state'):
        fixtures.write(self.root/'execution/events'/('event_%06d.json'%number),
                       {'schema':'opt50_durable_event_v1','sequence':number,'action':action,
                        'phase':state['stage'],'state':state})

    def test_full_reader_uses_same_publish_order_and_fresh_live_checks(self):
        self.now=1001.; state=dict(self.state,heartbeat_monotonic=self.now); self.event(4,state)
        errors=[]
        def writer():
            try: time.sleep(.26); atomic_status(self.root/'execution/status.json',state)
            except BaseException as error: errors.append(error)
        thread=threading.Thread(target=writer); thread.start(); self.addCleanup(thread.join)
        saved,events=self.c._journal_and_state({})
        thread.join(2); self.assertEqual(errors,[])
        self.assertEqual(saved,state); self.assertEqual(len(events),4); self.assertGreaterEqual(self.live_calls,3)

    def test_full_reader_rejects_live_lock_change_during_wait(self):
        self.now=1001.; self.event(4,dict(self.state,heartbeat_monotonic=self.now)); clock=[0.]
        def advance(delay): clock[0]+=delay; self.live['flock']={}
        with patch.object(self.c.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(self.c.time,'sleep',side_effect=advance):
            with self.assertRaisesRegex(ValueError,'live flock changed'):
                self.c._journal_and_state({})
        self.assertLessEqual(clock[0],.11)

    def test_full_reader_unknown_history_or_action_after_held_is_not_pending(self):
        self.now=1001.; self.event(4,dict(self.state,heartbeat_monotonic=self.now),action='command')
        with patch.object(self.c.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'unexpected action/phase after held'):
                self.c._journal_and_state({})
            sleep.assert_not_called()

    def test_full_reader_validated_prefix_tamper_is_immediate_during_pending(self):
        self.now=1001.; self.event(4,dict(self.state,heartbeat_monotonic=self.now)); clock=[0.]
        def advance(delay):
            clock[0]+=delay
            path=self.root/'execution/events/event_000001.json'
            value=json.loads(path.read_bytes()); value['changed']='tamper'; fixtures.write(path,value)
        with patch.object(self.c.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(self.c.time,'sleep',side_effect=advance):
            with self.assertRaisesRegex(ValueError,'immutable journal prefix changed'):
                self.c._journal_and_state({})
        self.assertLessEqual(clock[0],.11)


if __name__=='__main__': unittest.main()
