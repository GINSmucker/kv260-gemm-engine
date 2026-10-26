"""Read-only active-version layout, source and configuration validation; no board access."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    value = json.loads((root/'VERSION.json').read_text(encoding='utf-8-sig'))
    if value['schema'] != 'kv260_version_branch_v1' or value['version'] not in ('v1','v2','v3'):
        raise ValueError('Unknown branch version')
    required = ['README.md','VERSION.json','docs/interface.md','docs/verification.md',
                *[name+'/README.md' for name in ('hls','model','board','docs','results')]]
    for name in required:
        path = root/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing explanatory entry: '+name)
    for name, expected in value['source_files'].items():
        path = root/name
        path.resolve().relative_to(root.resolve())
        if path.is_symlink():
            raise ValueError('Source links are not accepted')
        raw = path.read_bytes()
        if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
            raise ValueError('Active source differs: '+name)
    config = root/value['active_paths']['hls_config']
    sources = []
    for line in config.read_text(encoding='utf-8-sig').splitlines():
        if line.startswith(('syn.file=','tb.file=')):
            relative = line.split('=',1)[1]
            if ':' in relative or relative.startswith(('/', '\\')):
                raise ValueError('Active HLS config requires portable relative sources')
            path = config.parent/relative
            path.resolve().relative_to((root/'hls').resolve())
            if not path.is_file():
                raise ValueError('HLS source path missing: '+relative)
            sources.append(path)
    if not sources:
        raise ValueError('No active HLS sources')
    for name in ('hls','runtime','model'):
        path = root/value['active_paths'][name]
        path.resolve().relative_to(root.resolve())
        if not path.is_dir():
            raise ValueError('Active directory missing: '+name)
    print(json.dumps({'status':'PASS','version':value['version'],'branch':value['branch'],
                      'source_files_checked':len(value['source_files']),
                      'README_entries_checked':6,'hardware_access':False,'writes':False,
                      'scope':'Active branch layout and byte identity only; not HLS or board proof'},indent=2))


if __name__ == '__main__':
    main()
