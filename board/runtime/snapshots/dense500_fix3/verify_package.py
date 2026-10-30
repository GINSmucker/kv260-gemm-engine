"""Verify only this correction and its exact original Dense software."""
import argparse,hashlib,json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
HERE=Path(__file__).absolute().parent
def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest-sha256',required=True);a=p.parse_args()
    raw=(HERE/'files_manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=a.manifest_sha256:raise ValueError('fixed patch manifest differs')
    v=json.loads(raw)
    if v['schema']!='opt50_dense500_tool_files_v1' or v['target_total']!=500:raise ValueError('fixed Dense500 scope differs')
    for name,wanted in v['files'].items():
        path=Path(name)
        if path.is_absolute() or len(path.parts)!=1 or path.name in ('.','..') or (HERE/path).is_symlink():raise ValueError('ordinary patch file required')
        data=(HERE/path).read_bytes()
        if {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}!=wanted:raise ValueError('patch file changed: '+name)
    from check_dense_fix1 import verify_source
    manifest=verify_source()
    print(json.dumps({'status':'DENSE_ENTRY_FIX1_AND_ORIGINAL115_FILES_PASS_NO_HARDWARE_ACCESS','source_files':len(manifest['files']),'ARM_recompile_required':False,'hardware_executed':False},indent=2))
if __name__=='__main__':main()
