"""Native OPT50 C++ functional test using an existing Vitis toolchain; not RTL/HLS/board."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from check_release import ROOT,need,pin,verify
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--vitis-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    verify();out=a.out.absolute();need(not out.exists() and out.parent.is_dir(),'exclusive new output required')
    compiler=a.vitis_root/'win64/tools/clang-16/bin/clang++.exe';mingw=a.vitis_root/'tps/mingw/10.0.0/win64.o/nt'
    need(compiler.is_file(),'existing Windows Vitis clang required');out.mkdir();temporary=out/'compiler_tmp';temporary.mkdir()
    env=dict(os.environ);env['TEMP']=str(temporary);env['TMP']=str(temporary)
    env['PATH']=os.pathsep.join([str(a.vitis_root/'lib/win64.o'),str(mingw/'bin'),str(a.vitis_root/'win64/lib/csim'),env.get('PATH','')])
    command=[str(compiler),'--target=x86_64-w64-windows-gnu','--sysroot='+str(mingw),'-std=c++11','-O2','-Wno-unknown-pragmas','-ferror-limit=8',
        '-I',str(a.vitis_root/'include'),str(ROOT/'hls/src/model_gemm_top.cpp'),str(ROOT/'hls/tb/testbench.cpp'),'-pthread','-Wl,--stack,16777216','-o',str(out/'native_testbench.exe')]
    built=subprocess.run(command,cwd=out,env=env,capture_output=True,timeout=180)
    (out/'build.stdout').write_bytes(built.stdout);(out/'build.stderr').write_bytes(built.stderr)
    need(built.returncode==0,'native compilation failed; inspect output')
    run=subprocess.run([str(out/'native_testbench.exe'),'rtl'],cwd=out,env=env,capture_output=True,timeout=240)
    (out/'run.stdout').write_bytes(run.stdout);(out/'run.stderr').write_bytes(run.stderr)
    value={'status':'PASS' if run.returncode==0 else 'FAIL','scope':'Native C++ functional case-set named rtl; not RTL simulation,HLS synthesis or board execution',
        'command':command,'stdout':run.stdout.decode(errors='replace'),'stderr':run.stderr.decode(errors='replace'),'hardware_accessed':False,
        'HLS_top':pin((ROOT/'hls/src/model_gemm_top.cpp').read_bytes()),'HLS_core':pin((ROOT/'hls/src/model_gemm_core.hpp').read_bytes())}
    (out/'result.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value,indent=2));need(run.returncode==0,'native functional test failed')
if __name__=='__main__':main()
