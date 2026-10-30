"""Pure numerical/record checks for DENSE labeled CPU4/PL acceptance.

No files, devices, subprocesses or mutable global counters are used. NumPy is
imported lazily only by checked_logits/compare. input_sha256 denotes the whole
input .npy FILE; CPU_logits_sha256/PL_logits_sha256 denote 4000 raw FP32 bytes.
Aggregate verifies recorded evidence, not the logits' production or hardware.
"""
from __future__ import annotations

import hashlib
import math
from numbers import Integral, Real
import re

COMPARISON_SCHEMA = 'opt50_dense_logits_comparison_v1'
RECORD_SCHEMA = 'opt50_dense_accuracy_record_v1'
AGGREGATE_SCHEMA = 'opt50_dense_accuracy_aggregate_v1'
CLASSES = 1000
REQUIRED_RECORD = frozenset(('ordinal', 'id', 'label', 'image_sha256', 'input_sha256',
    'CPU_logits_sha256', 'PL_logits_sha256', 'CPU_top5', 'PL_top5', 'bitwise_mismatches',
    'max_abs_difference', 'CPU_reference_ms', 'inference_ms', 'completed_PL_calls',
    'Dense_calls', 'Sparse_calls', 'owner_exit_code', 'device_closed',
    'postrun_buffer_checks_verified', 'native_END_verified'))


def need(ok, message):
    if not ok:
        raise ValueError(message)


def integer(value, name, low=0, high=None):
    need(type(value) is int and value >= low and (high is None or value <= high),
         name+' must be a bounded JSON integer')
    return value


def label_value(value):
    need(isinstance(value, Integral) and not isinstance(value, bool) and 0 <= value < CLASSES,
         'label must be a zero-based model output index 0..999')
    return int(value)


def sha_value(value, name):
    need(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), name+' must be a lowercase SHA256')
    return value


def checked_logits(value):
    import numpy as np
    need(isinstance(value, np.ndarray) and value.dtype == np.dtype('float32') and
         value.shape == (1, CLASSES) and bool(np.isfinite(value).all()),
         'finite FP32 logits with exact shape [1,1000] required; no implicit cast')
    return np.ascontiguousarray(value)


def compare(cpu, pl, label):
    """Compare every raw bit and independently verify stable-sort true-label rank."""
    import numpy as np
    label = label_value(label)
    cpu, pl = checked_logits(cpu), checked_logits(pl)
    result = {'schema': COMPARISON_SCHEMA, 'label': label, 'compared_logits': CLASSES}
    ids = np.arange(CLASSES)
    for name, values in (('CPU', cpu), ('PL', pl)):
        row = values[0]
        order = np.argsort(-row, kind='stable')
        rank = 1+int(np.count_nonzero(row > row[label]))+int(np.count_nonzero((row == row[label]) & (ids < label)))
        need(rank == int(np.flatnonzero(order == label)[0])+1, 'independent true-label rank disagrees with stable sorting')
        top5 = [int(i) for i in order[:5]]
        need((top5[0] == label) == (rank == 1) and (label in top5) == (rank <= 5),
             'independent Top1/Top5 membership disagrees')
        result.update({name+'_logits_sha256': hashlib.sha256(values.tobytes(order='C')).hexdigest(),
                       name+'_top5': top5, name+'_true_label_rank': rank,
                       name+'_top1_hit': rank == 1, name+'_top5_hit': rank <= 5})
    cb, pb = cpu.view(np.uint32).ravel(), pl.view(np.uint32).ravel()
    mismatch = np.flatnonzero(cb != pb)
    result.update(bitwise_mismatches=int(mismatch.size),
                  max_abs_difference=float(np.max(np.abs(cpu.astype(np.float64)-pl.astype(np.float64)))),
                  status='NUMERICAL_PASS' if mismatch.size == 0 else 'NUMERICAL_FAIL',
                  first_differences=[{'index': int(i), 'CPU_bits': format(int(cb[i]), '08x'),
                      'PL_bits': format(int(pb[i]), '08x'), 'CPU_value': float(cpu[0, i]),
                      'PL_value': float(pl[0, i])} for i in mismatch[:8]])
    return result


def _sample(value):
    need(type(value) is dict, 'frozen expected sample must be an object')
    need(type(value.get('id')) is str and 0 < len(value['id']) <= 256 and
         not any(c in value['id'] for c in '\0\r\n'), 'nonempty bounded sample id required')
    need(type(value.get('image')) is dict and type(value.get('input')) is dict,
         'frozen expected image/input pins must be objects')
    return {'ordinal': integer(value.get('ordinal'), 'ordinal'), 'id': value['id'],
            'label': integer(value.get('label'), 'label', 0, CLASSES-1),
            'image_sha256': sha_value(value.get('image', {}).get('sha256'), 'expected image.sha256'),
            'input_sha256': sha_value(value.get('input', {}).get('sha256'), 'expected input.sha256')}


def _finite_number(value, name):
    need(isinstance(value, Real) and not isinstance(value, bool), name+' must be a JSON number')
    try:
        converted = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(name+' must be finite and nonnegative') from error
    need(math.isfinite(converted) and converted >= 0, name+' must be finite and nonnegative')
    return converted


def validate_record(record, expected_sample=None):
    """Check a completed record; bind labels/images/input FILE pins when supplied.

    Required closure: owner_exit_code=0, device_closed, native_END_verified,
    postrun_buffer_checks_verified; exactly 36 calls, Dense36+Sparse0. Optional
    timing diagnostics do not gate numerical accuracy. Additional provenance
    fields are preserved, but cannot replace any required evidence.
    """
    need(type(record) is dict and REQUIRED_RECORD <= record.keys(), 'incomplete per-image accuracy record')
    need(record.get('schema', RECORD_SCHEMA) == RECORD_SCHEMA, 'accuracy record schema differs; comparison must be wrapped as a record')
    out = dict(record); out['schema'] = RECORD_SCHEMA
    expected = _sample({'ordinal': record['ordinal'], 'id': record['id'], 'label': record['label'],
                        'image': {'sha256': record['image_sha256']}, 'input': {'sha256': record['input_sha256']}})
    if expected_sample is not None:
        need(expected == _sample(expected_sample), 'record image/input/label/id differs from frozen expected sample')
    for name in ('CPU', 'PL'):
        sha_value(record[name+'_logits_sha256'], name+' raw logits SHA')
        top = record[name+'_top5']
        need(type(top) is list and len(top) == 5, name+' Top5 must contain exactly five class indices')
        for value in top:
            integer(value, name+' Top5 index', 0, CLASSES-1)
        need(len(set(top)) == 5, name+' Top5 has duplicate class indices')
        hits = (top[0] == expected['label'], expected['label'] in top)
        for suffix, hit in zip(('top1_hit', 'top5_hit'), hits):
            key = name+'_'+suffix
            if key in record:
                need(type(record[key]) is bool and record[key] == hit, 'recorded '+key+' contradicts Top5/label')
            out[key] = hit
        rank_key = name+'_true_label_rank'
        if rank_key in record:
            rank = integer(record[rank_key], rank_key, 1, CLASSES)
            need((rank == 1) == hits[0] and (rank <= 5) == hits[1] and
                 (not hits[1] or rank == top.index(expected['label'])+1), rank_key+' contradicts Top5')
    count = integer(record['bitwise_mismatches'], 'bitwise_mismatches', 0, CLASSES)
    difference = _finite_number(record['max_abs_difference'], 'max_abs_difference')
    if count == 0:
        need(record['CPU_logits_sha256'] == record['PL_logits_sha256'] and difference == 0 and
             record['CPU_top5'] == record['PL_top5'], 'zero bit mismatches contradict SHA/Top5/difference')
    else:
        need(record['CPU_logits_sha256'] != record['PL_logits_sha256'], 'positive bit mismatches contradict equal raw SHA')
    if 'status' in record:
        need(record['status'] == ('NUMERICAL_PASS' if count == 0 else 'NUMERICAL_FAIL'), 'record numerical status contradicts bit count')
    for name, exact in (('completed_PL_calls', 36), ('Dense_calls', 36), ('Sparse_calls', 0), ('owner_exit_code', 0)):
        need(integer(record[name], name) == exact, name+' differs from the closed 36-call D36/S0 contract')
    for name in ('device_closed', 'postrun_buffer_checks_verified', 'native_END_verified'):
        need(record[name] is True, 'completed closed proof required: '+name)
    for name, exact in (('worker_runner_calls', 1), ('compared_logits', CLASSES)):
        if name in record:
            need(integer(record[name], name) == exact, name+' differs from the single-image comparison contract')
    if 'native_END_completed' in record:
        need(record['native_END_completed'] is True, 'contradictory native END evidence')
    for name in ('hot_path_disk_events', 'graph_owner_sync_calls'):
        if name in record:
            integer(record[name], name)
    for name in ('CPU_reference_ms', 'inference_ms'):
        out[name] = _finite_number(record[name], name)
    return out


def aggregate(records, expected_count=1800, expected_samples=None):
    """Aggregate only complete contiguous closed records; never skip bad samples.

    expected_samples must be the frozen manifest's ordered samples with nested
    image.sha256/input.sha256. Without it the result is IDENTITY_UNBOUND, never
    NUMERICAL_PASS. Contiguous selected subsets retain global dataset ordinals.
    Missing/duplicate/malformed/unclosed records raise ValueError.
    Authenticating the frozen dataset/archive is the caller's responsibility.
    """
    integer(expected_count, 'expected_count', 1)
    rows = list(records)
    need(len(rows) == expected_count, 'sample count incomplete or excessive; denominator must not shrink')
    targets = None if expected_samples is None else list(expected_samples)
    need(targets is None or len(targets) == expected_count, 'frozen expected sample count differs')
    start = 0 if targets is None else _sample(targets[0])['ordinal']
    seen_id, seen_image = set(), set()
    checked = []
    for index, row in enumerate(rows):
        expected = None if targets is None else targets[index]
        item = validate_record(row, expected)
        need(item['ordinal'] == start+index, 'sample ordinal must be contiguous in frozen manifest order')
        need(item['id'] not in seen_id and item['image_sha256'] not in seen_image,
             'duplicate sample id or encoded image in completed records')
        seen_id.add(item['id']); seen_image.add(item['image_sha256']); checked.append(item)
    mismatch_images = sum(item['bitwise_mismatches'] != 0 for item in checked)
    bound = targets is not None
    numerical = 'NUMERICAL_PASS' if mismatch_images == 0 else 'NUMERICAL_FAIL'
    accuracy = {}
    for name in ('CPU', 'PL'):
        c1 = sum(item[name+'_top1_hit'] for item in checked)
        c5 = sum(item[name+'_top5_hit'] for item in checked)
        accuracy[name] = {'images': expected_count, 'top1_correct': c1, 'top5_correct': c5,
                          'top1_pct': 100*c1/expected_count, 'top5_pct': 100*c5/expected_count}
    diagnostics = {name: (sum(item[name] for item in checked) if all(name in item for item in checked) else None)
                   for name in ('hot_path_disk_events', 'graph_owner_sync_calls')}
    return {'schema': AGGREGATE_SCHEMA, 'status': numerical if bound else 'IDENTITY_UNBOUND',
            'numerical_status': numerical, 'dataset_binding_verified': bound,
            'full_1800_acceptance': bound and start == 0 and expected_count == 1800 and numerical == 'NUMERICAL_PASS',
            'ordinal_start': start, 'ordinal_stop_exclusive': start+expected_count,
            'images': expected_count, 'classes_ranked': CLASSES, 'true_label_classes': len({r['label'] for r in checked}),
            'compared_logits': CLASSES*expected_count, 'bitwise_mismatch_images': mismatch_images,
            'bitwise_mismatches': sum(item['bitwise_mismatches'] for item in checked),
            'max_abs_difference': max(item['max_abs_difference'] for item in checked),
            'accuracy': accuracy, 'completed_PL_calls': 36*expected_count,
            'Dense_calls': 36*expected_count, 'Sparse_calls': 0*expected_count,
            'all_devices_closed': True, 'all_native_END_verified': True, 'all_postrun_buffer_checks_verified': True,
            'CPU_reference_ms_sum': sum(item['CPU_reference_ms'] for item in checked),
            'inference_ms_sum': sum(item['inference_ms'] for item in checked),
            'input_sha256_scope': 'Whole input .npy FILE bytes; not raw tensor SHA',
            'logits_sha256_scope': 'Raw C-contiguous FP32 [1,1000] bytes, 4000 bytes each',
            'topk_policy': 'All 1000 classes; descending score; ties by lower output index',
            'historical_B_CPU1_equivalence': 'NOT_CLAIMED', 'performance_verified': False,
            'optional_diagnostics': diagnostics,
            'scope': 'Recorded per-image CPU4/PL bit comparison and independent labeled accuracy; no logits or hardware recomputation'}
