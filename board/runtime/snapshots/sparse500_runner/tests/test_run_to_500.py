"""Finite orchestration tests; all board/API/subprocess observations are injected."""
from contextlib import contextmanager
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).absolute().parents[1]
spec=importlib.util.spec_from_file_location('_finite500_runner_test',ROOT/'run_to_500.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
SHA='a'*64


class Fake:
    def __init__(self,prefix=305,existing=False,pilot_after=310):
        self.prefix=prefix;self.existing=existing;self.calls=[];self.records=[];self.submissions=[]
        self.failure=None;self.changed=None;self.report_failure=False;self.managed=False
        self.pilot_after=pilot_after
    @contextmanager
    def session(self):
        self.calls.append('session');self.managed=True
        try:yield
        finally:self.managed=False
    def wait_software(self):self.calls.append('verify_arm_files')
    def wait_existing_pilot(self):self.calls.append('wait_known_pilot');return self.existing
    def snapshot(self):
        self.calls.append('strict_index')
        value={'prefix':self.prefix,'boot_id':'boot','dataset_pin':'dataset'}
        if self.submissions and self.changed:value.update(self.changed)
        return value
    def pilot(self,view):return '/fixed-five-image/plan.json' if view['prefix']>=self.pilot_after else None
    def record(self,stage,**details):self.records.append((stage,details))
    def submit(self,start,count,pilot):
        self.submissions.append((start,count,pilot))
        if self.failure:raise self.failure
        self.prefix+=count
        return {'plan':'/var/lib/opt50-b24-model-v1/plan_0123456789ab/plan.json'}
    def wait_inner(self,plan):self.calls.append('wait_own_inner_closed')
    def report(self):
        self.calls.append('report500')
        if self.report_failure:raise ValueError('saved logits changed')
        return {'out':'new-export/report'}


class FiniteTests(unittest.TestCase):
    def test_reboot305_prefix_gets_new_five_then_fifties_without_replaying_accepted_images(self):
        f=Fake(305,pilot_after=310);value=r.run(True,True,SHA,f)
        self.assertEqual(value['completed_prefix'],500)
        self.assertEqual([(start,count) for start,count,_ in f.submissions],
                         [(305,5),(310,50),(360,50),(410,50),(460,40)])
        self.assertIsNone(f.submissions[0][2]);self.assertTrue(all(pilot for _,_,pilot in f.submissions[1:]))
        self.assertEqual(sum(count for _,count,_ in f.submissions),195)
        self.assertEqual(f.records[0][1]['maximum_additional_START_commands'],7020)

    def test_finite305_to500_new_five_then_sequential_fifties_and_final_remainder(self):
        f=Fake();value=r.run(True,True,SHA,f)
        self.assertEqual(value['completed_prefix'],500);self.assertEqual(len(f.submissions),5)
        self.assertEqual(f.submissions[0],(305,5,None));self.assertEqual(f.submissions[-1][0:2],(460,40))
        self.assertEqual(sum(n for _,n,_ in f.submissions),195)
        self.assertEqual(len({start for start,_,_ in f.submissions}),len(f.submissions))
        self.assertTrue(all(n<=50 for _,n,_ in f.submissions))
        self.assertTrue(all(pilot is not None for _,n,pilot in f.submissions if n>5))
        self.assertEqual(f.calls[-1],'report500');self.assertFalse(f.managed)

    def test_existing_five_pilot_is_only_observed_before_new_unexecuted_images(self):
        f=Fake(310,True);r.run(True,True,SHA,f)
        self.assertEqual(f.submissions[0][0:2],(310,50));self.assertEqual(sum(x[1] for x in f.submissions),190)
        self.assertLess(f.calls.index('wait_known_pilot'),f.calls.index('strict_index'))

    def test_existing_pilot_success_without_indexed_five_is_not_permission_to_replay(self):
        f=Fake(305,True)
        with self.assertRaisesRegex(ValueError,'five-image proof'):r.run(True,True,SHA,f)
        self.assertEqual(f.submissions,[])

    def test_missing_authorization_uart_or_pin_performs_no_session_or_submission(self):
        for authorized,uart,sha in ((False,True,SHA),(True,False,SHA),(True,True,'unknown')):
            f=Fake()
            with self.assertRaises(ValueError):r.run(authorized,uart,sha,f)
            self.assertEqual(f.calls,[]);self.assertEqual(f.submissions,[])

    def test_batch_failure_or_uncertain_submission_stops_after_one_call_no_report(self):
        for reason in ('RETAINED owner','partial closed','submit return uncertain'):
            f=Fake();f.failure=ValueError(reason)
            with self.assertRaisesRegex(ValueError,reason):r.run(True,True,SHA,f)
            self.assertEqual(len(f.submissions),1);self.assertNotIn('report500',f.calls)
            self.assertEqual(f.records[-1][0],'STOPPED_NO_RETRY')

    def test_progress_must_equal_exact_new_closed_prefix_and_same_boot_dataset(self):
        for changed in ({'prefix':305},{'prefix':311},{'boot_id':'another'},{'dataset_pin':'changed'}):
            f=Fake();f.changed=changed
            with self.assertRaisesRegex(ValueError,'prefix/boot/dataset'):r.run(True,True,SHA,f)
            self.assertEqual(len(f.submissions),1);self.assertNotIn('report500',f.calls)

    def test_target500_does_not_submit_a_new_model_and_report_failure_does_not_replay(self):
        f=Fake(500);f.report_failure=True
        with self.assertRaisesRegex(ValueError,'logits changed'):r.run(True,True,SHA,f)
        self.assertEqual(f.submissions,[]);self.assertEqual(f.calls.count('report500'),1)

    def test_bounds_reject_bool_holes_or_over500_and_never_authorize_large_without_pilot(self):
        for prefix in (True,0,99,501):
            with self.assertRaises(ValueError):r.selection(prefix,None)
        self.assertEqual(r.selection(100,None),5);self.assertEqual(r.selection(499,'pilot'),1)

    def test_reboot_entry_refuses_an_older_prefix_without_submitting_or_exporting(self):
        for prefix in (100,304):
            f=Fake(prefix)
            with self.assertRaisesRegex(ValueError,'at least305'):r.run(True,True,SHA,f)
            self.assertEqual(f.submissions,[]);self.assertNotIn('report500',f.calls)


class ReadonlyWaitTests(unittest.TestCase):
    def test_running_fixed_unit_waits_readonly_then_success_without_process_actions(self):
        rt=r.Runtime(SHA);rt.record=Mock();rt.no_other_active=Mock();clock=[0.]
        rt.service=Mock(side_effect=[{'LoadState':'loaded','MainPID':'12','SubState':'running'},
            {'LoadState':'loaded','MainPID':'0','SubState':'exited','Result':'success','ExecMainStatus':'0'}])
        with patch.object(r.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(r.time,'sleep',side_effect=lambda d:clock.__setitem__(0,clock[0]+d)), \
             patch.object(r.subprocess,'run',side_effect=AssertionError('no process submission')):
            self.assertTrue(rt.wait_existing_pilot())
        self.assertEqual(clock[0],5);self.assertEqual(rt.service.call_args.args,(r.PILOT_UNIT,))
        rt.no_other_active.assert_not_called()  # Its legitimate dynamic inner is running.

    def test_timeout_never_kills_stops_or_retries_existing_unit(self):
        rt=r.Runtime(SHA);rt.record=Mock();rt.no_other_active=Mock()
        rt.service=Mock(return_value={'LoadState':'loaded','MainPID':'12','SubState':'running'});clock=[0.]
        with patch.object(r.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(r.time,'sleep',side_effect=lambda d:clock.__setitem__(0,clock[0]+d)), \
             patch.object(r.subprocess,'run',side_effect=AssertionError('no process control')):
            with self.assertRaisesRegex(ValueError,'wait expired'):rt.wait_existing_pilot()
        self.assertEqual(clock[0],1200)

    def test_missing_pilot_is_not_started_by_wait_and_failed_pilot_is_refused(self):
        rt=r.Runtime(SHA);rt.record=Mock();rt.no_other_active=Mock()
        rt.service=Mock(return_value={'LoadState':'not-found'})
        self.assertFalse(rt.wait_existing_pilot())
        rt.service=Mock(return_value={'LoadState':'loaded','MainPID':'0','SubState':'failed','Result':'exit-code','ExecMainStatus':'2'})
        with self.assertRaisesRegex(ValueError,'successfully'):rt.wait_existing_pilot()

    def test_software_service_default_success_cannot_replace_strict_build_receipt_verification(self):
        rt=r.Runtime(SHA);rt.wait_service=Mock(return_value=False);rt.record=Mock()
        rt.a=types.SimpleNamespace(tools={'fixed':True},runtime=types.SimpleNamespace(MANIFEST_SHA='model'))
        rt.entry=types.SimpleNamespace(verify_build=Mock(side_effect=ValueError('missing receipt')))
        with tempfile.TemporaryDirectory(prefix='.h1-receipt-',dir=ROOT) as temporary:
            public=Path(temporary);(public/'build_arm').mkdir();(public/'build_arm/build_receipt.json').write_text('{}')
            with patch.object(r,'PUBLIC',public),patch.object(r.subprocess,'run',side_effect=AssertionError('existing receipt must not stage')):
                with self.assertRaisesRegex(ValueError,'missing receipt'):rt.wait_software()
        rt.entry.verify_build.assert_called_once_with(rt.a.tools,'model');rt.record.assert_not_called()

    def test_json_log_parser_rejects_hidden_nonjson_diagnostics(self):
        self.assertEqual(r.json_records('{}\n{"stage":"PASS"}\n')[-1]['stage'],'PASS')
        with self.assertRaises(ValueError):r.json_records('{}\nnot proof\n')


class SingleSubmissionTests(unittest.TestCase):
    def fixture(self):
        rt=r.Runtime(SHA,Path('/fixture/new-guardian/plan.json'),Path('/fixture/new-layer/plan.json'))
        rt.folder=Path('/fixture/new-runner');rt.boot='boot'
        rt.no_other_active=Mock();rt.entry=types.SimpleNamespace(new_bytes=Mock(),module_checked=Mock())
        rt.a=types.SimpleNamespace(c=types.SimpleNamespace(read_json=Mock()),durable=types.SimpleNamespace(new_json=Mock()),tools={})
        return rt

    def test_failed_real_submit_helper_calls_only_fixed_run_once_once_without_timeout_or_kill(self):
        rt=self.fixture();child=types.SimpleNamespace(returncode=2,stdout='retained',stderr='error')
        with patch.object(r.subprocess,'run',return_value=child) as invoke:
            with self.assertRaisesRegex(ValueError,'failed or uncertain'):rt.submit(100,5,None)
        self.assertEqual(invoke.call_count,1)
        self.assertEqual(invoke.call_args.args[0][:3],['/usr/bin/python3','-B',str(r.PUBLIC/'run_once.py')])
        self.assertNotIn('timeout',invoke.call_args.kwargs);self.assertFalse(invoke.call_args.kwargs['check'])
        self.assertNotIn('--pilot-plan',invoke.call_args.args[0]);rt.a.durable.new_json.assert_called_once()
        argv=invoke.call_args.args[0]
        self.assertEqual(argv[argv.index('--guardian-plan')+1],str(rt.guardian))
        self.assertEqual(argv[argv.index('--layer-plan')+1],str(rt.layer))
        self.assertEqual(rt.entry.new_bytes.call_count,2,'both child logs must be saved even on failure')

    def test_helper_refuses_large_batch_without_five_pilot_before_any_child(self):
        rt=self.fixture()
        with patch.object(r.subprocess,'run') as invoke:
            with self.assertRaisesRegex(ValueError,'pilot required'):rt.submit(105,50,None)
        invoke.assert_not_called();rt.a.durable.new_json.assert_not_called()

    def test_submit_cannot_escape500_or50_budget_even_if_called_directly(self):
        for start,count in ((490,50),(500,1),(105,51),(True,5),(100,0)):
            rt=self.fixture()
            with patch.object(r.subprocess,'run') as invoke:
                with self.assertRaisesRegex(ValueError,'range exceeded'):rt.submit(start,count,'pilot')
            invoke.assert_not_called();rt.a.durable.new_json.assert_not_called()


class ReentrySoftwarePreparationTests(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory(prefix='.h1-stage-',dir=ROOT);self.addCleanup(temporary.cleanup)
        public=Path(temporary.name);rt=r.Runtime(SHA);rt.folder=public/'runner';rt.folder.mkdir()
        rt.record=Mock();rt.no_other_active=Mock()
        rt.a=types.SimpleNamespace(tools={'fixed':True},runtime=types.SimpleNamespace(MANIFEST_SHA='model'),
            c=object(),durable=types.SimpleNamespace(new_json=Mock()))
        rt.entry=types.SimpleNamespace(new_bytes=Mock(),verify_build=Mock(return_value={'schema':'verified-source-identical-reuse'}))
        return public,rt

    def test_missing_directory_prepares_exact_stage_once_then_strictly_verifies(self):
        public,rt=self.fixture()
        def stage(argv,**kwargs):
            self.assertEqual(argv,['/usr/bin/python3','-B',str(public/'stage_arm.py')])
            self.assertNotIn('timeout',kwargs);self.assertFalse(kwargs['check'])
            (public/'build_arm').mkdir();(public/'build_arm/build_receipt.json').write_text('{}')
            return types.SimpleNamespace(returncode=0,stdout='source-identical CPU tests passed',stderr='')
        with patch.object(r,'PUBLIC',public),patch.object(r.subprocess,'run',side_effect=stage) as invoke:
            rt.wait_software();self.assertEqual(invoke.call_count,1)
            rt.wait_software();self.assertEqual(invoke.call_count,1,'existing complete receipt must never rerun stage')
        self.assertEqual(rt.entry.verify_build.call_count,2)
        rt.a.durable.new_json.assert_called_once();self.assertEqual(rt.entry.new_bytes.call_count,2)

    def test_existing_partial_directory_is_refused_without_stage_verify_or_overwrite(self):
        public,rt=self.fixture();build=public/'build_arm';build.mkdir();(build/'partial').write_text('preserve')
        with patch.object(r,'PUBLIC',public),patch.object(r.subprocess,'run') as invoke:
            with self.assertRaisesRegex(ValueError,'partial/linked'):rt.wait_software()
        invoke.assert_not_called();rt.entry.verify_build.assert_not_called()
        self.assertEqual((build/'partial').read_text(),'preserve')

    def test_failed_or_uncertain_stage_is_not_retried_or_accepted(self):
        public,rt=self.fixture();child=types.SimpleNamespace(returncode=2,stdout='partial CPU preparation',stderr='failure')
        with patch.object(r,'PUBLIC',public),patch.object(r.subprocess,'run',return_value=child) as invoke:
            with self.assertRaisesRegex(ValueError,'failed or uncertain'):rt.wait_software()
        self.assertEqual(invoke.call_count,1);rt.entry.verify_build.assert_not_called()
        self.assertEqual(rt.entry.new_bytes.call_count,2)

    def test_zero_return_without_receipt_is_refused_and_never_submits_compute(self):
        public,rt=self.fixture();child=types.SimpleNamespace(returncode=0,stdout='unproven',stderr='')
        with patch.object(r,'PUBLIC',public),patch.object(r.subprocess,'run',return_value=child) as invoke:
            with self.assertRaisesRegex(ValueError,'complete receipt'):rt.wait_software()
        self.assertEqual(invoke.call_count,1);rt.entry.verify_build.assert_not_called()

    def test_new_reentry_source_and_unused_pilot_are_bound_without_waiting_old_failed_unit(self):
        self.assertEqual(r.FIX2,{'bytes':25141,'sha256':'38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d'})
        self.assertIn('b24_accuracy_resident_20261030_reboot_r1/software',r.PUBLIC.as_posix())
        self.assertEqual(r.PILOT_UNIT,'opt50-b24-resident-v4-reboot-r1-pilot.service')
        self.assertIn('acceptance500_reboot_exports',str(r.EXPORTS))


class CurrentBootProofTests(unittest.TestCase):
    def fixture(self):
        rt=r.Runtime(SHA,Path('/fixture/new-guardian/plan.json'),Path('/fixture/new-layer/plan.json'))
        guardian={'boot_id':'new-boot'}
        layer={'boot_id':'new-boot','guardian_plan':str(rt.guardian)}
        selected={'unit':'opt50-loadonly-v3-0123456789ab','boot_id':'new-boot'}
        rt.a=types.SimpleNamespace(c=types.SimpleNamespace(
            configure=Mock(return_value=selected),read_json=Mock(side_effect=[guardian,layer])))
        return rt,guardian,layer

    def test_fresh_guardian_and_layer_bind_dynamic_unit_and_current_boot(self):
        rt,_,_=self.fixture()
        with patch.object(r.Path,'read_text',return_value='new-boot\n'):
            rt.current_proofs()
        rt.a.c.configure.assert_called_once_with(rt.guardian)
        self.assertEqual(rt.boot,'new-boot')
        self.assertEqual(rt.guardian_unit,'opt50-loadonly-v3-0123456789ab.service')

    def test_either_old_boot_or_another_guardian_in_layer_refuses_before_stage_or_submission(self):
        for mutation in ('guardian_boot','layer_boot','layer_guardian'):
            rt,guardian,layer=self.fixture()
            if mutation=='guardian_boot':guardian['boot_id']='old-boot'
            elif mutation=='layer_boot':layer['boot_id']='old-boot'
            else:layer['guardian_plan']='/fixture/old-guardian/plan.json'
            with patch.object(r.Path,'read_text',return_value='new-boot\n'),patch.object(r.subprocess,'run') as child:
                with self.assertRaisesRegex(ValueError,'another boot or residency'):rt.current_proofs()
            child.assert_not_called()

    def test_missing_proofs_have_no_fallback_to_old_boot(self):
        rt=r.Runtime(SHA);rt.a=types.SimpleNamespace(c=types.SimpleNamespace(configure=Mock()))
        with self.assertRaisesRegex(ValueError,'explicit current-boot'):rt.current_proofs()
        rt.a.c.configure.assert_not_called()
        with patch.object(r,'Runtime') as runtime,patch.object(r.sys,'stderr',new=io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                r.main(['--manifest-sha256',SHA,'--authorized-to-500','--uart-log-confirmed'])
        self.assertEqual(stopped.exception.code,2);runtime.assert_not_called()


if __name__=='__main__':unittest.main()
