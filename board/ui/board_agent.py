"""Loopback-only KV260 UI agent. Uses frozen B runtime; no hardware management."""
import argparse
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EXPECTED_MANIFEST = '77680bacbcf6c46571fd6fc99c40778528dd366e950023b73058b6932515962e'
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED'}

class RequestError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status

def stamp():
    # Board calendar is known to be stale; boot and monotonic timing are retained.
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def quantile(values, p):
    xs = sorted(values)
    f = (len(xs) - 1) * p
    a, b = math.floor(f), math.ceil(f)
    return xs[a] + (xs[b] - xs[a]) * (f - a)

class Engine:
    def __init__(self, bundle, runs, c_ready=None, bridge=None, bridge_config=None):
        from model.b.runtime import verify_bundle
        from model.b.common import sha
        if platform.machine().lower() not in ('aarch64', 'arm64'):
            raise RuntimeError('Agent must execute on the ARM board')
        self.bundle, self.runs = bundle.resolve(), runs.resolve()
        if sha(self.bundle / 'bundle_manifest.json') != EXPECTED_MANIFEST:
            raise RuntimeError('Unexpected B bundle manifest identity')
        self.identity = verify_bundle(self.bundle)
        self.runners = {}
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.runs.mkdir(parents=True, exist_ok=True)
        self.pl = None
        if any((c_ready,bridge,bridge_config)):
            if not all((c_ready,bridge,bridge_config)):raise RuntimeError('All three real C dependencies are required')
            from model.b.pl_case import dependencies
            from model.b.gemm_abi import Bridge
            cfg=dependencies(c_ready,bridge,bridge_config);adapter=Bridge(bridge,cfg)
            try:
                identity=adapter.identity;info=adapter.info()
                if identity.get('software_test') is not False or identity['boot_id']!=self.boot or not info['safe'] or info['active'] or info['faulted']:
                    raise RuntimeError('PL owner is not a real, safe, current-boot owner')
                self.pl={'ready':Path(c_ready).resolve(),'bridge':Path(bridge).resolve(),'config':Path(bridge_config).resolve(),
                    'ready_sha256':sha(c_ready),'config_sha256':sha(bridge_config),'identity':identity}
            finally:adapter.close()

    @property
    def owner_pid(self):
        return self.pl['identity']['owner_pid']

    @property
    def pl_enabled(self):
        from model.b.common import sha
        if not self.pl:return False
        try:
            identity=self.pl['identity'];text=Path(f"/proc/{identity['owner_pid']}/stat").read_text()
            birth=text[text.rfind(')')+2:].split()[19]
            return birth==identity['owner_birth'] and sha(self.pl['config'])==self.pl['config_sha256'] and sha(self.pl['ready'])==self.pl['ready_sha256']
        except (FileNotFoundError,ProcessLookupError):return False

    def runner(self, backend):
        from model.b.runtime import Runner
        if backend=='pl49' and not self.pl_enabled:raise RequestError('PL_NOT_READY','当前owner或三份C依赖已失效，没有回退',409)
        if backend not in self.runners:
            options={'c_ready':self.pl['ready'],'bridge':self.pl['bridge'],'bridge_config':self.pl['config']} if backend=='pl49' else {}
            self.runners[backend] = Runner(self.bundle, {'fp32':'arm_fp32', 'w8a8':'arm_w8a8_reference', 'custom_cpu':'arm_custom_cpu','pl49':'arm_pl'}[backend], threads=4,**options)
        return self.runners[backend]

    def health(self):
        return {'schema':'kv260_ui_board_health_v1', 'source':'BOARD', 'connected':True,
                'execution_host':platform.node(), 'architecture':platform.machine(), 'boot_id':self.boot,
                'agent_pid':os.getpid(),
                'bundle_identity':self.identity, 'payload_verified':True,
                'backends':['fp32','w8a8','custom_cpu']+(['pl49'] if self.pl_enabled else []), 'pl_ready':self.pl_enabled,
                'pl_identity':self.pl['identity'] if self.pl_enabled else None,
                'pl_reason':None if self.pl_enabled else '需要真实C readiness及当前窗口owner绑定。',
                'serial_connected':False, 'calendar_note':'Board UTC is not synchronized; use boot/client UTC/monotonic durations.'}

    def image(self, encoded, folder):
        from PIL import Image
        from model.b.preprocess import preprocess
        if not isinstance(encoded, dict) or encoded.get('mime') not in ('image/png','image/jpeg','image/webp'):
            raise RequestError('BAD_IMAGE', '需要PNG/JPEG/WebP图片字节')
        try:
            data = base64.b64decode(encoded.get('base64', ''), validate=True)
        except (ValueError, TypeError):
            raise RequestError('BAD_IMAGE', '图片编码错误')
        if not 0 < len(data) <= 10 * 1024 * 1024:
            raise RequestError('BAD_IMAGE', '图片必须为1字节至10MiB')
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in ('PNG','JPEG','WEBP') or im.width * im.height > 50_000_000:
                    raise ValueError('Unsupported image or excessive dimensions')
                im.verify()
        except Exception as e:
            raise RequestError('BAD_IMAGE', '板端图片解码失败：' + str(e))
        path = folder / 'input.image'
        path.write_bytes(data)
        start = time.perf_counter_ns()
        tensor = preprocess(path)
        return tensor, hashlib.sha256(data).hexdigest(), (time.perf_counter_ns()-start)/1e6

    def infer(self, tensor, backend, folder, image_id, progress=None):
        runner = self.runner(backend)
        trace = folder / (image_id + '.layers.jsonl') if backend in ('custom_cpu','pl49') else None
        dump = folder / (image_id+'_raw') if backend=='pl49' else None
        done=threading.Event();errors=[]
        def observed():
            if not trace or not trace.exists():return []
            rows=[]
            for line in trace.read_text().splitlines():
                try:row=json.loads(line)
                except json.JSONDecodeError:break  # only an incomplete last append
                index=len(rows)
                if index>=49 or row['layer_index']!=index or row['layer_id']!=runner.layers[index]['layer_id'] or row['starts_delta']!=1 or row['fallback_count']!=0:
                    raise RuntimeError('Invalid actual PL progress receipt')
                if row['request_id']!=f'{folder.name}_{image_id}_l{index}' or row['starts_after']-row['starts_before']!=1 or row['owner_pid']!=self.owner_pid:
                    raise RuntimeError('Actual PL progress belongs to another request or owner')
                if json.loads(row['actual_identity_json'])!=self.pl['identity']:raise RuntimeError('Changed actual owner identity')
                rows.append(row)
            return rows
        def watch():
            while not done.wait(.08):
                try:
                    rows=observed()
                    if progress:progress(rows)
                except Exception as exc:errors.append(exc);return
        watcher=None
        if backend=='pl49':watcher=threading.Thread(target=watch,daemon=True);watcher.start()
        try:result = runner.run(tensor, folder.name, image_id, trace=trace,dump=dump)
        finally:
            done.set()
            if watcher:watcher.join(timeout=5)
        if errors:raise errors[0]
        if backend=='pl49':
            result['layers']=observed()
            if progress:progress(result['layers'])
        result.update({'boot_id':self.boot, 'source':'BOARD'})
        write_json(folder / (image_id + '.result.json'), result)
        return result

    def layer(self, options, folder):
        import numpy as np
        from model.b.case_reader import load_case
        from model.b.golden_int64 import gemm
        case_id = f"l{options['layerIndex']:02d}_{options['case']}"
        case, arrays = load_case(self.bundle, case_id)
        begin = time.perf_counter_ns()
        binding={}
        if options.get('backend')=='pl49':
            if not self.pl_enabled:raise RequestError('PL_NOT_READY','当前PL绑定未就绪',409)
            from model.b.pl_case import run
            actual,binding=run(case,arrays,self.pl['ready'],self.pl['bridge'],self.pl['config'],folder.name,'layer')
            actual.astype('<i4').tofile(folder/'actual_Y.bin')
        else:actual = gemm(arrays['Xq'], arrays['Wq'])
        elapsed = (time.perf_counter_ns()-begin)/1e6
        mismatch = int(np.count_nonzero(actual != arrays['Y_golden']))
        mismatch_reference=int(np.count_nonzero(actual!=gemm(arrays['Xq'],arrays['Wq'])))
        pl=bool(binding)
        result = {'schema':'b_layer_case_result_v1', 'source':'BOARD', 'status':'PASS' if mismatch == 0 else 'FAIL',
                  'case_id':case_id, 'layer_id':case['layer_id'], 'M':case['M'], 'K':case['K'], 'N':case['N'],
                  'execution_kind':'FPGA_LAYER' if pl else 'CPU_REFERENCE', 'integer_mismatches':mismatch,'independent_INT64_mismatches':mismatch_reference,'api_elapsed_ms':elapsed,
                  'timing_scope':'Synchronous C bridge and independent validation' if pl else 'NumPy INT64 full matrix reference',
                  'PL_starts':1 if pl else 0,'fallback_count':0, 'boot_id':self.boot, 'execution_host':platform.node(), 'bundle_identity':self.identity,
                  'kernel_cycles':None, 'kernel_cycles_reason':'UNAVAILABLE'}
        result.update(binding)
        if mismatch_reference:result['status']='FAIL'
        write_json(folder / 'layer.result.json', result)
        return result

class Jobs:
    def __init__(self, engine):
        self.engine, self.jobs, self.active = engine, {}, None
        self.paused = False
        self.lock, self.logs = threading.RLock(), []

    def log(self, job, status, message):
        event = {'at':stamp(), 'source':'BOARD', 'status':status, 'message':message}
        job['events'].append(event)
        self.logs.append({**event, 'level':'ERROR' if status == 'FAILED' else 'INFO', 'jobId':job['id']})
        self.logs = self.logs[-400:]

    def create(self, data):
        if data.get('source') != 'BOARD':
            raise RequestError('BAD_SOURCE', '板端只接受BOARD任务')
        kind, options = data.get('type'), data.get('options', {})
        if kind not in ('inference','layer','benchmark') or not isinstance(options, dict):
            raise RequestError('BAD_TASK', '任务类型无效')
        if options.get('scenario', 'normal') != 'normal':
            raise RequestError('BAD_SCENARIO', '真实后端不接受模拟故障情景')
        backend = options.get('backend', 'fp32')
        if backend not in ('fp32','w8a8','custom_cpu','pl49') or (backend=='pl49' and not getattr(self.engine,'pl_enabled',False)):
            raise RequestError('PL_NOT_READY', 'C桥接尚未通过，不能执行PL；没有CPU回退', 409)
        if kind == 'layer' and (type(options.get('layerIndex')) is not int or not 0 <= options['layerIndex'] < 49 or options.get('case') not in ('real','zero','extreme')):
            raise RequestError('BAD_LAYER', '层或case无效')
        if kind == 'benchmark' and (type(options.get('runs')) is not int or not 3 <= options['runs'] <= 30 or type(options.get('warmup')) is not int or not 0 <= options['warmup'] <= 5):
            raise RequestError('BAD_RUNS', '正式3～30次，预热0～5次')
        if kind != 'layer' and not isinstance(data.get('image'), dict):
            raise RequestError('BAD_IMAGE', '真实任务缺少图片字节，不能按文件名返回样例')
        with self.lock:
            if self.paused:
                raise RequestError('WINDOW_PAUSED', 'C安全窗口正在占用板卡，暂不接收新的模型任务', 409)
            if self.active:
                raise RequestError('DEVICE_BUSY', '板端已有UI任务运行', 409)
            if self.engine.runs.exists() and sum(p.stat().st_size for p in self.engine.runs.rglob('*') if p.is_file()) > 2 * 1024**3:
                raise RequestError('STORAGE_BUDGET', 'UI运行证据已达2GiB，请先归档', 409)
            if shutil.disk_usage(self.engine.runs).free < 4 * 1024**3:
                raise RequestError('STORAGE_BUDGET','板端可用空间不足4GiB，停止接新任务',409)
            ident = 'board_' + uuid.uuid4().hex[:12]
            folder = self.engine.runs / ident
            folder.mkdir()
            job = {'schema':'kv260_ui_job_v1', 'id':ident, 'source':'BOARD', 'type':kind, 'status':'VALIDATING',
                   'progress':None, 'layerCompleted':0, 'layerTotal':49 if kind=='inference' else 1 if kind=='layer' else options['runs'],
                   'realPLStarts':0, 'options':options, 'startedAt':stamp(), 'completedAt':None, 'elapsedMs':0,
                   'result':None, 'error':None, 'events':[], 'cancelRequested':False,
                   'boot_id':self.engine.boot, 'evidence_dir':str(folder)}
            self.jobs[ident] = job
            self.active = ident
            if len(self.jobs) > 50:
                del self.jobs[next(iter(self.jobs))]
            self.log(job, 'VALIDATING', '收到真实任务与图片字节；无PL加载')
            threading.Thread(target=self.execute, args=(job,data.get('image'),folder), daemon=True).start()
            return self.copy(job)

    def admission(self, paused):
        with self.lock:
            if paused and self.active:
                raise RequestError('DEVICE_BUSY', '当前模型任务须先安全完成', 409)
            self.paused = bool(paused)
            return {'paused':self.paused, 'active_job':self.active}

    def copy(self, job):
        return json.loads(json.dumps(job))

    def stage(self, job, status, message):
        with self.lock:
            if job['cancelRequested']:
                raise RequestError('CANCELLED', '停止请求已在软件安全边界确认')
            job['status'] = status
            self.log(job, status, message)

    def execute(self, job, encoded, folder):
        begin = time.perf_counter_ns()
        try:
            options = job['options']
            if job['type'] == 'layer':
                self.stage(job, 'RUNNING', '执行真实PL层及独立INT64' if options.get('backend')=='pl49' else '执行板端完整INT64矩阵参考')
                raw = self.engine.layer(options, folder)
                if raw['status'] != 'PASS':
                    raise RequestError('INTEGER_MISMATCH','实际整数核对不一致，不标记成功')
            else:
                self.stage(job, 'PREPROCESSING', '板端验证图片并执行冻结预处理')
                tensor, source_sha, prep_ms = self.engine.image(encoded, folder)
                job['input_sha256'] = source_sha
                if job['type'] == 'inference':
                    pl=options['backend']=='pl49'
                    self.stage(job, 'RUNNING', 'KV260完整模型，逐层读取实际完成trace' if pl else 'KV260 ARM运行完整模型')
                    def progress(rows):
                        with self.lock:
                            job['layerCompleted']=len(rows);job['realPLStarts']=sum(r['starts_delta'] for r in rows);job['progress']=int(100*len(rows)/49)
                    raw = self.engine.infer(tensor, options['backend'], folder, 'img0000',progress=progress) if pl else self.engine.infer(tensor, options['backend'], folder, 'img0000')
                    raw.update({'source_sha256':source_sha, 'preprocess_or_tensor_load_ms':prep_ms})
                    write_json(folder / 'img0000.result.json', raw)
                else:
                    # Session load occurs before warmup and is excluded from samples.
                    for backend in ('fp32','w8a8'):
                        self.stage(job, 'RUNNING', '加载板端对照模型session')
                        self.engine.runner(backend)
                    for i in range(options['warmup']):
                        for backend in ('fp32','w8a8'):
                            self.stage(job, 'RUNNING', f'预热 {i+1}/{options["warmup"]} {backend}')
                            self.engine.infer(tensor, backend, folder, f'warm_{i}_{backend}')
                    samples = []
                    for i in range(options['runs']):
                        pair = {}
                        for backend in (('fp32','w8a8') if i%2 == 0 else ('w8a8','fp32')):
                            self.stage(job, 'RUNNING', f'正式样本 {i+1}/{options["runs"]} {backend}')
                            pair[backend] = self.engine.infer(tensor, backend, folder, f'sample_{i}_{backend}')['inference_ms']
                        samples.append({'index':i+1, 'fp32Ms':pair['fp32'], 'w8a8Ms':pair['w8a8'], 'source':'BOARD_CPU'})
                        with self.lock:
                            job['layerCompleted'] = len(samples)
                            job['progress'] = round(100 * len(samples)/options['runs'])
                    xs = [s['w8a8Ms'] for s in samples]
                    raw = {'schema':'kv260_ui_cpu_benchmark_v1', 'source':'BOARD', 'execution_kind':'CPU_REFERENCE',
                           'samples':samples, 'p50Ms':quantile(xs,.5), 'p95Ms':quantile(xs,.95),
                           'fp32P50Ms':quantile([s['fp32Ms'] for s in samples],.5), 'warmup':options['warmup'],
                           'PL_starts':0, 'timingsSource':'BOARD_CPU_MEASURED', 'accuracy':None,
                           'source_sha256':source_sha, 'boot_id':self.engine.boot, 'bundle_identity':self.engine.identity,
                           'timing_scope':'Same tensor, warm sessions, alternating FP32/quant semantic CPU ORT session.run; not PL speedup'}
                    write_json(folder / 'benchmark.result.json', raw)
            self.stage(job, 'POSTPROCESSING', '绑定实际结果、模型与输入身份')
            with self.lock:
                if job['cancelRequested']:
                    raise RequestError('CANCELLED', '实际ORT调用结束后确认停止，结果已废弃')
                job.update({'status':'SUCCEEDED', 'result':raw, 'progress':100,
                            'layerCompleted':raw.get('completed_linear_count') or raw.get('observed_custom_calls') or (1 if job['type']=='layer' else len(raw.get('samples',[]))),
                            'realPLStarts':raw.get('PL_starts',0)})
                self.log(job, 'SUCCEEDED', f"真实板端计算已完成；实际PL starts={job['realPLStarts']}")
        except Exception as exc:
            with self.lock:
                cancelled = isinstance(exc, RequestError) and exc.code == 'CANCELLED'
                job.update({'status':'CANCELLED' if cancelled else 'FAILED', 'result':None,
                            'error':None if cancelled else {'code':getattr(exc,'code','BOARD_MODEL_ERROR'), 'message':str(exc) if not isinstance(exc,RequestError) else exc.message}})
                self.log(job, job['status'], '任务停止，未强杀进程' if cancelled else job['error']['message'])
        finally:
            with self.lock:
                job['elapsedMs'] = (time.perf_counter_ns()-begin)/1e6
                job['completedAt'] = stamp()
                write_json(folder / 'job.json', job)
                self.active = None

    def get(self, ident):
        with self.lock:
            if ident not in self.jobs:
                if not re.fullmatch(r'board_[0-9a-f]{12}',ident):
                    raise RequestError('NOT_FOUND', '任务不存在', 404)
                saved=self.engine.runs/ident/'job.json'
                if not saved.exists() or not saved.resolve().is_relative_to(self.engine.runs.resolve()):
                    raise RequestError('NOT_FOUND', '任务不存在', 404)
                job=json.loads(saved.read_text(encoding='utf-8'))
                if job.get('id')!=ident or job.get('schema')!='kv260_ui_job_v1' or job.get('status') not in TERMINAL:
                    raise RequestError('UNKNOWN_JOB', '旧任务没有已验证终态，不能重发或假定完成', 409)
                self.jobs[ident]=job
            return self.copy(self.jobs[ident])

    def cancel(self, ident):
        with self.lock:
            self.get(ident)
            job = self.jobs[ident]
            if job['status'] not in TERMINAL:
                job['cancelRequested'] = True
                job['status'] = 'CANCELLING'
                self.log(job, 'CANCELLING', '停止后续提交，等待当前整图安全返回')
            return self.copy(job)

def make_handler(jobs):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No request bodies or uploaded names in shared logs.

        def reply(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(data)

        def dispatch(self):
            try:
                if self.headers.get('Origin') or not self.headers.get('Host','').startswith(('127.0.0.1:','localhost:')):
                    raise RequestError('ORIGIN_BLOCKED','Only loopback proxy clients are allowed',403)
                p = self.path
                data = {}
                if self.command == 'POST':
                    length = int(self.headers.get('Content-Length','0'))
                    if not 0 < length <= 15 * 1024**2 or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                        raise RequestError('BAD_REQUEST','Invalid JSON body length or type')
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise RequestError('BAD_REQUEST','Expected object')
                if p == '/health' and self.command == 'GET':
                    return self.reply(200,{**jobs.engine.health(),'active_job':jobs.active,'paused':jobs.paused})
                if p in ('/control/pause','/control/resume') and self.command == 'POST':
                    return self.reply(200,jobs.admission(p=='/control/pause'))
                if p == '/jobs' and self.command == 'GET':
                    with jobs.lock:
                        return self.reply(200,{'jobs':[jobs.copy(x) for x in reversed(list(jobs.jobs.values()))]})
                if p == '/logs' and self.command == 'GET':
                    return self.reply(200,{'logs':jobs.logs})
                if p == '/jobs' and self.command == 'POST':
                    return self.reply(201,jobs.create(data))
                match = re.fullmatch(r'/jobs/(board_[0-9a-f]{12})(/cancel)?',p)
                if match:
                    if self.command == 'POST' and match[2]:
                        return self.reply(200,jobs.cancel(match[1]))
                    if self.command == 'GET' and not match[2]:
                        return self.reply(200,jobs.get(match[1]))
                raise RequestError('NOT_FOUND','Unknown agent endpoint',404)
            except RequestError as exc:
                self.reply(exc.status, {'error':{'code':exc.code,'message':exc.message},'source':'BOARD'})
            except Exception as exc:
                self.reply(400, {'error':{'code':'BAD_REQUEST','message':str(exc)},'source':'BOARD'})

        do_GET = dispatch
        do_POST = dispatch
    return Handler

def main():
    for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_DFL)
    p = argparse.ArgumentParser()
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--runs',type=Path,required=True)
    p.add_argument('--port',type=int,default=8740)
    p.add_argument('--c-ready',type=Path);p.add_argument('--bridge',type=Path);p.add_argument('--bridge-config',type=Path)
    args = p.parse_args()
    engine = Engine(args.bundle,args.runs,args.c_ready,args.bridge,args.bridge_config)
    server = ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(Jobs(engine)))
    print(json.dumps({'status':'READY','boot_id':engine.boot,'port':args.port,'bundle_identity':engine.identity,'PL_enabled':engine.pl_enabled}),flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
