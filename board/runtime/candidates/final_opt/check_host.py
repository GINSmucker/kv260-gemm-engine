"""Check candidate SHA ranges and diagnostic memory records on an existing host compiler."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from prepare import HERE, BASE, pin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--crypto-library', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    output, compiler, crypto = args.out.absolute(), args.compiler.resolve(), args.crypto_library.resolve()
    if output.exists() or not compiler.is_file() or not crypto.is_file():
        raise ValueError('new output and existing compiler/crypto library required')
    output.mkdir(parents=True)
    env = dict(os.environ)
    env['PATH'] = str(compiler.parent)+os.pathsep+str(crypto.parent)+os.pathsep+env.get('PATH','')
    version = subprocess.check_output([str(compiler),'--version'],env=env).decode()
    result = {'status':'FAIL','hardware_executed':False,'ARM_build':'NOT RUN',
              'scope':'Host C++ header/SHA validation on actual packed bytes; not XRT BO, full Runner, ARM or performance verification',
              'compiler':{'path':str(compiler),'version':version,**pin(compiler.read_bytes())},
              'crypto_library':{'path':str(crypto),**pin(crypto.read_bytes())},
              'inputs':{name:pin((args.bundle/'model'/name).read_bytes()) for name in ('pl_w_data.bin','pl_w_meta.bin')},
              'sources':{'test_host.cpp':pin((HERE/'test_host.cpp').read_bytes()),
                         'diagnostic.hpp':pin((HERE/'diagnostic.hpp').read_bytes()),
                         'sha256_crypto.h':pin((BASE/'source/headers/sha256_crypto.h').read_bytes())},'steps':[]}
    try:
        for failure in (False,True):
            exe=output/('sha_failure.exe' if failure else 'sha_and_records.exe')
            command=[str(compiler),'-std=c++14','-O2','-Wall','-Wextra','-Werror',
                     '-I'+str(HERE),'-I'+str(BASE/'source'),'-I'+str(BASE/'source/headers'),str(HERE/'test_host.cpp')]
            command += ['-DOPT50_TEST_SHA_FAILURE'] if failure else [str(crypto)]
            command += ['-o',str(exe)]
            compiled=subprocess.run(command,env=env,capture_output=True,timeout=120)
            step={'compile':command,'compile_returncode':compiled.returncode,'compile_stderr':compiled.stderr.decode(errors='replace')}
            result['steps'].append(step)
            if compiled.returncode:
                raise RuntimeError('host compilation failed')
            argv=[str(exe)] if failure else [str(exe),*(str(args.bundle.resolve()/'model'/name) for name in ('pl_w_data.bin','pl_w_meta.bin'))]
            ran=subprocess.run(argv,env=env,capture_output=True,timeout=120)
            step.update(run=argv,run_returncode=ran.returncode,stdout=ran.stdout.decode(),stderr=ran.stderr.decode())
            if ran.returncode or ran.stderr:
                raise RuntimeError('host checks failed')
        result['status']='PASS'
    finally:
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
