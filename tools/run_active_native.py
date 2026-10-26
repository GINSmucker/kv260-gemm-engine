"""Native C++ functional check of THIS branch only; not HLS/RTL/board execution.

Requires an existing Windows Vitis toolchain. Creates a NEW output directory,
compiler temporaries, executable and logs. No tool installation or hardware access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vitis-root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    meta = json.loads((root/'VERSION.json').read_text(encoding='utf-8-sig'))
    subprocess.run([os.sys.executable, '-B', str(root/'tools/check_version_layout.py')], check=True)
    compiler = args.vitis_root/'win64/tools/clang-16/bin/clang++.exe'
    mingw = args.vitis_root/'tps/mingw/10.0.0/win64.o/nt'
    if not compiler.is_file() or args.out.exists() or args.out.is_symlink():
        raise ValueError('Existing toolchain and NEW output directory required')
    args.out.mkdir(parents=True)
    temporary = args.out/'compiler_tmp'
    temporary.mkdir()
    env = os.environ.copy()
    env['TEMP'] = str(temporary.resolve())
    env['TMP'] = str(temporary.resolve())
    env['PATH'] = os.pathsep.join([str(args.vitis_root/'lib/win64.o'), str(mingw/'bin'),
                                 str(args.vitis_root/'win64/lib/csim'), env.get('PATH','')])
    top = root/'hls/src'/('gemm.cpp' if meta['version']=='v1' else 'model_gemm_top.cpp')
    testbench = root/'hls/tb/testbench.cpp'
    executable = args.out/'native_testbench.exe'
    command = [str(compiler),'--target=x86_64-w64-windows-gnu','--sysroot='+str(mingw),
               '-std=c++11','-O2','-Wno-unknown-pragmas','-ferror-limit=8',
               '-I',str(args.vitis_root/'include'),str(top),str(testbench),
               '-pthread','-Wl,--stack,16777216','-o',str(executable.resolve())]
    built = subprocess.run(command,cwd=args.out,env=env,capture_output=True,timeout=180)
    (args.out/'build.stdout').write_bytes(built.stdout)
    (args.out/'build.stderr').write_bytes(built.stderr)
    if built.returncode:
        raise RuntimeError('Native compilation failed; see '+str(args.out/'build.stderr'))
    profile = [] if meta['version']=='v1' else ['rtl']
    run = subprocess.run([str(executable.resolve()),*profile],cwd=args.out,env=env,
                         capture_output=True,timeout=240)
    (args.out/'run.stdout').write_bytes(run.stdout)
    (args.out/'run.stderr').write_bytes(run.stderr)
    result = {'status':'PASS' if run.returncode==0 else 'FAIL', 'version':meta['version'],
              'branch':meta['branch'],'build_returncode':built.returncode,'run_returncode':run.returncode,
              'profile':profile or ['default'],'source_files':meta['source_files'],
              'stdout_sha256':hashlib.sha256(run.stdout).hexdigest(),
              'scope':'Native C++ only; rtl is a case-set name, not RTL simulation',
              'hardware_access':False,'FPGA_latency_measured':False}
    (args.out/'native_result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(result,indent=2))
    if run.returncode:
        raise SystemExit(1)


if __name__=='__main__':
    main()
