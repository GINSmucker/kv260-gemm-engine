"""Exact target, denominator, lifecycle grouping and real export/archive tests."""
import copy
import csv
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).absolute().parents[1]
spec=importlib.util.spec_from_file_location('report500_test',ROOT/'report500.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def fixture():
    rows=[]
    for ordinal in range(500):
        label=ordinal%250
        top=[label,(label+1)%1000,(label+2)%1000,(label+3)%1000,(label+4)%1000]
        if ordinal%2:top=[top[1],top[2],top[0],top[3],top[4]]
        lifecycle='fresh-owner-per-image' if ordinal<45 else 'resident-bounded-batch-v4'
        revision='1'*64 if ordinal<45 else '2'*64 if ordinal<100 else '3'*64
        rows.append({'ordinal':ordinal,'id':'image_%04d'%ordinal,'label':label,'CPU_top5':top,'PL_top5':top.copy(),
            'CPU_top1_hit':ordinal%2==0,'PL_top1_hit':ordinal%2==0,'CPU_top5_hit':True,'PL_top5_hit':True,
            'bitwise_mismatches':0,'max_abs_difference':0.0,'image_sha256':format(ordinal,'064x'),
            'input_sha256':format(ordinal+500,'064x'),'CPU_logits_sha256':format(ordinal+1000,'064x'),
            'PL_logits_sha256':format(ordinal+1000,'064x'),'inference_ms':130.0+ordinal%9,
            'CPU_reference_ms':300.0+ordinal%7,'measurement_profile':r.PROFILE,'lifecycle_profile':lifecycle,
            'software_revision':revision,'ORT_intra_threads':4,'ORT_inter_threads':1,
            'completed_PL_calls':36,'Dense_calls':12,'Sparse_calls':24,
            'plan_path':'/original/plan_%02d/plan.json'%(ordinal//50),'sample_dir':'/original/sample_%06d'%ordinal,
            'image_path':'/original/image.jpg','input_path':'/original/input.npy',
            'cpu_logits_path':'/original/cpu_reference.npy','pl_logits_path':'/original/model_logits.npy'})
    result={'images':500,'compared_logits':500000,'completed_PL_calls':18000,'Dense_calls':6000,'Sparse_calls':12000,
        'ordinal_start':0,'ordinal_stop_exclusive':500,'all_devices_closed':True,'all_native_END_verified':True,
        'all_postrun_buffer_checks_verified':True,'dataset_binding_verified':True,'bitwise_mismatches':0,
        'bitwise_mismatch_images':0,'max_abs_difference':0.0,'numerical_status':'NUMERICAL_PASS',
        'true_label_classes':250,'full_1800_acceptance':False,
        'accuracy':{device:{'images':500,'top1_correct':250,'top5_correct':500,'top1_pct':50.0,'top5_pct':100.0}
                    for device in ('CPU','PL')}}
    review={'result':result,'all_saved_logits_recomputed':True,'hardware_accessed_by_review':False,
            'source_model_sha256':'a'*64,'dataset_manifest':{'bytes':99,'sha256':'b'*64}}
    return rows,review


class CounterAndGroupingTests(unittest.TestCase):
    def test_exact500_has_completed_target_but_no_full1800_or_all_correct_claim(self):
        rows,review=fixture();out=r.summarize(rows,review)
        self.assertTrue(out['target_500_complete'] and out['subset500_accuracy_computed'])
        self.assertFalse(out['full_1800_acceptance'] or out['ImageNet_full_validation'] or out['all_images_classified_correctly_claimed'])
        self.assertEqual(out['compared_FP32_values_bitwise'],500000)
        self.assertEqual((out['PL_calls'],out['Dense_calls'],out['Sparse_calls']),(18000,6000,12000))
        self.assertEqual(out['actual_true_label_classes'],250)
        self.assertEqual(out['accuracy']['PL']['top1_correct'],250)
        self.assertEqual(out['classes_ranked_per_image'],1000)
    def test_missing_duplicate_or_excess_images_never_shrink_denominator(self):
        rows,review=fixture()
        for bad in (rows[:-1],rows+[rows[-1]],rows[:-1]+[rows[0]]):
            with self.assertRaisesRegex(ValueError,'exactly ordinals'):r.summarize(bad,review)
    def test_duplicate_jpeg_or_id_is_refused(self):
        for key in ('id','image_sha256'):
            rows,review=fixture();rows[1][key]=rows[0][key]
            with self.assertRaisesRegex(ValueError,'duplicate image'):r.summarize(rows,review)
    def test_old45_and_resident_revisions_are_separate_groups(self):
        rows,review=fixture();out=r.summarize(rows,review)
        self.assertEqual(sorted(group['images'] for group in out['latency_groups']),[45,55,400])
        old=next(group for group in out['latency_groups'] if group['lifecycle_profile']=='fresh-owner-per-image')
        self.assertEqual(old['ordinal_ranges_1based'],[[1,45]])
        self.assertEqual(out['overall_recorded_Runner_run']['samples'],500)
        self.assertIn('not a steady-state',out['latency_scope'])
    def test_reboot_revision_after305_stays_separate_even_with_same_resident_lifecycle(self):
        rows,review=fixture()
        for row in rows[305:]:row['software_revision']='4'*64
        out=r.summarize(rows,review)
        self.assertEqual(sorted(group['images'] for group in out['latency_groups']),[45,55,195,205])
        reboot=next(group for group in out['latency_groups'] if group['software_revision']=='4'*64)
        self.assertEqual(reboot['ordinal_ranges_1based'],[[306,500]])
        self.assertEqual(reboot['Runner_run']['samples'],195)
    def test_timing_percentiles_are_linear_and_setup_is_not_added(self):
        out=r.distribution([1,2,3,4,100]);self.assertEqual(out['median_ms'],3)
        self.assertAlmostEqual(out['p90_ms'],61.6);self.assertAlmostEqual(out['p95_ms'],80.8)
        self.assertEqual(r.distribution([17])['p95_ms'],17)
        rows,review=fixture();rows[0]['setup_ms']=99999999
        self.assertEqual(r.summarize(rows,review)['overall_recorded_Runner_run']['max_ms'],138.0)
    def test_nan_negative_or_boolean_timing_is_refused(self):
        for value in (float('nan'),float('inf'),-1,True):
            with self.assertRaises(ValueError):r.distribution([value])
    def test_unclosed_wrong_thread_or_mismatch_never_passes(self):
        rows,review=fixture();review['result']['all_devices_closed']=False
        with self.assertRaises(ValueError):r.summarize(rows,review)
        rows,review=fixture();rows[0]['ORT_intra_threads']=1
        with self.assertRaisesRegex(ValueError,'thread'):r.summarize(rows,review)
        rows,review=fixture();rows[0]['bitwise_mismatches']=1;review['result']['bitwise_mismatches']=1
        with self.assertRaisesRegex(ValueError,'did not pass'):r.summarize(rows,review)
    def test_ground_truth_counts_are_independent_from_numerical_equivalence(self):
        rows,review=fixture();review['result']['accuracy']['PL']['top1_correct']=500
        with self.assertRaisesRegex(ValueError,'labeled accuracy'):r.summarize(rows,review)
    def test_index_is_only_exact_explicit_plan_discovery(self):
        batches={f'/var/lib/opt50-b24-model-v1/plan_{i:012x}/plan.json':{'ordinals':list(range(i*50,(i+1)*50))} for i in range(10)}
        index={'batches':batches};self.assertEqual(len(r.discover_paths(index)),10)
        broken=copy.deepcopy(index);broken['batches'].pop(next(iter(batches)))
        with self.assertRaisesRegex(ValueError,'no duplicates/gaps'):r.discover_paths(broken)
        broken=copy.deepcopy(index);broken['batches']['duplicate']={'ordinals':[0]}
        with self.assertRaises(ValueError):r.discover_paths(broken)
        broken=copy.deepcopy(index);list(broken['batches'].values())[-1]['ordinals'].append(500)
        with self.assertRaisesRegex(ValueError,'straddles'):r.discover_paths(broken)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.report500-test-',dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.rows,self.review=fixture();self.evidence=[]
        for name,raw,include in (('input.npy',b'large input stays at source',False),
                                  ('cpu_reference.npy',b'CPU logits fixture',True),
                                  ('model_logits.npy',b'PL logits fixture',True)):
            path=self.root/name;path.write_bytes(raw)
            self.evidence.append({'path':str(path),'archive_name':'original/sample/'+name,
                                  **r.pin(raw),'included_in_optional_archive':include})
    def test_real_csv_and_checksums_and_archive_exclude_input_and_preserve_sources(self):
        before={p['path']:Path(p['path']).read_bytes() for p in self.evidence}
        out=self.root/'export';result=r.export(out,self.rows,self.review,self.evidence,{'explicit_plans':['fixture']},True)
        self.assertEqual(result['images'],500)
        with (out/'per_image.csv').open(newline='',encoding='utf-8') as stream:csv_rows=list(csv.DictReader(stream))
        self.assertEqual(len(csv_rows),500);self.assertEqual(json.loads(csv_rows[0]['CPU_top5']),self.rows[0]['CPU_top5'])
        for line in (out/'SHA256SUMS').read_text().splitlines():
            digest,name=line.split('  ',1);self.assertEqual(r.pin((out/name).read_bytes())['sha256'],digest)
        self.assertEqual(result['archive_sha256'],r.pin((out/'evidence.tar.gz').read_bytes())['sha256'])
        with tarfile.open(out/'evidence.tar.gz','r:gz') as tar:
            names=tar.getnames();self.assertNotIn('original/sample/input.npy',names)
            self.assertIn('original/sample/cpu_reference.npy',names);self.assertIn('report/summary.json',names)
            self.assertTrue(all(member.mtime==0 and not member.issym() for member in tar.getmembers()))
        self.assertEqual(before,{name:Path(name).read_bytes() for name in before})
    def test_existing_export_is_never_overwritten(self):
        out=self.root/'export';out.mkdir();(out/'keep').write_text('unchanged')
        with self.assertRaisesRegex(ValueError,'never overwrite'):r.export(out,self.rows,self.review,[],{},False)
        self.assertEqual((out/'keep').read_text(),'unchanged')
    def test_changed_backup_bytes_fail_without_original_modification(self):
        (self.root/'cpu_reference.npy').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError,'evidence changed'):r.export(self.root/'export',self.rows,self.review,self.evidence,{},True)
        self.assertEqual((self.root/'cpu_reference.npy').read_bytes(),b'changed source')
        self.assertFalse((self.root/'export/evidence.tar.gz.sha256').exists())
    def test_fixed_export_parent_can_be_created_without_chmodding_original_records(self):
        parent=self.root/'public_exports'
        with patch.object(r,'EXPORT_ROOT',parent):result=r.export(parent/'action_token',self.rows,self.review,[],{},False)
        self.assertTrue(parent.is_dir());self.assertIsNone(result['archive'])


class ReleasedReviewDelegationTests(unittest.TestCase):
    def test_collect_calls_original_full_logit_review_and_index_only_discovers_paths(self):
        with tempfile.TemporaryDirectory(prefix='.report-review-',dir=ROOT) as temporary:
            records=Path(temporary)/'records';root=records/'plan_000000000001';root.mkdir(parents=True)
            def put(path,raw):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            rows,_=fixture();rows=rows[:2]
            samples=[]
            for row in rows:
                ordinal=row['ordinal'];folder=root/'samples'/('%06d'%ordinal)
                entries={'input.npy':b'input','cpu_reference.npy':b'cpu','model_logits.npy':b'pl'}
                for name,raw in entries.items():put(folder/name,raw)
                put(folder/'commit.json',r.encoded({'files':{name:r.pin(raw) for name,raw in entries.items()}}))
                image=b'JPEG fixture'+str(ordinal).encode();name='images/%06d.jpg'%ordinal;put(root/'dataset'/name,image)
                samples.append({'ordinal':ordinal,'image':{'file':name,**r.pin(image)}})
            dataset=r.encoded({'samples':samples});put(root/'dataset/manifest.json',dataset)
            put(root/'model/manifest.json',b'{}\n')
            plan={'dataset_manifest':r.pin(dataset),'source_model_sha256':'a'*64,'measurement_profile':r.PROFILE,
                  'tool_files':{'manifest':{'sha256':'1'*64}}}
            path=root/'plan.json';put(path,r.encoded(plan))
            original_calls=[]
            def original(base,values):
                original_calls.append((base,len(values)));return [dict(row,independently_recomputed=True) for row in values]
            review=types.SimpleNamespace(metrics=object(),recompute_records=original)
            def released_review(paths,progress=False):
                self.assertEqual(paths,[path]);self.assertTrue(progress)
                review.recompute_records(root,rows)
                return {'chunks':[{'plan':str(path),'plan_pin':r.pin(path.read_bytes())}],
                        'dataset_manifest':r.pin(dataset),'source_model_sha256':'a'*64}
            review.review=released_review
            # No index samples/accuracy/commit cache is supplied: it cannot
            # replace the original release's logit/closure review.
            index=types.SimpleNamespace(load=lambda a:({'batches':{str(path):{'ordinals':[0,1]}}},b'index fixture'))
            with patch.object(r,'COUNT',2),patch.object(r,'RECORDS',records):
                exported,report,evidence,provenance=r.collect(review,index)
            self.assertEqual(original_calls,[(root,2)])
            self.assertIs(review.recompute_records,original)
            self.assertTrue(all(row['independently_recomputed'] for row in exported))
            self.assertEqual(provenance['explicit_plans'],[str(path)])
            self.assertTrue(any(item['path'].endswith('cpu_reference.npy') for item in evidence))
            self.assertFalse(next(item for item in evidence if item['path'].endswith('input.npy'))['included_in_optional_archive'])

    def test_delegate_is_restored_when_released_review_refuses(self):
        review=types.SimpleNamespace(metrics=object(),recompute_records=lambda *args:None)
        original=review.recompute_records
        def refused(*args,**kwargs):raise ValueError('released deep verification refused')
        review.review=refused
        index=types.SimpleNamespace(load=lambda a:({'batches':{'plan':{'ordinals':list(range(500))}}},b'index'))
        with self.assertRaisesRegex(ValueError,'deep verification'):r.collect(review,index)
        self.assertIs(review.recompute_records,original)


if __name__=='__main__':unittest.main()
