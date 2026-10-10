"""Explicit ctypes v2a mirror and framed recovery tools; no ABI autodetect."""
import ctypes as C
import hashlib,json,socket,struct,time
from pathlib import Path
import numpy as np
class Request(C.Structure):
    _fields_=[(n,C.c_uint32) for n in ('abi_version','struct_bytes','M','K','N','layer_index')]+[
        ('request_id',C.c_char*192),('model_run_id',C.c_char*96),('image_id',C.c_char*96),('layer_id',C.c_char*96),('expected_W_sha256',C.c_char*65)]
class Receipt(C.Structure):
    _fields_=[(n,C.c_uint32) for n in ('abi_version','struct_bytes','status','one_start','done','kernel_error','guards_ok','inputs_unchanged','tag0','tag1')]+[
        ('owner_pid',C.c_uint64),('starts_before',C.c_uint64),('starts_after',C.c_uint64),('native_us',C.c_double),
        ('X_sha256',C.c_char*65),('W_sha256',C.c_char*65),('Y_sha256',C.c_char*65),('native_result_file',C.c_char*1024),('error',C.c_char*512)]
assert C.sizeof(Request)==572 and C.sizeof(Receipt)==1808
def digest(b):return hashlib.sha256(b).hexdigest()
def compact(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def receipt_dict(r):return {n:getattr(r,n).decode('ascii') if isinstance(getattr(r,n),bytes) else getattr(r,n) for n,_ in r._fields_}
def load(path):
    lib=C.CDLL(str(Path(path).resolve()))
    lib.kv260_gemm_open_v2a.argtypes=[C.c_char_p,C.POINTER(C.c_void_p),C.c_void_p,C.c_size_t]
    lib.kv260_gemm_info_v2a.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];lib.kv260_gemm_close_v2a.argtypes=[C.c_void_p]
    lib.kv260_gemm_run_v2a.argtypes=[C.c_void_p,C.POINTER(Request),C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t,C.POINTER(Receipt)]
    for op in ('open','info','close','run'):getattr(lib,'kv260_gemm_'+op+'_v2a').restype=C.c_int
    return lib
class Bridge:
    def __init__(self,path,config):
        self.library=load(path);self.handle=C.c_void_p();actual=C.create_string_buffer(8192)
        code=self.library.kv260_gemm_open_v2a(compact(config),C.byref(self.handle),actual,len(actual))
        if code:raise RuntimeError(f'v2a open {code}: '+actual.value.decode('ascii'))
        self.identity=json.loads(actual.value)
    def info(self):
        actual=C.create_string_buffer(16384);code=self.library.kv260_gemm_info_v2a(self.handle,actual,len(actual))
        if code:raise RuntimeError(f'v2a info {code}')
        return json.loads(actual.value)
    def close(self):return self.library.kv260_gemm_close_v2a(self.handle)
    def run(self,layer,x,w,run_id,image_id):
        r=Request(abi_version=2,struct_bytes=C.sizeof(Request),M=layer['M'],K=layer['K'],N=layer['N'],layer_index=layer['layer_index'])
        r.model_run_id=run_id.encode('ascii');r.image_id=image_id.encode('ascii');r.layer_id=layer['layer_id'].encode('ascii')
        r.request_id=f'{run_id}_{image_id}_l{layer["layer_index"]}'.encode('ascii');r.expected_W_sha256=digest(w.tobytes()).encode('ascii')
        x=np.ascontiguousarray(x,dtype=np.int8);w=np.ascontiguousarray(w,dtype=np.int8)
        y=np.full((layer['M'],layer['N']),-559038737,dtype='<i4');receipt=Receipt(abi_version=2,struct_bytes=C.sizeof(Receipt))
        code=self.library.kv260_gemm_run_v2a(self.handle,C.byref(r),x.ctypes.data,x.nbytes,w.ctypes.data,w.nbytes,y.ctypes.data,y.nbytes,C.byref(receipt))
        return code,y,receipt_dict(receipt)
def envelope(config,type):return dict(schema='kv260_gemm_stream_v2a',wire_version=2,type=type,identity=config['identity'],body_bytes=0)
def exchange(config,header,body=b'',timeout=130):
    data=compact(header);assert 1<=len(data)<=16384 and len(body)==header['body_bytes']<=605184
    deadline=time.monotonic()+timeout
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(max(0.001,deadline-time.monotonic()));s.connect(config['socket'])
        pid,uid,gid=struct.unpack('3i',s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        assert pid==config['identity']['owner_pid'] and uid==1000
        s.sendall(struct.pack('<I',len(data))+data+body)
        def receive(n):
            out=b''
            while len(out)<n:
                s.settimeout(max(0.001,deadline-time.monotonic()));b=s.recv(n-len(out))
                if not b:raise EOFError('v2a original frame peer closed')
                out+=b
            return out
        length=struct.unpack('<I',receive(4))[0];assert 1<=length<=16384
        raw=receive(length);h=json.loads(raw);assert h['schema']=='kv260_gemm_stream_v2a' and h['wire_version']==2 and h['identity']==config['identity']
        size=h['body_bytes'];assert type(size) is int and 0<=size<=605184;data=receive(size)
        if h['type']=='RESULT':assert digest(data)==h['Y_sha256'] and len(data)==h['request']['Y_bytes']
        return h,data,raw
