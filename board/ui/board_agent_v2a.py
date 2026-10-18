"""Explicit V2A UI entry; real ready/runtime/owner binding, no ABI fallback."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import board_agent as base

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/legacy/model_backend/v2a'))
from abi import Bridge
from model.b.common import sha
from model.b.runtime_v2a import Runner, _strict_file


class Engine(base.Engine):
    def __init__(self,bundle,runs,c_ready=None,bridge=None,bridge_config=None,plugin=None):
        super().__init__(bundle,runs)
        if not any((c_ready,bridge,bridge_config,plugin)):return
        if not all((c_ready,bridge,bridge_config,plugin)):
            raise RuntimeError('V2A requires explicit ready, library, config and plugin')
        cfg=_strict_file(bridge_config)
        # The same versioned runtime used in S3 checks all frozen B/A2, ABI,
        # plugin/ready/config and registration bindings before creating ORT.
        runner=Runner(bundle,'arm_pl_v2a',4,False,c_ready,bridge,bridge_config,plugin)
        adapter=Bridge(bridge,cfg)
        try:
            identity=adapter.identity;info=adapter.info();binding=identity['identity']
            if (identity.get('software_test') is not False or identity.get('abi_version')!=2
                or identity.get('wire_version')!=2 or identity.get('scope')!='B_SYSTEM_VERIFIED'
                or binding!=cfg['identity'] or binding['boot_id']!=self.boot
                or not info['safe'] or info['active'] or info['faulted'] or info['bridge_faulted']):
                raise RuntimeError('V2A owner is not a real, safe, current-window owner')
            self.pl=dict(ready=Path(c_ready).resolve(),bridge=Path(bridge).resolve(),config=Path(bridge_config).resolve(),
                plugin=Path(plugin).resolve(),ready_sha256=sha(c_ready),config_sha256=sha(bridge_config),
                plugin_sha256=sha(plugin),identity=identity,binding=binding)
            self.runners['pl49']=runner
        finally:adapter.close()

    @property
    def owner_pid(self):return self.pl['binding']['owner_pid']

    @property
    def pl_enabled(self):
        if not self.pl:return False
        try:
            b=self.pl['binding'];text=Path(f"/proc/{b['owner_pid']}/stat").read_text()
            birth=int(text.rsplit(')',1)[1].split()[19])
            return (birth==b['owner_birth_ticks'] and sha(self.pl['config'])==self.pl['config_sha256']
                and sha(self.pl['ready'])==self.pl['ready_sha256'] and sha(self.pl['bridge'])==b['bridge_sha256']
                and sha(self.pl['plugin'])==self.pl['plugin_sha256'])
        except (FileNotFoundError,ProcessLookupError):return False

    def runner(self,backend):
        if backend!='pl49':return super().runner(backend)
        if not self.pl_enabled:raise base.RequestError('PL_NOT_READY','当前V2A owner或依赖已失效，没有回退',409)
        return self.runners['pl49']

    def health(self):
        value=super().health();value['model_protocol']='v2a';return value

    def layer(self,options,folder):
        if options.get('backend')!='pl49':return super().layer(options,folder)
        import numpy as np
        from model.b.case_reader import load_case
        from model.b.golden_int64 import gemm
        if not self.pl_enabled:raise base.RequestError('PL_NOT_READY','当前V2A绑定未就绪',409)
        case,arrays=load_case(self.bundle,f"l{options['layerIndex']:02d}_{options['case']}")
        cfg=_strict_file(self.pl['config']);adapter=Bridge(self.pl['bridge'],cfg);begin=time.perf_counter_ns()
        try:
            if adapter.identity!=self.pl['identity']:raise RuntimeError('Changed actual V2A owner identity')
            before=adapter.info();code,y,receipt=adapter.run(case,arrays['Xq'],arrays['Wq'],folder.name,'layer')
            if code:raise RuntimeError(f"OWNER_FAULT: {receipt['error']}; no retry or fallback")
            after=adapter.info()
            if after['starts']!=before['starts']+1 or receipt['starts_after']!=receipt['starts_before']+1:
                raise RuntimeError('V2A layer requires exactly one start')
            y.astype('<i4').tofile(folder/'actual_Y.bin')
            mismatch=int(np.count_nonzero(y!=arrays['Y_golden']));independent=int(np.count_nonzero(y!=gemm(arrays['Xq'],arrays['Wq'])))
            value=dict(schema='b_layer_case_result_v2a',source='BOARD',status='PASS' if not(mismatch or independent) else 'FAIL',
                case_id=case['case_id'],layer_id=case['layer_id'],M=case['M'],K=case['K'],N=case['N'],execution_kind='FPGA_LAYER',
                integer_mismatches=mismatch,independent_INT64_mismatches=independent,api_elapsed_ms=(time.perf_counter_ns()-begin)/1e6,
                timing_scope='Synchronous V2A C bridge and independent validation',PL_starts=1,fallback_count=0,boot_id=self.boot,
                execution_host=base.platform.node(),bundle_identity=self.identity,kernel_cycles=None,kernel_cycles_reason='UNAVAILABLE',
                receipt=receipt,actual_owner_identity=adapter.identity,bridge_sha256=sha(self.pl['bridge']),
                bridge_config_sha256=sha(self.pl['config']),C_ready_sha256=sha(self.pl['ready']),candidate_sha256=cfg['identity']['candidate_sha256'],
                window_manifest_sha256=cfg['window_manifest_sha256'],ABI_version=2,wire_version=2,v2a_plugin_sha256=self.pl['plugin_sha256'])
            base.write_json(folder/'layer.result.json',value);return value
        finally:adapter.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--runs',type=Path,required=True)
    p.add_argument('--port',type=int,default=8742)
    for n in ('c-ready','bridge','bridge-config','plugin'):p.add_argument('--'+n,type=Path)
    a=p.parse_args()
    for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_DFL)
    engine=Engine(a.bundle,a.runs,a.c_ready,a.bridge,a.bridge_config,a.plugin)
    server=ThreadingHTTPServer(('127.0.0.1',a.port),base.make_handler(base.Jobs(engine)))
    print(json.dumps(dict(status='READY',boot_id=engine.boot,port=a.port,model_protocol='v2a',PL_enabled=engine.pl_enabled)),flush=True)
    server.serve_forever()

if __name__=='__main__':main()
