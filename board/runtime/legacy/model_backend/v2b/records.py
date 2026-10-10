"""Bounded read-only V2B record collector. It never submits or repairs a log."""
import hashlib,json,os,stat,struct
from pathlib import Path

LIMIT=128*1024*1024
def sha(data):return hashlib.sha256(data).hexdigest()

def load(task):
    path=Path(task)/'records.v2b'
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        assert stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o600 and before.st_uid==1000 and before.st_nlink==1
        assert 0<before.st_size<=LIMIT
        with os.fdopen(os.dup(fd),'rb') as f:data=f.read(LIMIT+1)
        after=os.fstat(fd);named=path.lstat()
        for s in (after,named):
            assert (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_nlink)==(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_mode,before.st_nlink)
        assert len(data)==before.st_size
    finally:os.close(fd)
    rows=[];offset=0;previous=bytes(32);entries={};pending=None
    def pairs(items):
        out={}
        for k,v in items:
            assert k not in out,'duplicate record key';out[k]=v
        return out
    while offset<len(data):
        assert len(rows)<65537 and len(data)-offset>=96
        magic,version,header,n,seq,prior,payload_sha=struct.unpack_from('<8sIIQQ32s32s',data,offset)
        assert magic==b'KV2BLOG\0' and version==1 and header==96 and 0<n<=16384
        assert seq==len(rows)+1 and prior==previous and offset+96+n<=len(data)
        payload=data[offset+96:offset+96+n]
        assert hashlib.sha256(payload).digest()==payload_sha
        r=json.loads(payload,object_pairs_hook=pairs,parse_constant=lambda s:(_ for _ in ()).throw(ValueError(s)))
        assert r['schema']=='kv260_record_v2b_v1'
        row=dict(sequence=seq,record=r,payload_sha256=sha(payload),offset=offset)
        if seq==1:
            assert set(r)=={'schema','type','task'} and r['type']=='TASK';identity=r['task']['identity']
        elif r['type']=='SUBMITTED':
            assert set(r)=={'schema','type','execution_id','request','caller','identity'}
            name=r['execution_id'];assert pending is None and name not in entries and r['request']['execution_id']==name and r['identity']==identity
            assert set(r['caller'])=={'pid','birth_ticks'} and all(type(v) is int and v>0 for v in r['caller'].values())
            pending=name;entries[name]={'submitted':row,'completed':None}
        else:
            assert set(r)=={'schema','type','execution_id','submitted_sequence','result'} and r['type']=='COMPLETED'
            name=r['execution_id'];assert name==pending and r['submitted_sequence']==entries[name]['submitted']['sequence']
            assert r['result']['request']==entries[name]['submitted']['record']['request'] and r['result']['identity']==identity
            entries[name]['completed']=row;pending=None
        previous=hashlib.sha256(data[offset:offset+96+n]).digest();offset+=96+n;rows.append(row)
    return dict(task=rows[0]['record']['task'],records=rows,entries=entries,pending=pending,file_sha256=sha(data))
