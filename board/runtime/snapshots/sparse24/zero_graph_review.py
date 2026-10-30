"""One incident, zero graphs: inspect locally, then authenticated normal STOP once.

No BEGIN, RUN, INFO, ABORT, signals, unit stop, firmware action or original
record mutation. A failed/uncertain attempt is permanently consumed.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import sys
import time
import types

sys.dont_write_bytecode = True
PLAN = Path('/var/lib/opt50-b24-model-v1/plan_c4b539de5ba7/plan.json')
BASE = Path('/var/lib/opt50-b24-zero-close-v1/plan_c4b539de5ba7')
PUBLIC = Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_resident_20261029_v4_fix1/software')
GUARDIAN = Path('/var/lib/opt50-loadonly-fd96539d-v3/plan_95074ccbbd4d/plan.json')
TOOLS_PIN = {'bytes':23152,'sha256':'03d0b13bb25bc04dbffd561e08267812db8bdd6697ad474ee0b2f40294773fb1'}
BOOT = '2b0eaebd-2dd9-45c5-b135-26315bfdbb7f'
OWNER, START_TICKS, GUARDIAN_PID = 52117, 5366475, 15656
UNIT = 'opt50-b24-model-c4b539de5ba7.service'
GROUP = '/system.slice/'+UNIT
PROC = Path('/proc')
COMPANION = Path('/var/lib/opt50-first-access-fd96539d-v1/companion.lock')
READY = b'KV5_READY 52117\n'
CLOSED = READY+b'KV5_STOP calls=0 graphs=0 max_graphs=50\n'
SCHEMA = 'opt50_b24_zero_graph_close_receipt_v1'


def need(value, reason):
    if not value: raise ValueError(reason)


def pin(raw): return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def encoded(value): return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def decode(raw):
    def pairs(items):
        out = {}
        for key,value in items:
            need(key not in out,'duplicate JSON key'); out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))


def ordinary(path):
    path=Path(path).absolute(); need('..' not in path.parts,'parent traversal refused')
    for part in (path,*path.parents):
        info=part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'linked path refused')
    return path


def metadata(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,
            info.st_birthtime_ns if os.name=='nt' else info.st_ctime_ns,info.st_mode,info.st_uid,info.st_nlink)


def read(path,private=True,limit=64*1024*1024):
    path=ordinary(path); before=path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=limit,'bounded single-link file required')
    if private: need(before.st_uid==0 and not before.st_mode&0o077,'root-private input required')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    with os.fdopen(fd,'rb') as stream:
        need(metadata(os.fstat(stream.fileno()))==metadata(before),'input replaced before read')
        raw=stream.read(limit+1)
        need(metadata(os.fstat(stream.fileno()))==metadata(before),'input changed during read')
    need(metadata(path.lstat())==metadata(before) and len(raw)==before.st_size,'input replaced after read')
    return raw


def private_dir(path):
    info=ordinary(path).lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o077,'root-private directory required')


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def new_dir(path):
    parent=Path(path).parent
    if Path(path)==BASE.parent and parent==Path('/var/lib'):
        info=ordinary(parent).lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'trusted /var/lib parent required')
    else: private_dir(parent)
    os.mkdir(path,0o700); sync_dir(Path(path).parent)


def publish(path,raw):
    path=Path(path); private_dir(path.parent)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|getattr(os,'O_BINARY',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        need(stream.write(raw)==len(raw),'short exclusive write'); stream.flush(); os.fsync(stream.fileno())
    sync_dir(path.parent); need(read(path)==raw,'published bytes differ')


def journal(folder,native=False):
    private_dir(folder); paths=sorted(Path(folder).iterdir()); rows=[]; last=-1
    for number,path in enumerate(paths,1):
        need(path.name=='event_%06d.json'%number,'journal gap or extra entry')
        value=decode(read(path)); need(type(value.get('sequence')) is int and value['sequence']==number and
            type(value.get('phase')) is str,'journal sequence/phase differs')
        need(value.get('schema')==('opt50_model_native_event_v1' if native else 'opt50_durable_event_v1'),'journal schema differs')
        if native:
            need(set(value)=={'schema','sequence','phase','details','monotonic_ns'} and
                 type(value['details']) is str and type(value['monotonic_ns']) is int and value['monotonic_ns']>last,
                 'Native chronology differs')
            last=value['monotonic_ns']
        rows.append(value)
    return rows


def validate_parent(rows,expected):
    need(len(rows)==2 and all(row.get('action')=='owner' for row in rows) and
         [row.get('phase') for row in rows]==['INTENT','SPAWNED'] and
         rows[0].get('argv')==rows[1].get('argv')==expected and rows[1].get('pid')==OWNER,
         'only the original owner INTENT/SPAWNED may exist; no worker or graph authority')


def unused_outputs(ex,samples,closed):
    private_dir(ex); private_dir(samples)
    need(not list(Path(samples).iterdir()),'any sample output forbids zero-graph closure')
    allowed={'native_events','owner.stdout','owner.stderr','held_before.json','runtime.sock.lock'}
    if not closed: allowed|={'runtime.sock','runtime.sock.ctl'}
    need({path.name for path in Path(ex).iterdir()}==allowed,'missing/unknown/worker/graph execution artifact')


def validate_native(rows,closed=False):
    need(len(rows)==(155 if closed else 141),'exact original Native prefix/normal close count required')
    def item(number,phase,detail=None):
        row=rows[number-1]
        need(row['phase']==phase and (detail is None or row['details']==detail),'Native phase/detail differs at '+str(number))
        return row['details']
    item(1,'NATIVE_ENTERED','resident max_graphs=50 total_START_budget=1800; no model/performance acceptance')
    addresses=[0xfd615000,0xfd380000,0xfd380014]*2+[0xa0010000+4*i for _ in range(2) for i in range(16)]+[0xa0000000]
    returned=[]
    for index,address in enumerate(addresses):
        number=2+index*2
        item(number,'RAW_MMIO_READ_INTENT',str(address)+(' AP_CTRL_COR_DONE_READY' if address==0xa0000000 else ''))
        value=item(number+1,'RAW_MMIO_READ_RETURNED')
        match=re.fullmatch(str(address)+r' value=([0-9]+)',value)
        need(match is not None and int(match[1])<=0xffffffff,'raw read address/value differs'); returned.append(int(match[1]))
    for i in (0,3): need(returned[i]&0x300==0x200 and returned[i+1]&0xb==returned[i+2]&0xb==0,'PS preflight widths differ')
    a,b=returned[6:22],returned[22:38]
    need(a==b and a[:4]==[0x46383031,0x10000,4254487453,1287452466] and a[4:8]==[0]*4 and a[15]==1 and
         returned[-1]&4 and not returned[-1]&0x81,'raw observer/idle proof differs')
    item(80,'RAW_PREFLIGHT_PASS','PS fields, observer identity/stability and initial COR control only; no START; no reset/clock write')
    item(81,'XRT_DEVICE_OPEN_INTENT','device=0 after raw observer preflight')
    item(82,'XRT_METADATA_REGISTRATION_INTENT','MUTATION: metadata only, not firmware programming')
    item(83,'XRT_OBSERVER_OPEN_INTENT','axi_observer:{status_1}')
    def pair(number,kind,offset,value):
        item(number,kind+'_INTENT',str(offset)+(' COR_DONE_READY' if kind=='GEMM_READ' and offset==0 else
                                             ' value='+str(value) if kind=='GEMM_WRITE' else ''))
        item(number+1,kind+'_RETURNED',str(offset) if kind=='GEMM_WRITE' else str(offset)+' value='+str(value))
    for i,value in enumerate((0x46383031,0x10000,4254487453,1287452466)): pair(84+2*i,'OBSERVER_READ',4*i,value)
    item(92,'XRT_GEMM_OPEN_INTENT','model_gemm_top:{gemm_1}'); pair(93,'GEMM_READ',0,4)
    for i,(offset,value) in enumerate(((20,0),(24,0),(28,0),(60,1),(16,0))): pair(95+2*i,'OBSERVER_READ',offset,value)
    pair(105,'GEMM_WRITE',4,0); pair(107,'GEMM_WRITE',8,0); pair(109,'GEMM_READ',4,0); pair(111,'GEMM_READ',8,0)
    caps=(113472,2654208,331840,453888); allocations=[((n+128+4095)//4096)*4096 for n in caps]; regions=[]
    for slot,size in enumerate(allocations):
        number=113+4*slot; item(number,'BO_ALLOC_INTENT',f'slot={slot} cacheable group0 bytes={size}')
        detail=item(number+1,'BO_ALLOC_RETURNED')
        match=re.fullmatch(r'slot='+str(slot)+r' address_hex=0x([0-9a-f]{16}) address_dec=([0-9]+) bytes='+str(size)+
                           r' actual_BO_bytes='+str(size)+r' alignment_remainder=0',detail)
        need(match is not None and int(match[1],16)==int(match[2]),'BO address proof differs')
        address=int(match[2]); need(address%64==0 and (64<=address and address+size<=0x80000000 or
                                                   0x800000000<=address and address+size<=0x880000000),'BO range differs')
        need(all(address+size<=start or address>=end for start,end in regions),'overlapping BOs'); regions.append((address,address+size))
        item(number+2,'BO_MAP_INTENT',str(slot)); item(number+3,'BO_MAP_RETURNED',detail)
        item(129+2*slot,'BO_TO_DEVICE_INTENT',f'slot={slot} whole allocation bytes={size}')
        item(130+2*slot,'BO_TO_DEVICE_RETURNED',str(slot))
    item(137,'NATIVE_READY','single CU/four global-guarded BOs; weights synchronized once; shared payload offset=64; H576-aligned client-only per-layer X/Y cache synchronization')
    for slot,number in ((0,138),(3,140)):
        item(number,'BO_EXPORT_DUP_INTENT',f'slot={slot}; XRT export handle remains borrowed; owner receives an owned duplicate')
        need(re.fullmatch(r'slot='+str(slot)+r' borrowed_XRT_fd=[0-9]+ owned_owner_fd=[0-9]+',item(number+1,'BO_EXPORT_DUP_RETURNED')),
             'export duplicate evidence differs')
    if closed:
        pair(142,'GEMM_READ',0,4)
        for i,(offset,value) in enumerate(((20,0),(24,0),(28,0),(60,1),(16,0))): pair(144+2*i,'OBSERVER_READ',offset,value)
        item(154,'RESOURCE_CLOSE_INTENT','allocated_BO_count=4; no unload/reset/recovery')
        item(155,'RESOURCES_CLOSED','released_BO_count=4; exported client handles must already be closed; resident firmware unchanged')


@contextmanager
def release_modules(root,listing):
    names=('verify_tools','legacy_model'); previous={name:sys.modules.get(name) for name in names}
    try:
        for name in names:
            module=types.ModuleType(name); module.__file__=str(root/(name+'.py')); sys.modules[name]=module
            exec(compile(read(root/(name+'.py')),module.__file__,'exec'),module.__dict__)
        module=types.ModuleType('_zero_close_frozen_coordinator'); module.__file__=str(root/'run_model.py')
        exec(compile(read(root/'run_model.py'),module.__file__,'exec'),module.__dict__)
        yield module
    finally:
        for name,value in previous.items():
            if value is None: sys.modules.pop(name,None)
            else: sys.modules[name]=value


class Runtime:
    def native(self):
        need(os.name=='posix' and os.geteuid()==0 and os.uname().machine=='aarch64','root ARM Linux required')
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==BOOT,'fixed incident boot differs')
    def group(self):
        path=Path('/sys/fs/cgroup'+GROUP)/'cgroup.procs'
        return sorted(int(n) for n in path.read_text().split()) if path.exists() else []
    def process(self,expected):
        root=PROC/str(OWNER); data=(root/'stat').read_bytes(); text=data.decode(); fields=text[text.rfind(') ')+2:].split()
        need(len(fields)>=20 and ' (' in text and ') ' in text and int(text.split(' (')[0])==OWNER and fields[0] in ('S','R') and int(fields[1])==1 and
             int(fields[19])==START_TICKS,'owner PID/start/PPID/state differs')
        need((root/'cmdline').read_bytes()==b''.join(str(x).encode()+b'\0' for x in expected),'owner command differs')
        need((root/'cgroup').read_text()=='0::'+GROUP+'\n' and self.group()==[OWNER],'owner service membership differs')
        uid=(root/'status').read_text().split('Uid:')[1].splitlines()[0].split(); need(uid==['0']*4,'owner UID differs')
        need(os.readlink(root/'exe')==str(PLAN.parent/'native_owner'),'owner executable differs')
        sockets=[]; lock_fds=[]
        for fd in (root/'fd').iterdir():
            try: target=os.readlink(fd)
            except FileNotFoundError: continue
            if target.startswith('socket:['): sockets.append(int(target[8:-1]))
            if target==str(COMPANION):
                current=fd.stat(); wanted=ordinary(COMPANION).stat()
                need((current.st_dev,current.st_ino)==(wanted.st_dev,wanted.st_ino),'inherited companion FD inode differs')
                lock_fds.append(fd.name)
        need(expected[6] in lock_fds and decode(read(COMPANION))=={'schema':'opt50_first_access_global_companion_lock_v1'},
             'owner no longer holds the original inherited companion lock descriptor')
        table={}
        for line in Path('/proc/net/unix').read_text().splitlines()[1:]:
            row=line.split(); table[int(row[6])]=row
        need(len(sockets)==2 and {table[n][7] for n in sockets if len(table.get(n,[]))==8}==
             {str(PLAN.parent/'execution/runtime.sock'),str(PLAN.parent/'execution/runtime.sock.ctl')} and
             all(int(table[n][3],16)&0x10000 for n in sockets),'owner has accepted/unknown sockets; not an unused zero-graph service')
        return {'pid':OWNER,'start_ticks':START_TICKS,'ppid':1,'cgroup':GROUP,'members':[OWNER]}
    def guardian(self,c,p,closed):
        """Full fixed-source/history checks with explicit owner-only occupancy.

        An unused-CU predicate would reject the legitimate resident owner. No
        original verifier is patched: the distinct occupancy policy is checked
        here, while all existing guardian source/history validators remain.
        """
        c.configure(GUARDIAN); gp,listing=c._guardian_bundle()
        state,records=c._journal_and_state(gp)  # A publication race refuses; it is never swallowed.
        observed=c._observe_guardian(gp,state,p)
        base=GUARDIAN.parent
        board=c._load_readonly(base,listing,'board.py')
        artifacts=c._load_readonly(base,listing,'artifacts.py')
        firmware=c._load_readonly(base,listing,'firmware.py')
        c._validate_commands(gp,records); c._validate_staged(gp,records,board,firmware)
        need(artifacts.audit(Path(gp['package']),Path(gp['cases']))==gp['artifacts'],'guardian source artifacts/cases changed')
        board.loaded(c.read_json(base/'execution/after_load_observations.json',private=True),BOOT)
        occupancy=board.snapshot_api().device_users(Path('/proc'))
        need(occupancy['coverage_complete'] and occupancy['error_count']==0 and occupancy.get('users_omitted',0)==0 and
             {user['pid'] for user in occupancy['users']}== (set() if closed else {OWNER}),
             'fresh device occupancy is not exactly the original owner (or empty after close)')
        need(Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()=='operating' and
             sorted(path.name for path in Path('/sys/kernel/config/device-tree/overlays').iterdir())==
             ['kv260_opt50_fd96539d_4cbcf732_image_1'] and
             Path('/sys/class/drm/renderD128/device/driver').resolve()==Path('/sys/bus/platform/drivers/zocl-drm') and
             Path('/sys/class/drm/renderD128/device/driver/module').resolve()==Path('/sys/module/zocl') and
             Path('/sys/module/zocl/srcversion').read_text().strip()==board.SRCVERSION,'fresh resident OS identity differs')
        clock=board.snapshot_api().clock_row(Path('/sys/kernel/debug/clk/clk_summary'))
        need(clock['rate_hz'] in (124999999,125000000) and clock['enable_count']>0 and clock['prepare_count']>0,
             'fresh clock framework identity differs')
        live=c.monitor_guardian(p)
        need(live['pid']==observed['pid']==GUARDIAN_PID and live['start_ticks']==observed['start_ticks'] and
             live['boot_id']==BOOT and live['flock']==observed['flock'],'fresh guardian identity/lock differs')
        return {'guardian_pid':live['pid'],'guardian_start_ticks':live['start_ticks'],'guardian_flock':live['flock'],
                'scope':'Fixed guardian sources, coherent complete history, staged artifacts, fresh process/lock and explicit owner-only occupancy'}
    def audit(self,closed=False):
        self.native(); root=PLAN.parent; ex=root/'execution'; pins={}
        raw=read(PUBLIC/'tools_manifest.json',False); need(pin(raw)==TOOLS_PIN,'fixed public fix1 release differs')
        listing=decode(raw); need(pin(read(root/'tools/tools_manifest.json'))==TOOLS_PIN,'frozen fix1 tools differ')
        for name,expected in listing['files'].items():
            parts=Path(name).parts; need(not Path(name).is_absolute() and '..' not in parts,'unsafe sealed filename')
            need(pin(read(PUBLIC/name,False))==expected,'public fix1 file changed: '+name)
            path=root/name if name.startswith('model/') else root/'tools'/name
            need(pin(read(path))==expected,'frozen fix1 file changed: '+name); pins[str(path)]=expected
        with release_modules(root/'tools',listing) as entry:
            a=entry.api(private=True); p=entry.load_plan(a,PLAN,fresh=False); c=a.c
            need(p['boot_id']==BOOT and p['sample_start']==100 and p['sample_count']==50 and p['guardian_plan']==str(GUARDIAN),
                 'fixed incident plan selection differs')
            builder=entry.module_checked('build_arm.py',a.tools)
            original=builder.verify_build(PUBLIC/'build_arm',root=PUBLIC)
            need(original==p['board_build'] and read(PUBLIC/'build_arm/build_receipt.json',False)==read(root/'build_receipt.json'),
                 'source-identical ARM receipt/origin differs')
            state=c.read_json(root/'batch_status.json',private=True)
            need(state.get('stage')=='RETAINED_BATCH_STATE_UNCERTAIN' and state.get('owner_pid')==OWNER and
                 state.get('child_pid') is None and state.get('committed_samples')==0 and state.get('verified_pending_samples',0)==0 and
                 state.get('current_ordinal') is None and state.get('current_id') is None,'incident retained state differs')
            rows=journal(root/'batch_events'); expected=rows[0].get('argv') if rows else None
            need(type(expected) is list and len(expected)==8 and expected[:5]==[str(root/'native_owner'),str(root/'model/weights.bin'),
                 str(ex/'runtime.sock'),str(root/'metadata.xclbin'),str(ex)] and expected[5]==str(root/'job.kv') and
                 re.fullmatch('[0-9]{1,4}',expected[6]) and expected[7]=='--authorized-resident-batch','owner expected argv differs')
            validate_parent(rows,expected)
            unused_outputs(ex,root/'samples',closed)
            need(read(ex/'owner.stdout')==(CLOSED if closed else READY) and read(ex/'owner.stderr')==b'','owner output differs')
            native=journal(ex/'native_events',True); validate_native(native,closed)
            guardian=self.guardian(c,p,closed)
            if not closed: process=self.process(expected)
            else:
                need(not Path('/proc',str(OWNER)).exists() and self.group()==[],'owner has not exited or service cgroup is not empty')
                process={'pid_absent':True,'cgroup_empty':True,'owner_exit_code_observed':False}
            for path in (PLAN,root/'tools/tools_manifest.json',root/'build_receipt.json',root/'job.kv',root/'native_owner',root/'plugin.so',
                         root/'metadata.xclbin',root/'batch_status.json',root/'batch_started.json',ex/'held_before.json',GUARDIAN):
                pins[str(path)]=pin(read(path))
            for folder in (root/'batch_events',ex/'native_events'):
                for path in folder.iterdir(): pins[str(path)]=pin(read(path))
            for path in (GUARDIAN.parent/'tools').iterdir(): pins[str(path)]=pin(read(path))
            pins[str(ex/'owner.stdout')]=pin(read(ex/'owner.stdout')); pins[str(ex/'owner.stderr')]=pin(read(ex/'owner.stderr'))
            return {'incident_plan':str(PLAN),'incident_plan_pin':pins[str(PLAN)],'sealed_tools_manifest':TOOLS_PIN,'file_pins':pins,
                'boot_id':BOOT,'owner_pid':OWNER,'owner_start_ticks':START_TICKS,'guardian_plan':str(GUARDIAN),
                'guardian_plan_pin':pins[str(GUARDIAN)],'guardian_pid':GUARDIAN_PID,'native_event_count':len(native),
                'guardian_review':guardian,'process':process,'calls':0,'graphs':0,'max_graphs':50,
                'original_plan_status_completion_written_by_tool':False}
    def stop(self,weights_sha):
        # Only HELLO(1) then STOP(7). No INFO/BEGIN/RUN/ABORT/MAP exists here.
        stream=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            stream.settimeout(10); stream.connect(str(PLAN.parent/'execution/runtime.sock.ctl'))
            peer=struct.unpack('3i',stream.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            need(peer[:2]==(OWNER,0),'management peer PID/UID differs')
            for operation,body in ((1,bytes.fromhex(weights_sha)),(7,b'')):
                stream.sendall(struct.pack('<IHHQIIII',0x3035504f,50,operation,0,0,0,len(body),0)+body)
                raw=b''
                while len(raw)<32:
                    part=stream.recv(32-len(raw)); need(bool(part),'truncated management reply'); raw+=part
                need(struct.unpack('<IHHQIIII',raw)==(0x3035504f,50,operation,0,0,0,0,0),'management rejected/Busy/unknown; no retry')
        finally: stream.close()
    def wait_closed(self):
        deadline=time.monotonic()+30
        while Path('/proc',str(OWNER)).exists() or self.group():
            need(time.monotonic()<deadline,'normal owner close overdue; no retry or process termination')
            time.sleep(0.2)
    def require_frozen_tool(self,folder,receipt):
        need(Path(__file__).absolute()==folder/'tools/zero_close.py' and
             pin(read(folder/'tools/zero_close.py'))==receipt['tool_source_pin'],'run only the sealed root-private check tool')


def check(runtime=None):
    runtime=runtime or Runtime(); runtime.native()
    need(not (BASE/'finish_intent.json').exists() and not (BASE/'receipt.json').exists(),'incident finish already consumed; do not retry')
    proof=runtime.audit(False)
    for folder in (BASE.parent,BASE):
        if folder.exists(): private_dir(folder)
        else: new_dir(folder)
    folder=BASE/('check_'+secrets.token_hex(6)); new_dir(folder); new_dir(folder/'tools')
    raw=Path(__file__).read_bytes(); publish(folder/'tools/zero_close.py',raw)
    receipt={'schema':'opt50_b24_zero_graph_check_v1','status':'CHECKED_ZERO_GRAPH_OWNER_NO_CONTACT',
             'created_monotonic_ns':time.monotonic_ns(),'expires_seconds':300,'tool_source_pin':pin(raw),'evidence':proof}
    publish(folder/'check.json',encoded(receipt))
    return {'status':receipt['status'],'receipt':str(folder/'check.json'),'owner_contacted':False,
            'finish_command':f'sudo /usr/bin/python3 -B {folder}/tools/zero_close.py --finish --receipt {folder}/check.json --authorized-zero-graph-stop'}


def finish(path,authorized,runtime=None):
    need(authorized,'explicit zero-graph STOP authorization required'); runtime=runtime or Runtime(); runtime.native()
    path=Path(path).absolute(); folder=path.parent
    need(folder.parent==BASE and re.fullmatch('check_[a-f0-9]{12}',folder.name) and path.name=='check.json','fixed independent check namespace required')
    receipt=decode(read(path)); need(receipt.get('schema')=='opt50_b24_zero_graph_check_v1' and
        receipt.get('status')=='CHECKED_ZERO_GRAPH_OWNER_NO_CONTACT' and receipt.get('expires_seconds')==300 and
        type(receipt.get('created_monotonic_ns')) is int and 0<=time.monotonic_ns()-receipt['created_monotonic_ns']<=300_000_000_000,
        'check receipt expired/invalid; no owner contact')
    runtime.require_frozen_tool(folder,receipt)
    need(not (BASE/'finish_intent.json').exists() and not (BASE/'receipt.json').exists(),'incident finish already consumed; never retry')
    before=runtime.audit(False); need(before==receipt['evidence'],'fresh zero-graph proof differs; no owner contact')
    need(time.monotonic_ns()-receipt['created_monotonic_ns']<=300_000_000_000,'check expired during fresh review')
    intent={'schema':'opt50_b24_zero_graph_finish_intent_v1','check_receipt':str(path),'check_pin':pin(read(path)),
            'tool_source_pin':receipt['tool_source_pin'],
            'owner_contact_may_have_occurred':True,'automatic_retry':False,'operations':['HELLO','STOP']}
    publish(BASE/'finish_intent.json',encoded(intent))  # Permanent consumption before socket contact.
    try:
        plan=decode(read(PLAN)); runtime.stop(plan['logical_weights_sha256']); runtime.wait_closed()
        after=runtime.audit(True)
        before_pins=before['file_pins']; after_pins=after['file_pins']
        exempt=str(PLAN.parent/'execution/owner.stdout')
        need(all(after_pins.get(name)==value for name,value in before_pins.items() if name!=exempt),
             'original sealed source/prefix changed during close')
        result={'schema':SCHEMA,'status':'CLOSED_ZERO_GRAPHS','tool_source_pin':receipt['tool_source_pin'],
                'check_pin':pin(read(path)),'before':before,'after':after,'closed_at_monotonic_ns':time.monotonic_ns(),
                'calls':0,'graphs':0,'max_graphs':50,'owner_exit_code_observed':False,
                'normal_return_inferred_from_frozen_stdout_and_close':True,'automatic_retry':False,
                'original_plan_status_completion_written_by_tool':False,'history_index_modified':False,
                'only_native_owner_appended_normal_close_evidence':True}
        publish(BASE/'receipt.json',encoded(result)); return result
    except (OSError,ValueError,KeyError,TypeError) as error:
        result={'schema':SCHEMA,'status':'RETAINED_ZERO_CLOSE_UNCERTAIN_NO_RETRY','reason':type(error).__name__+': '+str(error),
                'owner_contact_may_have_occurred':True,'automatic_retry':False,
                'original_plan_status_completion_written_by_tool':False}
        publish(BASE/'failure.json',encoded(result)); return result


def review_failed_close(runtime=None):
    """Re-review actual closed evidence after a failed audit; never retry STOP."""
    runtime=runtime or Runtime()
    need(not (BASE/'receipt.json').exists(),'use completed receipt review')
    intent=decode(read(BASE/'finish_intent.json')); failure=decode(read(BASE/'failure.json'))
    need(intent.get('schema')=='opt50_b24_zero_graph_finish_intent_v1' and intent.get('operations')==['HELLO','STOP'] and
         failure.get('schema')==SCHEMA and failure.get('status')=='RETAINED_ZERO_CLOSE_UNCERTAIN_NO_RETRY',
         'one consumed failed finish required; no owner contact')
    path=Path(intent['check_receipt']); need(path.parent.parent==BASE and path.name=='check.json' and
         re.fullmatch('check_[a-f0-9]{12}',path.parent.name) and pin(read(path))==intent['check_pin'],'original check binding differs')
    checked=decode(read(path)); need(checked.get('schema')=='opt50_b24_zero_graph_check_v1' and
        checked.get('status')=='CHECKED_ZERO_GRAPH_OWNER_NO_CONTACT' and
        checked.get('tool_source_pin')==intent['tool_source_pin']==pin(read(path.parent/'tools/zero_close.py')) and
        pin(Path(__file__).read_bytes())==intent['tool_source_pin'],'original frozen review tool differs')
    before=checked['evidence']; after=runtime.audit(True)
    need(before['incident_plan']==after['incident_plan']==str(PLAN) and before['native_event_count']==141 and
         after['native_event_count']==155 and after.get('calls')==after.get('graphs')==0 and
         after.get('max_graphs')==50,'zero-graph original evidence differs')
    exempt=str(PLAN.parent/'execution/owner.stdout')
    need(all(after['file_pins'].get(name)==expected for name,expected in before['file_pins'].items() if name!=exempt),
         'original source/141 Native prefix changed')
    return {'schema':'opt50_b24_zero_graph_closure_review_v1','status':'VERIFIED_ZERO_GRAPH_CLOSE_FROM_ORIGINAL_EVIDENCE',
            'finish_success_claimed':False,'check_pin':pin(read(path)),'finish_intent_pin':pin(read(BASE/'finish_intent.json')),
            'failure_pin':pin(read(BASE/'failure.json')),'before':before,'after':after,
            'owner_contacted_by_review':False,'owner_exit_code_observed':False,
            'original_plan_status_completion_written_by_tool':False,'history_index_modified':False}


def review_closed(path=None,runtime=None):
    """Re-audit original records/process/OS; never connect to the owner."""
    path=BASE/'receipt.json' if path is None else Path(path).absolute()
    need(path==BASE/'receipt.json','fixed zero-close receipt required')
    if not path.exists(): return review_failed_close(runtime)
    value=decode(read(path)); need(value.get('schema')==SCHEMA and value.get('status')=='CLOSED_ZERO_GRAPHS' and
        value.get('calls')==value.get('graphs')==0 and value.get('max_graphs')==50 and
        value.get('owner_exit_code_observed') is False and value.get('history_index_modified') is False,
        'completed independent zero-graph receipt required')
    before,after=value['before'],value['after']; need(before['incident_plan']==after['incident_plan']==str(PLAN) and
        before['sealed_tools_manifest']==after['sealed_tools_manifest']==TOOLS_PIN and
        before['native_event_count']==141 and after['native_event_count']==155,'zero-close evidence binding differs')
    for name,expected in after['file_pins'].items(): need(pin(read(name))==expected,'closed evidence changed: '+name)
    for name,expected in before['file_pins'].items():
        if name!=str(PLAN.parent/'execution/owner.stdout'):
            need(after['file_pins'].get(name)==expected,'original source/Native prefix changed')
    rows=journal(PLAN.parent/'batch_events'); validate_parent(rows,rows[0].get('argv'))
    validate_native(journal(PLAN.parent/'execution/native_events',True),True)
    need(read(PLAN.parent/'execution/owner.stdout')==CLOSED and read(PLAN.parent/'execution/owner.stderr')==b'',
        'normal zero-graph owner close log differs')
    need(not list((PLAN.parent/'samples').iterdir()),'zero-close must not contain any sample')
    need(Runtime().group()==[],'original service cgroup is no longer empty')
    fresh=(runtime or Runtime()).audit(True)
    need(fresh==after,'fresh complete closed evidence differs from the receipt')
    return {'schema':SCHEMA,'status':'CLOSED_ZERO_GRAPHS_REVIEWED_READ_ONLY','receipt_pin':pin(read(path)),
            'calls':0,'graphs':0,'max_graphs':50,'owner_exit_code_observed':False,'owner_contacted_by_review':False,
            'original_plan_status_completion_written_by_tool':False,'history_index_modified':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check',action='store_true'); group.add_argument('--finish',action='store_true')
    group.add_argument('--review',action='store_true')
    parser.add_argument('--receipt',type=Path); parser.add_argument('--authorized-zero-graph-stop',action='store_true')
    args=parser.parse_args(argv)
    try:
        value=check() if args.check else (review_closed() if args.review else finish(args.receipt,args.authorized_zero_graph_stop))
        print(json.dumps(value,indent=2,allow_nan=False)); return 0 if value['status'] in ('CHECKED_ZERO_GRAPH_OWNER_NO_CONTACT',
            'CLOSED_ZERO_GRAPHS','CLOSED_ZERO_GRAPHS_REVIEWED_READ_ONLY','VERIFIED_ZERO_GRAPH_CLOSE_FROM_ORIGINAL_EVIDENCE') else 2
    except (OSError,ValueError,KeyError,TypeError) as error:
        print('ZERO_CLOSE_REFUSED_NO_CONTACT_OR_RETRY: '+type(error).__name__+': '+str(error),file=sys.stderr); return 2


if __name__=='__main__': raise SystemExit(main())
