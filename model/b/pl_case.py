"""Explicit ready-bound PL layer entry; independent INT64 remains the oracle."""
from pathlib import Path
from .common import read_json, sha
from .gemm_abi import Bridge

def dependencies(c_ready,bridge,config):
    if not c_ready or not bridge or not config:
        raise ValueError('NOT_READY: explicit C readiness, bridge and configuration required')
    ready=read_json(c_ready)
    if ready.get('status')!='PASS' or ready.get('abi_version')!=1:
        raise ValueError('NOT_READY: C bridge gate has not passed')
    if ready.get('bridge_sha256')!=sha(bridge):
        raise ValueError('BAD_IDENTITY: C bridge binary hash')
    if ready.get('A2_gate_sha256')!='a2c1e1f93c81d275488ebf87ae813a12cbd7931674a0f6f21578301ba81ba80d':
        raise ValueError('BAD_IDENTITY: A2 gate binding')
    return read_json(config)

def run(case,arrays,c_ready,library,config,run_id,image_id):
    cfg=dependencies(c_ready,library,config)
    adapter=Bridge(library,cfg)
    try:
        if adapter.identity.get('software_test') is not False:
            raise ValueError('BAD_IDENTITY: software mock cannot execute a production PL case')
        before=adapter.info()
        code,y,receipt=adapter.run(case,arrays['Xq'],arrays['Wq'],run_id,image_id)
        if code:raise RuntimeError(f"OWNER_FAULT: {receipt['error']}; no retry or fallback")
        after=adapter.info()
        if after['starts']!=before['starts']+1 or receipt['starts_after']!=receipt['starts_before']+1:
            raise ValueError('BAD_IDENTITY: layer requires exactly one start')
        return y,{'receipt':receipt,'actual_owner_identity':adapter.identity,
                  'bridge_sha256':sha(library),'bridge_config_sha256':sha(config),'C_ready_sha256':sha(c_ready),
                  'candidate_sha256':cfg.get('candidate_sha256'),'window_manifest_sha256':cfg.get('window_manifest_sha256')}
    finally:
        adapter.close()
