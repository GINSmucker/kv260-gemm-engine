"""Software-only preparation orchestration with all ARM/service/file helpers mocked.

No real root-path reads, service commands, owner, run_once, signals, compilation
or device access occur. The before_owner/build helper contracts have separate
native/file tests; this suite checks call ordering and refusal/no-retry behavior.
"""
from __future__ import annotations
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

ROOT=Path(__file__).absolute().parents[1]
spec=importlib.util.spec_from_file_location('prepare_fix1_under_test',ROOT/'prepare_fix1.py')
prepare=importlib.util.module_from_spec(spec); spec.loader.exec_module(prepare)


class MockPath:
    """A path value that never calls the filesystem."""
    def __init__(self,text,harness): self.text=text; self.harness=harness
    def __truediv__(self,name): return MockPath(self.text.rstrip('/')+'/'+str(name),self.harness)
    @property
    def parent(self): return MockPath(self.text.rsplit('/',1)[0],self.harness)
    def is_file(self):
        self.harness.path_queries.append(self.text)
        return self.harness.receipt_exists
    def __str__(self): return self.text
    def __repr__(self): return 'MockPath('+repr(self.text)+')'
    def __eq__(self,other): return isinstance(other,MockPath) and self.text==other.text


class Harness:
    def __init__(self,*,receipt_exists=False,states=None,times=None):
        self.receipt_exists=receipt_exists; self.path_queries=[]; self.order=[]
        self.current=types.SimpleNamespace(c=object(),tools={'manifest':{'bytes':123,'sha256':'a'*64}})
        self.refusal={'stage':'REFUSED_BATCH_BEFORE_OWNER','hardware_accessed':False,'plan':'exact old plan'}
        self.closed={'MainPID':'0','ActiveState':'active','SubState':'exited','ExecMainStatus':'0','Result':'success'}
        self.states=list(states or [self.closed]); self.times=list(times or [0.0])
        self.root=MockPath('/software/fix1',self)
        self.target=MockPath('/var/lib/opt50-b24-model-v1/plan_b06c1e69f409/plan.json',self)
        self.receipt={'status':'ARM_RESIDENT_SOURCE_IDENTICAL_REUSE_PASS_NOT_BOARD_RUN',
                      'build_method':'reuse_verified_source_identical'}
        self.verify=mock.Mock(side_effect=lambda:self.order.append('verify-static'))
        self.api=mock.Mock(side_effect=lambda:(self.order.append('api') or self.current))
        self.retire=mock.Mock(side_effect=lambda a:(self.order.append('retire') or self.refusal))
        self.validate=mock.Mock(side_effect=lambda *a:(self.order.append('validate-refusal') or self.refusal))
        self.service=mock.Mock(side_effect=self.observe)
        self.build_verify=mock.Mock(side_effect=lambda output:(self.order.append('verify-build') or self.receipt))
        self.reuse=mock.Mock(side_effect=lambda output:(self.order.append('reuse-build') or self.receipt))
        self.forbidden=mock.Mock(side_effect=AssertionError('forbidden owner/run_once/signal/spawn operation'))
        def need(ok,reason):
            if not ok: raise ValueError(reason)
        self.verify_module=types.ModuleType('verify_tools')
        self.verify_module.verify=self.verify; self.verify_module.need=need; self.verify_module.ROOT=self.root
        self.run_module=types.ModuleType('run_model'); self.run_module.api=self.api
        for name in ('run','check','worker_context'): setattr(self.run_module,name,self.forbidden)
        self.before_module=types.ModuleType('before_owner')
        self.before_module.TARGET=self.target; self.before_module.retire=self.retire
        self.before_module.validate_refusal=self.validate; self.before_module.service=self.service
        self.build_module=types.ModuleType('build_arm')
        self.build_module.verify_build=self.build_verify; self.build_module.reuse_build=self.reuse
        self.front_module=types.ModuleType('run_once'); self.front_module.run_once=self.forbidden

    def observe(self,unit):
        self.order.append('observe-outer')
        if unit != prepare.OUTER: raise AssertionError('only exact old outer may be observed')
        if not self.states: raise AssertionError('unexpected extra service observation')
        return self.states.pop(0)

    def invoke(self,authorized=True,verify_only=False):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(sys.modules,{
                'verify_tools':self.verify_module,'run_model':self.run_module,
                'before_owner':self.before_module,'build_arm':self.build_module,'run_once':self.front_module}))
            stack.enter_context(mock.patch.object(prepare.os,'name','posix'))
            stack.enter_context(mock.patch.object(prepare.os,'geteuid',return_value=0,create=True))
            stack.enter_context(mock.patch.object(prepare.os,'kill',self.forbidden))
            stack.enter_context(mock.patch.object(prepare.os,'killpg',self.forbidden,create=True))
            stack.enter_context(mock.patch.object(prepare.os,'system',self.forbidden))
            stack.enter_context(mock.patch.object(subprocess,'run',self.forbidden))
            stack.enter_context(mock.patch.object(subprocess,'Popen',self.forbidden))
            self.clock=stack.enter_context(mock.patch.object(prepare.time,'monotonic',side_effect=self.times))
            self.sleep=stack.enter_context(mock.patch.object(prepare.time,'sleep'))
            self.prints=stack.enter_context(mock.patch('builtins.print'))
            return prepare.prepare(authorized,verify_only)

    def assert_no_hardware(self,test): self.forbidden.assert_not_called()


class PrepareFix1Tests(unittest.TestCase):
    def test_missing_authorization_performs_no_verification_retirement_service_or_build(self):
        h=Harness()
        with self.assertRaisesRegex(ValueError,'authorization'): h.invoke(False,False)
        for op in (h.verify,h.api,h.retire,h.validate,h.service,h.build_verify,h.reuse): op.assert_not_called()
        self.assertEqual(h.path_queries,[]); h.assert_no_hardware(self)

    def test_before_owner_refusal_blocks_staging_and_service_wait(self):
        h=Harness(); h.retire.side_effect=ValueError('old failed plan has ambiguous Native artifacts')
        with self.assertRaisesRegex(ValueError,'ambiguous'): h.invoke()
        h.service.assert_not_called(); h.reuse.assert_not_called(); h.build_verify.assert_not_called()
        h.assert_no_hardware(self)

    def test_live_old_wrapper_is_only_observed_until_it_exits_naturally(self):
        live={'MainPID':'321','ActiveState':'active','SubState':'running'}
        h=Harness(states=[live,live,{'MainPID':'0','ActiveState':'failed','SubState':'failed'}],times=[0,1,2])
        value=h.invoke()
        self.assertEqual(h.service.call_count,3); self.assertEqual(h.sleep.call_args_list,[mock.call(1),mock.call(1)])
        self.assertLess(h.order.index('retire'),h.order.index('observe-outer'))
        self.assertEqual(h.order[-1],'reuse-build'); h.assert_no_hardware(self)
        self.assertEqual(value['status'],'FIX1_SOFTWARE_READY_NO_NEW_COMPUTE')
        self.assertFalse(value['signals_sent']); self.assertFalse(value['hardware_accessed'])
        self.assertEqual(value['START_commands_issued'],0); self.assertFalse(value['old_failed_plan_reused'])

    def test_wrapper_timeout_never_signals_or_reuses_build(self):
        h=Harness(states=[{'MainPID':'321','ActiveState':'active','SubState':'running'}],times=[0,31])
        with self.assertRaisesRegex(ValueError,'no kill/replay'): h.invoke()
        h.service.assert_called_once_with(prepare.OUTER)
        h.reuse.assert_not_called(); h.build_verify.assert_not_called(); h.sleep.assert_not_called()
        h.assert_no_hardware(self)

    def test_a_nonzero_pid_is_not_mistaken_for_a_finished_wrapper(self):
        h=Harness(states=[{'MainPID':'321','ActiveState':'failed','SubState':'failed'}],times=[0,31])
        with self.assertRaisesRegex(ValueError,'not exited'): h.invoke()
        h.reuse.assert_not_called(); h.assert_no_hardware(self)

    def test_existing_receipt_is_verified_without_reuse_or_overwrite(self):
        h=Harness(receipt_exists=True); value=h.invoke()
        h.build_verify.assert_called_once_with(h.root/'build_arm'); h.reuse.assert_not_called()
        self.assertEqual(h.path_queries,['/software/fix1/build_arm/build_receipt.json'])
        self.assertEqual(value['build_method'],'reuse_verified_source_identical'); h.assert_no_hardware(self)

    def test_empty_build_output_delegates_to_exact_source_identical_reuse_once(self):
        h=Harness(receipt_exists=False); h.invoke()
        h.reuse.assert_called_once_with(h.root/'build_arm'); h.build_verify.assert_not_called()
        self.assertEqual(h.path_queries,['/software/fix1/build_arm/build_receipt.json']); h.assert_no_hardware(self)

    def test_verify_only_reads_existing_retirement_and_build_without_writing(self):
        h=Harness(receipt_exists=False); h.invoke(False,True)
        h.retire.assert_not_called(); h.validate.assert_called_once_with(h.current.c,h.target.parent,h.current.tools['manifest'])
        h.build_verify.assert_called_once_with(h.root/'build_arm'); h.reuse.assert_not_called()
        self.assertEqual(h.path_queries,[])  # verify_only never probes an output to decide whether to create it.
        h.assert_no_hardware(self)

    def test_verify_only_refusal_cannot_fall_back_to_creating_retirement(self):
        h=Harness(); h.validate.side_effect=ValueError('missing verified retirement')
        with self.assertRaisesRegex(ValueError,'missing verified'): h.invoke(False,True)
        h.retire.assert_not_called(); h.reuse.assert_not_called(); h.build_verify.assert_not_called()
        h.service.assert_not_called(); h.assert_no_hardware(self)

    def test_build_reuse_error_is_propagated_without_retry_or_compute(self):
        h=Harness(); h.reuse.side_effect=ValueError('source mismatch')
        with self.assertRaisesRegex(ValueError,'source mismatch'): h.invoke()
        h.reuse.assert_called_once(); h.build_verify.assert_not_called(); h.retire.assert_called_once()
        h.assert_no_hardware(self)


if __name__=='__main__': unittest.main(verbosity=2)
