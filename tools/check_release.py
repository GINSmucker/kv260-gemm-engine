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
def verify_supplemental(root=ROOT):
    """Check copied sources and the local dependencies that their consumers use."""
    root = Path(root)
    manifest = json.loads((root / 'supplemental_sources.json').read_bytes())
    need(manifest['schema'] == 'opt50_supplemental_sources_v1', 'supplemental schema')
    entries = manifest['files']
    cache = {}

    def read(name):
        need(name in entries, 'supplemental dependency not declared: ' + name)
        path = safe(root, name)
        need(path.is_file(), 'supplemental dependency missing: ' + name)
        if name not in cache:
            raw = path.read_bytes()
            wanted = {k: entries[name][k] for k in ('bytes', 'sha256')}
            need(pin(raw) == wanted, 'supplemental source differs: ' + name)
            cache[name] = raw
        return cache[name]

    def decode(name):
        return json.loads(read(name))

    for name, item in entries.items():
        need(re.fullmatch(r'[0-9a-f]{40}', item['source_commit']), 'source commit required')
        safe(root, item['source_path'])
        read(name)

    recipe = json.loads((root / 'results/platform_history/platform_project_manifest.json').read_bytes())
    local_testbenches = {
        'tests/tb_opt50_board_logic.sv': 'board/platform/scripts/logic_sim.tcl',
        'input/observer/tb_f8_status.sv': 'board/platform/scripts/create_system.tcl',
    }
    for relative, consumer in local_testbenches.items():
        name = 'board/platform/' + relative
        need(pin(read(name)) == recipe['files'][relative], 'original simulation source differs: ' + name)
        need(' '.join(relative.split('/')) in (root / consumer).read_text(),
             'platform consumer no longer references ' + name)

    proof = json.loads((root / 'board/platform/verification_v2/verification_manifest.json').read_bytes())
    reports = ('check_timing.rpt', 'clocks.rpt', 'drc.rpt', 'route_status.rpt',
               'timing_summary.rpt', 'utilization.rpt')
    for report in reports:
        name = 'results/platform_history/implementation_reports/' + report
        expected = proof['finish_evidence']['execution/reports/' + report]
        need(pin(read(name)) == expected, 'historical implementation report differs: ' + report)

    index = decode('results/model_planning/h576_closeout_20261016_01/delivery_index.json')
    for key in ('performance', 'quality', 'C_endurance', 'Chrome_real_PL_desktop'):
        spec = index['evidence'][key]
        need(pin(read(spec['path']))['sha256'] == spec['sha256'], 'presentation evidence differs: ' + key)
    chrome = decode(index['evidence']['Chrome_real_PL_desktop']['path'])
    for name in ('benchmark.json', 'pl.json'):
        path = 'results/model_planning/' + chrome['run_id'] + '/local_chrome/' + name
        need(pin(read(path))['sha256'] == chrome['browser_files'][name], 'browser export differs: ' + name)

    read('start-workbench.ps1')
    decode('board/ui/package.json')
    html = read('board/ui/public/index.html').decode('utf-8')
    for name in re.findall(r'(?:src|href)=["\'](/[^"\']+)', html):
        read('board/ui/public/' + name.lstrip('/'))
    for name in ('tokens.css', 'assets/bridge.svg', 'assets/dog.svg', 'assets/pattern.svg'):
        read('board/ui/public/' + name)
    modules = ['board/ui/server.mjs'] + [
        n for n in entries if n.startswith('board/ui/public/') and n.endswith('.js')]
    visited = set()
    while modules:
        name = modules.pop()
        if name in visited:
            continue
        visited.add(name)
        source = read(name).decode('utf-8')
        for _, target in re.findall(r'\b(?:from\s*|import\s*)(["\'])(\.[^"\']+)\1', source):
            resolved = (root / name).parent.joinpath(target).resolve()
            need(resolved.is_relative_to(root.resolve()), 'website import escapes repository')
            dependency = resolved.relative_to(root.resolve()).as_posix()
            read(dependency)
            modules.append(dependency)

    architecture = decode('docs/diagrams/verification.json')
    need(pin(read('docs/diagrams/architecture.html'))['sha256'] == architecture['artifact_sha256'],
         'historical architecture artifact differs')
    need(pin(read('docs/diagrams/architecture.json'))['sha256'] == architecture['specification_sha256'],
         'historical architecture specification differs')
    read('docs/diagrams/LICENSE.archify.txt')
    return {'files_checked': len(entries), 'website_modules_checked': len(visited),
            'platform_testbenches_checked': len(local_testbenches), 'historical_reports_checked': len(reports)}


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
    supplemental = verify_supplemental(root)
    return {'status':'PASS','frozen_files_checked':len(lock['frozen_files']),'archived_main_files_checked':len(history['files']),'frozen_bundles':len(lock['bundles']),
        'supplemental':supplemental,
        'external_payload_files':external,'external_payloads_present':False,'hardware_accessed':False,
        'scope':'Git source identities,layout and declared metrics; no asset restoration,HLS or board execution'}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args()
    print(json.dumps(verify(a.root),indent=2))
if __name__=='__main__':main()
