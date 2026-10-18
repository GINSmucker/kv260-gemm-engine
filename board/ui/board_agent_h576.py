"""UI adapter for the immutable H576 runtime; hardware belongs to its window."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import numpy as np
import onnxruntime as ort
import board_agent as base

MANIFEST = 'c18de455de083363d0b73a7b526f5262a5cb2371c8e91b5900a4447328694a76'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def birth(pid):
    return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])


class Engine(base.Engine):
    def __init__(self, package, assets, runs, binding):
        self.package, self.assets, self.runs = package, assets, runs
        assert digest(package/'manifest.json') == MANIFEST
        manifest = json.loads((package/'manifest.json').read_bytes())
        for name, spec in manifest['files'].items():
            assert digest(package/name) == spec['sha256'], name
        self.bundle = package/'bundle'
        self.layers = json.loads((self.bundle/'layers.json').read_bytes())
        self.labels = json.loads((assets/'labels_imagenet1k.json').read_bytes())
        self.identity = dict(status='PASS', manifest_sha256=MANIFEST, candidate_id='AB_H576_OPT45_PS_GELU_20261016')
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.binding = json.loads(binding.read_bytes()) if binding else None
        if self.binding:
            assert self.binding['package_manifest_sha256'] == MANIFEST and self.binding['boot_id'] == self.boot
            assert self.binding['owner_birth_ticks'] == birth(self.binding['owner_pid'])
        self.runs.mkdir(parents=True, exist_ok=True)
        self.runners = {}
        self.completed_PL_starts = 0
        self.execution_unknown = False
        sys.path.insert(0, str(package))
        from runtime import Runner
        self.Runner = Runner

    @property
    def pl_enabled(self):
        if not self.binding:return False
        try:
            return birth(self.binding['owner_pid']) == self.binding['owner_birth_ticks'] and Path(self.binding['socket']).exists()
        except (FileNotFoundError, ProcessLookupError):
            return False

    def runner(self, backend):
        if backend not in ('fp32','w8a8','pl49'):
            raise base.RequestError('UNSUPPORTED_BACKEND','H576支持FP32、W8A8语义及固定PS/PL',409)
        if backend == 'pl49' and not self.pl_enabled:
            raise base.RequestError('PL_NOT_READY','H576 owner已失效',409)
        if backend not in self.runners:
            # One session lives at a time; the existing four-worker pool is reused.
            for old in self.runners.values():
                if hasattr(old,'close'):
                    old.close()
            self.runners.clear()
            gc.collect()
            start = time.perf_counter_ns()
            if backend == 'pl49':
                obj = self.Runner(self.bundle,self.package/'libquant_v5.so',self.binding['socket'])
            else:
                os.sched_setaffinity(0,{0})
                opts = ort.SessionOptions()
                opts.intra_op_num_threads=4
                opts.inter_op_num_threads=1
                opts.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
                opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
                for key,value in [('session.intra_op_thread_affinities','2;3;4'),('session.intra_op.allow_spinning','0'),('session.inter_op.allow_spinning','0')]:
                    opts.add_session_config_entry(key,value)
                obj = ort.InferenceSession(str(self.assets/('h576_fp32.onnx' if backend=='fp32' else 'h576_w8.onnx')),opts,providers=['CPUExecutionProvider'])
            self.runners[backend] = obj
            self.session_load_ms=(time.perf_counter_ns()-start)/1e6
        return self.runners[backend]

    def health(self):
        layers=[dict(index=i,id=x['layer_id'],family=('head' if i==48 else ('qkv','proj','fc1','fc2')[i%4]),M=x['M'],K=x['K'],N=x['N'],backend='PL' if i<48 and i%4!=1 else 'CPU') for i,x in enumerate(self.layers)]
        return dict(schema='kv260_ui_board_health_v1',source='BOARD',connected=True,execution_host=platform.node(),architecture=platform.machine(),boot_id=self.boot,agent_pid=os.getpid(),bundle_identity=self.identity,payload_verified=True,model_protocol='h576_v5',backends=['fp32','w8a8']+(['pl49'] if self.pl_enabled else []),pl_ready=self.pl_enabled,pl_identity=self.binding if self.pl_enabled else None,pl_reason=None if self.pl_enabled else '当前owner未就绪',serial_connected=False,calendar_note='Use client UTC and monotonic time',PL_layers=36,CPU_layers=13,catalog=dict(model=dict(name='DeiT Tiny H576',blocks=12,tokens=197,embedding=192,heads=3,classes=1000,input='224 × 224',mlp_width=576,source='BOARD',identity=MANIFEST),layers=layers,capabilities=dict(modes=['dense_w8a8'],sparse='UNSUPPORTED',w4='UNSUPPORTED',source='BOARD')))

    def infer(self,tensor,backend,folder,image_id,progress=None):
        runner=self.runner(backend)
        started=time.perf_counter_ns()
        try:
            y=(runner.run(tensor,time.monotonic_ns()) if backend=='pl49' else runner.run(None,{'image':tensor})[0]).reshape(1000)
        except BaseException:
            if backend=='pl49':self.execution_unknown=True
            raise
        elapsed=(time.perf_counter_ns()-started)/1e6
        if backend=='pl49':self.completed_PL_starts+=36
        order=np.argsort(-y,kind='stable')[:5]
        exp=np.exp(y-y.max(),dtype=np.float32)
        probability=exp/exp.sum(dtype=np.float32)
        routes=[dict(layer_index=i,layer_id=x['layer_id'],M=x['M'],K=x['K'],N=x['N'],backend='PL' if i<48 and i%4!=1 else 'CPU',evidence='FROZEN_ROUTE_COMPLETED_GRAPH') for i,x in enumerate(self.layers)]
        result=dict(schema='b_inference_result_h576_v5',model_protocol='h576_v5',source='BOARD',status='PASS',run_id=folder.name,image_id=image_id,backend='arm_h576_pl' if backend=='pl49' else 'arm_h576_'+backend,boot_id=self.boot,execution_host=platform.node(),architecture=platform.machine(),execution_kind='FPGA_MODEL' if backend=='pl49' else 'CPU_FP32' if backend=='fp32' else 'CPU_REFERENCE',bundle_identity=self.identity,model_linear_count=49,observed_custom_calls=None,completed_linear_count=49 if backend=='pl49' else None,PL_starts=36 if backend=='pl49' else 0,fallback_count=0,input_tensor_sha256=hashlib.sha256(tensor.tobytes()).hexdigest(),logits=y.tolist(),top5=[dict(self.labels[int(i)],logit=float(y[i]),probability=float(probability[i])) for i in order],inference_ms=elapsed,session_load_ms=self.session_load_ms,timing_scope='Complete fixed model invocation; setup, preprocessing and serialization excluded',hardware_binding=self.binding if backend=='pl49' else None,route_manifest=routes if backend=='pl49' else None,progress_basis='No per-layer timing or completion observer; completed graph only',kernel_cycles=None)
        base.write_json(folder/(image_id+'.result.json'),result)
        return result

    def layer(self,options,folder):
        index=options['layerIndex']
        layer=self.layers[index]
        pl=options.get('backend')=='pl49'
        if pl and (index==48 or index%4==1):
            raise base.RequestError('CPU_ROUTE','该层在冻结模型中固定由CPU执行',409)
        m,k,n=[layer[key] for key in ('M','K','N')]
        w=np.fromfile(self.bundle/layer['Wq']['file'],dtype=np.int8).reshape(k,n)
        if options['case']=='real':
            x=np.fromfile(self.assets/'layer_inputs'/f'{index}_Xq.bin',dtype=np.int8).reshape(m,k)
        elif options['case']=='zero':
            x=np.zeros((m,k),dtype=np.int8)
        else:
            x=np.resize(np.array([-128,127],dtype=np.int8),m*k).reshape(m,k)
        reference=x.astype(np.int64)@w.astype(np.int64)
        started=time.perf_counter_ns()
        extra={}
        if pl:
            for obj in self.runners.values():
                if hasattr(obj,'close'):obj.close()
            self.runners.clear();gc.collect()
            x.tofile(folder/'Xq.bin')
            command=[str(self.assets/'h576_layer'),self.binding['socket'],digest(self.package/'weights.bin'),str(time.monotonic_ns()),str(index),str(folder/'Xq.bin'),str(folder/'Y.bin')]
            self.pending_layer=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                stdout,stderr=self.pending_layer.communicate(timeout=150)
            except subprocess.TimeoutExpired:
                self.execution_unknown=True
                raise RuntimeError('层调用超时，执行计数未知；保留客户端和owner，不重放RUN')
            base.write_json(folder/'process.json',dict(returncode=self.pending_layer.returncode,stdout=stdout,stderr=stderr))
            if self.pending_layer.returncode:
                self.execution_unknown=True
                raise RuntimeError(stderr)
            try:
                extra=json.loads(stdout)
                assert extra['status']=='PASS' and extra['target_PL_starts']==1
                assert type(extra['PL_starts']) is int and extra['PL_starts']==index-(index+3)//4+1
            except (ValueError,KeyError,TypeError,AssertionError):
                self.execution_unknown=True
                raise RuntimeError('层回执不完整，执行计数未知；不重放RUN')
            self.completed_PL_starts+=extra['PL_starts']
            actual=np.fromfile(folder/'Y.bin',dtype='<i4').reshape(m,n)
        else:
            actual=reference.astype(np.int32)
        mismatches=int(np.count_nonzero(actual!=reference))
        result=dict(schema='b_layer_case_result_h576_v5',model_protocol='h576_v5',source='BOARD',status='FAIL' if mismatches else 'PASS',target_layer=index,layer_id=layer['layer_id'],M=m,K=k,N=n,execution_kind='FPGA_LAYER' if pl else 'CPU_REFERENCE',integer_mismatches=mismatches,independent_INT64_mismatches=mismatches,api_elapsed_ms=(time.perf_counter_ns()-started)/1e6,PL_starts=extra.get('PL_starts',0),target_PL_starts=extra.get('target_PL_starts',0),fallback_count=0,boot_id=self.boot,bundle_identity=self.identity,hardware_binding=self.binding if pl else None,timing_scope='Fixed owner requires zero-filled preceding PL layers; total starts includes that prefix, target starts is one',reference_basis='Frozen H576 weight, existing real Xq or deterministic zero/extreme input, full INT64 reference')
        base.write_json(folder/'layer.result.json',result)
        return result


class Jobs(base.Jobs):
    def admission(self,paused):
        with self.lock:
            self.paused=bool(paused)
            return dict(paused=self.paused,active_job=self.active)

    def create(self,data):
        if self.engine.execution_unknown:
            raise base.RequestError('EXECUTION_UNKNOWN','执行状态未知，停止新提交并等待窗口安全收尾',409)
        return super().create(data)

    def completed(self,job):
        return None if self.engine.execution_unknown else self.engine.completed_PL_starts-job['PL_starts_at_admission']

    def stage(self,job,status,message):
        with self.lock:job['realPLStarts']=self.completed(job)
        if status=='RUNNING' and job['type']=='inference' and job['options'].get('backend')=='pl49':
            message='KV260 H576完整模型；等待整图完成回执，逐层进度不可观测'
        return super().stage(job,status,message)

    def log(self,job,status,message):
        if status in ('FAILED','CANCELLED') and 'PL_starts_at_admission' in job:
            job['realPLStarts']=self.completed(job)
        return super().log(job,status,message)

    def execute(self,job,encoded,folder):
        job['PL_starts_at_admission']=self.engine.completed_PL_starts
        if job['type']!='benchmark':
            return super().execute(job,encoded,folder)
        started=time.perf_counter_ns()
        try:
            self.stage(job,'PREPROCESSING','板端读取图片并执行冻结预处理')
            tensor,source_sha,_=self.engine.image(encoded,folder)
            job['input_sha256']=source_sha
            options=job['options'];values={}
            backends=('fp32','w8a8','pl49') if self.engine.pl_enabled else ('fp32','w8a8')
            for backend in backends:
                self.stage(job,'RUNNING',f'加载H576 {backend}；不计入样本')
                self.engine.runner(backend)
                for i in range(options['warmup']):
                    self.stage(job,'RUNNING',f'{backend} 预热 {i+1}/{options["warmup"]}')
                    self.engine.infer(tensor,backend,folder,f'warm_{backend}_{i}')
                values[backend]=[]
                for i in range(options['runs']):
                    self.stage(job,'RUNNING',f'{backend} 正式 {i+1}/{options["runs"]}')
                    value=self.engine.infer(tensor,backend,folder,f'sample_{backend}_{i}')
                    values[backend].append(value['inference_ms'])
            samples=[dict(index=i+1,fp32Ms=values['fp32'][i],w8a8Ms=values['w8a8'][i],plMs=values['pl49'][i] if 'pl49' in values else None,source='BOARD_H576_MEASURED') for i in range(options['runs'])]
            raw=dict(schema='kv260_ui_h576_benchmark_v1',model_protocol='h576_v5',source='BOARD',status='PASS',samples=samples,p50Ms=base.quantile(values['w8a8'],.5),p95Ms=base.quantile(values['w8a8'],.95),fp32P50Ms=base.quantile(values['fp32'],.5),plP50Ms=base.quantile(values['pl49'],.5) if 'pl49' in values else None,plP95Ms=base.quantile(values['pl49'],.95) if 'pl49' in values else None,warmup=options['warmup'],PL_starts=36*(options['warmup']+options['runs']) if 'pl49' in values else 0,fallback_count=0,source_sha256=source_sha,boot_id=self.engine.boot,bundle_identity=self.engine.identity,timingsSource='BOARD_H576_MEASURED',accuracy=None,timing_scope='Same H576 input and four-thread model invocation; each backend has a sequential warm session; initialization and serialization excluded; UI probe is not the formal bracket comparison')
            raw['hardware_binding']=self.engine.binding
            base.write_json(folder/'benchmark.result.json',raw)
            self.stage(job,'POSTPROCESSING','保存全部测评样本')
            with self.lock:
                if job['cancelRequested']:
                    raise base.RequestError('CANCELLED','当前整图安全完成后停止，结果已废弃')
                job.update(status='SUCCEEDED',result=raw,progress=100,layerCompleted=options['runs'],realPLStarts=raw['PL_starts'])
                self.log(job,'SUCCEEDED','H576 CPU与PS/PL测评完成')
        except Exception as exc:
            cancelled=isinstance(exc,base.RequestError) and exc.code=='CANCELLED'
            with self.lock:
                job.update(status='CANCELLED' if cancelled else 'FAILED',result=None,error=None if cancelled else dict(code=getattr(exc,'code','BOARD_MODEL_ERROR'),message=str(exc)))
                self.log(job,job['status'],'等待当前调用完成后停止' if cancelled else str(exc))
        finally:
            with self.lock:
                job['elapsedMs']=(time.perf_counter_ns()-started)/1e6
                job['completedAt']=base.stamp()
                base.write_json(folder/'job.json',job)
                self.active=None


def main():
    parser=argparse.ArgumentParser()
    for name in ('package','assets','runs'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--binding',type=Path)
    parser.add_argument('--port',type=int,default=8742)
    args=parser.parse_args()
    engine=Engine(args.package,args.assets,args.runs,args.binding)
    jobs=Jobs(engine)
    original_health=engine.health
    engine.health=lambda: dict(original_health(),completed_PL_starts=engine.completed_PL_starts,execution_unknown=engine.execution_unknown)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),base.make_handler(jobs))
    def shutdown(*_):
        if jobs.active:return
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    print(json.dumps(dict(status='READY',port=args.port,model_protocol='h576_v5')),flush=True)
    try:server.serve_forever()
    finally:
        server.server_close()
        for runner in engine.runners.values():
            if hasattr(runner,'close'):runner.close()


if __name__=='__main__':
    main()
