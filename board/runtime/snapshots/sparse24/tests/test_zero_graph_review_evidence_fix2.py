"""Real immutable-file receipts plus simulated management protocol; no hardware.

Native fixture follows the observed sequence: RAW_PREFLIGHT_PASS80,
NATIVE_READY137, X/Y exports138..141, and write-return details are only '4'/'8'.
Windows models ownership/directory-fsync only; it does not claim native proof.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).absolute().parents[1]
spec=importlib.util.spec_from_file_location('zero_close_test',ROOT/'zero_graph_review.py')
z=importlib.util.module_from_spec(spec);spec.loader.exec_module(z)


def native_fixture(closed=False):
    pairs=[]
    def event(phase,details): pairs.append((phase,details))
    event('NATIVE_ENTERED','resident max_graphs=50 total_START_budget=1800; no model/performance acceptance')
    raw=[(0xfd615000,0x200),(0xfd380000,0),(0xfd380014,0)]*2
    observer=[0x46383031,0x10000,4254487453,1287452466,0,0,0,0,17,23,0,0,0,0,0,1]
    raw += [(0xa0010000+4*i,value) for _ in range(2) for i,value in enumerate(observer)]
    raw += [(0xa0000000,4)]
    for address,value in raw:
        event('RAW_MMIO_READ_INTENT',str(address)+(' AP_CTRL_COR_DONE_READY' if address==0xa0000000 else ''))
        event('RAW_MMIO_READ_RETURNED',f'{address} value={value}')
    event('RAW_PREFLIGHT_PASS','PS fields, observer identity/stability and initial COR control only; no START; no reset/clock write')
    event('XRT_DEVICE_OPEN_INTENT','device=0 after raw observer preflight')
    event('XRT_METADATA_REGISTRATION_INTENT','MUTATION: metadata only, not firmware programming')
    event('XRT_OBSERVER_OPEN_INTENT','axi_observer:{status_1}')
    for offset,value in ((0,0x46383031),(4,0x10000),(8,4254487453),(12,1287452466)):
        event('OBSERVER_READ_INTENT',str(offset));event('OBSERVER_READ_RETURNED',f'{offset} value={value}')
    event('XRT_GEMM_OPEN_INTENT','model_gemm_top:{gemm_1}')
    idle=[('GEMM_READ_INTENT','0 COR_DONE_READY'),('GEMM_READ_RETURNED','0 value=4')]
    for offset,value in ((20,0),(24,0),(28,0),(60,1),(16,0)):
        idle.extend([('OBSERVER_READ_INTENT',str(offset)),('OBSERVER_READ_RETURNED',f'{offset} value={value}')])
    pairs.extend(idle)
    # Literal observed entries105..112; do not derive write replies from reader.
    pairs.extend([('GEMM_WRITE_INTENT','4 value=0'),('GEMM_WRITE_RETURNED','4'),
                  ('GEMM_WRITE_INTENT','8 value=0'),('GEMM_WRITE_RETURNED','8'),
                  ('GEMM_READ_INTENT','4'),('GEMM_READ_RETURNED','4 value=0'),
                  ('GEMM_READ_INTENT','8'),('GEMM_READ_RETURNED','8 value=0')])
    sizes=(114688,2658304,335872,454656)
    for slot,size in enumerate(sizes):
        address=0x10000000+slot*0x1000000
        detail=f'slot={slot} address_hex=0x{address:016x} address_dec={address} bytes={size} actual_BO_bytes={size} alignment_remainder=0'
        event('BO_ALLOC_INTENT',f'slot={slot} cacheable group0 bytes={size}')
        event('BO_ALLOC_RETURNED',detail);event('BO_MAP_INTENT',str(slot));event('BO_MAP_RETURNED',detail)
    for slot,size in enumerate(sizes):
        event('BO_TO_DEVICE_INTENT',f'slot={slot} whole allocation bytes={size}');event('BO_TO_DEVICE_RETURNED',str(slot))
    event('NATIVE_READY','single CU/four global-guarded BOs; weights synchronized once; shared payload offset=64; H576-aligned client-only per-layer X/Y cache synchronization')
    for slot,borrowed,owned in ((0,12,13),(3,14,15)):
        event('BO_EXPORT_DUP_INTENT',f'slot={slot}; XRT export handle remains borrowed; owner receives an owned duplicate')
        event('BO_EXPORT_DUP_RETURNED',f'slot={slot} borrowed_XRT_fd={borrowed} owned_owner_fd={owned}')
    if closed:
        pairs.extend(idle)
        event('RESOURCE_CLOSE_INTENT','allocated_BO_count=4; no unload/reset/recovery')
        event('RESOURCES_CLOSED','released_BO_count=4; exported client handles must already be closed; resident firmware unchanged')
    return [dict(schema='opt50_model_native_event_v1',sequence=i,phase=phase,details=detail,monotonic_ns=i*100)
            for i,(phase,detail) in enumerate(pairs,1)]


class NativeEvidenceTests(unittest.TestCase):
    def test_observed_141_prefix_and_155_normal_close_accept_nonzero_old_statistics(self):
        rows=native_fixture();self.assertEqual(len(rows),141)
        self.assertEqual(rows[79]['phase'],'RAW_PREFLIGHT_PASS');self.assertEqual(rows[136]['phase'],'NATIVE_READY')
        self.assertEqual(rows[105]['details'],'4');self.assertEqual(rows[107]['details'],'8')
        z.validate_native(rows);z.validate_native(native_fixture(True),True)

    def test_mirrored_wrong_write_return_format_is_rejected(self):
        rows=native_fixture();rows[105]['details']='4 value=0'
        with self.assertRaisesRegex(ValueError,'106'):z.validate_native(rows)

    def test_native_fault_start_added_event_or_missing_close_is_rejected(self):
        for number in (0,79,136,140):
            rows=native_fixture();rows[number]['phase']='START_WRITE_RETURNED'
            with self.assertRaises(ValueError):z.validate_native(rows)
        with self.assertRaises(ValueError):z.validate_native(native_fixture(True)[:-1],True)

    def test_raw_observer_motion_or_wrong_bo_bounds_refused(self):
        rows=native_fixture();rows[48]['details']='2684420096 value=99'
        with self.assertRaises(ValueError):z.validate_native(rows)
        rows=native_fixture();rows[113]['details']=rows[113]['details'].replace('alignment_remainder=0','alignment_remainder=4')
        with self.assertRaises(ValueError):z.validate_native(rows)

    def test_parent_worker_or_graph_intent_is_never_accepted_as_zero_graph(self):
        argv=['native','w','s','m','e','j','3','--authorized-resident-batch']
        rows=[dict(action='owner',phase='INTENT',argv=argv),dict(action='owner',phase='SPAWNED',argv=argv,pid=52117)]
        z.validate_parent(rows,argv)
        for extra in ({'action':'worker','phase':'INTENT'},{'action':'sample','phase':'GRAPH_INTENT'}):
            with self.assertRaises(ValueError):z.validate_parent(rows+[extra],argv)


class ManagementProtocolTests(unittest.TestCase):
    def fake_socket(self,busy=False,wrong_peer=False):
        class Wire:
            def __init__(self):self.sent=[];self.replies=b'';self.closed=False
            def settimeout(self,n):pass
            def connect(self,path):self.path=path
            def getsockopt(self,*args):return struct.pack('3i',999 if wrong_peer else 52117,0,0)
            def sendall(self,raw):
                header=struct.unpack('<IHHQIIII',raw[:32]);self.sent.append(header[2])
                self.replies+=struct.pack('<IHHQIIII',0x3035504f,50,header[2],0,0,0,0,1 if busy and header[2]==7 else 0)
            def recv(self,n):part=self.replies[:min(n,7)];self.replies=self.replies[len(part):];return part
            def close(self):self.closed=True
        return Wire()
    def test_only_hello_and_stop_no_info_or_compute(self):
        wire=self.fake_socket()
        with patch.object(z.socket,'socket',return_value=wire),patch.object(z.socket,'SO_PEERCRED',17,create=True), \
             patch.object(z.socket,'AF_UNIX',1,create=True):
            z.Runtime().stop('a'*64)
        self.assertEqual(wire.sent,[1,7]);self.assertTrue(wire.closed)
    def test_busy_or_wrong_peer_never_retries(self):
        for busy,wrong,expected in ((True,False,[1,7]),(False,True,[])):
            wire=self.fake_socket(busy,wrong)
            with patch.object(z.socket,'socket',return_value=wire),patch.object(z.socket,'SO_PEERCRED',17,create=True), \
                 patch.object(z.socket,'AF_UNIX',1,create=True):
                with self.assertRaises(ValueError):z.Runtime().stop('a'*64)
            self.assertEqual(wire.sent,expected);self.assertTrue(wire.closed)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.zero-close-test-',dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.addCleanup(patch.stopall)
        self.plan=self.root/'original/plan.json';self.plan.parent.mkdir();self.plan.write_bytes(z.encoded({'logical_weights_sha256':'a'*64}))
        self.base=self.root/'records/plan_c4b539de5ba7';self.base.parent.mkdir()
        patch.object(z,'PLAN',self.plan).start();patch.object(z,'BASE',self.base).start()
        if os.name=='nt':
            original=z.read
            patch.object(z,'read',side_effect=lambda p,private=True,limit=64*1024*1024:original(p,False,limit)).start()
            patch.object(z,'private_dir',side_effect=lambda p:z.need(Path(p).is_dir(),'directory missing')).start()
            patch.object(z,'sync_dir').start()
            patch.object(z.os,'O_NOFOLLOW',0,create=True).start()
        me=self
        class Fake:
            def __init__(self):self.contacts=0;self.reviews=0;self.busy=False;self.change=False;self.closed=False;self.post_error=False
            def native(self):pass
            def require_frozen_tool(self,folder,receipt):
                z.need(z.pin(z.read(folder/'tools/zero_close.py'))==receipt['tool_source_pin'],'tool changed')
            def audit(self,closed=False):
                self.reviews+=1
                if closed and self.post_error:raise ValueError('guardian tail and status differ')
                return {'incident_plan':str(me.plan),'sealed_tools_manifest':z.TOOLS_PIN,'native_event_count':155 if closed else 141,
                        'file_pins':{str(me.plan):z.pin(z.read(me.plan))},'stable':not self.change,'calls':0,'graphs':0,'max_graphs':50}
            def stop(self,sha):
                self.contacts+=1
                if self.busy:raise ValueError('Busy; no retry')
                self.closed=True
            def wait_closed(self):pass
        self.runtime=Fake()
    def checked(self):return Path(z.check(self.runtime)['receipt'])
    def test_check_writes_only_independent_receipt_and_never_contacts_owner(self):
        original=self.plan.read_bytes();p=self.checked()
        self.assertTrue(p.exists());self.assertEqual(self.runtime.contacts,0)
        self.assertEqual(self.plan.read_bytes(),original)
    def test_finish_once_writes_honest_receipt_without_owner_exit_code_claim(self):
        p=self.checked();value=z.finish(p,True,self.runtime)
        self.assertEqual(value['status'],'CLOSED_ZERO_GRAPHS');self.assertEqual(self.runtime.contacts,1)
        self.assertFalse(value['owner_exit_code_observed']);self.assertNotIn('owner_exit_code',value)
        self.assertFalse(value['original_plan_status_completion_written_by_tool'])
        with self.assertRaisesRegex(ValueError,'consumed'):z.finish(p,True,self.runtime)
        self.assertEqual(self.runtime.contacts,1)
    def test_busy_consumes_permanent_intent_and_blocks_all_future_contacts(self):
        p=self.checked();self.runtime.busy=True
        self.assertEqual(z.finish(p,True,self.runtime)['status'],'RETAINED_ZERO_CLOSE_UNCERTAIN_NO_RETRY')
        self.assertTrue((self.base/'finish_intent.json').exists())
        with self.assertRaises(ValueError):z.finish(p,True,self.runtime)
        with self.assertRaises(ValueError):z.check(self.runtime)
        self.assertEqual(self.runtime.contacts,1)
    def test_changed_fresh_evidence_refuses_before_consumption_or_contact(self):
        p=self.checked();self.runtime.change=True
        with self.assertRaisesRegex(ValueError,'fresh'):z.finish(p,True,self.runtime)
        self.assertEqual(self.runtime.contacts,0);self.assertFalse((self.base/'finish_intent.json').exists())
    def test_expired_or_unauthorized_receipt_does_not_contact_owner(self):
        p=self.checked()
        with self.assertRaises(ValueError):z.finish(p,False,self.runtime)
        value=z.decode(p.read_bytes());value['created_monotonic_ns']-=301_000_000_000;p.write_bytes(z.encoded(value))
        with self.assertRaisesRegex(ValueError,'expired'):z.finish(p,True,self.runtime)
        self.assertEqual(self.runtime.contacts,0)
    def test_frozen_tool_change_refuses_before_owner_contact(self):
        p=self.checked();(p.parent/'tools/zero_close.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'tool changed'):z.finish(p,True,self.runtime)
        self.assertEqual(self.runtime.contacts,0)
    def test_post_stop_reader_failure_never_claims_success_or_resends_stop(self):
        p=self.checked();self.runtime.post_error=True
        result=z.finish(p,True,self.runtime)
        self.assertEqual(result['status'],'RETAINED_ZERO_CLOSE_UNCERTAIN_NO_RETRY')
        self.assertTrue(self.runtime.closed);self.assertFalse((self.base/'receipt.json').exists())
        with self.assertRaises(ValueError):z.finish(p,True,self.runtime)
        self.assertEqual(self.runtime.contacts,1)
        self.runtime.post_error=False
        reviewed=z.review_failed_close(self.runtime)
        self.assertEqual(reviewed['status'],'VERIFIED_ZERO_GRAPH_CLOSE_FROM_ORIGINAL_EVIDENCE')
        self.assertFalse(reviewed['finish_success_claimed'] or reviewed['owner_contacted_by_review'])
        self.assertEqual(self.runtime.contacts,1)
        self.assertFalse((self.base/'receipt.json').exists(),'read-only review must not fabricate finish success')
    def test_readonly_review_of_unclosed_evidence_still_refuses_without_socket(self):
        p=self.checked();self.runtime.post_error=True;z.finish(p,True,self.runtime)
        with self.assertRaisesRegex(ValueError,'guardian'):z.review_failed_close(self.runtime)
        self.assertEqual(self.runtime.contacts,1)
    def test_evidence_changed_after_stop_is_retained_not_success(self):
        p=self.checked()
        original=self.runtime.stop
        def stop(sha):original(sha);self.plan.write_bytes(z.encoded({'logical_weights_sha256':'b'*64}))
        self.runtime.stop=stop
        self.assertEqual(z.finish(p,True,self.runtime)['status'],'RETAINED_ZERO_CLOSE_UNCERTAIN_NO_RETRY')
        self.assertEqual(self.runtime.contacts,1)
        with self.assertRaisesRegex(ValueError,'prefix'):z.review_failed_close(self.runtime)


class LiveAndArtifactBoundaryTests(unittest.TestCase):
    def test_wrong_pid_start_or_parent_refuses_before_any_owner_contact(self):
        with tempfile.TemporaryDirectory(prefix='.zero-proc-',dir=ROOT) as temporary:
            proc=Path(temporary);owner=proc/str(z.OWNER);owner.mkdir()
            for ppid,ticks in ((1,z.START_TICKS+1),(999,z.START_TICKS)):
                fields=['S',str(ppid)]+['0']*17+[str(ticks)]
                (owner/'stat').write_text(str(z.OWNER)+' (native_owner) '+' '.join(fields)+'\n')
                with patch.object(z,'PROC',proc):
                    with self.assertRaisesRegex(ValueError,'PID/start/PPID'):z.Runtime().process([])
    def test_worker_files_and_any_sample_forbid_zero_graph_claim(self):
        with tempfile.TemporaryDirectory(prefix='.zero-artifacts-',dir=ROOT) as temporary:
            root=Path(temporary);ex=root/'execution';samples=root/'samples';ex.mkdir();samples.mkdir()
            names=('native_events','owner.stdout','owner.stderr','held_before.json','runtime.sock.lock','runtime.sock','runtime.sock.ctl')
            for name in names:(ex/name).write_bytes(b'fixture')
            with patch.object(z,'private_dir',side_effect=lambda p:z.need(Path(p).is_dir(),'missing directory')):
                z.unused_outputs(ex,samples,False)
                (ex/'worker_protocol.jsonl').write_bytes(b'')
                with self.assertRaisesRegex(ValueError,'worker/graph'):z.unused_outputs(ex,samples,False)
                (ex/'worker_protocol.jsonl').unlink();(samples/'000100').mkdir()
                with self.assertRaisesRegex(ValueError,'sample output'):z.unused_outputs(ex,samples,False)
    def test_var_lib_755_is_allowed_only_for_exact_top_level_creation(self):
        info=types.SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0)
        with patch.object(z,'BASE',Path('/var/lib/opt50-b24-zero-close-v1/plan_c4b539de5ba7')), \
             patch.object(z,'ordinary',return_value=types.SimpleNamespace(lstat=lambda:info)), \
             patch.object(z.os,'mkdir') as mkdir,patch.object(z,'sync_dir'), \
             patch.object(z,'private_dir',side_effect=ValueError('private mode required')):
            z.new_dir(Path('/var/lib/opt50-b24-zero-close-v1'));mkdir.assert_called_once()
            with self.assertRaisesRegex(ValueError,'private mode'):z.new_dir(Path('/var/lib/unrelated'))


if __name__=='__main__':unittest.main()
