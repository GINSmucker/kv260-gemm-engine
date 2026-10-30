"""Synthetic CPU-only baseline/collector checks; ORT/board locks are mocked."""
import contextlib
import copy
import gc
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import weakref

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dense_cpu_tested',ROOT/'cpu_dense_baseline.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
mspec=importlib.util.spec_from_file_location('dense_metrics',ROOT/'accuracy_metrics.py')
metrics=importlib.util.module_from_spec(mspec);mspec.loader.exec_module(metrics)


def raw_pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


class Fixture:
    def __init__(self,root):
        self.root=root;self.active=False;self.events=[];self.sessions=[];self.materialized=[];self.raise_at=None
        self.model=root/'external_dense.onnx';self.model.write_bytes(b'PINNED_SYNTHETIC_MODEL')
        self.model_pin=raw_pin(self.model.read_bytes());self.dataset=root/'JPEG_DATA';self.dataset.mkdir()
        self.manifest_pin={'bytes':111,'sha256':'b'*64}
        image=np.zeros((1,3,224,224),np.float32);stream=io.BytesIO();np.save(stream,image,allow_pickle=False)
        self.npy=stream.getvalue();self.input_pin=dict(raw_pin(self.npy),raw_sha256=raw_pin(image.tobytes())['sha256'])
        self.samples=[{'ordinal':i,'id':f'val_{i:07d}','label':i//2,
            'image':{'sha256':hashlib.sha256(str(i).encode()).hexdigest()},'input':dict(self.input_pin)} for i in range(1800)]
        aggregate=hashlib.sha256()
        for _ in range(1800):aggregate.update(image.tobytes())
        self.aggregate=aggregate.hexdigest()
        owner=self
        class Session:
            def __init__(self,raw,options,providers):
                if not owner.active:raise AssertionError('CPU session created outside lock')
                if raw!=owner.model.read_bytes() or providers!=['CPUExecutionProvider'] or options!='EXACT_CPU4_OPTIONS':
                    raise AssertionError('wrong CPU model/session options/provider')
                owner.events.append('session_create');owner.sessions.append(weakref.ref(self))
            def get_inputs(self):return [types.SimpleNamespace(name='image')]
            def get_outputs(self):return [types.SimpleNamespace(name='logits')]
            def run(self,outputs,feeds):
                if not owner.active:raise AssertionError('inference outside lock')
                owner.events.append('Session.run')
                return [np.zeros((1,1000),np.float32)]
            def __del__(self):owner.events.append('session_closed')
        @contextlib.contextmanager
        def locked():
            if self.active:raise AssertionError('concurrent lock acquisition')
            self.active=True;self.events.append('lock_enter')
            try:yield 99
            finally:self.events.append('lock_exit');self.active=False
        def materialize(root,sample):
            if not self.active:raise AssertionError('input prepared outside lock')
            self.materialized.append(sample['ordinal']);self.events.append('materialize')
            if self.raise_at==sample['ordinal']:raise ValueError('synthetic image failure')
            return self.npy
        def read(path,limit=64*1024*1024):
            raw=Path(path).read_bytes()
            if len(raw)>limit:raise ValueError('oversized fixture')
            return raw
        def checked(root,verify_payloads=True):
            if not verify_payloads:raise AssertionError('JPEG content checks disabled')
            return {'manifest':{'samples':self.samples},'manifest_pin':dict(self.manifest_pin)}
        def options(ort,bind_affinity=True):
            if not bind_affinity or not self.active:raise AssertionError('CPU4 affinity must be bound inside lock')
            self.events.append('session_options');return 'EXACT_CPU4_OPTIONS'
        ds=types.SimpleNamespace(read=read,decode=json.loads,ordinary=lambda p:Path(p),
            verify_dataset=checked,dependencies=lambda:None,materialize_input=materialize,INPUTS_SHA=self.aggregate)
        rt=types.SimpleNamespace(dependencies=lambda:(np,types.SimpleNamespace(InferenceSession=Session)),session_options=options)
        r=types.SimpleNamespace(root_linux=lambda:self.events.append('root_linux'),companion_locked=locked)
        spec=importlib.util.spec_from_file_location('dense_durable_fixture',ROOT/'support_py/durable.py')
        durable=importlib.util.module_from_spec(spec);spec.loader.exec_module(durable)
        durable.fsync_dir=lambda path:None  # No claim of native Windows directory durability.
        if os.name=='nt':
            durable._identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_birthtime_ns)
        self.api=types.SimpleNamespace(dataset=ds,runtime=rt,metrics=metrics,resident=r,durable=durable,
            tools={'files':{}},tools_pin={'bytes':10,'sha256':'c'*64})

    def patches(self):
        stack=contextlib.ExitStack()
        stack.enter_context(patch.object(d,'apis',return_value=self.api))
        stack.enter_context(patch.object(d,'verification_apis',return_value=self.api))
        stack.enter_context(patch.object(d,'DENSE_PIN',self.model_pin))
        stack.enter_context(patch.object(d,'boot_id',return_value='11111111-1111-4111-8111-111111111111'))
        return stack


class BaselineTests(unittest.TestCase):
    def setUp(self):
        cache=ROOT/'tests/.test_build';cache.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=cache);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.f=Fixture(self.root)
        self.stack=self.f.patches();self.addCleanup(self.stack.close)

    def test_subset_one_session_original_order_lock_and_exact_model(self):
        result=d.run(self.f.model,self.f.dataset,self.root/'out',start=5,count=3)
        self.assertEqual(result['status'],'CPU_DENSE_SUBSET_COMPLETE_NOT_FULL_ACCEPTANCE')
        self.assertFalse(result['full_1800_complete']);self.assertEqual(result['issued_PL_START_commands'],0)
        self.assertEqual(self.f.materialized,[5,6,7]);self.assertEqual(len(self.f.sessions),1)
        self.assertEqual(self.f.events.count('Session.run'),3)
        self.assertLess(self.f.events.index('lock_enter'),self.f.events.index('session_create'))
        self.assertLess(self.f.events.index('session_closed'),self.f.events.index('lock_exit'))
        self.assertTrue((self.root/'out/subset_summary.json').is_file())
        self.assertFalse((self.root/'out/summary.json').exists())
        self.assertFalse(list((self.root/'out').rglob('*.onnx')))

    def test_saved_file_and_raw_logits_hashes_are_distinct(self):
        d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        row=json.loads(next((self.root/'out/entries').glob('*.json')).read_bytes())
        self.assertEqual(row['CPU_top5'],[0,1,2,3,4]);self.assertEqual(row['CPU_true_label_rank'],1)
        self.assertEqual(row['input_sha256'],raw_pin(self.f.npy)['sha256'])
        self.assertNotEqual(row['CPU_logits_file']['sha256'],row['CPU_logits_sha256'])
        saved=(self.root/'out'/row['CPU_logits_file']['file']).read_bytes()
        logits=np.load(io.BytesIO(saved),allow_pickle=False)
        self.assertEqual(row['CPU_logits_sha256'],raw_pin(logits.tobytes())['sha256'])

    def test_existing_output_refused_without_more_inference(self):
        d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        count=self.f.events.count('Session.run')
        with self.assertRaises(ValueError):d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        self.assertEqual(self.f.events.count('Session.run'),count)

    def test_wrong_external_model_refused_before_output(self):
        self.f.model.write_bytes(b'WRONG MODEL')
        with self.assertRaises(ValueError):d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        self.assertFalse((self.root/'out').exists());self.assertFalse(self.f.sessions)

    def test_lock_contention_prevents_model_or_inference(self):
        @contextlib.contextmanager
        def busy():raise BlockingIOError('CPU/PL companion busy');yield
        self.f.api.resident.companion_locked=busy
        with self.assertRaises(BlockingIOError):d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        self.assertFalse(self.f.sessions);self.assertFalse((self.root/'out').exists())

    def test_materialization_failure_preserves_partial_no_summary_and_closes_session(self):
        self.f.raise_at=1
        with self.assertRaises(ValueError):d.run(self.f.model,self.f.dataset,self.root/'out',count=3)
        self.assertTrue((self.root/'out/run.json').exists())
        self.assertEqual(len(list((self.root/'out/entries').glob('*.json'))),1)
        self.assertFalse((self.root/'out/summary.json').exists());self.assertFalse((self.root/'out/subset_summary.json').exists())
        self.assertIn('session_closed',self.f.events);self.assertFalse(self.f.active)

    def test_input_npy_pin_checked_even_if_materializer_wrong(self):
        self.f.api.dataset.materialize_input=lambda *_:b'not the pinned NPY'
        with self.assertRaises(ValueError):d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        self.assertNotIn('Session.run',self.f.events)

    def test_bad_logits_no_summary(self):
        self.f.api.metrics=types.SimpleNamespace(checked_logits=lambda _:(_ for _ in ()).throw(ValueError('bad logits')))
        with self.assertRaises(ValueError):d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        self.assertFalse((self.root/'out/subset_summary.json').exists())

    def test_invalid_empty_or_out_of_bounds_ranges_refused(self):
        for start,count in [(-1,1),(0,0),(1799,2),(1800,1),(True,1)]:
            with self.subTest(start=start,count=count),self.assertRaises(ValueError):d.validate_range(start,count,1800)

    def test_subset_is_not_collectable_as_full_dense_baseline(self):
        d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        with self.assertRaises(OSError):d.read_baseline(self.root/'out',self.f.manifest_pin,self.f.samples)

    def test_boot_change_prevents_next_cpu_call(self):
        with patch.object(d,'boot_id',side_effect=['first','second']):
            with self.assertRaises(ValueError):d.run(self.f.model,self.f.dataset,self.root/'out',count=1)
        self.assertNotIn('Session.run',self.f.events)

    def test_dense_literal_model_identity_is_known(self):
        with patch.object(d,'DENSE_PIN',{'bytes':19628633,'sha256':'f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'}):
            self.assertNotEqual(d.DENSE_PIN['sha256'],'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43')


class FullSavedBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache=ROOT/'tests/.test_build';cache.mkdir(exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(dir=cache);cls.root=Path(cls.temp.name)
        cls.f=Fixture(cls.root);cls.stack=cls.f.patches();cls.out=cls.root/'out'
        with contextlib.redirect_stdout(io.StringIO()):cls.result=d.run(cls.f.model,cls.f.dataset,cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.stack.close();cls.temp.cleanup()

    def test_complete1800_counts_and_independent_saved_logits_review(self):
        self.assertTrue(self.result['full_1800_complete'])
        report=d.read_baseline(self.out,self.f.manifest_pin,self.f.samples)
        self.assertEqual(report['verified_entries'],1800)
        self.assertTrue(report['all_saved_CPU_logits_recomputed'])
        self.assertEqual(report['accuracy']['top1_correct'],2)
        self.assertEqual(report['accuracy']['top5_correct'],10)
        self.assertFalse(report['hardware_numerical_parity_verified'])
        self.assertEqual(report['B_CPU1_equivalence'],'NOT_CLAIMED')
        self.assertEqual(len(self.f.sessions),1)

    def test_wrong_dataset_manifest_or_short_samples_refused(self):
        for pin,samples in [({'bytes':111,'sha256':'f'*64},self.f.samples),(self.f.manifest_pin,self.f.samples[:5])]:
            with self.assertRaises(ValueError):d.read_baseline(self.out,pin,samples)

    def test_changed_logits_file_detected_without_repair(self):
        file=next((self.out/'logits').glob('*.npy'));raw=file.read_bytes()
        try:
            file.write_bytes(raw+b'changed')
            with self.assertRaises(ValueError):d.read_baseline(self.out,self.f.manifest_pin,self.f.samples)
            self.assertTrue(file.read_bytes().endswith(b'changed'))
        finally:file.write_bytes(raw)

    def test_self_consistent_forged_top5_is_recomputed_from_real_saved_logits(self):
        summary_path=self.out/'summary.json';original=summary_path.read_bytes();summary=json.loads(original)
        entry=summary['entries'][0];path=self.out/entry['entry']['file'];old=path.read_bytes();row=json.loads(old)
        try:
            row['CPU_top5']=[5,6,7,8,9];new=(json.dumps(row)+'\n').encode();path.write_bytes(new)
            entry['entry'].update(raw_pin(new));summary_path.write_text(json.dumps(summary),encoding='utf8')
            with self.assertRaises(ValueError):d.read_baseline(self.out,self.f.manifest_pin,self.f.samples)
        finally:path.write_bytes(old);summary_path.write_bytes(original)

    def test_summary_accuracy_cannot_replace_1800_actual_logits(self):
        path=self.out/'summary.json';raw=path.read_bytes();value=json.loads(raw)
        try:
            value['accuracy']['top1_correct']=1800;path.write_text(json.dumps(value),encoding='utf8')
            with self.assertRaises(ValueError):d.read_baseline(self.out,self.f.manifest_pin,self.f.samples)
        finally:path.write_bytes(raw)

    def test_read_api_never_acquires_lock_or_starts_model(self):
        before=self.f.events.copy()
        report=d.read_baseline(self.out,self.f.manifest_pin,self.f.samples)
        self.assertEqual(self.f.events,before)
        self.assertEqual(report['issued_PL_START_commands'],0)


if __name__=='__main__':unittest.main()
