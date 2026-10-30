import copy
import io
import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
import resident_proof as p
import accuracy_metrics as metrics


def performance(run=1,count=5):
    value={'schema':'opt50_performance_native_resident_v4','profile':'b24-resident-low-overhead-v4',
        'boot_id':'b','source_model_sha256':'s','run_id':run,'max_graphs':count,'total_START_budget':36*count,
        'START_commands_issued':36,'completed_PL_calls':36,'PL_dense':12,'PL_sparse':24,
        'device_closed':False,'native_END_completed':True,'guards_verified_after_graph':True,
        'static_weights_verified_after_graph':True,'postrun_buffer_checks_verified':True,
        'graph_owner_sync_calls':0,'graph_owner_sync_bytes':0,'per_layer_input_sha_checks':0,
        'per_layer_guard_checks':0,'per_layer_y_poison_writes':0,'initial_weight_to_device_sync_calls':2,
        'postrun_owner_sync_calls':4,'postrun_guard_checks':4,'postrun_static_weight_sha_checks':2,
        'hot_path_fsync_count':0,'hot_path_disk_events':0,'lifetime_START_commands':36*run,
        'lifetime_completed_graphs':run,'last_verified_run':run,'prior_verified_run':run-1,
        'initial_weight_sync_scope':'owner_lifetime',
        'initial_weight_to_device_sync_bytes':p.allocation(2654208)+p.allocation(331840),
        'postrun_owner_sync_bytes':sum(p.allocation(n) for n in (113472,2654208,331840,453888))}
    rows=[('MODEL_BEGIN',49,0,0)]
    for call,layer in enumerate(p.PL,1): rows.extend((phase,layer,call,int(layer%4 in (2,3))) for phase in p.PHASES)
    rows.append(('MODEL_END_COMPLETED',49,36,0))
    value['ram_events']=[dict(zip(('phase','layer','command','mode'),row),monotonic_ns=i+1) for i,row in enumerate(rows)]
    value.update(postrun_checks_started_ns=183,postrun_checks_completed_ns=184)
    return value


def npy(array,version=None):
    out=io.BytesIO()
    if version is None: np.save(out,array,allow_pickle=False)
    else: np.lib.format.write_array(out,array,version=version,allow_pickle=False)
    return out.getvalue()


class ResidentProofTests(unittest.TestCase):
    def test_three_sequential_native_receipts(self):
        plan={'boot_id':'b','source_model_sha256':'s','sample_count':5}
        for i in range(1,4): self.assertEqual(p.performance(performance(i),plan,i)['completed_PL_calls'],36)

    def test_native_counter_route_time_and_closure_mutations(self):
        plan={'boot_id':'b','source_model_sha256':'s','sample_count':5}
        for key,value in [('device_closed',True),('prior_verified_run',1),('lifetime_START_commands',72),
            ('initial_weight_to_device_sync_calls',4),('postrun_guard_checks',0),('graph_owner_sync_calls',1),
            ('START_commands_issued',True),('postrun_checks_started_ns',180)]:
            changed=performance(); changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):p.performance(changed,plan,1)
        for key,value in [('layer',1),('mode',1),('command',7),('phase','START_WRITE_INTENT'),('monotonic_ns',0)]:
            changed=performance(); changed['ram_events'][1][key]=value
            with self.subTest(event=key),self.assertRaises(ValueError):p.performance(changed,plan,1)

    def test_window_and_boundaries(self):
        plan={'sample_count':5,'created_monotonic_ns':100}
        p.within_window(plan,100); p.within_window(plan,900_000_000_100)
        for stamp in (99,900_000_000_101):
            with self.assertRaises(ValueError):p.within_window(plan,stamp)
        for count in (0,51,True,1.0):
            with self.assertRaises(ValueError):p.budget(count)

    def test_normal_owner_close_does_not_mean_per_image_close(self):
        names=['NATIVE_ENTERED','RAW_PREFLIGHT_PASS','NATIVE_READY',
            'PERFORMANCE_COMPACT_PUBLISHED','PERFORMANCE_COMPACT_PUBLISHED','RESOURCE_CLOSE_INTENT','RESOURCES_CLOSED']
        self.assertEqual(p.owner_close([{'phase':x,'details':('allocated_BO_count=4;' if x == 'RESOURCE_CLOSE_INTENT' else 'released_BO_count=4;' if x == 'RESOURCES_CLOSED' else '')} for x in names],2)['completed_PL_calls'],72)
        for names,count in [(names[:-1],2),(names,1),(names[:-2]+list(reversed(names[-2:])),2)]:
            with self.assertRaises(ValueError):p.owner_close([{'phase':x,'details':('allocated_BO_count=4;' if x == 'RESOURCE_CLOSE_INTENT' else 'released_BO_count=4;' if x == 'RESOURCES_CLOSED' else '')} for x in names],count)

    def test_independent_actual_1000_bits(self):
        a=np.arange(1000,dtype=np.float32).reshape(1,1000); b=a.copy(); b[0,999]+=1
        for version in (None,(2,0)):
            report=metrics.compare(a,b,2)
            got=p.compare_saved(npy(a,version),npy(b,version),{'accuracy':report})
            self.assertEqual(got['bitwise_mismatches'],1)
            self.assertEqual(got['CPU_top5'],report['CPU_top5'])
            report['bitwise_mismatches']=0
            with self.assertRaises(ValueError):p.compare_saved(npy(a),npy(b),{'accuracy':report})

    def test_signed_zero_is_a_bit_difference(self):
        a=np.zeros((1,1000),dtype=np.float32); b=a.copy(); b[0,42]=np.float32(-0.)
        got=p.compare_saved(npy(a),npy(b),{'accuracy':metrics.compare(a,b,0)})
        self.assertEqual(got['bitwise_mismatches'],1); self.assertEqual(got['max_abs_difference'],0)

    def test_invalid_npy_and_nonfinite_refused(self):
        for array in (np.zeros((1000,),dtype=np.float32),np.zeros((1,1000),dtype=np.float64),
                      np.full((1,1000),np.nan,dtype=np.float32),np.zeros((1,999),dtype=np.float32)):
            with self.assertRaises(ValueError):p.npy_logits(npy(array))
        a=npy(np.zeros((1,1000),dtype=np.float32))
        for raw in (a[:-1],a+b'\0',b'not-npy'):
            with self.assertRaises(ValueError):p.npy_logits(raw)


if __name__=='__main__':unittest.main()
