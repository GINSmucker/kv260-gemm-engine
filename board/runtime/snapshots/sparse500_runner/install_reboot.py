"""Static reboot copy only. No runtime, build, owner, signals, or hardware access."""
from pathlib import Path
import argparse
import json
import os
import stat
import sys
sys.dont_write_bytecode=True
import verify_package as v

HERE=Path(__file__).absolute().parent
SOURCE=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_resident_20261029_v4_fix2h1/software')
TARGET=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_resident_20261030_reboot_r1/software')
OLD={'bytes':24547,'sha256':'8f256e8eca931b80ea9513c62f0921ec0700558c5604476ea68902833411bd1e'}
NEW={'bytes':25141,'sha256':'38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d'}
CHANGED={'README.md','release_compat.py','run_model.py','verify_tools.py'}
ADDED={'reboot_review.py','predecessor_h1_tools_manifest.json','tests/test_reboot_review_r1.py'}


def read(path):
    path=v.ordinary(path); before=path.lstat()
    v.need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=64*1024*1024,'bounded regular static source required')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    with os.fdopen(fd,'rb') as stream:
        v.need(v.key(os.fstat(stream.fileno()))==v.key(before),'static input replaced before read')
        raw=stream.read(64*1024*1024+1)
        v.need(v.key(os.fstat(stream.fileno()))==v.key(before),'static input changed during read')
    v.need(v.key(path.lstat())==v.key(before) and len(raw)==before.st_size,'static input replaced after read')
    return raw


def delta(old,new):
    v.need(type(old.get('files')) is dict and len(old['files'])==155 and type(new.get('files')) is dict and
           len(new['files'])==158 and not set(old['files'])&ADDED and
           set(new['files'])==set(old['files'])|ADDED,'fixed 155-to-158 static inventory differs')
    expected=dict(old,files=new['files'],software_revision='resident-bounded-batch-v4-reboot-r1',
                  predecessor_h1_tools_manifest=OLD)
    v.need(new==expected,'fixed reboot static metadata differs')
    v.need({name for name in old['files'] if old['files'][name]!=new['files'][name]}==CHANGED,'exact four changed static files required')
    protected={name for name in old['files'] if name.startswith(('source/','model/','data/')) or Path(name).suffix in ('.cpp','.h','.hpp')}
    actual={name for name in new['files'] if name.startswith(('source/','model/','data/')) or Path(name).suffix in ('.cpp','.h','.hpp')}
    v.need(len(protected)==95 and actual==protected and all(old['files'][name]==new['files'][name] for name in protected),'95 protected model/data/Native pins must remain identical')


def verify_tree(folder,listing,manifest_pin):
    v.need(v.identity(read(folder/'tools_manifest.json'))==manifest_pin,'static manifest changed')
    for name,pin in listing['files'].items():
        v.relative(name); v.need(v.identity(read(folder/name))==pin,'static source changed: '+name)


def sync(folder):
    v.ordinary(folder)
    fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def mkdir(path):
    v.ordinary(path.parent); os.mkdir(path,0o755)
    os.chmod(path,0o755); sync(path); sync(path.parent)


def write(path,raw):
    v.ordinary(path.parent)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    with os.fdopen(fd,'wb') as stream:
        os.fchmod(stream.fileno(),0o644); v.need(stream.write(raw)==len(raw),'short static copy')
        stream.flush(); os.fsync(stream.fileno())
    sync(path.parent); v.need(read(path)==raw,'static destination bytes differ')


def install(manifest_sha,verify_only=False):
    v.need(os.name=='posix' and os.geteuid()==0,'static installer requires native root Linux')
    v.verify_package(HERE,manifest_sha)
    old_raw=read(SOURCE/'tools_manifest.json'); new_raw=read(HERE/'software_payload/tools_manifest.json')
    v.need(v.identity(old_raw)==OLD and v.identity(new_raw)==NEW,'fixed old/new software manifest differs')
    old,new=v.decode(old_raw),v.decode(new_raw); delta(old,new); verify_tree(SOURCE,old,OLD)
    replacements={name:read(HERE/'software_payload'/name) for name in CHANGED|ADDED}
    v.need(all(v.identity(raw)==new['files'][name] for name,raw in replacements.items()),'exact frozen reboot replacement differs')
    if verify_only: verify_tree(v.ordinary(TARGET),new,NEW)
    else:
        v.need(not TARGET.parent.exists() and not TARGET.parent.is_symlink(),'reboot target exists or partial; preserve it, never overwrite/resume')
        mkdir(TARGET.parent); mkdir(TARGET)
        for name in sorted(new['files']):
            target=TARGET.joinpath(*v.relative(name))
            missing=[]; parent=target.parent
            while not parent.exists(): missing.append(parent); parent=parent.parent
            for parent in reversed(missing): mkdir(parent)
            raw=replacements[name] if name in replacements else read(SOURCE/name)
            v.need(v.identity(raw)==new['files'][name],'source changed before copy: '+name); write(target,raw)
        write(TARGET/'tools_manifest.json',new_raw)
        verify_tree(TARGET,new,NEW); verify_tree(SOURCE,old,OLD)
    return {'status':'REBOOT_STATIC_158_FILES_PASS_NO_EXECUTION','source':str(SOURCE),'target':str(TARGET),
            'files_checked':158,'protected_files_unchanged':95,'verify_only':verify_only,
            'source_unchanged':True,'build_copied':False,'programs_started':False,'hardware_accessed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-sha256',required=True); parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    try: print(json.dumps(install(args.manifest_sha256,args.verify_only),indent=2))
    except Exception as error:
        raise SystemExit('H1_STATIC_INSTALL_REFUSED_PARTIAL_PRESERVED: '+type(error).__name__+': '+str(error))
