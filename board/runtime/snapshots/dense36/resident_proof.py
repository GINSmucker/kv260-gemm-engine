"""Device-free checks of every resident graph and the final real owner close."""
import math
import ast
import hashlib
import struct
import json

PROFILE = 'resident-bounded-batch-v4'
PL = [i for i in range(48) if i % 4 != 1]
PHASES = ('DMA_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED',
          'DMA_COMPLETION_PROVEN', 'LAYER_COMPLETION_VERIFIED')


def need(value, message):
    if not value:
        raise ValueError(message)


def budget(count, completed=0):
    need(type(count) is int and 1 <= count <= 50 and type(completed) is int and
         0 <= completed <= count, 'bounded resident graph budget required')
    return 36*count


def within_window(plan, now_ns):
    budget(plan['sample_count'])
    created = plan.get('created_monotonic_ns')
    need(type(created) is int and created > 0 and type(now_ns) is int and
         0 <= now_ns-created <= 900_000_000_000, 'resident graph admission expired')


def allocation(payload):
    return ((payload+128+4095)//4096)*4096


def performance(value, plan, run_id):
    count = plan['sample_count']; budget(count)
    need(type(run_id) is int and 1 <= run_id <= count, 'resident run id bound')
    fixed = {'schema': 'opt50_performance_native_resident_v4',
             'profile': 'dense36-resident-low-overhead-v4', 'boot_id': plan['boot_id'],
             'source_model_sha256': plan['source_model_sha256'], 'run_id': run_id,
             'max_graphs': count, 'total_START_budget': 36*count,
             'START_commands_issued': 36, 'completed_PL_calls': 36, 'PL_dense': 36,
             'PL_sparse': 0, 'device_closed': False, 'native_END_completed': True,
             'guards_verified_after_graph': True, 'static_weights_verified_after_graph': True,
             'postrun_buffer_checks_verified': True, 'graph_owner_sync_calls': 0,
             'graph_owner_sync_bytes': 0, 'per_layer_input_sha_checks': 0,
             'per_layer_guard_checks': 0, 'per_layer_y_poison_writes': 0,
             'initial_weight_to_device_sync_calls': 2, 'postrun_owner_sync_calls': 4,
             'postrun_guard_checks': 4, 'postrun_static_weight_sha_checks': 2,
             'hot_path_fsync_count': 0, 'hot_path_disk_events': 0,
             'lifetime_START_commands': 36*run_id,
             'lifetime_completed_graphs': run_id, 'last_verified_run': run_id, 'prior_verified_run':run_id-1,
             'initial_weight_sync_scope':'owner_lifetime',
             'initial_weight_to_device_sync_bytes':allocation(3981312)+allocation(64),
             'postrun_owner_sync_bytes':sum(allocation(n) for n in (113472,3981312,64,453888))}
    need(type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v
                                   for k, v in fixed.items()), 'resident native graph counters/identity differ')
    events = value.get('ram_events')
    need(type(events) is list and len(events) == 182, '182 native graph markers required')
    expected = [('MODEL_BEGIN', 49, 0, 0)]
    for call, layer in enumerate(PL, 1):
        expected.extend((phase, layer, call, 0) for phase in PHASES)
    expected.append(('MODEL_END_COMPLETED', 49, 36, 0))
    last = 0
    for row, want in zip(events, expected):
        need(type(row) is dict and tuple(row.get(k) for k in ('phase','layer','command','mode')) == want and
             all(type(row.get(k)) is int for k in ('layer','command','mode','monotonic_ns')) and
             row['monotonic_ns'] > last, 'ordered resident START/return/drain route proof differs')
        last = row['monotonic_ns']
    begin, end = value.get('postrun_checks_started_ns'), value.get('postrun_checks_completed_ns')
    need(type(begin) is int and type(end) is int and last < begin < end,
         'postrun checks must finish after graph END')
    return {'run_id': run_id, 'START_commands_issued': 36, 'completed_PL_calls': 36,
            'PL_dense': 36, 'PL_sparse': 0, 'native_END_verified': True,
            'postrun_buffer_checks_verified': True, 'device_closed': False,
            'physical_PL_starts': 'NOT_INDEPENDENTLY_MEASURED'}


def worker(value, plan, sample, run_id):
    fixed = {'schema':'opt50_dense_resident_worker_result_v4', 'run_id':run_id,
             'sample_ordinal':sample['ordinal'], 'sample_id':sample['id'], 'label':sample['label'],
             'Runner_closed':False, 'CPU_session_closed':False, 'expected_PL_starts':36,
             'compared_logits':1000,'boot_id':plan['boot_id'],'plan_token':plan['token'],
             'source_model_sha256':plan['source_model_sha256'], 'reference_sha256':plan['reference_sha256'],
             'custom_graph_sha256':plan['custom_graph_sha256'],'logical_weights_sha256':plan['logical_weights_sha256'],
             'model_manifest_sha256':plan['model_manifest_sha256'],'measurement_profile':plan['measurement_profile'],
             'resident_profile':PROFILE,'completed_graphs_in_session':run_id,'runner_calls':1,
             'image_sha256':sample['image']['sha256'],'input_sha256':sample['input']['sha256']}
    need(type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v
                                   for k,v in fixed.items()), 'resident worker sample/session identity differs')
    for k in ('CPU_reference_ms','runner_run_ms'):
        need(type(value.get(k)) in (int,float) and math.isfinite(value[k]) and value[k] >= 0,
             'finite resident timing required')
    need(value.get('input_file') == {k:sample['input'][k] for k in ('bytes','sha256')},
         'resident worker input file pin differs')
    need(type(value.get('bitwise_mismatches')) is int and 0 <= value['bitwise_mismatches'] <= 1000,
         'resident bit comparison missing')
    need(value.get('ORT_options') == {'intra_op_threads':4,'inter_op_threads':1,'optimization':'OFF','sequential':True,
         'spinning':0,'log_severity':3,'affinity':[0],'worker_affinities':'2;3;4'}, 'resident ORT configuration differs')
    a=value.get('accuracy')
    need(type(a) is dict and a.get('label') == sample['label'] and a.get('compared_logits') == 1000, 'accuracy binding differs')
    for key in ('bitwise_mismatches','max_abs_difference','CPU_top5','PL_top5'):
        need(a.get(key) == value.get(key),'worker comparison disagrees: '+key)
    need(a.get('CPU_logits_sha256') == value.get('cpu_logits_raw_sha256') and
         a.get('PL_logits_sha256') == value.get('logits_raw_sha256'),'raw logit identity differs')
    return value


def owner_close(events, completed):
    need(type(events) is list and bool(events), 'native journal required')
    phases = [e['phase'] for e in events]
    for name in ('NATIVE_ENTERED','RAW_PREFLIGHT_PASS','NATIVE_READY',
                 'RESOURCE_CLOSE_INTENT','RESOURCES_CLOSED'):
        need(phases.count(name) == 1, 'one real owner lifecycle required: '+name)
    need(phases.index('NATIVE_ENTERED') < phases.index('RAW_PREFLIGHT_PASS') <
         phases.index('NATIVE_READY') < phases.index('RESOURCE_CLOSE_INTENT') <
         phases.index('RESOURCES_CLOSED') == len(phases)-1,
         'owner initialization/normal close order differs')
    need(phases.count('PERFORMANCE_COMPACT_PUBLISHED') == completed,
         'one durable INFO receipt per completed graph required')
    for name,detail in (('RESOURCE_CLOSE_INTENT','allocated_BO_count=4;'),('RESOURCES_CLOSED','released_BO_count=4;')):
        record=next(e for e in events if e['phase'] == name)
        need(record.get('details','').startswith(detail),'all four Native allocations must close')
    return {'device_closed':True,'completed_graphs':completed,'completed_PL_calls':36*completed,
            'native_event_count':len(events),
            'native_journal_sha256':hashlib.sha256(json.dumps(events,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'physical_PL_starts':'NOT_INDEPENDENTLY_MEASURED'}



def npy_logits(raw):
    need(isinstance(raw,bytes) and 10 <= len(raw) <= 8192 and raw[:6] == b'\x93NUMPY','bounded NPY required')
    version=raw[6:8]
    need(version in (b'\x01\x00',b'\x02\x00'),'fixed NPY format required')
    width=2 if version == b'\x01\x00' else 4
    start=8+width; size=int.from_bytes(raw[8:start],'little')
    need(0 < size <= 1024 and start+size+4000 == len(raw),'NPY payload size differs')
    header=ast.literal_eval(raw[start:start+size].decode('ascii').strip())
    need(type(header) is dict and set(header) == {'descr','fortran_order','shape'} and
         header['descr'] == '<f4' and header['fortran_order'] is False and header['shape'] == (1,1000),
         'FP32 C-order [1,1000] NPY required')
    payload=raw[start+size:]; values=struct.unpack('<1000f',payload)
    need(all(math.isfinite(v) for v in values),'finite logits required')
    return payload,values


def compare_saved(cpu_raw,pl_raw,worker):
    cb,cv=npy_logits(cpu_raw); pb,pv=npy_logits(pl_raw)
    checks={'CPU_logits_sha256':hashlib.sha256(cb).hexdigest(),'PL_logits_sha256':hashlib.sha256(pb).hexdigest(),
        'CPU_top5':sorted(range(1000),key=lambda i:(-cv[i],i))[:5],
        'PL_top5':sorted(range(1000),key=lambda i:(-pv[i],i))[:5],
        'bitwise_mismatches':sum(cb[i:i+4] != pb[i:i+4] for i in range(0,4000,4)),
        'max_abs_difference':max(abs(a-b) for a,b in zip(cv,pv))}
    for key,value in checks.items():
        need(worker['accuracy'].get(key) == value,'independent 1000-logit comparison differs: '+key)
    return checks
