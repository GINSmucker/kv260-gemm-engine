"""ctypes mirror of the frozen C header, shared by software and actual PL tests."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import numpy as np

class Request(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('abi_version','struct_bytes','M','K','N','layer_index')] + [
        ('request_id', C.c_char*192), ('model_run_id', C.c_char*96),
        ('image_id', C.c_char*96), ('layer_id', C.c_char*96), ('expected_W_sha256', C.c_char*65)]

class Receipt(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('abi_version','struct_bytes','status','one_start','done','kernel_error',
                                       'guards_ok','inputs_unchanged','tag0','tag1')] + [
        ('owner_pid', C.c_uint64), ('starts_before', C.c_uint64), ('starts_after', C.c_uint64),
        ('native_us', C.c_double), ('X_sha256', C.c_char*65), ('W_sha256', C.c_char*65),
        ('Y_sha256', C.c_char*65), ('native_result_file', C.c_char*1024), ('error', C.c_char*512)]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def receipt_dict(r):
    return {n:(getattr(r,n).decode() if isinstance(getattr(r,n),bytes) else getattr(r,n)) for n,_ in r._fields_}

def load(path):
    library=C.CDLL(str(Path(path).resolve()))
    library.kv260_gemm_open_v1.argtypes=[C.c_char_p,C.POINTER(C.c_void_p),C.c_void_p,C.c_size_t]
    library.kv260_gemm_info_v1.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t]
    library.kv260_gemm_close_v1.argtypes=[C.c_void_p]
    library.kv260_gemm_run_v1.argtypes=[C.c_void_p,C.POINTER(Request),C.c_void_p,C.c_size_t,
                                    C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t,C.POINTER(Receipt)]
    for name in ('open','info','close','run'):
        getattr(library,'kv260_gemm_'+name+'_v1').restype=C.c_int
    return library

class Bridge:
    def __init__(self,path,config):
        self.library=load(path);self.handle=C.c_void_p();output=C.create_string_buffer(8192)
        code=self.library.kv260_gemm_open_v1(json.dumps(config).encode(),C.byref(self.handle),output,len(output))
        if code:raise RuntimeError(f'bridge open error {code}: {output.value.decode()}')
        self.identity=json.loads(output.value)
    def info(self):
        out=C.create_string_buffer(16384);code=self.library.kv260_gemm_info_v1(self.handle,out,len(out))
        if code:raise RuntimeError(f'bridge info error {code}')
        return json.loads(out.value)
    def close(self):
        return self.library.kv260_gemm_close_v1(self.handle)
    def run(self,meta,x,w,run_id,image_id):
        r=Request(abi_version=1,struct_bytes=C.sizeof(Request),M=meta['M'],K=meta['K'],N=meta['N'],layer_index=meta['layer_index'])
        r.model_run_id=run_id.encode();r.image_id=image_id.encode();r.layer_id=meta['layer_id'].encode()
        r.request_id=f"{run_id}_{image_id}_l{meta['layer_index']}".encode();r.expected_W_sha256=digest(w.tobytes()).encode()
        x=np.ascontiguousarray(x,dtype=np.int8);w=np.ascontiguousarray(w,dtype=np.int8)
        y=np.full((meta['M'],meta['N']),-559038737,dtype='<i4')
        receipt=Receipt(abi_version=1,struct_bytes=C.sizeof(Receipt))
        code=self.library.kv260_gemm_run_v1(self.handle,C.byref(r),x.ctypes.data,x.nbytes,w.ctypes.data,w.nbytes,
                                         y.ctypes.data,y.nbytes,C.byref(receipt))
        return code,y,receipt_dict(receipt)
