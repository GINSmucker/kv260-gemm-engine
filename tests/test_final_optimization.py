"""Candidate boundaries and wall-accounting checks; synthetic records are not board data."""
import copy
import ast
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import types
import io
import threading
import hashlib
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT/'board/runtime/candidates/final_opt'


def load(name):
    spec = importlib.util.spec_from_file_location('final_opt_'+name, DIRECTORY/(name+'.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


prepare, analyze = load('prepare'), load('analyze')


def fixture():
    capture = {'schema':'opt50_final_capture_v1', 'status':'PASS',
               'kind':'instrumented_diagnostic_not_performance', 'run_id':1, 'boot_id':'test-boot',
               'runner_start_ns':1, 'runner_end_ns':10000,
               'source_model_sha256':'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43',
               'bitwise_mismatches':0, 'CPU_logits_sha256':'a'*64, 'logits_sha256':'a'*64, 'linear_stamps':[]}
    native = {k:capture[k] for k in ('run_id','boot_id','source_model_sha256')}
    native.update(completed_PL_calls=36, START_commands_issued=36, PL_dense=12, PL_sparse=24,
                  native_END_completed=True, postrun_buffer_checks_verified=True, diagnostic_control_v1=[],
                  postrun_checks_started_ns=10001,postrun_checks_completed_ns=10100,
                  ram_events=[{'phase':'MODEL_BEGIN','monotonic_ns':2}])
    for i in range(49):
        base = 10+i*100
        pl = i < 48 and i%4 != 1
        v = [base,base+1,base+10,base+20 if pl else base+10,base+60 if pl else base+10,base+70,base+80]
        capture['linear_stamps'].append(v)
        if pl:
            native['diagnostic_control_v1'].append({'layer':i, 'stamps':[base+21,base+30,base+35,base+55,base+59,2]})
            phases = ('DMA_ACTIVE_BEFORE_START','START_WRITE_INTENT','START_WRITE_RETURNED',
                      'DMA_COMPLETION_PROVEN','LAYER_COMPLETION_VERIFIED')
            for offset, phase in zip((31,32,36,56,58),phases):
                native['ram_events'].append({'phase':phase,'monotonic_ns':base+offset,'layer':i,
                                             'command':len(native['diagnostic_control_v1']),'mode':int(i%4 in (2,3))})
    native['ram_events'].append({'phase':'MODEL_END_COMPLETED','monotonic_ns':9999})
    return capture, native


class FinalOptimizationTests(unittest.TestCase):
    def test_request_measurement_waits_for_commit_and_never_retries_post(self):
        path=ROOT/'tools/latency_diagnostics/measure_requests.py'
        spec=importlib.util.spec_from_file_location('request_measurement_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        template=json.loads((ROOT/'results/final_optimization/20261101/request_receipts/540f6e02c690488480bb65d4774fd3fe.result.json').read_bytes())
        calls=[];posts=[];status_count=0;query_count=0
        def fetch(url,data=None,headers=None):
            nonlocal status_count,query_count
            calls.append(url)
            if url.endswith('/status'):
                status_count+=1
                return {'status':{'stage':'READY','busy':status_count==1,'boot_id':template['boot_id']}}
            if '/infer/' in url:
                posts.append(url.rsplit('/',1)[1])
                if len(posts)==2:raise RuntimeError('uncertain POST response')
                return {}
            query_count+=1
            if query_count==1:raise module.HTTPError(url,409,'incomplete result',None,io.BytesIO(b'incomplete JSON'))
            return {'result':dict(template,request_id=posts[-1],image_sha256=hashlib.sha256(b'JPEG test bytes').hexdigest())}
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_build') as directory:
            folder=Path(directory);jpeg=folder/'input.jpg';jpeg.write_bytes(b'JPEG test bytes')
            argv=['measure_requests.py','--jpeg',str(jpeg),'--out',str(folder/'out'),'--count','2']
            with mock.patch.object(sys,'argv',argv),mock.patch.object(module,'fetch',side_effect=fetch),mock.patch.object(module.time,'sleep'),mock.patch('builtins.print'):
                with self.assertRaisesRegex(RuntimeError,'uncertain POST'):module.main()
            self.assertEqual(len(posts),2)
            self.assertEqual(len(set(posts)),2)
            self.assertTrue(calls[0].endswith('/status') and calls[1].endswith('/status'))
            self.assertEqual(len(json.loads((folder/'out/samples.json').read_bytes())),1)
            self.assertEqual(query_count,2)
            self.assertEqual(json.loads((folder/'out/samples.json').read_bytes())[0]['GET_transient_errors'],1)
            self.assertEqual(len({u for u in calls if '/result/' in u}),1)
            self.assertEqual(len(list((folder/'out').glob('*.intent.json'))),2)

    def test_service_transform_keeps_cpu_reference_info_and_budgets(self):
        tree=ast.parse((DIRECTORY/'install_trial.py').read_text(encoding='utf-8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='optimized_backend')
        namespace={'replace_once':prepare.replace_once}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'service_transform','exec'),namespace)
        original=(ROOT/'results/final_optimization/20261101/original_backend.py').read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),'2ab0d2080cf0e501f8e3f6eff63c0d051c64560eb6395c28ca6de088d5e51ce5')
        transformed=namespace['optimized_backend'](original)
        for marker in (b'cpu = s.cpu.run(None,',b'self.admin.call(8)',b"'total_START_budget': 1800",b'self.completed < 50'):
            self.assertEqual(original.count(marker),transformed.count(marker))
        self.assertIn(b'options=pl_options(s.runtime.dependencies()[1],1,force_stop=True)',transformed)
        self.assertIn(b'INFO_ms=info_ms',transformed)
        self.assertIn(b'request_other_ms=',transformed)
        dynamic=namespace['optimized_backend'](original,dynamic_block=True)
        self.assertIn(b'force_stop=True,dynamic_block=True)',dynamic)
        self.assertEqual(dynamic.count(b"'ORT_dynamic_block_base': 4"),3)
        model_only=namespace['optimized_backend'](original,parallel_rgb=False)
        self.assertNotIn(b'from parallel_cpu import',model_only)
        self.assertIn(b'from cpu_optimizations import ExactBundleAudit, preprocess as exact_preprocess',model_only)
        self.assertEqual(model_only.count(b"'python_profile': 'exact-rgb-path-audit-v1'"),2)
        with self.assertRaises(ValueError):namespace['optimized_backend'](transformed)

    @unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('PIL'),'optional host image libraries unavailable')
    def test_preprocessing_thread_failure_propagates_and_joins(self):
        import numpy as np
        from PIL import Image
        spec=importlib.util.spec_from_file_location('host_dataset_failure',ROOT/'board/runtime/snapshots/sparse24/dataset.py')
        dataset=importlib.util.module_from_spec(spec);spec.loader.exec_module(dataset)
        dataset.dependencies=lambda:(np,Image)
        parallel=load('parallel_cpu')
        stream=io.BytesIO()
        with Image.new('RGB',(249,301),(17,31,47)) as picture:picture.save(stream,format='JPEG')
        before={t.ident for t in threading.enumerate() if t.name=='opt50-rgb-sha'}
        with mock.patch('os.sched_setaffinity',side_effect=RuntimeError('injected affinity failure'),create=True):
            with self.assertRaisesRegex(RuntimeError,'injected affinity failure'):
                parallel.preprocess(stream.getvalue(),dataset)
        self.assertEqual(before,{t.ident for t in threading.enumerate() if t.name=='opt50-rgb-sha'})

    def test_spinning_options_change_only_the_selected_entry(self):
        spec=importlib.util.spec_from_file_location('frozen_options',ROOT/'board/runtime/snapshots/sparse24/model_runtime.py')
        frozen=importlib.util.module_from_spec(spec);spec.loader.exec_module(frozen)
        options_module=load('runtime_options')
        class Options:
            def __init__(self):self.entries={}
            def add_session_config_entry(self,key,value):
                if key in self.entries:raise ValueError('duplicate ORT config')
                self.entries[key]=value
        ort=types.SimpleNamespace(SessionOptions=Options,
                                  ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL='sequential'),
                                  GraphOptimizationLevel=types.SimpleNamespace(ORT_DISABLE_ALL='off'))
        with mock.patch('os.sched_setaffinity',create=True):
            original=frozen.session_options(ort)
            baseline=options_module.pl_options(ort,0)
            candidate=options_module.pl_options(ort,1)
            stopped=options_module.pl_options(ort,1,force_stop=True)
            dynamic=options_module.pl_options(ort,1,force_stop=True,dynamic_block=True)
        self.assertEqual(vars(original),vars(baseline))
        candidate.entries['session.intra_op.allow_spinning']='0'
        self.assertEqual(vars(original),vars(candidate))
        self.assertEqual(stopped.entries.pop('session.force_spinning_stop'),'1')
        stopped.entries['session.intra_op.allow_spinning']='0'
        self.assertEqual(vars(original),vars(stopped))
        self.assertEqual(dynamic.entries.pop('session.dynamic_block_base'),'4')
        dynamic.entries.pop('session.force_spinning_stop')
        dynamic.entries['session.intra_op.allow_spinning']='0'
        self.assertEqual(vars(original),vars(dynamic))

    def test_profiled_non_linear_intervals_close_without_linear_parent_sum(self):
        capture,native=fixture()
        capture['runner_start_ns']*=1000;capture['runner_end_ns']*=1000
        capture['linear_stamps']=[[v*1000 for v in row] for row in capture['linear_stamps']]
        for event in native['ram_events']:event['monotonic_ns']*=1000
        for item in native['diagnostic_control_v1']:item['stamps'][:5]=[v*1000 for v in item['stamps'][:5]]
        native['postrun_checks_started_ns']*=1000;native['postrun_checks_completed_ns']*=1000
        events=[{'ph':'X','name':'model_run','ts':0,'dur':10000}]
        for i,v in enumerate(capture['linear_stamps']):
            events.append({'ph':'X','cat':'Node','tid':1,'name':'BQL_%d_kernel_time'%i,
                           'ts':v[0]/1000-1,'dur':(v[-1]-v[0])/1000+2,'args':{'op_name':'QuantLinear'}})
        events.append({'ph':'X','cat':'Node','tid':1,'name':'Softmax_kernel_time','ts':95,'dur':10,'args':{'op_name':'Softmax'}})
        result=analyze.combine_profile([capture],[native],events)['samples'][0]
        self.assertEqual(sum(result['exclusive_profiled_parts_ns'].values()),result['runner_ns'])
        self.assertEqual(result['exclusive_profiled_parts_ns']['attention_Softmax'],10000)

    def test_disjoint_closure_and_native_parent(self):
        result = analyze.decompose(*fixture())
        self.assertEqual(sum(result['exclusive_parts_ns'].values()), result['runner_ns'])
        self.assertEqual(result['aggregates_ns']['PS/proj']['CPU_MatMulInteger'], 12*60)
        child = result['layers'][0]['native_children_ns']
        self.assertEqual(child['full_start_control'],14)
        self.assertEqual(sum(v for k,v in child.items() if k != 'pause_calls'),40)

    def test_overlap_bad_route_and_failed_comparison_refused(self):
        for change in ('overlap','outside_ipc','route','numerical'):
            capture, native = fixture()
            if change == 'overlap': capture['linear_stamps'][1][0]=20
            if change == 'outside_ipc': native['diagnostic_control_v1'][0]['stamps'][0]=1
            if change == 'route': native['diagnostic_control_v1'].append(copy.deepcopy(native['diagnostic_control_v1'][0]))
            if change == 'numerical': capture['bitwise_mismatches']=1
            with self.subTest(change=change), self.assertRaises(ValueError):
                analyze.decompose(capture,native)

    def test_nested_ort_erf_and_integer_matmul_not_double_counted(self):
        def event(name,start,duration,thread=1):
            return {'cat':'Node','ph':'X','tid':thread,'name':name+'_kernel_time',
                    'ts':start,'dur':duration,'args':{'op_name':name}}
        events = [event('QuantLinear',0,80),event('MatMulInteger',20,30),event('Softmax',81,10),event('Erf',20,30,2)]
        result = analyze.ort_summary(events)
        self.assertEqual(result['threads']['1']['top_level_operator_ns'], {'QuantLinear':80000,'Softmax':10000})
        self.assertEqual(result['threads']['2']['top_level_operator_ns'], {'Erf':30000})

    def test_source_transforms_preserve_arithmetic_and_guards(self):
        original = (prepare.BASE/'source/b_plugin.cpp').read_text(encoding='utf-8')
        transformed = prepare.plugin(original)
        for call in ('kv260_b3::quantize(', 'kv260_b3::dequantize(', 'kv260_b3::dequantize_qkv(', 'cpu_op.Invoke('):
            self.assertEqual(original.count(call),transformed.count(call))
        source = (prepare.BASE/'source/kernel.cpp').read_text(encoding='utf-8')
        candidate = prepare.crypto(source)
        expected = source.replace('#include "kernel.h"', '#include "kernel.h"\n#include "sha256_crypto.h"',1).replace(
            'const auto data_sha=sha(payload(1)),meta_sha=sha(payload(2));',
            'const auto data_sha=kv260_crypto_sha256(map_[1]+guard,caps_[1]),meta_sha=kv260_crypto_sha256(map_[2]+guard,caps_[2]);',1)
        self.assertEqual(candidate,expected)
        self.assertIn('all_guards();checks_.static_hashes(graph_hot_,2);',candidate)
        self.assertIn('data_sha==opt50_model::data_sha&&meta_sha==opt50_model::meta_sha',candidate)
        self.assertIn('diagnostic_control_v1',prepare.owner(source))

    def test_changed_source_anchor_refused(self):
        with self.assertRaises(ValueError): prepare.plugin('unrecognized source')
        with self.assertRaises(ValueError): prepare.crypto('unrecognized source')


if __name__ == '__main__':
    unittest.main()
