"""Read-only comparison of TWO normally closed same-weight fair arms.

No loader, device, socket, signal, START, lock acquisition or output file is
used. A single pair is a latency observation, not a stable benchmark. The
original unpruned model's accuracy is a separate experiment.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
BOOT = 'f0f2eed8-5820-4df5-ba19-d05527720f97'
PROFILE = 'same-weights-fair-single-shot-v1'
PASS = 'PASS_FAIR_ARM_36_STARTS_SYNTHETIC_BITWISE_SINGLE_SAMPLE'
RECORDS = Path('/var/lib/opt50-fair-fd96539d-v1')
WEIGHTS = '88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6'
REFERENCE = '50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49'
GRAPH = '8a462d73b5e8bb6544f65c6187dc621b560ae1329520f5556430ff42c97f0dfa'
MANIFEST = 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb'
INPUT = '2449003209f501e928cd67ed0335614accf844bdf3432a3e6146b1f836249dc9'
METADATA = '715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551'
META = '93e24c85920cc50453e19c829127d7c9afed9b5de20e2961c207172cb6f7870b'
ACTIVE = {'all_dense': '45d95d610314148a5b63eb9807ce18fe78337df40880cc4c2da29a080a81fb68',
          'mixed': '73986692f0f1a9f5a4eccb8f6f5522ad896c84e5892b0ae0939ce640106cec4d'}
PL = [i for i in range(48) if i % 4 != 1]
SPARSE = {26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47}
PLUGIN_FIELDS = ('quantization', 'client_gemm_or_PS_matmul', 'dequantization', 'custom_op_total')


def need(ok, why):
    if not ok:
        raise ValueError(why)


def positive(value, why, *, zero=False):
    need(type(value) in (int, float) and math.isfinite(value) and
         (value >= 0 if zero else value > 0), why)
    return value


def sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def pin(value):
    need(type(value) is dict and set(value) == {'bytes', 'sha256'} and
         type(value['bytes']) is int and value['bytes'] > 0 and sha(value['sha256']),
         'bounded file identity missing')
    return value


def duration(value, prefix):
    ns = value.get(prefix+'_ns')
    ms = value.get(prefix+'_ms')
    need(type(ns) is int and ns > 0 and positive(ms, 'nonpositive/nonfinite '+prefix) == ns/1_000_000,
         'monotonic duration copies differ: '+prefix)
    return ms


def plugin_intervals(value):
    need(type(value) is dict and value.get('schema') == 'opt50_perf_plugin_timing_v1',
         'plugin timing contract missing')
    status = value.get('status')
    if status in ('UNKNOWN_NOT_EXPORTED', 'UNKNOWN_CAPTURE_FAILED'):
        need(value.get('aggregate_ns') is None and value.get('layers') is None,
             'unavailable plugin timing contains measurements')
        return {'status': status, 'aggregate_ms': None, 'PS_13_Linear_ms': None}
    need(status == 'AVAILABLE_PLUGIN_MEMORY_ONLY' and
         value.get('captured_after_RUN_before_close') is True and
         type(value.get('hardware_commands_issued')) is int and value['hardware_commands_issued'] == 0,
         'plugin timing is not a post-run memory-only observation')
    aggregate, layers = value.get('aggregate_ns'), value.get('layers')
    need(type(aggregate) is dict and set(aggregate) == set(PLUGIN_FIELDS) and
         all(type(aggregate[key]) is int and aggregate[key] >= 0 for key in PLUGIN_FIELDS) and
         type(layers) is list and len(layers) == 49 and
         value.get('raw_aggregate_u64') == [aggregate[key] for key in PLUGIN_FIELDS],
         'plugin aggregate/raw/layer inventory differs')
    raw, totals, ps = [], dict.fromkeys(PLUGIN_FIELDS, 0), dict.fromkeys(PLUGIN_FIELDS, 0)
    for index, row in enumerate(layers):
        need(type(row) is dict and row.get('layer_index') == index and type(row['layer_index']) is int and
             row.get('route') == ('PL' if index in PL else 'PS'), 'plugin layer route/order differs')
        numbers = [row.get(key+'_ns') for key in PLUGIN_FIELDS]
        need(all(type(n) is int and n >= 0 for n in numbers) and sum(numbers[:3]) <= numbers[3],
             'plugin layer timing nesting differs')
        raw.extend(numbers)
        for key, ns in zip(PLUGIN_FIELDS, numbers):
            totals[key] += ns
            if index not in PL:
                ps[key] += ns
    need(value.get('raw_layers_u64') == raw and totals == aggregate,
         'plugin aggregate/per-layer timing copies differ')
    return {'status': status, 'aggregate_ms': {key: ns/1_000_000 for key, ns in aggregate.items()},
            'PS_13_Linear_ms': {key: ns/1_000_000 for key, ns in ps.items()}}


def native_intervals(compact, native, mode):
    expected_dense, expected_sparse = (36, 0) if mode == 'all_dense' else (24, 12)
    need(type(compact) is dict and compact.get('schema') == 'opt50_fair_native_v1' and
         compact.get('profile') == PROFILE and compact.get('boot_id') == BOOT and
         compact.get('execution_mode') == mode and compact.get('device_closed') is False and
         native.get('device_closed') is True, 'fair native identity/closed proof differs')
    for value in (compact, native):
        counts = {'START_commands_issued': 36, 'completed_PL_calls': 36,
                  'PL_dense': expected_dense, 'PL_sparse': expected_sparse,
                  'active_data_capacity_bytes': 3981312, 'graph_owner_sync_calls': 0,
                  'hot_path_disk_events': 0}
        need(all(type(value.get(key)) is int and value[key] == number for key, number in counts.items()) and
             value.get('execution_mode') == mode and value.get('active_data_sha256') == ACTIVE[mode] and
             value.get('active_metadata_sha256') == META and
             value.get('native_END_completed') is True and value.get('postrun_buffer_checks_verified') is True and
             value.get('guards_verified_after_graph') is True and value.get('static_weights_verified_after_graph') is True,
             'fair route/active payload/postrun checks differ')
    need(type(compact.get('poll_delay_us')) is int and compact['poll_delay_us'] == 50 and
         type(compact.get('deadline_seconds')) is int and compact['deadline_seconds'] == 120,
         'poll/deadline contract differs')
    events = compact.get('ram_events')
    need(type(events) is list and len(events) == 182, 'exact 182-event native proof required')
    expected = [('MODEL_BEGIN', 49, 0)]
    for command, layer in enumerate(PL, 1):
        expected.extend((phase, layer, command) for phase in ('DMA_ACTIVE_BEFORE_START', 'START_WRITE_INTENT',
            'START_WRITE_RETURNED', 'DMA_COMPLETION_PROVEN', 'LAYER_COMPLETION_VERIFIED'))
    expected.append(('MODEL_END_COMPLETED', 49, 36))
    last = -1
    for row, (phase, layer, command) in zip(events, expected):
        need(type(row) is dict and set(row) == {'phase', 'layer', 'command', 'mode', 'monotonic_ns'} and
             (row['phase'], row['layer'], row['command']) == (phase, layer, command) and
             type(row['layer']) is int and type(row['command']) is int and type(row['mode']) is int and
             row['mode'] == int(mode == 'mixed' and layer in SPARSE) and
             type(row['monotonic_ns']) is int and row['monotonic_ns'] > last,
             'native RAM event order/mode/clock differs')
        last = row['monotonic_ns']
    started, finished = compact.get('postrun_checks_started_ns'), compact.get('postrun_checks_completed_ns')
    need(type(started) is int and type(finished) is int and last < started < finished,
         'postrun checks were not outside model END')
    all_wait, selected_wait = 0, 0
    for index, layer in enumerate(PL):
        chain = events[1+5*index:1+5*(index+1)]
        ns = chain[3]['monotonic_ns']-chain[2]['monotonic_ns']
        all_wait += ns
        if layer in SPARSE:
            selected_wait += ns
    native_ms = (events[-1]['monotonic_ns']-events[0]['monotonic_ns'])/1_000_000
    need(native.get('native_model_interval_ms') == native_ms and
         native.get('postrun_buffer_checks_ms') == (finished-started)/1_000_000,
         'native summary/timestamp copies differ')
    return {'native_BEGIN_to_END_ms': native_ms,
            'sum_START_returned_to_completion_ms_all36': all_wait/1_000_000,
            'sum_START_returned_to_completion_ms_selected12': selected_wait/1_000_000,
            'postrun_buffer_checks_ms_excluded': (finished-started)/1_000_000,
            'scope': 'Host-observed register return through DONE/idle/drain detection; includes polling and observer overhead; NOT pure PL cycles or independent hardware START count'}


def validate_arm(plan, report, compact, mode):
    need(type(plan) is dict and type(report) is dict and
         plan.get('schema') == 'opt50_fair_model_plan_v1' and plan.get('boot_id') == BOOT and
         plan.get('measurement_profile') == PROFILE and plan.get('execution_mode') == mode and
         type(plan.get('token')) is str and re.fullmatch('[0-9a-f]{12}', plan['token']) and
         plan.get('expected_PL_starts') == 36 and plan.get('run_id') == 1,
         'fair plan identity/mode differs')
    need(report.get('schema') == 'opt50_fair_model_completion_v1' and report.get('boot_id') == BOOT and
         report.get('plan_token') == plan['token'] and report.get('execution_mode') == mode and
         report.get('measurement_profile') == PROFILE and report.get('single_sample_only') is True and
         report.get('accuracy_verified') is False and report.get('performance_verified') is False and
         type(report.get('owner_exit_code')) is int and report['owner_exit_code'] == 0 and
         type(report.get('owner_pid')) is int and report['owner_pid'] > 1,
         'normal closed single-sample completion required')
    files = plan['inputs']['files']
    for name in ('input_zero.npy', 'native_owner', 'plugin.so', 'metadata.xclbin', 'model/weights.bin',
                 'model/manifest.json', 'model/reference.onnx', 'model/graphs/model_w8a8_pl.onnx'):
        pin(files.get(name))
    need(files['input_zero.npy']['sha256'] == INPUT and files['model/weights.bin']['sha256'] == WEIGHTS and
         files['model/manifest.json']['sha256'] == MANIFEST and files['model/reference.onnx']['sha256'] == REFERENCE and
         files['model/graphs/model_w8a8_pl.onnx']['sha256'] == GRAPH and files['metadata.xclbin']['sha256'] == METADATA and
         plan.get('input_pin') == files['input_zero.npy'] and plan.get('plugin_pin') == files['plugin.so'] and
         plan.get('model_manifest_pin') == files['model/manifest.json'] and
         plan.get('logical_weights_sha256') == WEIGHTS and plan.get('reference_sha256') == REFERENCE,
         'same frozen input/model/metadata identity required')
    need(plan['source_summary']['source_release'] == plan['inputs']['source_release'] and
         plan['source_summary']['board_build'] == plan['inputs']['board_build'] and
         plan['inputs']['board_build']['binaries']['opt50_model_owner'] == files['native_owner'] and
         plan['inputs']['board_build']['binaries']['libopt50_model.so'] == files['plugin.so'],
         'same compiled owner/plugin/source evidence required')
    pin(plan['inputs']['source_release'])
    pin(plan['inputs']['board_build']['receipt'])
    pin(plan['tool_files']['manifest'])
    need(type(plan['tool_files'].get('files')) is dict and
         {'run_model.py', 'model_worker.py', 'perf_proof.py', 'compare.py', 'resident.py'} <= set(plan['tool_files']['files']),
         'frozen comparison/coordinator tool inventory missing')
    for value in plan['tool_files']['files'].values():
        pin(value)
    cpu, worker = report['CPU_reference'], report['worker']
    for value in (cpu, worker):
        need(value.get('boot_id') == BOOT and value.get('plan_token') == plan['token'] and
             value.get('execution_mode') == mode and value.get('measurement_profile') == PROFILE and
             value.get('single_sample_only') is True and value.get('performance_verified') is False and
             value.get('input_sha256') == INPUT and value.get('reference_sha256') == REFERENCE and
             value.get('logical_weights_sha256') == WEIGHTS and value.get('model_manifest_sha256') == MANIFEST and
             sha(value.get('logits_raw_sha256')), 'CPU/model result identity differs')
    need(cpu.get('numpy_version') == '1.26.4' and cpu.get('ort_version') == '1.22.1' and
         cpu.get('CPU_session_closed') is True and cpu.get('shape') == [1, 1000] and cpu.get('dtype') == 'float32' and
         worker.get('custom_graph_sha256') == GRAPH and worker.get('Runner_closed') is True and
         worker.get('runner_calls') == 1 and worker.get('compared_logits') == 1000 and
         type(worker.get('bitwise_mismatches')) is int and worker['bitwise_mismatches'] == 0 and
         positive(worker.get('max_abs_difference'), 'invalid max difference', zero=True) == 0 and
         worker.get('logits_raw_sha256') == cpu['logits_raw_sha256'] and
         worker.get('accuracy_verified') is False,
         '1000 exact finite-model comparison/closed CPU and Runner required')
    runner_ms = duration(worker, 'runner_run')
    setup_ms, cpu_ms, cpu_setup_ms = duration(worker, 'setup'), duration(cpu, 'reference'), duration(cpu, 'reference_setup')
    need(report.get('latency_ms') == runner_ms and report.get('CPU_reference_ms') == cpu_ms and
         report.get('Runner_setup_ms') == setup_ms and report.get('CPU_reference_setup_ms') == cpu_setup_ms and
         report.get('latency_scope') == worker.get('timing_scope') and
         type(worker.get('timing_scope')) is str and bool(worker['timing_scope']) and
         report.get('plugin_timing') == worker.get('plugin_timing'), 'copied timing/scope fields differ')
    held = report.get('held_after', {})
    guardian = held.get('guardian', {})
    clock = held.get('resident_OS_observations', {}).get('clock_framework', {})
    need(held.get('schema') == 'opt50_fair_resident_review_v1' and
         held.get('status') == 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START' and held.get('boot_id') == BOOT and
         guardian.get('boot_id') == BOOT and type(guardian.get('pid')) is int and
         guardian['pid'] == plan.get('guardian_pid') and type(guardian.get('start_ticks')) is int and
         guardian['start_ticks'] == plan.get('guardian_start_ticks') and guardian['start_ticks'] > 0 and
         guardian.get('old_board_lock_transferred') is False and held.get('old_board_lock_transferred') is False and
         held.get('load_plan') == plan.get('load_guardian_plan') and
         type(clock.get('rate_hz')) is int and clock['rate_hz'] in (124999999, 125000000) and
         type(clock.get('enable_count')) is int and clock['enable_count'] > 0 and
         type(clock.get('prepare_count')) is int and clock['prepare_count'] > 0,
         'same held guardian/125MHz OS clock proof missing')
    native = report['native']
    interval = native_intervals(compact, native, mode)
    need(interval['native_BEGIN_to_END_ms'] <= runner_ms, 'native interval lies outside Runner timing')
    owner = report['owner_metrics']
    need(owner.get('completed_PL_calls') == 36 and owner.get('wire_version') == 50 and
         owner.get('layer_call_sequence') == PL and type(owner.get('layers')) is list and len(owner['layers']) == 36,
         'owner ordered 36-call diagnostics required')
    dispatch = positive(owner.get('owner_Run_dispatch_ms'), 'invalid owner dispatch duration')
    need(dispatch <= runner_ms, 'owner dispatch exceeds full Runner duration')
    for row, layer in zip(owner['layers'], PL):
        need(row.get('layer_index') == layer and type(row.get('mode')) is int and
             row['mode'] == int(mode == 'mixed' and layer in SPARSE), 'owner layer mode/order differs')
        positive(row.get('owner_Run_dispatch_ms'), 'invalid per-layer dispatch duration')
    plugin = plugin_intervals(report['plugin_timing'])
    return {'Runner_run_ms': runner_ms, 'CPU_reference_ms': cpu_ms,
            'Runner_setup_ms_excluded': setup_ms, 'CPU_reference_setup_ms_excluded': cpu_setup_ms,
            'owner_Run_dispatch_ms_overlapping': dispatch, 'native_intervals_ms_overlapping': interval,
            'plugin_intervals_ms_overlapping': plugin, 'PL_dense': native['PL_dense'], 'PL_sparse': native['PL_sparse'],
            'PL_clock_hz_OS_observation': clock['rate_hz'], 'logits_raw_sha256': worker['logits_raw_sha256']}


def compare_records(dense_plan, dense_report, mixed_plan, mixed_report, *, dense_compact, mixed_compact):
    """Pure comparison of already authenticated records; performs no I/O."""
    dense = validate_arm(dense_plan, dense_report, dense_compact, 'all_dense')
    mixed = validate_arm(mixed_plan, mixed_report, mixed_compact, 'mixed')
    need(dense_plan['token'] != mixed_plan['token'], 'two distinct frozen plans required')
    for key in ('boot_id', 'measurement_profile', 'tool_files', 'source_summary', 'interpreter', 'command_tools',
                'input_pin', 'plugin_pin', 'model_manifest_pin', 'reference_sha256', 'logical_weights_sha256',
                'guardian_pid', 'guardian_start_ticks', 'load_guardian_plan'):
        need(dense_plan.get(key) is not None and dense_plan.get(key) == mixed_plan.get(key),
             'paired execution differs in '+key)
    for key in ('files', 'board_build', 'source_release'):
        need(dense_plan['inputs'][key] == mixed_plan['inputs'][key], 'paired build/model input differs: '+key)
    need(dense_report['worker']['timing_scope'] == mixed_report['worker']['timing_scope'] and
         dense['logits_raw_sha256'] == mixed['logits_raw_sha256'] and
         dense_report['worker']['logits_file'] == mixed_report['worker']['logits_file'] and
         dense_report['CPU_reference']['logits_file'] == mixed_report['CPU_reference']['logits_file'],
         'paired scope or exact model output differs')
    dense_ms, mixed_ms = dense['Runner_run_ms'], mixed['Runner_run_ms']
    return {'schema': 'opt50_fair_pair_comparison_v1', 'status': 'PASS_TWO_CLOSED_FAIR_ARMS_SINGLE_SAMPLE_ONLY',
            'measurement_profile': PROFILE, 'boot_id': BOOT,
            'dense_plan_token': dense_plan['token'], 'mixed_plan_token': mixed_plan['token'],
            'same_logical_pruned_weights': True, 'same_binaries_and_tools': True, 'same_firmware_and_BO_capacities': True,
            'single_sample_per_arm': True, 'accuracy_verified': False, 'stable_performance_verified': False,
            'Dense_all36': dense, 'Mixed_24D_12S': mixed,
            'Runner_speedup_Dense_over_Mixed': dense_ms/mixed_ms,
            'Runner_latency_reduction_ms': dense_ms-mixed_ms,
            'Runner_latency_reduction_percent': 100*(dense_ms-mixed_ms)/dense_ms,
            'logical_PL_weight_payload_bytes': {'all_dense': 3981312, 'mixed': 3483648,
                'selected_12_layers_reduction_percent': 37.5, 'all_36_PL_layers_reduction_percent': 12.5,
                'scope': 'Encoded Wdata+used metadata bytes, not measured DDR transactions; X/Y and allocated BO sizes unchanged'},
            'scope': 'One zero-input complete-model Runner.run per arm, same quantized/pruned weights and software; NOT original-unpruned accuracy comparison or a steady-state benchmark',
            'timing_warning': 'Plugin, owner and native intervals overlap Runner.run and each other; never add them to total latency. Host completion observations are not pure PL cycles.',
            'sampling_warning': 'No warmup, median, P95 or statistical significance from this pair; negative savings are retained honestly',
            'hardware_or_processes_modified': False}


def read_closed_arm(window, c, path, mode):
    plan = window.load_plan(c, Path(path), fresh=False)
    need(plan['execution_mode'] == mode, 'plan supplied in wrong comparison slot')
    folder = Path(plan['execution'])
    state = c.read_json(folder/'status.json', private=True)
    report = c.read_json(folder/'completion.json', private=True)
    need(state.get('schema') == 'opt50_fair_model_status_v1' and state.get('stage') == PASS and
         state.get('boot_id') == BOOT and state.get('unit') == plan['unit'] and state.get('execution_mode') == mode and
         state.get('owner_pid') is None and state.get('child_pid') is None and state.get('completion') == report,
         'closed successful status/completion differ')
    paths = sorted((folder/'events').glob('event_*.json'))
    need(bool(paths), 'parent durable journal missing')
    records = []
    for index, event_path in enumerate(paths, 1):
        row = c.read_json(event_path, private=True)
        need(event_path.name == 'event_%06d.json' % index and row.get('schema') == 'opt50_durable_event_v1' and
             type(row.get('sequence')) is int and row['sequence'] == index, 'parent journal sequence differs')
        records.append(row)
    need(records[-1].get('action') == 'state' and records[-1].get('state') == state,
         'last durable parent record differs from final status')
    cpu = window.validate_cpu(c, plan, folder)
    worker = window.validate_worker(c, plan, folder)
    need(report.get('CPU_reference') == cpu and report.get('worker') == worker and
         report.get('native') == window.native_completion(c, folder, closed=True) and
         report.get('held_after') == c.read_json(folder/'held_after.json', private=True) and
         report.get('owner_metrics') == c.read_json(folder/'owner_metrics.json', private=True),
         'saved completion copies differ from authenticated source records')
    module = c.compiled_module(Path(plan['plan_path']).parent/'tools/model_worker.py',
                               plan['tool_files']['files']['model_worker.py']['sha256'])
    for name, result in (('cpu_worker', cpu), ('model_worker', worker)):
        need(c.decode_json(c.read_file(folder/(name+'.stdout'), private=True)) == result,
             'worker stdout/saved receipt differs')
        module.validate_warnings(c.read_file(folder/(name+'.stderr'), private=True))
        exited = [row for row in records if row.get('action') == name and row.get('phase') == 'RETURNED']
        need(len(exited) == 1 and type(exited[0].get('returncode')) is int and exited[0]['returncode'] == 0 and
             all(exited[0].get(suffix) == c.identity(c.read_file(folder/(name+'.'+suffix), private=True))
                 for suffix in ('stdout', 'stderr')), 'worker reaped-zero-exit/log identity proof missing')
    exited = [row for row in records if row.get('action') == 'owner' and row.get('phase') == 'RETURNED']
    need(len(exited) == 1 and exited[0].get('pid') == report.get('owner_pid') and
         type(exited[0].get('returncode')) is int and exited[0]['returncode'] == 0 and
         c.read_file(folder/'owner.stdout', private=True) ==
         ('KV5_READY %d\nKV5_STOP calls=36\n' % report['owner_pid']).encode() and
         c.read_file(folder/'owner.stderr', private=True) == b'', 'owner normal 36-call exit evidence differs')
    service = window.terminal_service(c, plan, success=True)
    need(c.read_json(folder/'status.json', private=True) == state and
         c.read_json(folder/'completion.json', private=True) == report,
         'comparison records changed during reading')
    return plan, report, c.read_json(folder/'performance_native.json', private=True), service


def compare(dense_path, mixed_path):
    import run_model as window
    c = window.api()
    c.root_linux()
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT,
         'current boot differs; do not reuse these execution records as current-device evidence')
    window.verify_tools(c)
    # Read-only final-journal, native closure, process and service proof for any
    # executed sibling plan. No lock/STOP/recovery action is performed here.
    window.previous_models_clear(c)
    dense = read_closed_arm(window, c, dense_path, 'all_dense')
    mixed = read_closed_arm(window, c, mixed_path, 'mixed')
    result = compare_records(dense[0], dense[1], mixed[0], mixed[1], dense_compact=dense[2], mixed_compact=mixed[2])
    result['plans'] = {'all_dense': str(dense_path), 'mixed': str(mixed_path)}
    result['terminal_services'] = {'all_dense': dense[3], 'mixed': mixed[3]}
    result['saved_evidence_only'] = True
    result['warning'] = 'No fresh CSR inspection, START, retry, unload, clock change, process signal or recovery authorized'
    return result


def main(argv=None):
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dense-plan', required=True, type=Path)
    parser.add_argument('--mixed-plan', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        value = compare(args.dense_plan, args.mixed_plan)
    except Exception as error:
        print(json.dumps({'status': 'FAIR_COMPARISON_REFUSED_READ_ONLY',
                          'error': type(error).__name__+': '+str(error),
                          'hardware_or_processes_modified': False}, indent=2))
        return 1
    print(json.dumps(value, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
