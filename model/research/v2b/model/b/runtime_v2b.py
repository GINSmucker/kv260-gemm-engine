"""Development-only V2B runtime; frozen complete numerical run unchanged.

No automatic ABI detection or PL fallback. The caller supplies the new plugin,
verified v2b C ready, library and private per-window configuration. This source
does not constitute a B/C ready or a numerical/PL validation result.
"""
import hashlib
import json
import platform
from pathlib import Path
import time
import onnxruntime as ort
from . import runtime as base
from .common import read_json, sha
from . import __version__

ABI_SHA='3af0b482e6a36810ffcf5dd77af3a9cb879bd0594f6a2793d12e1d9d9840515c'
A2_SHA='2c25dcf1dc2734bad5a11500de8b0a1b9bf49aa6b56b5c281ecef7c73b3003d0'
B_SHA='77680bacbcf6c46571fd6fc99c40778528dd366e950023b73058b6932515962e'
IDENTITY_KEYS={'candidate_sha256','ready_sha256','bridge_sha256','owner_sha256','driver_sha256','driver_srcversion',
    'platform_manifest_sha256','A2_gate_sha256','B_manifest_sha256','B_tools_wheel_sha256','boot_id','window_id',
    'owner_pid','owner_birth_ticks','uuid','tag0','tag1'}
def _pairs(items):
    value={}
    for k,v in items:
        if k in value:raise ValueError('BAD_IDENTITY: duplicate JSON configuration key')
        value[k]=v
    return value
def _strict_file(path):
    b=Path(path).read_bytes()
    if not b or len(b)>65536 or b.startswith(b'\xef\xbb\xbf') or b'\x00' in b:
        raise ValueError('BAD_IDENTITY: configuration file bounds/encoding')
    def constant(_):raise ValueError('BAD_IDENTITY: nonfinite JSON configuration')
    return json.loads(b.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=constant)
def _version(value,abi=3):
    return type(value.get('abi_version')) is int and value['abi_version']==abi and type(value.get('wire_version')) is int and value['wire_version']==3

class Runner(base.Runner):
    def __init__(self,bundle,backend,threads=4,allow_unfrozen=False,c_ready=None,bridge=None,bridge_config=None,plugin_v2b=None):
        self.v2b_backend=backend
        if backend in ('arm_pl','windows_pl'):
            raise ValueError('BAD_IDENTITY: V2B requires explicit arm_pl_v2b, no ABI detection')
        pl=backend=='arm_pl_v2b'
        custom_cpu=backend in ('arm_custom_cpu_v2b','windows_custom_cpu_v2b')
        if not pl and not custom_cpu:
            super().__init__(bundle,backend,threads,allow_unfrozen,c_ready,bridge,bridge_config)
            return
        architecture=platform.machine().lower()
        if backend.startswith('arm_') and architecture not in ('aarch64','arm64'):
            raise ValueError('ARM backend must execute on ARM')
        if backend.startswith('windows_') and platform.system()!='Windows':
            raise ValueError('Windows backend must execute on Windows')
        if not plugin_v2b:raise ValueError('NOT_READY: explicit versioned V2B plugin required')
        if pl and allow_unfrozen:raise ValueError('BAD_IDENTITY: PL cannot use an unfrozen model')
        self.bundle=Path(bundle).resolve()
        # Base._run retains all original numerical/result checks. Internally
        # use its proven PL branch; only the external version is changed below.
        self.backend='arm_pl' if pl else 'arm_custom_cpu' if backend.startswith('arm_') else 'windows_custom_cpu'
        self.identity=base.verify_bundle(self.bundle,allow_unfrozen)
        self.layers=read_json(self.bundle/'layers.json')
        if len(self.layers)!=49 or len({l['layer_id'] for l in self.layers})!=49:
            raise ValueError('Model requires49 unique Linear layers')
        self.labels=read_json(self.bundle/'labels_imagenet1k.json');self.custom=True;self.threads=threads
        self.plugin_path=Path(plugin_v2b).resolve();self.plugin_sha256=sha(self.plugin_path)
        self.backend_env='pl_v2b' if pl else 'cpu_reference';self.bridge_env={};self.c_binding={}
        self.v2b_identity=None
        if pl:
            if not c_ready or not bridge or not bridge_config:raise ValueError('NOT_READY: explicit V2B C files required')
            ready=_strict_file(c_ready);cfg=_strict_file(bridge_config)
            if ready.get('schema')!='kv260_gemm_backend_development_v2b' or ready.get('status')!='PASS_SCOPED_PROTOCOL' or not _version(ready):
                raise ValueError('NOT_READY: actual scoped V2B protocol/recovery gate required')
            if cfg.get('schema')!='kv260_gemm_bridge_config_v2b' or not _version(cfg) or cfg.get('scope')!='C_S2_VALIDATION_ONLY':
                raise ValueError('BAD_IDENTITY: old or validation-only C configuration')
            actual=cfg.get('identity')
            if not isinstance(actual,dict) or set(actual)!=IDENTITY_KEYS:
                raise ValueError('BAD_IDENTITY: complete V2B owner identity keys')
            if self.identity['manifest_sha256']!=B_SHA or ready.get('A2_gate_sha256')!=A2_SHA or ready.get('B_manifest_sha256')!=B_SHA:
                raise ValueError('BAD_IDENTITY: frozen B/A2 contract')
            if ready.get('ABI_header_sha256')!=ABI_SHA or sha(Path(__file__).parent/'contracts/gemm_backend_v2b.h')!=ABI_SHA:
                raise ValueError('BAD_IDENTITY: V2B ABI declaration binding')
            if ready.get('plugin_sha256')!=self.plugin_sha256 or ready.get('bridge_sha256')!=sha(bridge) or actual['ready_sha256']!=sha(c_ready):
                raise ValueError('BAD_IDENTITY: new plugin/bridge/ready payload binding')
            for key in ('candidate_sha256','bridge_sha256','owner_sha256','driver_sha256','driver_srcversion','platform_manifest_sha256',
                        'A2_gate_sha256','B_manifest_sha256','B_tools_wheel_sha256','uuid','tag0','tag1'):
                if actual[key]!=ready.get(key):raise ValueError('BAD_IDENTITY: V2B ready/config '+key)
            if type(actual['owner_pid']) is not int or type(actual['owner_birth_ticks']) is not int or min(actual['owner_pid'],actual['owner_birth_ticks'])<=0:
                raise ValueError('BAD_IDENTITY: actual owner PID/birth')
            for i,(l,c) in enumerate(zip(self.layers,cfg.get('layers',[]))):
                if any(c.get(k)!=l[k] for k in ('layer_id','M','K','N')) or l['Wq']['sha256'] not in c.get('weights',[]):
                    raise ValueError('BAD_IDENTITY: V2B frozen layer/W '+str(i))
            if len(cfg.get('layers',[]))!=49:raise ValueError('BAD_IDENTITY: V2B49 registration')
            self.v2b_identity=actual
            self.bridge_env={'KV260_GEMM_BRIDGE':str(Path(bridge).resolve()),'KV260_GEMM_CONFIG':Path(bridge_config).read_text(encoding='utf-8')}
            self.c_binding={'C_ready_sha256':sha(c_ready),'bridge_sha256':sha(bridge),'bridge_config_sha256':sha(bridge_config),
                'candidate_sha256':actual['candidate_sha256'],'window_manifest_sha256':cfg['window_manifest_sha256'],
                'ABI_version':3,'wire_version':3,'v2b_plugin_sha256':self.plugin_sha256}
            binding=('V2B',sha(c_ready),sha(bridge),sha(bridge_config),self.plugin_sha256)
            with base._EXECUTION_LOCK:
                if base._PL_BINDING is not None and base._PL_BINDING!=binding:
                    raise ValueError('BAD_IDENTITY: one process retains one ABI/owner binding')
                base._PL_BINDING=binding
        so=ort.SessionOptions();so.intra_op_num_threads=threads;so.inter_op_num_threads=1
        so.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL;so.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        so.register_custom_ops_library(str(self.plugin_path));self.graph='model_w8a8_pl.onnx'
        begin=time.perf_counter_ns();self.session=ort.InferenceSession(str(self.bundle/'graphs'/self.graph),so,providers=['CPUExecutionProvider'])
        self.session_load_ms=(time.perf_counter_ns()-begin)/1e6;self.graph_sha256=sha(self.bundle/'graphs'/self.graph)
        self.protocol_sha256=sha(self.bundle/'quant_protocol.json')

    def _run(self,tensor,run_id=None,image_id='img0000',trace=None,dump=None):
        result=super()._run(tensor,run_id,image_id,trace,dump)
        if self.v2b_backend=='arm_pl_v2b':
            actual=result['actual_owner_identity']
            if actual.get('schema')!='kv260_gemm_actual_identity_v2b' or not _version(actual) or actual.get('scope')!='C_S2_VALIDATION_ONLY' or actual.get('identity')!=self.v2b_identity:
                raise ValueError('BAD_IDENTITY: actual C receipt is not this V2B contract')
            if actual.get('window_manifest_sha256')!=self.c_binding['window_manifest_sha256']:
                raise ValueError('BAD_IDENTITY: actual C window manifest')
            rows=[json.loads(line) for line in Path(trace).read_text(encoding='utf-8').splitlines()]
            if any(r.get('schema')!='b_layer_trace_v2b' or r.get('backend')!='pl_v2b' for r in rows):
                raise ValueError('BAD_IDENTITY: old/mixed actual layer trace')
        if self.v2b_backend.endswith('_v2b'):
            result['schema']='b_inference_result_v2b';result['backend']=self.v2b_backend
            result['b_runtime_version']=__version__;result['v2b_plugin_sha256']=self.plugin_sha256
        return result
