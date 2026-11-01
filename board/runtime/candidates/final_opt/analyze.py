"""Close diagnostic wall intervals; nested Native work is never added to its IPC parent."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def need(ok, message):
    if not ok:
        raise ValueError(message)


def family(layer):
    return 'head' if layer == 48 else ('qkv', 'proj', 'fc1', 'fc2')[layer % 4]


def decompose(capture, native):
    need(capture['schema'] == 'opt50_final_capture_v1' and capture['status'] == 'PASS', 'verified capture required')
    need(capture['kind'] == 'instrumented_diagnostic_not_performance', 'diagnostic scope')
    need(capture['run_id'] == native['run_id'] and capture['boot_id'] == native['boot_id'], 'run/boot identity')
    need(capture['source_model_sha256'] == native['source_model_sha256'] ==
         'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43', 'source model identity')
    need(capture['bitwise_mismatches'] == 0 and capture['CPU_logits_sha256'] == capture['logits_sha256'], 'CPU/PL comparison required')
    need(native['completed_PL_calls'] == native['START_commands_issued'] == 36 and
         native['PL_dense'] == 12 and native['PL_sparse'] == 24 and native['native_END_completed'] and
         native['postrun_buffer_checks_verified'], 'complete current-graph INFO required')
    start, end = capture['runner_start_ns'], capture['runner_end_ns']
    need(type(start) is int and type(end) is int and 0 < start < end, 'Runner window')
    events = native['ram_events']
    need(len(events) == 182 and events[0]['phase'] == 'MODEL_BEGIN' and
         events[-1]['phase'] == 'MODEL_END_COMPLETED', 'Native BEGIN/36 calls/END trace')
    event_times = [e['monotonic_ns'] for e in events]
    need(start <= event_times[0] and event_times[-1] <= end and
         all(a < b for a,b in zip(event_times,event_times[1:])), 'Native trace within Runner')
    need(native['postrun_checks_completed_ns'] >= native['postrun_checks_started_ns'] > end,
         'INFO must follow Runner')
    stamps = capture['linear_stamps']
    need(len(stamps) == 49, 'all49 Linear required')
    control = native['diagnostic_control_v1']
    pl_layers = [i for i in range(48) if i % 4 != 1]
    need([v['layer'] for v in control] == pl_layers, 'ordered unique 36 Native controls')
    controls = {v['layer']: v['stamps'] for v in control}
    aggregates = defaultdict(lambda: defaultdict(int))
    totals = defaultdict(int)
    layers = []
    previous = start
    command = 0
    for index, v in enumerate(stamps):
        need(len(v) == 7 and all(type(n) is int and n > 0 for n in v), 'Linear stamp types')
        need(previous <= v[0] <= v[1] <= v[2] <= v[3] <= v[4] <= v[5] <= v[6] <= end,
             'Linear wall intervals must be ordered, disjoint and within Runner')
        previous = v[-1]
        route = 'PL' if index in controls else 'PS'
        parts = {'setup': v[1]-v[0], 'quantization': v[2]-v[1], 'postprocess': v[6]-v[5]}
        child = None
        if route == 'PL':
            parts.update(input_sync=v[3]-v[2], IPC=v[4]-v[3], output_sync=v[5]-v[4])
            c = controls[index]
            need(len(c) == 6 and all(type(n) is int and n >= 0 for n in c), 'control stamp types')
            need(v[3] <= c[0] <= c[1] <= c[2] <= c[3] <= c[4] <= v[4], 'Native control must fit IPC parent')
            group = events[1+5*command:6+5*command]
            command += 1
            phases = ('DMA_ACTIVE_BEFORE_START','START_WRITE_INTENT','START_WRITE_RETURNED',
                      'DMA_COMPLETION_PROVEN','LAYER_COMPLETION_VERIFIED')
            need(tuple(e['phase'] for e in group) == phases and
                 all(e['layer'] == index and e['command'] == command and
                     e['mode'] == int(index%4 in (2,3)) for e in group), 'Native layer/command/mode')
            active, intent, returned, complete, verified = [e['monotonic_ns'] for e in group]
            need(c[1] <= active <= intent <= c[2] <= returned <= c[3] <= complete <= verified <= c[4],
                 'diagnostic and original Native phases disagree')
            child = {'full_start_control': c[2]-c[0], 'start_return_to_completion': c[3]-c[2],
                     'completion_to_return': c[4]-c[3], 'IPC_other': parts['IPC']-(c[4]-c[0]),
                     'pause_calls': c[5]}
        else:
            need(v[2] == v[3] == v[4], 'PS must not claim client sync/IPC')
            parts['CPU_MatMulInteger'] = v[5]-v[2]
        need(sum(parts.values()) == v[-1]-v[0], 'Linear interval closure')
        key = route+'/'+family(index)
        for name, ns in parts.items():
            totals[name] += ns
            aggregates[key][name] += ns
        layers.append({'layer': index, 'route': route, 'family': family(index), 'parts_ns': parts,
                       'native_children_ns': child, 'total_ns': v[-1]-v[0]})
    linear_total = sum(totals.values())
    remaining = end-start-linear_total
    need(remaining >= 0, 'negative uncovered wall time')
    totals['outside_Linear_uncovered'] = remaining
    need(sum(totals.values()) == end-start, 'Runner interval closure')
    return {'status': 'PASS', 'kind': 'instrumented_diagnostic_not_performance', 'runner_ns': end-start,
            'exclusive_parts_ns': dict(totals), 'aggregates_ns': {k:dict(v) for k,v in aggregates.items()},
            'layers': layers, 'uncovered_scope': 'PS non-Linear operators, ORT scheduling/allocation, Python BEGIN/END and uninstrumented control',
            'nested_scope': 'Native control is inside IPC; pause counts are not durations; no pure PL cycles claimed'}


def ort_summary(events):
    """Operator wall durations on each execution thread, with nested kernels excluded."""
    kernels = [e for e in events if e.get('cat') == 'Node' and e.get('ph') == 'X'
               and e.get('name', '').endswith('_kernel_time')]
    by_thread = defaultdict(list)
    for event in kernels:
        need(event['dur'] >= 0, 'negative ORT kernel duration')
        by_thread[str(event['tid'])].append(event)
    result = {}
    for tid, entries in by_thread.items():
        top = []
        for event in sorted(entries, key=lambda e:(e['ts'], -e['dur'])):
            if top and event['ts'] < top[-1]['ts']+top[-1]['dur']:
                need(event['ts']+event['dur'] <= top[-1]['ts']+top[-1]['dur'], 'overlapping non-nested ORT intervals')
                continue
            top.append(event)
        totals = defaultdict(float)
        for e in top:
            totals[e.get('args', {}).get('op_name', e['name'])] += e['dur']*1000
        result[tid] = {'top_level_operator_ns': dict(totals), 'nodes': top}
    return {'threads': result, 'scope': 'Separate ORT profiler wall times; do not add across threads or to Linear/IPC parents; profiler overhead requires an uninstrumented paired baseline'}


def combine_profile(captures, natives, events):
    """Account each profiled graph, anchoring ORT's relative clock with all49 Linear envelopes."""
    summary=ort_summary(events)
    runs=sorted((e for e in events if e.get('ph')=='X' and e.get('name')=='model_run'),key=lambda e:e['ts'])
    need(len(runs)==len(captures)==len(natives),'one ORT model_run per capture')
    records=[]
    for capture,native,run in zip(captures,natives,runs):
        record=decompose(capture,native)
        choices=[]
        for thread in summary['threads'].values():
            nodes=[e for e in thread['nodes'] if run['ts']<=e['ts'] and e['ts']+e['dur']<=run['ts']+run['dur']]
            linear=[e for e in nodes if e['args'].get('op_name') in ('QuantLinear','QuantQKV','QuantLinearPremax')]
            if len(linear)==49:choices.append((nodes,linear))
        need(len(choices)==1,'one main execution thread with all49 Linear required')
        nodes,linear=choices[0]
        # Bounds enforce C++ Compute containment within each matching ORT kernel.
        low=max(v[-1]-int((e['ts']+e['dur'])*1000) for v,e in zip(capture['linear_stamps'],linear))
        high=min(v[0]-int(e['ts']*1000) for v,e in zip(capture['linear_stamps'],linear))
        need(low<=high,'ORT/C++ clock containment bounds do not intersect')
        offset=(low+high)//2
        parts=dict(record['exclusive_parts_ns']);parts.pop('outside_Linear_uncovered')
        node_rows=[]
        for e in nodes:
            op=e['args'].get('op_name')
            if op in ('QuantLinear','QuantQKV','QuantLinearPremax'):continue
            start=offset+int(e['ts']*1000);end=start+int(e['dur']*1000)
            need(capture['runner_start_ns']<=start<=end<=capture['runner_end_ns'],'ORT node within Runner')
            for v in capture['linear_stamps']:
                need(end<=v[0] or start>=v[-1],'non-Linear node overlaps a Linear interval')
            name=e['name']
            if op=='MatMul' and '/attn/' in name:key='attention_QK' if '/MatMul_kernel_time' in name else 'attention_AV'
            elif op=='Softmax':key='attention_Softmax'
            elif op=='Gelu4':key='GELU'
            elif op=='LayerNormalization':key='LayerNorm'
            elif op=='Conv' and '/patch_embed/' in name:key='PatchEmbed_Conv'
            elif op in ('Transpose','AttentionMerge4','QkvLayout4','Reshape','Concat','Slice','Shape','Gather'):key='layout_and_shape'
            else:key='PS_'+str(op)
            parts[key]=parts.get(key,0)+(end-start)
            node_rows.append({'name':name,'operator':op,'category':key,'start_ns':start,'end_ns':end})
        parts['ORT_dispatch_alloc_and_other']=record['runner_ns']-sum(parts.values())
        need(parts['ORT_dispatch_alloc_and_other']>=0,'negative uncovered profiled time')
        need(sum(parts.values())==record['runner_ns'],'profiled exclusive wall closure')
        record.update(exclusive_profiled_parts_ns=parts,ORT_nodes=node_rows,
                      ORT_clock_offset_bounds_ns=[low,high],ORT_clock_alignment_uncertainty_ns=high-low)
        records.append(record)
    return {'status':'PASS','scope':'Only these instrumented complete graphs; profiler overhead is not removed or transferred to the no-profiler baseline',
            'samples':records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--ort', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = decompose(json.loads(args.capture.read_bytes()), json.loads(args.native.read_bytes()))
    if args.ort:
        result['ORT_diagnostic'] = ort_summary(json.loads(args.ort.read_bytes()))
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
