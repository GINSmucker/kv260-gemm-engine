"""Read-only source/model audit of the isolated original-Dense release."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import stat
import sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).absolute().parent
MODEL_SHA='f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'
def need(ok,why):
    if not ok:raise ValueError(why)
def identity(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def decode(raw):
    def pairs(items):
        out={}
        for k,v in items:need(k not in out,'duplicate JSON key');out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
def read(path):
    path=Path(path).absolute();need('..' not in path.parts,'parent traversal')
    for p in (path,*path.parents):
        s=p.lstat();need(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400,'linked source')
    before=path.lstat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=64*1024*1024,'bounded regular source')
    raw=path.read_bytes();after=path.lstat()
    key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
    need(key(before)==key(after) and len(raw)==before.st_size,'source changed');return raw
def verify(root=ROOT):
    root=Path(root).absolute();v=decode(read(root/'tools_manifest.json'))
    required={'run_model.py','run_once.py','model_worker.py','model_runtime.py','legacy_model.py','resident_proof.py',
        'paired_reference.py','release_compat.py','history_index.py','guardian_checkpoint.py','dataset.py','accuracy_metrics.py',
        'status_receipt.py','build_arm.py','source/kernel.cpp','source/model_core.hpp','source/native_gate.hpp',
        'source/owner.cpp','source/b_plugin.cpp','source/client.cpp','model/manifest.json','model/reference.onnx',
        'test_model_core.cpp','test_performance_core.cpp','verify_tools.py'}
    need(v.get('schema')=='opt50_dense_batch_tools_v1' and v.get('source_model_sha256')==MODEL_SHA and
        v.get('software_revision')=='original-dense36-resident-v1' and v.get('hardware_executed') is False and
        type(v.get('files')) is dict and required<=v['files'].keys() and len(v['files'])<=200,'fixed Dense release inventory')
    for name,p in v['files'].items():
        part=PurePosixPath(name)
        need(type(name) is str and str(part)==name and not part.is_absolute() and '..' not in part.parts and
             not any(c in name for c in '\\:\0\r\n'),'unsafe source name')
        need(type(p) is dict and set(p)=={'bytes','sha256'} and identity(read(root.joinpath(*part.parts)))==p,'source identity differs: '+name)
    return v
if __name__=='__main__':
    value=verify();print(json.dumps({'status':'DENSE36_FILES_PASS_NO_HARDWARE_ACCESS','files_checked':len(value['files']),'hardware_executed':False},indent=2))
