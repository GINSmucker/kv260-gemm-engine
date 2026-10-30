"""No-Native recovery evidence tests; OS ownership/flock and services are explicit fakes."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT=Path(__file__).absolute().parent.parent
sys.path.insert(0,str(ROOT))
import before_owner as b


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(value,sort_keys=True)+'\n').encode())


class C:
    # Platform primitives are covered by the separate native POSIX smoke test.
    def private_dir(self,path):
        if not Path(path).is_dir():raise ValueError('not directory')
    def read_file(self,path,**_):return Path(path).read_bytes()
    def read_json(self,path,**_):return json.loads(Path(path).read_bytes())
    def decode_json(self,raw):return json.loads(raw)
    def root_linux(self):pass
    @contextmanager
    def companion_locked(self):yield 99


class D:
    @staticmethod
    def replace_json(path,value):save(path,value)
    @staticmethod
    def new_json(path,value):
        with Path(path).open('xb') as stream:stream.write(json.dumps(value,sort_keys=True).encode())


class BeforeOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.test_build',prefix='before-owner-')
        self.records=Path(self.temp.name)/'records';self.root=self.records/'plan_b06c1e69f409'
        (self.root/'tools').mkdir(parents=True);(self.root/'model').mkdir()
        self.plan_path=self.root/'plan.json'; self.boot='2b0eaebd-2dd9-45c5-b135-26315bfdbb7f'
        files={}
        for n in range(140):
            name='fixture_%03d.py'%n;raw=('data%d'%n).encode()
            (self.root/'tools'/name).write_bytes(raw);files[name]=b.identity(raw)
        raw=json.dumps({'files':files}).encode();(self.root/'tools/tools_manifest.json').write_bytes(raw)
        self.pin=b.identity(raw)
        self.p={'schema':'opt50_b24_acceptance_batch_plan_v1','plan_path':str(self.plan_path),
            'token':'b06c1e69f409','unit':'opt50-b24-model-b06c1e69f409','boot_id':self.boot,
            'tool_files':{'manifest':self.pin},'lifecycle_profile':'resident-bounded-batch-v4',
            'measurement_profile':'b24-batch-acceptance-cpu4-pl4-v1','guardian_pid':15656,
            'sample_start':45,'sample_count':5,'total_START_budget':180}
        save(self.plan_path,self.p);self.a=types.SimpleNamespace(c=C(),durable=D(),tools={'manifest':{'bytes':2,'sha256':'f'*64}})
        self.unit=self.p['unit']+'.service'
        self.props={'Id':self.unit,'LoadState':'loaded','ActiveState':'failed','SubState':'failed',
                    'MainPID':'0','ExecMainStatus':'2','Result':'exit-code'}
        self.event={'message':{'status':'B24_RESIDENT_REFUSED_NO_RETRY','error':
            "FileNotFoundError: [Errno 2] No such file or directory: "+repr(str(self.root/'batch_events'))},'event_sha256':'e'*64}
        self.patches=[mock.patch.object(b,'TARGET',self.plan_path),mock.patch.object(b,'RECORDS',self.records),
            mock.patch.object(b,'V4',self.pin),mock.patch.object(b,'current_boot',return_value=self.boot),
            mock.patch.object(b,'service',return_value=self.props),mock.patch.object(b,'failure_event',return_value=self.event)]
        for item in self.patches:item.start()

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()

    def test_actual_future_startup_refusal_is_admitted_without_fake_close(self):
        import run_model as entry
        raw=(self.root/'tools/tools_manifest.json').read_bytes()+b' '
        (self.root/'tools/tools_manifest.json').write_bytes(raw)
        current=b.identity(raw);self.p['tool_files']['manifest']=current
        save(self.plan_path,self.p);self.a.tools['manifest']=current
        (self.root/'batch_events').mkdir()
        entry.startup_refusal(self.a,self.p,'CONSTRUCTING_COORDINATOR_BEFORE_EXECUTION',ValueError('mock constructor'))
        b.validate_refusal(self.a.c,self.root,current)
        self.assertFalse((self.root/'batch_completion.json').exists())
        self.assertFalse((self.root/'execution').exists())

    def test_check_only_writes_nothing(self):
        before=sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        result=b.retire(self.a,check_only=True)
        self.assertEqual(result['status'],'UNSTARTED_REVIEW_PASS_NO_STATUS_WRITTEN')
        self.assertEqual(before,sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_retire_is_truthful_zero_images_no_closed_claim_no_execution(self):
        result=b.retire(self.a)
        self.assertFalse(result['index_modified']);self.assertFalse(result['hardware_accessed'])
        state=self.a.c.read_json(self.root/'batch_status.json')
        self.assertEqual(state['stage'],b.STAGE);self.assertEqual(state['committed_samples'],0)
        self.assertNotIn('completion',state);self.assertNotIn('closed',state)
        self.assertFalse((self.root/'batch_completion.json').exists())
        self.assertFalse((self.root/'execution').exists())
        self.assertEqual(self.a.c.read_json(self.plan_path),self.p)
        b.validate_refusal(self.a.c,self.root,self.a.tools['manifest'])

    def test_repeated_retire_is_readonly(self):
        b.retire(self.a);path=self.root/'batch_status.json';raw=path.read_bytes();stamp=path.stat().st_mtime_ns
        self.assertEqual(b.retire(self.a)['status'],'UNSTARTED_ATTEMPT_ALREADY_RECORDED_NO_CHANGES')
        self.assertEqual(path.read_bytes(),raw);self.assertEqual(path.stat().st_mtime_ns,stamp)

    def test_any_execution_artifact_blocks_zero_claim(self):
        for name in b.ABSENT+('batch_events','batch_started.json','batch_completion.json'):
            path=self.root/name;path.mkdir()
            with self.subTest(name=name),self.assertRaises(ValueError):b.retire(self.a)
            self.assertFalse((self.root/'batch_status.json').exists());path.rmdir()

    def test_service_not_proven_failed_exited_two_refused(self):
        for key,value in [('MainPID','123'),('ExecMainStatus','0'),('ActiveState','active'),('Result','success'),('LoadState','not-found')]:
            original=self.props[key];self.props[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):b.retire(self.a)
            self.assertFalse((self.root/'batch_status.json').exists());self.props[key]=original

    def test_changed_source_refused_before_any_write(self):
        (self.root/'tools/fixture_000.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source changed'):b.retire(self.a)
        self.assertFalse((self.root/'batch_status.json').exists())

    def test_different_boot_or_selected_images_refused(self):
        self.p['sample_start']=46;save(self.plan_path,self.p)
        with self.assertRaises(ValueError):b.retire(self.a)
        self.p['sample_start']=45;save(self.plan_path,self.p)
        with mock.patch.object(b,'current_boot',return_value='other'),self.assertRaises(ValueError):b.retire(self.a)

    def test_missing_exact_journal_failure_refused_before_status(self):
        with mock.patch.object(b,'failure_event',side_effect=ValueError('no exact failure')),self.assertRaises(ValueError):b.retire(self.a)
        self.assertFalse((self.root/'batch_status.json').exists())

    def test_refusal_cannot_hide_native_attempt_or_changed_plan(self):
        b.retire(self.a);path=self.root/'batch_startup_refusal.json';original=self.a.c.read_json(path)
        for key,value in [('native_owner_attempted',True),('worker_attempted',True),('START_commands_issued_by_startup',True),
                          ('phase','AFTER_BEGIN'),('plan_pin',{'bytes':1,'sha256':'0'*64})]:
            changed=dict(original,**{key:value});save(path,changed)
            with self.subTest(key=key),self.assertRaises(ValueError):b.validate_refusal(self.a.c,self.root,self.a.tools['manifest'])
        save(path,original);(self.root/'execution').mkdir()
        with self.assertRaises(ValueError):b.validate_refusal(self.a.c,self.root,self.a.tools['manifest'])

    def test_completed_or_ambiguous_state_never_excluded(self):
        b.retire(self.a);path=self.root/'batch_status.json';original=self.a.c.read_json(path)
        for changed in (dict(original,owner_pid=7),dict(original,committed_samples=1),dict(original,completion={'closed':True})):
            save(path,changed)
            with self.assertRaises(ValueError):b.validate_refusal(self.a.c,self.root,self.a.tools['manifest'])

    def test_journal_message_is_bound_to_unit_boot_and_exact_error(self):
        record={'_BOOT_ID':self.boot.replace('-',''),'_SYSTEMD_UNIT':self.unit,'MESSAGE':json.dumps(self.event['message'])}
        real=self.real_failure_event
        result=types.SimpleNamespace(returncode=0,stdout=json.dumps(record),stderr='old archived warning')
        with mock.patch.object(b,'binary',return_value='/fixed/journalctl'),mock.patch.object(b.subprocess,'run',return_value=result):
            self.assertEqual(real(self.unit,self.boot,self.root)['message'],self.event['message'])
            for field,value in [('_BOOT_ID','bad'),('_SYSTEMD_UNIT','other')]:
                changed=dict(record,**{field:value});result.stdout=json.dumps(changed)
                with self.assertRaises(ValueError):real(self.unit,self.boot,self.root)


BeforeOwnerTests.real_failure_event=staticmethod(b.failure_event)


if __name__=='__main__':unittest.main()
