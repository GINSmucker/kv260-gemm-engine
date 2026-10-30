"""Compile two device-free C++ tests from one fully restored original runtime bundle."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from check_release import need,pin
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--compiler',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    bundle=a.bundle.absolute();out=a.out.absolute();compiler=a.compiler.absolute()
    need(bundle.is_dir() and compiler.is_file() and not out.exists() and out.parent.is_dir(),'restored source,existing compiler and NEW output required')
    subprocess.run([os.sys.executable,'-B',str(bundle/'verify_tools.py')],check=True)
    out.mkdir();temp=out/'compiler_tmp';temp.mkdir();env=dict(os.environ);env['PATH']=str(compiler.parent)+os.pathsep+env.get('PATH','');env['TEMP']=str(temp);env['TMP']=str(temp)
    flags=[str(compiler),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread','-DKV5_DIAGNOSTIC','-DOPT50_PERFORMANCE_LIGHT=1',
        '-DOPT50_H576_ALIGNED=1','-DOPT50_RESIDENT_BATCH=1','-fno-fast-math','-ffp-contract=off',
        '-I'+str(bundle/'model'),'-I'+str(bundle/'source'),'-I'+str(bundle/'source/headers')]
    records=[]
    for name in ('model_core','performance_core'):
        source=bundle/('test_'+name+'.cpp');exe=out/(name+('.exe' if os.name=='nt' else ''))
        cmd=flags+[str(source),'-o',str(exe)];compiled=subprocess.run(cmd,cwd=out,env=env,capture_output=True,timeout=180)
        (out/(name+'.build.stdout')).write_bytes(compiled.stdout);(out/(name+'.build.stderr')).write_bytes(compiled.stderr)
        need(compiled.returncode==0,'device-free C++ build failed; inspect output')
        argv=[str(exe)]
        if name=='model_core':argv += [os.path.relpath(bundle/'model'/n,out) for n in ('weights.bin','pl_w_data.bin','pl_w_meta.bin')]
        run=subprocess.run(argv,cwd=out,env=env,capture_output=True,timeout=120)
        need(run.returncode==0 and not run.stderr,'device-free self-test failed')
        records.append({'name':name,'status':'PASS','source':pin(source.read_bytes()),'stdout':run.stdout.decode()})
    value={'status':'PASS','tests':records,'model_manifest':pin((bundle/'model/manifest.json').read_bytes()),'hardware_accessed':False,
        'scope':'Native x86/host fake-backend tests of restored source,all49 canonical weights and bounded owner protocol; not ARM compilation or board execution'}
    (out/'result.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value,indent=2))
if __name__=='__main__':main()
