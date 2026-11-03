import io
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0,'/opt/opt50-demo-v1/private')
import dataset
import model_runtime as runtime
from PIL import Image
import numpy as np
from exact_candidates import ExactBundleAudit, preprocess

os.sched_setaffinity(0,{0})
root=Path(__file__).resolve().parent
cases=[]
for file in sorted((root/'images').glob('*.jpg')):cases.append((file.name,file.read_bytes()))
rng=np.random.default_rng(20261101)
for mode in ['RGB','L','CMYK']:
    for width,height in [(249,301),(301,249),(225,225),(901,255)]:
        count={'RGB':3,'L':1,'CMYK':4}[mode]
        array=rng.integers(0,256,size=(height,width,count) if count>1 else (height,width),dtype=np.uint8)
        for progressive in [False,True]:
            image=Image.fromarray(array,mode=mode)
            stream=io.BytesIO();exif=Image.Exif();exif[274]=6
            image.save(stream,format='JPEG',quality=92,progressive=progressive,exif=exif)
            cases.append((f'{mode}_{width}x{height}_{progressive}',stream.getvalue()))
for name,raw in cases:
    assert preprocess(raw,dataset)==dataset.preprocess(raw),'preprocessing differs: '+name
invalid=[]
for raw in [b'',b'not-a-jpeg',cases[0][1][:80]]:
    outcomes=[]
    for fn in [dataset.preprocess,lambda data:preprocess(data,dataset)]:
        try:fn(raw);outcomes.append('ACCEPT')
        except (ValueError,OSError):outcomes.append('REJECT')
    assert outcomes==['REJECT','REJECT'];invalid.append(outcomes)

audit_checks=[]
def fixture(folder):
    (folder/'graphs').mkdir()
    graph=b'checked graph';ref=b'checked reference'
    files={'graphs/model_w8a8_pl.onnx':graph,'reference.onnx':ref}
    for name,raw in files.items():(folder/name).write_bytes(raw)
    manifest={'files':{name:runtime.identity(raw) for name,raw in files.items()}}
    metadata={name:runtime.file_metadata((folder/name).lstat()) for name in files}
    return runtime.VerifiedBundle(folder,manifest,graph,ref,metadata)

names=['valid','extra','missing','mode','mtime','content','graph_memory','reference_memory','manifest','file_symlink','directory_symlink','hardlink','fifo']
for name in names:
    with tempfile.TemporaryDirectory(prefix='audit-',dir=root) as temporary:
        folder=Path(temporary);b=fixture(folder);candidate=ExactBundleAudit(b,runtime)
        candidate.unchanged(folder)
        path=folder/'reference.onnx'
        if name=='extra':(folder/'extra').write_bytes(b'x')
        elif name=='missing':path.unlink()
        elif name=='mode':path.chmod(0o400)
        elif name=='mtime':os.utime(path,ns=(1,1))
        elif name=='content':path.write_bytes(b'changed')
        elif name=='graph_memory':b['graph_bytes']=b'changed'
        elif name=='reference_memory':b.reference_bytes=b'changed'
        elif name=='manifest':b['manifest']['files']['reference.onnx']['sha256']='0'*64
        elif name=='file_symlink':path.rename(folder/'other');path.symlink_to(folder/'other')
        elif name=='directory_symlink':(folder/'graphs').rename(folder/'other');(folder/'graphs').symlink_to(folder/'other',target_is_directory=True)
        elif name=='hardlink':os.link(path,folder/'linked')
        elif name=='fifo':os.mkfifo(folder/'fifo')
        outcomes=[]
        for fn in [b.unchanged,candidate.unchanged]:
            try:fn(folder);outcomes.append('ACCEPT')
            except (ValueError,OSError):outcomes.append('REJECT')
        assert outcomes==(['ACCEPT']*2 if name=='valid' else ['REJECT']*2),(name,outcomes)
        audit_checks.append({'case':name,'original':outcomes[0],'candidate':outcomes[1]})

b=runtime.verify_bundle(Path('/var/lib/opt50-demo-v1/model'));audit=ExactBundleAudit(b,runtime)
audit.unchanged(Path(b['root']))
checks={'original':[],'candidate':[]}
for repeat in range(7):
    for label,fn in ([('original',b.unchanged),('candidate',audit.unchanged)] if repeat%2==0 else [('candidate',audit.unchanged),('original',b.unchanged)]):
        start=time.perf_counter_ns();fn(b['root']);checks[label].append((time.perf_counter_ns()-start)/1e6)
timings=[]
for name,raw in cases[:7]:
    samples={'original':[],'candidate':[]}
    for repeat in range(5):
        for label,fn in ([('original',dataset.preprocess),('candidate',lambda data:preprocess(data,dataset))] if repeat%2==0 else [('candidate',lambda data:preprocess(data,dataset)),('original',dataset.preprocess)]):
            start=time.perf_counter_ns();actual=fn(raw);samples[label].append((time.perf_counter_ns()-start)/1e6)
    timings.append({'file':name,'samples':samples,'median_ms':{k:statistics.median(v) for k,v in samples.items()}})
value={'status':'PASS','preprocessing_equivalent_cases':len(cases),'invalid_JPEG_cases':len(invalid),'audit_checks':audit_checks,
       'unchanged_samples_ms':checks,'unchanged_median_ms':{k:statistics.median(v) for k,v in checks.items()},'preprocessing':timings,
       'CPU_affinity':[0],'frozen_runtime_modified':False,'hardware_accessed':False}
(root/'candidates.json').write_text(json.dumps(value,indent=2))
print(json.dumps({k:v for k,v in value.items() if k not in ['preprocessing','unchanged_samples_ms','audit_checks']}))
print(json.dumps([{'file':r['file'],**r['median_ms']} for r in timings]))
