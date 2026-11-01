"""Prepare a fresh current-boot plan for the unmodified Native gate; no hardware access."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time

from prepare import pin
from build import verify

APP=Path('/opt/opt50-demo-v1/private')
WORK=Path('/var/lib/opt50-final-20261101')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant',choices=['baseline','diagnostic','openssl-info'],required=True)
    parser.add_argument('--count',type=int,required=True)
    parser.add_argument('--ort-profile',action='store_true')
    parser.add_argument('--spinning',type=int,choices=[0,1],default=0)
    parser.add_argument('--force-stop-spinning',action='store_true')
    parser.add_argument('--dynamic-block',action='store_true')
    parser.add_argument('--input',type=Path,action='append',required=True)
    args=parser.parse_args()
    if args.force_stop_spinning and args.spinning!=1:raise ValueError('forced stop applies to spinning candidate')
    if not (sys.platform=='linux' and os.geteuid()==0 and 1<=args.count<=50):
        raise ValueError('root Linux and bounded graph count required')
    candidate=WORK/'candidates'/args.variant;build=WORK/'builds'/args.variant
    manifest=verify(candidate);receipt=json.loads((build/'build_receipt.json').read_bytes())
    if receipt['status']!='PASS' or receipt['candidate_manifest']!=pin((candidate/'candidate_manifest.json').read_bytes()):
        raise ValueError('matching native build required')
    for name,wanted in receipt['binaries'].items():
        if pin((build/name).read_bytes())!=wanted:raise ValueError('binary changed')
    token=secrets.token_hex(6);root=Path('/var/lib/opt50-b24-model-v1')/('plan_'+token)
    os.umask(0o077);root.mkdir(parents=True,exist_ok=False)
    for folder in ('execution','tools','inputs'):(root/folder).mkdir(mode=0o700)
    shutil.copytree(candidate/'model',root/'model')
    for source,target in ((build/'opt50_model_owner',root/'native_owner'),(build/'libopt50_model.so',root/'libopt50_model.so')):
        shutil.copyfile(source,target)
    os.chmod(root/'native_owner',0o700)
    for path in Path(__file__).parent.glob('*.py'):
        shutil.copyfile(path,root/'tools'/('run_model.py' if path.name=='coordinate.py' else path.name))
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    boot_folder=Path('/var/lib/opt50-demo-v1')/('boot_'+boot)
    for name in ('metadata.xclbin','xrt.ini'):shutil.copyfile(boot_folder/name,root/name)
    if pin((root/'metadata.xclbin').read_bytes())['sha256']!='715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551':
        raise ValueError('paired metadata differs')
    inputs=[]
    for index,path in enumerate(args.input):
        raw=path.read_bytes();name='%02d.npy'%index;(root/'inputs'/name).write_bytes(raw)
        inputs.append({'name':name,'original_path':str(path),**pin(raw)})
    for p in root.rglob('*'):os.chmod(p,0o700 if p.is_dir() or p.name=='native_owner' else 0o600)
    plan={'schema':'opt50_final_current_boot_window_v1','token':token,'boot_id':boot,'root':str(root),
          'created_monotonic_ns':time.monotonic_ns(),'sample_count':args.count,'total_START_budget':36*args.count,
          'variant':args.variant,'ort_profile':args.ort_profile,'inputs':inputs,
          'ORT_intra_spinning':args.spinning,
          'ORT_force_spinning_stop':int(args.force_stop_spinning),
          'ORT_dynamic_block_base':4 if args.dynamic_block else 0,
          'source_model_sha256':manifest['source_model_sha256'],
          'authorization':'2026-11-01 user authorized exclusive final diagnostics, necessary normal reboot and validated paired installation',
          'candidate_manifest':pin((candidate/'candidate_manifest.json').read_bytes()),
          'build_receipt':pin((build/'build_receipt.json').read_bytes()),
          'files':{p.relative_to(root).as_posix():pin(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()}}
    sys.path.insert(0,str(APP));from common import create,encoded
    create(root/'plan.json',encoded(plan))
    print(json.dumps({'root':str(root),'unit':'opt50-b24-model-'+token,'plan':plan},indent=2))


if __name__=='__main__':main()
