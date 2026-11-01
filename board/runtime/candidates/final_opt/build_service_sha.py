"""Build an independently pinned INFO-SHA owner with the installed service gate intact."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from prepare import crypto,pin


def main():
    sys.path.insert(0,'/opt/opt50-demo-v1/private')
    import backend
    installed=backend.verify_install()
    app=Path('/opt/opt50-demo-v1/private');root=Path('/var/lib/opt50-final-20261101/service-openssl-info')
    os.umask(0o077);root.mkdir(mode=0o700,exist_ok=False)
    shutil.copytree(app/'source',root/'source')
    before=(root/'source/kernel.cpp').read_bytes()
    (root/'source/kernel.cpp').write_text(crypto(before.decode()),encoding='utf-8')
    compiler=str(Path(shutil.which('g++-13')).resolve())
    command=[compiler,'-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread','-DKV5_DIAGNOSTIC',
             '-DOPT50_PERFORMANCE_LIGHT=1','-DOPT50_H576_ALIGNED=1','-DOPT50_RESIDENT_BATCH=1',
             '-fno-fast-math','-ffp-contract=off','-I/usr/include/xrt',
             '-I/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include',
             '-I/var/lib/opt50-demo-v1/model','-I'+str(root/'source'),'-I'+str(root/'source/headers'),
             str(root/'source/owner.cpp'),str(root/'source/kernel.cpp'),'-lxrt_coreutil','-l:libuuid.so.1','-l:libcrypto.so.3',
             '-o',str(root/'opt50_model_owner')]
    result={'schema':'opt50_final_service_sha_build_v1','status':'FAIL','hardware_executed':False,'installed':False,
            'builder':pin(Path(__file__).read_bytes()),'installed_receipt':pin((app/'install_receipt.json').read_bytes()),
            'baseline_kernel':pin(before),'command':command,'compiler':{'path':compiler,**pin(Path(compiler).read_bytes())},
            'sources':{p.relative_to(root).as_posix():pin(p.read_bytes()) for p in sorted((root/'source').rglob('*')) if p.is_file()}}
    try:
        ran=subprocess.run(command,capture_output=True,timeout=240)
        (root/'compiler.stdout').write_bytes(ran.stdout);(root/'compiler.stderr').write_bytes(ran.stderr)
        result['compile_returncode']=ran.returncode
        if ran.returncode:raise RuntimeError('service SHA owner build failed')
        result['binary']=pin((root/'opt50_model_owner').read_bytes())
        # The service-specific gate is copied unchanged; only final_checks differs.
        if (root/'source/native_gate.hpp').read_bytes()!=(app/'source/native_gate.hpp').read_bytes():
            raise ValueError('service gate changed')
        backend.verify_install();result['status']='PASS'
    finally:(root/'build_receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
