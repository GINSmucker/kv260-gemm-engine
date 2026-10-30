"""Correct Dense check arguments while retaining all frozen admission checks."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import stat
import sys
import types
sys.dont_write_bytecode=True
PUBLIC=Path('/home/ubuntu/fpga_project/opt50_integration/dense500_same_pl_20261030_v1/software')
SOURCE={'bytes':17233,'sha256':'e60866fca203ee366c9c9717cdbc55256cdde6ced7c25f007faf923854ad7b46'}
def need(ok,why):
    if not ok:raise ValueError(why)
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def read(path):
    path=Path(path).absolute();need('..' not in path.parts,'source traversal')
    for parent in (path,*path.parents):
        info=parent.lstat();need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'linked Dense source')
    before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=64*1024*1024,'bounded single-link Dense source')
    raw=path.read_bytes();after=path.stat()
    key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    need(key(before)==key(after) and len(raw)==before.st_size,'Dense source changed during read');return raw
def verify_source():
    raw=read(PUBLIC/'tools_manifest.json');need(pin(raw)==SOURCE,'original Dense source manifest differs')
    manifest=json.loads(raw);need(len(manifest['files'])==115 and manifest['schema']=='opt50_dense_batch_tools_v1','original Dense inventory differs')
    for name,wanted in manifest['files'].items():
        part=PurePosixPath(name)
        need(str(part)==name and not part.is_absolute() and all(p not in ('','.','..') for p in name.split('/')) and not any(c in name for c in '\\:\0\r\n'),'unsafe Dense source name')
        need(pin(read(PUBLIC.joinpath(*part.parts)))==wanted,'original Dense source changed: '+name)
    return manifest
def parse(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('guardian-plan','layer-plan','sparse-report','dataset'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--start',type=int,required=True);parser.add_argument('--count',type=int,default=5)
    parser.add_argument('--pilot-plan',type=Path);parser.add_argument('--metadata',type=Path)
    parser.add_argument('--uart-log-confirmed',action='store_true')
    return parser.parse_args(argv)
def dispatch(args,entry):
    return entry.check(args.guardian_plan,args.layer_plan,args.metadata,args.uart_log_confirmed,
        previous_path=args.sparse_report,dataset=args.dataset,start=args.start,count=args.count,pilot_path=args.pilot_plan)
def main(argv=None):
    args=parse(argv)
    try:
        verify_source();sys.path.insert(0,str(PUBLIC))
        entry=types.ModuleType('_dense_frozen_admission_fix1');entry.__file__=str(PUBLIC/'run_model.py')
        exec(compile(read(PUBLIC/'run_model.py'),entry.__file__,'exec'),entry.__dict__)
        from bounded_sparse_reference import install
        install(entry)
        value=dispatch(args,entry)
        print(json.dumps(value,indent=2,allow_nan=False),flush=True);return 0
    except Exception as error:
        print(json.dumps({'status':'DENSE_CHECK_FIX1_REFUSED','error':type(error).__name__+': '+str(error)}),flush=True);return 2
if __name__=='__main__':raise SystemExit(main())
