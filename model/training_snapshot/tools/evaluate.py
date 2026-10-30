"""Portable FP32/W8A8 evaluation; rank all 1000 classes on labelled images."""
from pathlib import Path
import argparse,sys,time
sys.dont_write_bytecode=True
import numpy as np
from release_common import ROOT,rows,inside,sha,session,preprocess,metric,save

def evaluate(model,folder,limit=None):
    rs=rows(folder);rs=rs if limit is None else rs[:limit]
    if not rs:raise ValueError('Empty dataset')
    s=session(model);z=[];ys=[]
    for i,r in enumerate(rs):
        path=inside(folder,r['image'])
        if 'image_sha256' in r and sha(path)!=r['image_sha256']:raise ValueError('Image SHA mismatch')
        x=preprocess(path);z.append(s.run(None,{s.get_inputs()[0].name:x})[0][0]);ys.append(int(r['label']))
        if (i+1)%500==0:print('EVALUATED',i+1,flush=True)
    z=np.stack(z);ys=np.array(ys,np.int64);assert z.shape==(len(rs),1000)
    return metric(z,ys),z,ys

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',type=Path,default=ROOT/'models/final/model_w8a8.onnx')
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--limit',type=int)
    p.add_argument('--output',type=Path,default=ROOT/'work/evaluation/run01');a=p.parse_args()
    if a.output.exists():raise FileExistsError('Use a fresh output folder')
    if a.output.resolve().is_relative_to(ROOT) and not a.output.resolve().is_relative_to(ROOT/'work'):raise ValueError('Output must be under work/ or outside release')
    m,z,y=evaluate(a.model,a.dataset,a.limit);a.output.mkdir(parents=True)
    np.savez_compressed(a.output/'predictions.npz',logits=z,labels=y)
    save(a.output/'metrics.json',{'metrics':m,'model_sha256':sha(a.model),'labels_sha256':sha(a.dataset/'labels.csv'),
        'limited_smoke_run':a.limit is not None,'hardware_accessed':False});print(m)
