"""H576 architecture and graph-preserving export from the frozen training source."""
from pathlib import Path
import json,sys
sys.dont_write_bytecode=True
import numpy as np
import onnx
from onnx import numpy_helper
import torch
from torch import nn
from torch.nn import functional as F
from release_common import sha
class Attention(nn.Module):
    def __init__(self):
        super().__init__()
        self.qkv = nn.Linear(192,576)
        self.proj = nn.Linear(192,192)
    def forward(self,x):
        b,n,c=x.shape
        q,k,v=self.qkv(x).reshape(b,n,3,3,64).permute(2,0,3,1,4).unbind(0)
        attn=((q*0.125) @ k.transpose(-2,-1)).softmax(dim=-1)
        return self.proj((attn@v).transpose(1,2).reshape(b,n,c))

class MLP(nn.Module):
    def __init__(self):
        super().__init__(); self.fc1=nn.Linear(192,576); self.fc2=nn.Linear(576,192)
    def forward(self,x):
        return self.fc2(F.gelu(self.fc1(x), approximate='none'))

class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm1=nn.LayerNorm(192,eps=1e-6); self.attn=Attention()
        self.norm2=nn.LayerNorm(192,eps=1e-6); self.mlp=MLP()
    def forward(self,x):
        x=x+self.attn(self.norm1(x))
        return x+self.mlp(self.norm2(x))

class H576(nn.Module):
    def __init__(self):
        super().__init__()
        self.cls_token=nn.Parameter(torch.zeros(1,1,192))
        self.pos_embed=nn.Parameter(torch.zeros(1,197,192))
        self.patch_embed=nn.Module(); self.patch_embed.proj=nn.Conv2d(3,192,16,stride=16)
        self.blocks=nn.Sequential(*[Block() for _ in range(12)])
        self.norm=nn.LayerNorm(192,eps=1e-6); self.head=nn.Linear(192,1000)
    def forward(self,x):
        x=self.patch_embed.proj(x).flatten(2).transpose(1,2)
        x=torch.cat((self.cls_token.expand(x.shape[0],-1,-1),x),dim=1)+self.pos_embed
        return self.head(self.norm(self.blocks(x))[:,0])

def load_onnx_weights(path):
    model=H576()
    arrays={t.name:torch.from_numpy(numpy_helper.to_array(t).copy()) for t in onnx.load(path).graph.initializer}
    assert set(arrays)==set(model.state_dict()), (set(arrays)^set(model.state_dict()))
    model.load_state_dict(arrays,strict=True)
    return model

def enforce_masks(model,masks):
    with torch.no_grad():
        for name,p in model.named_parameters():
            if name in masks:
                p.mul_(masks[name])

def check_masks(model,masks):
    params=dict(model.named_parameters())
    for name,mask in masks.items():
        assert torch.count_nonzero(params[name].detach()[~mask]).item()==0,name
    return True

def export_original_graphs(model,candidate,out):
    """Keep every node/IO/constant, update trained tensors and all 49 W8A8 scales."""
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    state={n:v.detach().cpu().numpy().astype(np.float32) for n,v in model.state_dict().items()}
    fp=onnx.load(Path(candidate)/'model_fp32.onnx')
    for t in fp.graph.initializer:
        t.CopyFrom(numpy_helper.from_array(np.ascontiguousarray(state[t.name]),t.name))
    onnx.checker.check_model(fp); onnx.save(fp,out/'model_fp32.onnx')
    q=onnx.load(Path(candidate)/'model_w8a8.onnx')
    replacements={k:v for k,v in state.items() if k in {t.name for t in q.graph.initializer}}
    layers=json.loads((Path(candidate)/'layers.json').read_text(encoding='utf-8'))
    for layer in layers:
        i=layer['layer_index']; name=layer['layer_id']
        w=state[name+'.weight'].T.copy()
        a=np.max(np.abs(w),axis=0,keepdims=True)
        s=np.where(a==0,np.float32(1),np.maximum(a/np.float32(127),np.float32(np.finfo(np.float32).tiny))).astype(np.float32)
        wq=np.clip(np.rint(w/s),-127,127).astype(np.int8)
        replacements.update({f'BQL_{i}_Wf':wq.astype(np.float32),f'BQL_{i}_sf':s.ravel(),f'BQL_{i}_bf':state[name+'.bias']})
    for t in q.graph.initializer:
        if t.name in replacements:
            a=replacements.pop(t.name)
            assert a.shape==tuple(t.dims),(t.name,a.shape,tuple(t.dims))
            t.CopyFrom(numpy_helper.from_array(np.ascontiguousarray(a),t.name))
    assert not replacements
    onnx.checker.check_model(q); onnx.save(q,out/'model_w8a8.onnx')
    return {n:sha(out/n) for n in ['model_fp32.onnx','model_w8a8.onnx']}

def load_masks(candidate):
    masks={}
    for layer in json.loads((Path(candidate)/'layers.json').read_text(encoding='utf-8')):
        if layer['weights']:
            with np.load(Path(candidate)/layer['weights']) as z:mask=z['mask'].T.copy()
            assert mask.dtype==np.bool_ and np.all(mask.reshape(mask.shape[0],-1,4).sum(-1)==2)
            masks[layer['layer_id']+'.weight']=torch.from_numpy(mask)
    assert len(masks)==24
    return masks
