"""Verify only the fixed, standalone 500-image tool sources; no board access."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

sys.dont_write_bytecode = True
SCHEMA = 'opt50_b24_acceptance500_tool_files_v1'
SOURCE_FIX2 = {
    'path': '/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_resident_20261030_reboot_r1/software',
    'tools_manifest': {'bytes':25141,'sha256':'38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d'},
    'files':158,
}
FILES = {'run_to_500.py','report500.py','verify_package.py','README.md',
         'tests/test_run_to_500.py','tests/test_report500.py','tests/test_verify_package.py',
         'install_reboot.py','software_payload/README.md','software_payload/release_compat.py',
         'software_payload/run_model.py','software_payload/verify_tools.py','software_payload/reboot_review.py',
         'software_payload/predecessor_h1_tools_manifest.json','software_payload/tests/test_reboot_review_r1.py',
         'software_payload/tools_manifest.json'}
MAX_BYTES = 2*1024*1024


def need(value, reason):
    if not value: raise ValueError(reason)


def identity(raw):
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def decode(raw):
    def pairs(items):
        value={}
        for key,item in items:
            need(key not in value,'duplicate tool manifest key'); value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite tool manifest')))


def relative(name):
    need(type(name) is str and name and len(name)<=256 and not any(c in name for c in '\\:\0\r\n'),
         'unsafe tool path')
    path=PurePosixPath(name)
    need(not path.is_absolute() and str(path)==name and all(p not in ('','.','..') for p in name.split('/')),
         'unsafe tool path')
    return path.parts


def ordinary(path):
    path=Path(path).absolute(); need('..' not in path.parts,'parent traversal refused')
    for part in (path,*path.parents):
        info=part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,
             'linked tool path refused')
    return path


def key(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,
            info.st_birthtime_ns if os.name=='nt' else info.st_ctime_ns)


def read(path):
    path=ordinary(path); before=path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=MAX_BYTES,
         'bounded single-link tool file required')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    with os.fdopen(fd,'rb') as stream:
        need(key(os.fstat(stream.fileno()))==key(before),'tool replaced before read')
        raw=stream.read(MAX_BYTES+1)
        need(key(os.fstat(stream.fileno()))==key(before),'tool changed while reading')
    need(key(path.lstat())==key(before) and len(raw)==before.st_size,'tool replaced after read')
    return raw


def checked(path, pin):
    need(type(pin) is dict and set(pin)=={'bytes','sha256'} and type(pin['bytes']) is int and
         0<=pin['bytes']<=MAX_BYTES and type(pin['sha256']) is str and re.fullmatch('[a-f0-9]{64}',pin['sha256']),
         'invalid tool file identity')
    raw=read(path); need(identity(raw)==pin,'tool identity differs: '+str(path)); return raw


def verify_package(folder, manifest_sha):
    folder=ordinary(folder)
    need(type(manifest_sha) is str and re.fullmatch('[a-f0-9]{64}',manifest_sha),'explicit tool manifest SHA256 required')
    raw=read(folder/'files_manifest.json'); need(identity(raw)['sha256']==manifest_sha,'tool manifest SHA256 differs')
    value=decode(raw)
    need(type(value) is dict and set(value)=={'schema','target_total','source_fix2','files'} and
         value['schema']==SCHEMA and type(value['target_total']) is int and value['target_total']==500 and
         value['source_fix2']==SOURCE_FIX2 and type(value['files']) is dict and set(value['files'])==FILES,
         'fixed 500-image source/profile/tool inventory differs')
    for name,pin in value['files'].items():
        relative(name); checked(folder.joinpath(*relative(name)),pin)
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-sha256',required=True)
    args=parser.parse_args()
    listing=verify_package(Path(__file__).absolute().parent,args.manifest_sha256)
    print(json.dumps({'status':'ACCEPTANCE500_TOOL_FILES_PASS_NO_HARDWARE_ACCESS',
        'files_checked':len(listing['files']),'target_total':500,'source_fix2':SOURCE_FIX2,
        'hardware_accessed':False,'owner_started':False},indent=2))


if __name__=='__main__':
    try: main()
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise SystemExit('ACCEPTANCE500_TOOLS_REFUSED: '+type(error).__name__+': '+str(error))
