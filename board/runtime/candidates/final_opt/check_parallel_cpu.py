"""CPU-only equivalence and alternating baseline/candidate checks on the existing board stack."""
import io
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0,'/opt/opt50-demo-v1/private')
import dataset
from cpu_optimizations import preprocess as baseline
from parallel_cpu import preprocess as candidate


def main():
    os.sched_setaffinity(0,{0})
    np,Image=dataset.dependencies()
    root=Path('/var/lib/opt50-final-20261101');images=Path('/home/ubuntu/opt50-diagnostics-20261101/images')
    cases=[(p.name,p.read_bytes()) for p in sorted(images.glob('*.jpg'))]
    if not cases:raise ValueError('original JPEGs required')
    rng=np.random.default_rng(20261101)
    for mode in ('RGB','L','CMYK'):
        for width,height in ((249,301),(301,249),(225,225),(901,255)):
            channels={'RGB':3,'L':1,'CMYK':4}[mode]
            array=rng.integers(0,256,size=(height,width,channels) if channels>1 else (height,width),dtype=np.uint8)
            for progressive in (False,True):
                with Image.fromarray(array,mode=mode) as image:
                    stream=io.BytesIO();exif=Image.Exif();exif[274]=6
                    image.save(stream,format='JPEG',quality=92,progressive=progressive,exif=exif)
                    cases.append((f'{mode}_{width}_{height}_{progressive}',stream.getvalue()))
    for name,raw in cases:
        if baseline(raw,dataset)!=candidate(raw,dataset):raise ValueError('exact preprocessing mismatch: '+name)
    for raw in (b'',b'not-a-jpeg',cases[0][1][:80]):
        outcomes=[]
        for fn in (baseline,candidate):
            try:fn(raw,dataset);outcomes.append('ACCEPT')
            except (ValueError,OSError):outcomes.append('REJECT')
        if outcomes!=['REJECT','REJECT']:raise ValueError('rejection mismatch')
    timings=[]
    for name,raw in cases:
        if not name.endswith('.jpg'):continue
        samples={'baseline':[],'candidate':[]}
        for repeat in range(5):
            order=(('baseline',baseline),('candidate',candidate))
            if repeat%2:order=tuple(reversed(order))
            for key,fn in order:
                started=time.perf_counter_ns();fn(raw,dataset);samples[key].append((time.perf_counter_ns()-started)/1e6)
        timings.append({'file':name,'samples_ms':samples,'median_ms':{k:statistics.median(v) for k,v in samples.items()}})
    result={'status':'PASS','hardware_executed':False,'scope':'CPU-only exact equivalence/microbenchmark, not full requests',
            'equivalent_cases':len(cases),'invalid_cases':3,'main_affinity':[0],'RGB_SHA_thread_affinity':[1],'timings':timings}
    (root/'parallel-cpu-checks.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
