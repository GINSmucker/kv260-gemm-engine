"""Real file/cache transactions; no owner, hardware, signals, or model execution.

Windows mocks only POSIX root metadata, directory fsync, and flock. File IO,
SHA checking, atomic replacement and NumPy logit recomputation remain real.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


r=module('history_index_test',ROOT/'history_index.py')
metrics=module('history_index_metrics',ROOT/'accuracy_metrics.py')
durable=module('history_index_durable',ROOT/'support_py/durable.py')


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(r.encoded(value));path.chmod(0o600)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.history-test-',dir=ROOT.parent)
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.records=self.base/'records';self.records.mkdir(mode=0o700)
        self.index=self.base/'index'
        self.dataset={'bytes':807589,'sha256':'a'*64}
        self.tools_raw=r.encoded({'schema':'fixture_tools','files':{'review_results.py':r.identity((ROOT/'review_results.py').read_bytes())}})
        self.tools={'manifest':r.identity(self.tools_raw),'files':{'review_results.py':r.identity((ROOT/'review_results.py').read_bytes())}}
        self.rows={};self.deep=[];self.recomputed=[]
        def loader(path,*_,**__):
            path=Path(path);name=str(path)
            def completed(_path,allow_partial=False):
                self.deep.append((name,allow_partial));return copy.deepcopy(self.rows[name])
            entry=types.SimpleNamespace(read_completed_records=completed,
                api=lambda **_:object(),load_plan=lambda _,p,**__:json.loads(Path(p).read_bytes()),
                committed_records=lambda _,plan,root,**__:copy.deepcopy(self.rows[str(Path(root)/'plan.json')]))
            return entry,path.parent,{'tools_manifest':self.tools['manifest']}
        self.loader=Mock(side_effect=loader)
        self.a=types.SimpleNamespace(c=types.SimpleNamespace(root_linux=lambda:None,
                read_file=lambda p,**_:Path(p).read_bytes()),
                tools=self.tools,metrics=metrics,durable=durable,
                compat=types.SimpleNamespace(load_closed_entry=self.loader))
        self.addCleanup(patch.stopall)
        patch.object(r,'RECORDS',self.records).start();patch.object(r,'INDEX_DIR',self.index).start()
        self.lock=patch.object(r,'lock_fd').start()
        if os.name=='nt':
            # Preserve real regular-file/single-link checks; Windows cannot
            # establish native Linux ownership/modes, directory fsync or flock.
            def portable(info,directory=False):
                r.need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
                       (directory or info.st_nlink==1),'root private single-link history input required')
            patch.object(r,'private_metadata',side_effect=portable).start()
            patch.object(durable,'fsync_dir').start()
            # Python 3.12 Windows lstat/fstat expose different ctime semantics.
            # Match the portable creation-time policy already used by read().
            patch.object(durable,'_identity',side_effect=lambda s:(s.st_dev,s.st_ino,s.st_size,
                         s.st_mtime_ns,s.st_birthtime_ns)).start()

    def batch(self,start=0,count=2,token='000000000001',final=True):
        import numpy as np
        root=self.records/('plan_'+token)
        root.mkdir(mode=0o700)
        (root/'tools').mkdir(mode=0o700)
        path=root/'plan.json'
        (root/'tools/tools_manifest.json').write_bytes(self.tools_raw)
        (root/'tools/tools_manifest.json').chmod(0o600)
        plan={'schema':r.PLAN_SCHEMA,'measurement_profile':r.PROFILE,
              'source_model_sha256':r.MODEL,'model_manifest_sha256':r.MODEL_MANIFEST,
              'plan_path':str(path),'token':token,'unit':'unit_'+token,'boot_id':'boot-A',
              'dataset_manifest':self.dataset,'tool_files':self.tools,
              'sample_start':start,'sample_count':count,'total_START_budget':count*36,
              'expected_PL_starts':36,'expected_Dense_calls':12,'expected_Sparse_calls':24,'run_id':1}
        done={'schema':'opt50_b24_batch_completion_v1','plan_token':token,'dataset_manifest':self.dataset,
              'sample_start':start,'sample_count':count,'committed_samples':count,
              'stage':'PASS_B24_BATCH_ALL_IMAGES_BITWISE_CLOSED','closed':True,
              'owner_pid':None,'child_pid':None,'total_START_budget':36*count,
              'completed_PL_calls':36*count,'Dense_calls':12*count,'Sparse_calls':24*count,
              'compared_logits':1000*count}
        rows=[]
        for ordinal in range(start,start+count):
            folder=root/'samples'/('%06d'%ordinal);folder.mkdir(parents=True)
            scores=np.zeros((1,1000),dtype=np.float32);scores[0,ordinal]=1
            for name in ('cpu_reference.npy','model_logits.npy'):
                with (folder/name).open('wb') as f:np.save(f,scores,allow_pickle=False)
                (folder/name).chmod(0o600)
            comparison=metrics.compare(scores,scores,ordinal)
            row={'schema':'opt50_b24_accuracy_record_v1','ordinal':ordinal,'id':'val_%07d'%ordinal,'label':ordinal,
                 'image_sha256':'b'*64,'input_sha256':'c'*64,**{k:comparison[k] for k in (
                     'CPU_logits_sha256','PL_logits_sha256','CPU_top5','PL_top5','bitwise_mismatches','max_abs_difference')},
                 'CPU_reference_ms':1.0,'inference_ms':2.0,'completed_PL_calls':36,'Dense_calls':12,'Sparse_calls':24,
                 'owner_exit_code':0,'device_closed':True,'postrun_buffer_checks_verified':True,'native_END_verified':True}
            rows.append(row)
            write(folder/'commit.json',{'schema':'opt50_b24_batch_sample_commit_v1','plan_token':token,
                                       'closed':True,'sample':{'ordinal':ordinal},'files':{}})
        self.rows[str(path)]=rows
        write(path,plan);write(root/'batch_completion.json',done)
        state={'schema':'opt50_b24_acceptance_batch_status_v1','plan_token':token,'boot_id':'boot-A','unit':plan['unit'],
               'stage':done['stage'] if final else 'SAMPLE_COMMITTED_NATIVE_CLOSED','owner_pid':None,'child_pid':None}
        if final:state['completion']=done
        write(root/'batch_status.json',state)
        return path,plan,done

    def prepare(self,path,count):
        return r.prepare(self.a,[path],count)

    def history(self,*paths):
        return [{'plan':str(path)} for path in paths]

    def test_prepare_real_numpy_recompute_then_admit_does_not_recompute(self):
        path,_,_=self.batch()
        receipt=self.prepare(path,2)
        self.assertEqual(receipt['images_count'],2)
        self.assertEqual(self.deep,[(str(path),True)])
        value,_=r.load(self.a)
        self.assertEqual(value['samples']['0']['accuracy']['CPU_true_label_rank'],1)
        self.assertTrue(value['samples']['1']['accuracy']['PL_top1_hit'])
        self.loader.reset_mock()
        with patch.object(r,'recompute',side_effect=AssertionError('historical logits reread')):
            actual=r.admit(self.a,self.history(path),self.dataset,2,5)
        self.assertEqual(actual,receipt);self.loader.assert_not_called()

    def test_prepare_same_explicit_records_is_idempotent_without_deep_review_or_write(self):
        path,_,_=self.batch();first=self.prepare(path,2)
        before=(self.index/'index.json').stat().st_mtime_ns
        self.deep.clear();self.loader.reset_mock()
        with patch.object(durable,'replace_json',side_effect=AssertionError('unexpected rewrite')):
            second=self.prepare(path,2)
        self.assertEqual(first,second);self.assertFalse(self.deep);self.loader.assert_not_called()
        self.assertEqual(before,(self.index/'index.json').stat().st_mtime_ns)

    def test_prepare_can_add_a_new_closed_batch_without_rewriting_old_records(self):
        first,_,_=self.batch();self.prepare(first,2)
        value,_=r.load(self.a);old=copy.deepcopy(value['samples'])
        second,_,_=self.batch(2,1,'000000000002')
        self.deep.clear();receipt=r.prepare(self.a,[first,second],3)
        now,_=r.load(self.a)
        self.assertEqual(receipt['revision'],2)
        self.assertEqual(self.deep,[(str(second),True)])
        self.assertTrue(all(now['samples'][key]==row for key,row in old.items()))

    def test_expected_count_mismatch_does_not_publish_index(self):
        path,_,_=self.batch()
        with self.assertRaisesRegex(ValueError,'expected unique'):
            self.prepare(path,3)
        self.assertFalse((self.index/'index.json').exists())

    def test_duplicate_explicit_plans_refused(self):
        path,_,_=self.batch()
        with self.assertRaisesRegex(ValueError,'duplicate explicit'):
            r.prepare(self.a,[path,path],2)

    def test_duplicate_ordinal_different_plan_refused_preserving_old_index(self):
        first,_,_=self.batch();self.prepare(first,2)
        before=(self.index/'index.json').read_bytes()
        second,_,_=self.batch(1,1,'000000000002')
        with self.assertRaisesRegex(ValueError,'already committed by another plan'):
            r.prepare(self.a,[second],3)
        self.assertEqual(before,(self.index/'index.json').read_bytes())

    def test_changed_saved_logits_stop_prepare_without_publishing(self):
        import numpy as np
        path,_,_=self.batch()
        folder=path.parent/'samples/000000'
        value=np.load(folder/'model_logits.npy',allow_pickle=False);value[0,10]=1
        with (folder/'model_logits.npy').open('wb') as f:np.save(f,value,allow_pickle=False)
        with self.assertRaisesRegex(ValueError,'record differs from saved logits'):
            self.prepare(path,2)
        self.assertFalse((self.index/'index.json').exists())

    def test_admit_overlap_missing_index_and_wrong_dataset_refused(self):
        path,_,_=self.batch()
        with self.assertRaisesRegex(ValueError,'not prepared'):
            r.admit(self.a,self.history(path),self.dataset,2,1)
        self.prepare(path,2)
        with self.assertRaisesRegex(ValueError,'already has a committed'):
            r.admit(self.a,self.history(path),self.dataset,1,1)
        with self.assertRaisesRegex(ValueError,'dataset differs'):
            r.admit(self.a,self.history(path),{'bytes':1,'sha256':'d'*64},2,1)

    def test_closed_unindexed_batch_is_not_silently_accepted(self):
        first,_,_=self.batch();self.prepare(first,2)
        second,_,_=self.batch(2,1,'000000000002')
        with self.assertRaisesRegex(ValueError,'not indexed'):
            r.admit(self.a,self.history(first,second),self.dataset,3,1)

    def test_missing_indexed_batch_and_unknown_history_schema_refused(self):
        first,_,_=self.batch();self.prepare(first,2)
        with self.assertRaisesRegex(ValueError,'missing from current history'):
            r.admit(self.a,[],self.dataset,2,1)
        second,plan,_=self.batch(2,1,'000000000002');plan['schema']='unknown';write(second,plan)
        with self.assertRaisesRegex(ValueError,'unknown nonbatch'):
            r.admit(self.a,self.history(first,second),self.dataset,3,1)

    def test_changed_closed_plan_completion_or_tools_pin_refused(self):
        for member in ('plan.json','batch_completion.json','tools/tools_manifest.json'):
            with self.subTest(member=member):
                # Reuse a fresh fixture namespace for each independent mutation.
                token='%012x'%(len(self.rows)+1)
                start=len(self.rows)*2
                path,_,_=self.batch(start,2,token)
                expected=start+2
                r.prepare(self.a,[path],expected)
                before=(path.parent/member).read_bytes()
                (path.parent/member).write_bytes(before+b' ')
                with self.assertRaises(ValueError):
                    r.admit(self.a,self.history(*map(Path,self.rows)),self.dataset,expected,1)
                (path.parent/member).write_bytes(before)

    def test_unresolved_saved_batch_blocks_admission(self):
        path,_,_=self.batch();self.prepare(path,2)
        state=json.loads((path.parent/'batch_status.json').read_bytes());state['owner_pid']=123
        write(path.parent/'batch_status.json',state)
        with self.assertRaisesRegex(ValueError,'unresolved'):
            r.admit(self.a,self.history(path),self.dataset,2,1)

    def test_known_single_model_closed_provenance_is_excluded_from_acceptance_count(self):
        first,_,_=self.batch();self.prepare(first,2)
        root=self.records/'plan_000000000002';root.mkdir(mode=0o700)
        path=root/'plan.json'
        write(path,{'schema':'opt50_b24_model_plan_v1','unit':'old-unit','boot_id':'old-boot'})
        state={'unit':'old-unit','boot_id':'old-boot','owner_pid':None,'child_pid':None,
               'stage':'PASS_B24_FULL_MODEL_36_STARTS_REAL_INPUT_BITWISE_SINGLE_IMAGE'}
        write(root/'execution/status.json',state)
        write(root/'execution/completion.json',{'owner_exit_code':0,'native':{'device_closed':True}})
        result=r.admit(self.a,self.history(first,path),self.dataset,2,1)
        self.assertEqual(result['images_count'],2)
        state['stage']='RETAINED_MODEL_STATE_UNCERTAIN';write(root/'execution/status.json',state)
        with self.assertRaisesRegex(ValueError,'unresolved'):
            r.admit(self.a,self.history(first,path),self.dataset,2,1)

    def test_no_owner_single_model_refusal_cannot_claim_native_events(self):
        first,_,_=self.batch();self.prepare(first,2)
        root=self.records/'plan_000000000002';root.mkdir(mode=0o700);path=root/'plan.json'
        write(path,{'schema':'opt50_b24_model_plan_v1','unit':'old-unit','boot_id':'old-boot'})
        write(root/'execution/status.json',{'unit':'old-unit','boot_id':'old-boot','owner_pid':None,'child_pid':None,
             'stage':'REFUSED_BEFORE_OWNER_NO_MMIO','hardware_executed':False})
        self.assertEqual(r.admit(self.a,self.history(first,path),self.dataset,2,1)['images_count'],2)
        (root/'execution/native_events').mkdir()
        with self.assertRaisesRegex(ValueError,'no-owner proof'):
            r.admit(self.a,self.history(first,path),self.dataset,2,1)

    def test_structurally_corrupt_index_is_rejected_even_if_checksum_is_recomputed(self):
        path,_,_=self.batch();self.prepare(path,2)
        envelope=json.loads((self.index/'index.json').read_bytes())
        envelope['payload']['samples']['1']['accuracy']['device_closed']=False
        envelope['payload_sha256']=r.identity(r.encoded(envelope['payload']))['sha256']
        write(self.index/'index.json',envelope)
        with self.assertRaisesRegex(ValueError,'completed closed proof required'):
            r.load(self.a)

    def test_reparse_or_symlink_input_is_refused_before_open(self):
        path,_,_=self.batch();self.prepare(path,2)
        real=Path.lstat
        def linked(path,*args,**kwargs):
            info=real(path,*args,**kwargs)
            if path==self.index/'index.json':
                return types.SimpleNamespace(st_mode=stat.S_IFLNK|0o600,st_file_attributes=0x400)
            return info
        with patch.object(Path,'lstat',new=linked):
            with self.assertRaisesRegex(ValueError,'linked history path'):
                r.load(self.a)

    def test_index_checksum_and_duplicate_json_key_refused(self):
        path,_,_=self.batch();self.prepare(path,2)
        original=(self.index/'index.json').read_bytes()
        value=json.loads(original);value['payload']['images_count']=99
        write(self.index/'index.json',value)
        with self.assertRaisesRegex(ValueError,'checksum'):
            r.admit(self.a,self.history(path),self.dataset,2,1)
        (self.index/'index.json').write_bytes(original)
        (self.index/'index.json').write_bytes(b'{"schema":1,"schema":2}')
        with self.assertRaisesRegex(ValueError,'duplicate'):
            r.load(self.a)

    def test_hard_linked_index_is_refused(self):
        path,_,_=self.batch();self.prepare(path,2)
        if os.name=='posix':
            os.link(self.index/'index.json',self.index/'extra-link')
            with self.assertRaisesRegex(ValueError,'single-link'):
                r.load(self.a)
        else:
            # The Windows sandbox disallows os.link. Simulate only the extra
            # link-count field; the actual read/metadata policy remains active.
            real=Path.lstat
            def linked(path,*args,**kwargs):
                info=real(path,*args,**kwargs)
                if path==self.index/'index.json':
                    return types.SimpleNamespace(st_mode=info.st_mode,st_uid=info.st_uid,st_nlink=2,
                                                 st_file_attributes=info.st_file_attributes)
                return info
            with patch.object(Path,'lstat',new=linked):
                with self.assertRaisesRegex(ValueError,'single-link'):
                    r.load(self.a)

    def test_append_closed_works_before_final_batch_status_and_checks_all_samples(self):
        first,_,_=self.batch();receipt=self.prepare(first,2)
        second,plan,done=self.batch(2,1,'000000000002',final=False)
        plan['history_index']=receipt;write(second,plan)
        result=r.append_closed(self.a,plan,self.rows[str(second)],done)
        self.assertEqual(result['images_count'],3);self.assertEqual(result['revision'],2)
        # Final state is deliberately still unpublished. No subsequent admission
        # may treat the ledger alone as proof that the coordinator finished.
        with self.assertRaisesRegex(ValueError,'unresolved'):
            r.admit(self.a,self.history(first,second),self.dataset,3,1)

    def test_append_old_snapshot_and_second_commit_are_refused(self):
        first,_,_=self.batch();receipt=self.prepare(first,2)
        second,plan,done=self.batch(2,1,'000000000002',final=False)
        plan['history_index']=receipt;write(second,plan)
        r.append_closed(self.a,plan,self.rows[str(second)],done)
        with self.assertRaisesRegex(ValueError,'revision/pin changed'):
            r.append_closed(self.a,plan,self.rows[str(second)],done)

    def test_append_failure_before_publish_keeps_previous_index(self):
        first,_,_=self.batch();receipt=self.prepare(first,2)
        second,plan,done=self.batch(2,1,'000000000002',final=False)
        plan['history_index']=receipt;write(second,plan)
        before=(self.index/'index.json').read_bytes()
        with patch.object(durable,'replace_json',side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError,'disk error'):
                r.append_closed(self.a,plan,self.rows[str(second)],done)
        self.assertEqual(before,(self.index/'index.json').read_bytes())

    def test_error_after_atomic_publish_is_not_automatically_retried(self):
        first,_,_=self.batch();receipt=self.prepare(first,2)
        second,plan,done=self.batch(2,1,'000000000002',final=False)
        plan['history_index']=receipt;write(second,plan)
        real=durable.replace_json
        def publish_then_fail(*args,**kwargs):
            real(*args,**kwargs);raise OSError('directory fsync return uncertain')
        with patch.object(durable,'replace_json',side_effect=publish_then_fail) as writer:
            with self.assertRaisesRegex(OSError,'uncertain'):
                r.append_closed(self.a,plan,self.rows[str(second)],done)
            self.assertEqual(writer.call_count,1)
        value,_=r.load(self.a)
        self.assertEqual(value['images_count'],3)
        with self.assertRaisesRegex(ValueError,'revision/pin changed'):
            r.append_closed(self.a,plan,self.rows[str(second)],done)

    def test_append_backend_closure_failure_never_updates_cache(self):
        first,_,_=self.batch();receipt=self.prepare(first,2)
        second,plan,done=self.batch(2,1,'000000000002',final=False)
        plan['history_index']=receipt;write(second,plan)
        original=self.loader.side_effect
        def loader(*args,**kwargs):
            entry,root,source=original(*args,**kwargs)
            entry.committed_records=Mock(side_effect=ValueError('native close proof missing'))
            return entry,root,source
        before=(self.index/'index.json').read_bytes()
        self.loader.side_effect=loader
        with self.assertRaisesRegex(ValueError,'native close proof missing'):
            r.append_closed(self.a,plan,self.rows[str(second)],done)
        self.assertEqual(before,(self.index/'index.json').read_bytes())

    def test_own_lock_is_non_retrying_and_released_after_exception(self):
        path,_,_=self.batch();self.prepare(path,2)
        self.lock.reset_mock()
        with self.assertRaisesRegex(ValueError,'selected image'):
            r.admit(self.a,self.history(path),self.dataset,0,1)
        self.assertEqual([x.args[1] for x in self.lock.call_args_list],[True,False])
        def busy(fd,acquire):
            if acquire:raise BlockingIOError('locked')
        self.lock.side_effect=busy
        with self.assertRaises(BlockingIOError):
            r.admit(self.a,self.history(path),self.dataset,2,1)

    def test_mode_owner_and_reparse_checks_are_explicit(self):
        fake=types.SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_uid=0,st_nlink=1)
        # Test the actual policy function, outside Windows metadata simulation.
        original=private_policy
        with self.assertRaisesRegex(ValueError,'root private'):
            original(fake)
        fake.st_mode=stat.S_IFREG|0o600;fake.st_uid=1000
        with self.assertRaisesRegex(ValueError,'root private'):
            original(fake)
        fake.st_uid=0;fake.st_nlink=2
        with self.assertRaisesRegex(ValueError,'single-link'):
            original(fake)


private_policy=r.private_metadata


class NativeHistoryTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,
                         'requires native root Linux flock, ownership and directory fsync')
    def test_native_index_flock_and_atomic_fsync_smoke(self):
        native=module('native_history_smoke',ROOT/'history_index.py')
        sync=module('native_history_durable',ROOT/'support_py/durable.py')
        with tempfile.TemporaryDirectory(prefix='.history-native-',dir=ROOT.parent) as temporary:
            folder=Path(temporary);index=folder/'index'
            native.INDEX_DIR=index
            a=types.SimpleNamespace(c=types.SimpleNamespace(root_linux=lambda:None),durable=sync,metrics=metrics)
            value={'schema':native.SCHEMA,'revision':1,'dataset_manifest':{'bytes':1,'sha256':'a'*64},
                   'measurement_profile':native.PROFILE,'source_model_sha256':native.MODEL,
                   'model_manifest_sha256':native.MODEL_MANIFEST,'batches':{},'samples':{},'images_count':0}
            with native.locked(a,create=True):
                receipt=native.persist(a,value)
                descriptor=os.open(index/'index.lock',os.O_RDWR|os.O_NOFOLLOW)
                try:
                    with self.assertRaises(BlockingIOError):native.lock_fd(descriptor,True)
                finally:os.close(descriptor)
            loaded,raw=native.load(a)
            self.assertEqual(receipt,native.snapshot(loaded,raw))
            self.assertEqual(stat.S_IMODE(index.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE((index/'index.json').stat().st_mode),0o600)
            self.assertEqual((index/'index.json').stat().st_uid,0)
            os.link(index/'index.json',index/'extra-link')
            with self.assertRaisesRegex(ValueError,'single-link'):native.load(a)
            (index/'extra-link').unlink()


if __name__=='__main__':unittest.main()
