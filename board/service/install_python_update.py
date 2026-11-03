"""Install the two CPU-only service changes after a verified normal service stop."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

APP=Path('/opt/opt50-demo-v1/private')
BASELINE_BACKEND='1c2ea52e51a996267f0c469e56fd3f0f65561beef6907ab936e673620fa680be'


def need(ok,message):
    if not ok:raise RuntimeError(message)


def pin(raw):
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def publish(path,raw):
    temporary=path.with_name(path.name+'.python-update')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as handle:
        handle.write(raw);handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def main():
    need(os.geteuid()==0,'root required')
    stage=Path(__file__).resolve().parent
    expected=json.loads((stage/'update_manifest.json').read_bytes())
    payload={name:(stage/name).read_bytes() for name in ['backend.py','cpu_optimizations.py']}
    need(all(pin(raw)==expected['files'][name] for name,raw in payload.items()),'staged Python source differs')
    validation=json.loads((stage/'candidates.json').read_bytes())
    need(validation['status']=='PASS' and validation['preprocessing_equivalent_cases']==31
         and len(validation['audit_checks'])==13,'candidate validation missing')
    old_backend=(APP/'backend.py').read_bytes()
    old_receipt=(APP/'install_receipt.json').read_bytes()
    need(hashlib.sha256(old_backend).hexdigest()==BASELINE_BACKEND,'unexpected current backend; no overwrite')
    sys.path.insert(0,str(APP))
    import backend
    original=backend.verify_install()
    frozen={name:value for name,value in original['files'].items() if name!='backend.py'}
    need(subprocess.run(['systemctl','is-active','--quiet','opt50-demo-hardware.service']).returncode!=0,
         'normal service stop required; do not restart in the same boot')
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:argv=(process/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError,PermissionError,ProcessLookupError):continue
        need(not (argv and argv[0].endswith(b'/native_owner') and b'/var/lib/opt50-demo-v1/' in argv[0]),
             'Native owner remains; no update')
    backup=Path('/var/lib/opt50-demo-python-update-20261101')
    backup.mkdir(mode=0o700)
    publish(backup/'backend.py',old_backend)
    publish(backup/'install_receipt.json',old_receipt)
    receipt=dict(original)
    receipt['files']=dict(original['files'])
    for name,raw in payload.items():receipt['files'][name]=pin(raw)
    receipt['python_optimization']={'profile':'exact-rgb-path-audit-v1','baseline_backend_sha256':BASELINE_BACKEND,
                                  'compute_kernel_configuration_or_Native_changed':False}
    try:
        for name,raw in payload.items():publish(APP/name,raw)
        publish(APP/'install_receipt.json',(json.dumps(receipt,sort_keys=True)+'\n').encode())
        verified=backend.verify_install()
        need(all(verified['files'][name]==value and pin((APP/name).read_bytes())==value for name,value in frozen.items()),
             'frozen source or Native binary differs')
    except BaseException:
        publish(APP/'backend.py',old_backend)
        publish(APP/'install_receipt.json',old_receipt)
        raise
    result={'status':'PASS','profile':'exact-rgb-path-audit-v1','unchanged_files':len(frozen),
            'backup':str(backup),'changed_files':{name:pin(raw) for name,raw in payload.items()},
            'service_restarted':False,'requires_new_boot':True}
    publish(backup/'update_receipt.json',(json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps(result))


if __name__=='__main__':main()
