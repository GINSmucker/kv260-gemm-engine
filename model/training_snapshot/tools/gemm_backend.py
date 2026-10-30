"""GEMM-only backend contract. Hardware implementations must never silently fall back."""
from dataclasses import dataclass
from pathlib import Path
import sys
sys.dont_write_bytecode=True
import numpy as np
from release_common import ROOT,read
import axi_codec as codec

@dataclass
class Handle:
    layer:dict
    mode:int
    w_data:bytes
    w_meta:bytes
    released:bool=False

class CpuReferenceBackend:
    name='cpu_reference'
    hardware_accessed=False
    def __init__(self,root=ROOT):
        self.root=Path(root);self.layers={l['index']:l for l in read(self.root/'parameters/layers.json')};self.calls=[]
    def prepare(self,layer_index,mode_override=None):
        l=self.layers[layer_index];mode=l['mode'] if mode_override is None else int(mode_override)
        if mode not in [0,l['mode']]:raise ValueError('Sparse override cannot be applied to a Dense layer')
        data=l['weights']['sparse_data' if mode else 'dense_data'];meta=l['weights']['sparse_meta' if mode else 'dense_meta']
        return Handle(l,mode,(self.root/data).read_bytes(),(self.root/meta).read_bytes())
    def run(self,handle,xq):
        if handle.released:raise RuntimeError('Handle was released')
        if not isinstance(xq,np.ndarray) or xq.dtype!=np.int8 or xq.ndim!=2 or not xq.flags.c_contiguous:
            raise TypeError('Backend input must be C-contiguous INT8 Xq[M,K]')
        l=handle.layer
        if xq.shape[1]!=l['K']:raise ValueError('Backend K mismatch')
        result=codec.emulate_axi(codec.pack_int8(xq),handle.w_data,handle.w_meta if handle.mode else object(),
            len(xq),l['K'],l['N'],handle.mode)
        self.calls.append({'backend':self.name,'layer_index':l['index'],'mode':handle.mode,
            'M':len(xq),'K':l['K'],'N':l['N'],'hardware_accessed':False})
        return result
    def release(self,handle):handle.released=True

class HardwareBackendTemplate:
    """C implements capability checks, BO64 alignment, sync, ABI, wait and error handling."""
    name='UNIMPLEMENTED_HARDWARE_BACKEND'
    hardware_accessed=True
    def prepare(self,layer_index,mode_override=None):raise NotImplementedError('Bind the actual A/C platform ABI and capability list')
    def run(self,handle,xq):raise NotImplementedError('Return actual device INT32 output; never call CPU as fallback')
    def release(self,handle):raise NotImplementedError('Release actual device resources')

class ModelLayerAdapter:
    def __init__(self,backend,root=ROOT,layer_index=2,mode_override=None):
        from release_common import session
        self.root=Path(root);self.backend=backend;self.cut=read(self.root/f'runtime/cuts/layer_{layer_index:02d}/cut.json')
        self.prefix=session(self.root/self.cut['prefix']);self.suffix=session(self.root/self.cut['suffix'])
        self.layer=next(v for v in read(self.root/'parameters/layers.json') if v['index']==layer_index)
        self.handle=backend.prepare(layer_index,mode_override)
    def predict(self,x):
        values=self.prefix.run(None,{self.prefix.get_inputs()[0].name:x})
        prefix=dict(zip([v.name for v in self.prefix.get_outputs()],values))
        q=prefix[self.cut['quantized_input']]
        if not np.isfinite(q).all() or not np.all(q==np.rint(q)) or not np.all((q>=-127)&(q<=127)):
            raise ValueError('Prefix did not produce valid quantized activation values')
        xq=np.ascontiguousarray(q.reshape(-1,self.layer['K']),dtype=np.int8)
        yi=self.backend.run(self.handle,xq)
        if not isinstance(yi,np.ndarray) or yi.dtype!=np.int32 or yi.shape!=(len(xq),self.layer['N']):
            raise TypeError('Backend must return exact INT32 Y[M,N]')
        expected_shape=q.shape[:-1]+(self.layer['N'],)
        prefix[self.cut['integer_matmul_float_output']]=yi.astype(np.float32).reshape(expected_shape)
        feeds={name:prefix[name] for name in self.cut['suffix_inputs']}
        return self.suffix.run(None,feeds)[0]
    def close(self):self.backend.release(self.handle)
