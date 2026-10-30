"""Frozen ONNX CPU reference and tensor operations for exhaustive 16+4 search."""
from pathlib import Path
import copy,csv,hashlib,io,itertools,json,math,time
import numpy as np
import onnx
from onnx import numpy_helper
import onnxruntime as ort
import sys

ROOT=Path(__file__).absolute().parents[1]
PARENT=ROOT/'candidates/B_mlp16_finetuned_20261024_run01'
EXP=ROOT/'experiments/B_mlp16ft_add4_exhaustive70_20261024_run01'
DATA=ROOT/'data/finetune16_imagenet1k_20261024'
FRESH=ROOT/'data/exhaustive20_acceptance1800_20261024'
OUT=ROOT/'reports/B_mlp16ft_add4_exhaustive70_20261024_run01'
PKG=ROOT/'candidates/B_mlp20_exhaustive70_20261024_run01'
DENSE=Path('D:/FPGA/AI_Operator_Optimization/h576_sparse24/inputs/baseline/h576_w8.onnx')
REMAINING=(2,3,10,11,14,18,22,23)
BASE16=(6,7,15,19,26,27,30,31,34,35,38,39,42,43,46,47)
EXPECTED_FP='25e8151fb5051874acbd530241c37e9f513456066e347491f7b488a3a20efe4d'
EXPECTED_Q='1c971f85880424fea993ceb023dc247f94b426e1be1a29d43a50bc651e8aff4c'
sys.path.insert(0,str(ROOT/'vendor'))
from eval_models import preprocess_image,PREPROCESS_SPEC

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
    temp=path.with_name(path.name+'.tmp');temp.write_text(text,encoding='utf-8');temp.replace(path)
def image_identity(raw):
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as im:
        rgb=im.convert('RGB');return digest(str(rgb.size).encode()+b'\0'+rgb.tobytes())
def arrays(model):return {t.name:numpy_helper.to_array(t).copy() for t in model.graph.initializer}
def topology(model):
    c=copy.deepcopy(model);del c.graph.initializer[:];return digest(c.SerializeToString())
def replace(model,changes):
    c=copy.deepcopy(model);found=set()
    for t in c.graph.initializer:
        if t.name in changes:
            a=changes[t.name];old=numpy_helper.to_array(t)
            assert a.dtype==old.dtype and a.shape==old.shape and np.isfinite(a).all()
            t.CopyFrom(numpy_helper.from_array(np.ascontiguousarray(a),t.name));found.add(t.name)
    assert found==set(changes)
    onnx.checker.check_model(c)
    return c
def layer_name(i):return f"blocks.{i//4}.mlp.{'fc1' if i%4==2 else 'fc2'}"
def combo_name(ids):return '_'.join(f'L{i:02d}' for i in ids)
def quantize(w):
    maxima=np.max(np.abs(w),axis=0,keepdims=True)
    scale=np.where(maxima==0,np.float32(1),np.maximum(maxima/np.float32(127),np.float32(np.finfo(np.float32).tiny))).astype(np.float32)
    return np.clip(np.rint(w/scale),-127,127).astype(np.int8),scale
def make_pruning(w,bias):
    k,n=w.shape;groups=w.reshape(k//4,4,n)
    indices=np.argsort(-np.abs(groups),axis=1,kind='stable')[:,:2,:]
    mask=np.zeros_like(groups,dtype=np.bool_);np.put_along_axis(mask,indices,True,axis=1);mask=mask.reshape(k,n)
    positions=np.sort(indices.transpose(0,2,1),axis=-1).astype(np.uint8)
    pruned=np.where(mask,w,np.float32(0));q,s=quantize(pruned)
    values=np.take_along_axis(q.reshape(k//4,4,n).transpose(0,2,1),positions,axis=2)
    return {'W_fp32':pruned,'Wq':q,'sw':s,'bias':bias.copy(),'mask':mask,'positions':positions,'values':values}
def session(path):
    o=ort.SessionOptions();o.intra_op_num_threads=1;o.inter_op_num_threads=1;o.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    o.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    o.add_session_config_entry('session.intra_op.allow_spinning','0');o.add_session_config_entry('session.inter_op.allow_spinning','0')
    return ort.InferenceSession(str(path),o,providers=['CPUExecutionProvider'])
def read_rows(folder):
    with (Path(folder)/'labels.csv').open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def inputs(folder):
    rows=read_rows(folder);x=np.empty((len(rows),3,224,224),np.float32);h=hashlib.sha256()
    for j,r in enumerate(rows):
        raw=(Path(folder)/r['image']).read_bytes()
        assert not r.get('image_sha256') or digest(raw)==r['image_sha256']
        x[j]=preprocess_image(io.BytesIO(raw))[0];h.update(x[j].tobytes())
    x.setflags(write=False)
    return x,np.array([int(r['label']) for r in rows],np.int64),rows,h.hexdigest()
def infer(path,x,tag=''):
    s=session(path);name=s.get_inputs()[0].name;values=np.empty((len(x),1000),np.float32)
    for j in range(len(x)):
        z=s.run(None,{name:x[j:j+1]})[0]
        assert z.shape==(1,1000) and z.dtype==np.float32 and np.isfinite(z).all()
        values[j]=z[0]
    return values
def metrics(z,y):
    ranks=np.argsort(-z,axis=1,kind='stable')[:,:5]
    top1=int(np.sum(ranks[:,0]==y));top5=int(np.any(ranks==y[:,None],axis=1).sum())
    a=z.astype(np.float64);maximum=a.max(1)
    ce=float(np.mean(np.log(np.exp(a-maximum[:,None]).sum(1))+maximum-a[np.arange(len(y)),y]))
    return {'images':len(y),'top1_correct':top1,'top5_correct':top5,'top1_pct':100*top1/len(y),'top5_pct':100*top5/len(y),'cross_entropy':ce}
def key(result):
    m=result['metrics'];return (-m['top1_correct'],-m['top5_correct'],m['cross_entropy'],tuple(result['added_layers']))
