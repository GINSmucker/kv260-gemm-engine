"""Frozen ARM CPU selection evaluation; no PL or quality-set access."""
import argparse
import hashlib
import platform
import time
from pathlib import Path
import numpy as np
import onnxruntime as ort
from .common import read_json,write_json,sha,utc
from .preprocess import preprocess
from .sparse24_quality import session


def run(package,data,output):
    if platform.machine().lower() not in ('aarch64','arm64'):
        raise ValueError('ARM CPU required')
    manifest=read_json(package/'freeze.json')
    for name,value in manifest['files'].items():
        assert sha(package/name)==value,'Changed package file: '+name
    for name,value in manifest['sources'].items():
        assert sha(Path(__file__).with_name(name))==value,'Changed source: '+name
    rows=read_json(package/'selection.json')
    labels=np.array([r['label'] for r in rows])
    engines={name:session(package/path) for name,path in manifest['graphs'].items()}
    output.mkdir(parents=True,exist_ok=False)
    arrays={name:np.empty((len(rows),1000),np.float32) for name in engines}
    times={name:np.empty(len(rows),np.float64) for name in engines}
    for i,row in enumerate(rows):
        path=data/row['path']
        assert sha(path)==row['image_sha256']
        x=preprocess(path)
        assert hashlib.sha256(x.tobytes()).hexdigest()==manifest['input_sha256'][i]
        for name,engine in engines.items():
            start=time.perf_counter(); y=engine.run(None,{'image':x})[0]
            times[name][i]=time.perf_counter()-start
            assert y.shape==(1,1000) and np.isfinite(y).all()
            arrays[name][i]=y[0]
        if (i+1)%25==0:
            write_json(output/'progress.json',{'status':'RUNNING','completed':i+1,'total':len(rows),'utc':utc()})
            print('ARM_SELECTION',i+1,len(rows),flush=True)
    base=arrays['original_fp32']; bc=int(np.sum(np.argmax(base,axis=1)==labels))
    reports={}
    for name,a in arrays.items():
        rank=np.argsort(-a,axis=1,kind='stable')[:,:5]
        np.save(output/(name+'_logits.npy'),a)
        np.save(output/(name+'_times.npy'),times[name])
        reports[name]={'top1':int(np.sum(rank[:,0]==labels)),'top5':int(np.sum(np.any(rank==labels[:,None],axis=1))),'logits_mse':float(np.mean((a.astype(float)-base)**2)),'logits_sha256':sha(output/(name+'_logits.npy')),'seconds':float(times[name].sum())}
    eligible=[]
    for name in manifest['candidates']:
        if all(bc-reports[name+'_'+mode]['top1']<=5 for mode in ('fp32','w8a8')): eligible.append(name)
    write_json(output/'result.json',{'status':'COMPLETE_SELECTION_ONLY','utc':utc(),'samples':len(rows),'reports':reports,'eligible':eligible,'freeze_sha256':sha(package/'freeze.json'),'architecture':platform.machine(),'ORT':ort.__version__,'threads':4,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'formal_quality':'NOT_RUN','PL':'NOT_RUN'})
    write_json(output/'progress.json',{'status':'COMPLETE','completed':len(rows),'utc':utc()})
    print('ARM_SELECTION_COMPLETE',eligible,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    import fcntl
    with Path('/home/ubuntu/fpga_project/deploy/.f8_board.lock').open('r') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run(a.package,a.data,a.output)
