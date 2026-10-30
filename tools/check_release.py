"""Read-only final-source identities and reported-metric checks; no hardware access."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).absolute().parents[1]
def need(ok,why):
    if not ok:raise ValueError(why)
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def safe(root,name):
    rel=Path(name);need(name and not rel.is_absolute() and not rel.drive and '..' not in rel.parts and
        not any(c in name for c in '\\:\0\r\n') and all(p not in ('','.','..') for p in name.split('/')),'relative member required')
    path=root/rel
    need(path.absolute().is_relative_to(root.absolute()),'member escapes root')
    for p in (path,*path.parents):
        if not p.exists():continue
        need(not p.is_symlink() and not getattr(p.lstat(),'st_file_attributes',0)&0x400,'linked release path')
    return path
def verify(root=ROOT):
    root=Path(root);lock=json.loads((root/'release_sources.json').read_bytes())
    need(lock['schema']=='opt50_final_sources_v1','final source schema')
    for name,wanted in lock['frozen_files'].items():need(pin(safe(root,name).read_bytes())==wanted,'frozen source differs: '+name)
    history_root=root/'docs/provenance/pre-final-main'
    history=json.loads((history_root/'source_manifest.json').read_bytes())
    need(history['source_commit']==lock['base_main_commit'],'original main identity differs')
    for name,wanted in history['files'].items():need(pin(safe(history_root,name).read_bytes())==wanted,'archived original main differs: '+name)
    external=0
    for name,b in lock['bundles'].items():
        base=root/'board/runtime/snapshots'/name
        need(pin((base/b['manifest_name']).read_bytes())==b['manifest'],'original source manifest changed')
        for rel,wanted in b['stored_files'].items():need(pin(safe(base,rel).read_bytes())==wanted,'snapshot file differs: '+name+'/'+rel)
        external+=len(b['external_files'])
    metrics=json.loads((root/'results/board500/metrics.json').read_bytes())
    need(metrics['status']=='PASS' and metrics['images_accuracy']==500 and metrics['latency_images']==195,'fixed evidence scope')
    need(metrics['source_evidence']['sha256']==lock['evidence_archive']['sha256'],'evidence identity differs')
    need(metrics['accuracy']['Dense']['top1_correct']==353 and metrics['accuracy']['Sparse24']['top1_correct']==357,'reported accuracy changed')
    for p in ('hls','model','board','docs','results','tools'):need((root/p/'README.md').is_file(),'missing explanatory entry: '+p)
    cfg=(root/'hls/hls_config.cfg').read_text()
    for line in cfg.splitlines():
        if line.startswith(('syn.file=','tb.file=')):need(safe(root/'hls',line.split('=',1)[1].removeprefix('./')).is_file(),'HLS relative source missing')
    need('syn.top=model_gemm_top' in cfg and 'clock=8ns' in cfg,'OPT50 HLS identity')
    return {'status':'PASS','frozen_files_checked':len(lock['frozen_files']),'archived_main_files_checked':len(history['files']),'frozen_bundles':len(lock['bundles']),
        'external_payload_files':external,'external_payloads_present':False,'hardware_accessed':False,
        'scope':'Git source identities,layout and declared metrics; no asset restoration,HLS or board execution'}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args()
    print(json.dumps(verify(a.root),indent=2))
if __name__=='__main__':main()
