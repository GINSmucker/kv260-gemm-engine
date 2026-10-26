"""Read-only proof for a bounded same-weight arm; no command/device/socket I/O.

Only the fair schema and current boot are interpreted. Historical boot records
stay archived. File consumers require the execution_mode frozen by the caller.
"""
from pathlib import Path

BOOT = 'f0f2eed8-5820-4df5-ba19-d05527720f97'
PROFILE = 'same-weights-fair-single-shot-v1'
PL = [i for i in range(48) if i % 4 != 1]
SPARSE = {26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47}
DATA_CAPACITY = 3981312
META_SHA = '93e24c85920cc50453e19c829127d7c9afed9b5de20e2961c207172cb6f7870b'
# Derived independently from the frozen original catalog/canonical Wq:
# Dense = all 36 PL slices; mixed = original packed data plus zero capacity tail.
ACTIVE_DATA_SHA = {
    'all_dense': '45d95d610314148a5b63eb9807ce18fe78337df40880cc4c2da29a080a81fb68',
    'mixed': '73986692f0f1a9f5a4eccb8f6f5522ad896c84e5892b0ae0939ce640106cec4d',
}


def need(ok, why):
    if not ok:
        raise ValueError(why)


def route_counts(execution_mode):
    need(type(execution_mode) is str and execution_mode in ACTIVE_DATA_SHA,
         'explicit all_dense or mixed execution_mode required')
    return (36, 0) if execution_mode == 'all_dense' else (24, 12)


def layer_mode(execution_mode, layer):
    route_counts(execution_mode)
    return int(execution_mode == 'mixed' and layer in SPARSE)


def compact(value, expected_execution=None):
    required = {'schema', 'profile', 'boot_id', 'run_id', 'execution_mode',
        'active_data_sha256', 'active_data_capacity_bytes', 'active_metadata_sha256',
        'START_commands_issued', 'completed_PL_calls', 'PL_dense', 'PL_sparse',
        'device_closed', 'native_END_completed', 'guards_verified_after_graph',
        'static_weights_verified_after_graph', 'postrun_buffer_checks_verified',
        'hot_path_fsync_count', 'hot_path_disk_events', 'poll_delay_us', 'deadline_seconds',
        'ram_event_capacity', 'ram_events', 'hot_operations_count',
        'graph_owner_sync_calls', 'graph_owner_sync_bytes', 'per_layer_input_sha_checks',
        'per_layer_guard_checks', 'per_layer_y_poison_writes', 'postrun_owner_sync_calls',
        'postrun_owner_sync_bytes', 'postrun_guard_checks', 'postrun_static_weight_sha_checks',
        'postrun_checks_started_ns', 'postrun_checks_completed_ns',
        'initial_weight_to_device_sync_calls', 'initial_weight_to_device_sync_bytes'}
    need(type(value) is dict and set(value) == required, 'fair native receipt schema/inventory differs')
    execution = value['execution_mode']
    dense, sparse = route_counts(execution)
    if expected_execution is not None:
        route_counts(expected_execution)
        need(execution == expected_execution, 'native execution_mode differs from frozen plan')
    need(value['schema'] == 'opt50_fair_native_v1' and value['profile'] == PROFILE and
         value['boot_id'] == BOOT and value['active_data_sha256'] == ACTIVE_DATA_SHA[execution] and
         type(value['active_data_capacity_bytes']) is int and value['active_data_capacity_bytes'] == DATA_CAPACITY and
         value['active_metadata_sha256'] == META_SHA and value['device_closed'] is False and
         all(value[key] is True for key in ('native_END_completed', 'guards_verified_after_graph',
             'static_weights_verified_after_graph', 'postrun_buffer_checks_verified')),
         'fair native identity/active weight/safety profile differs')
    counts = {'run_id': 1, 'START_commands_issued': 36, 'completed_PL_calls': 36,
        'PL_dense': dense, 'PL_sparse': sparse, 'poll_delay_us': 50, 'deadline_seconds': 120,
        'ram_event_capacity': 256, 'graph_owner_sync_calls': 0, 'graph_owner_sync_bytes': 0,
        'per_layer_input_sha_checks': 0, 'per_layer_guard_checks': 0,
        'per_layer_y_poison_writes': 0, 'postrun_owner_sync_calls': 4,
        'postrun_owner_sync_bytes': 4722688, 'postrun_guard_checks': 4,
        'postrun_static_weight_sha_checks': 2, 'initial_weight_to_device_sync_calls': 2,
        'initial_weight_to_device_sync_bytes': 4153344,
        'hot_path_fsync_count': 0, 'hot_path_disk_events': 0}
    need(all(type(value[key]) is int and value[key] == expected for key, expected in counts.items()) and
         type(value['hot_operations_count']) is int and value['hot_operations_count'] >= 182,
         'fair route/counter/BO size/postrun check contract differs')
    events = value['ram_events']
    need(type(events) is list and len(events) == 182, 'exact 182 compact lifecycle events required')
    last = -1
    expected = [('MODEL_BEGIN', 49, 0)]
    for command, layer in enumerate(PL, 1):
        expected.extend((phase, layer, command) for phase in (
            'DMA_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED',
            'DMA_COMPLETION_PROVEN', 'LAYER_COMPLETION_VERIFIED'))
    expected.append(('MODEL_END_COMPLETED', 49, 36))
    for row, (phase, layer, command) in zip(events, expected):
        need(type(row) is dict and set(row) == {'phase', 'layer', 'command', 'mode', 'monotonic_ns'} and
             (row['phase'], row['layer'], row['command']) == (phase, layer, command) and
             type(row['mode']) is int and row['mode'] == layer_mode(execution, layer) and
             type(row['layer']) is int and type(row['command']) is int and
             type(row['monotonic_ns']) is int and row['monotonic_ns'] > last,
             'compact lifecycle mode/order/timestamp differs from chosen arm')
        last = row['monotonic_ns']
    need(type(value['postrun_checks_started_ns']) is int and type(value['postrun_checks_completed_ns']) is int and
         last < value['postrun_checks_started_ns'] < value['postrun_checks_completed_ns'],
         'full buffer checks must complete strictly after END, outside Runner.run')
    result = {key: value[key] for key in counts if key not in ('run_id', 'poll_delay_us', 'deadline_seconds', 'ram_event_capacity')}
    result.update(execution_mode=execution, active_data_sha256=ACTIVE_DATA_SHA[execution],
        active_data_capacity_bytes=DATA_CAPACITY, active_metadata_sha256=META_SHA,
        native_END_completed=True, postrun_buffer_checks_verified=True,
        guards_verified_after_graph=True, static_weights_verified_after_graph=True,
        device_closed=False, compact_lifecycle_events=182, physical_PL_starts='NOT_INDEPENDENTLY_MEASURED',
        postrun_buffer_checks_ms=(value['postrun_checks_completed_ns']-value['postrun_checks_started_ns'])/1_000_000,
        native_model_interval_ms=(events[-1]['monotonic_ns']-events[0]['monotonic_ns'])/1_000_000)
    return result


def native_completion(c, execution, read_events, closed=False, *, execution_mode=None):
    route_counts(execution_mode)  # Mandatory caller expectation before reading files.
    execution = Path(execution)
    result = compact(c.read_json(execution/'performance_native.json', private=True), execution_mode)
    result['compact_receipt'] = c.identity(c.read_file(execution/'performance_native.json', private=True))
    events = read_events(c, execution/'native_events')
    need(type(events) is list and bool(events), 'native lifecycle events missing')
    phases = [row['phase'] for row in events]
    need(phases[0] == 'NATIVE_ENTERED' and phases.count('NATIVE_ENTERED') == phases.count('NATIVE_READY') == 1 and
         phases.count('PERFORMANCE_COMPACT_PUBLISHED') == 1 and
         phases.index('NATIVE_ENTERED') < phases.index('NATIVE_READY') < phases.index('PERFORMANCE_COMPACT_PUBLISHED') and
         not any(phase in phases for phase in ('MODEL_BEGIN', 'MODEL_END_COMPLETED', 'MODEL_END_VERIFIED',
             'DMA_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED', 'DMA_COMPLETION_PROVEN',
             'LAYER_COMPLETION_VERIFIED', 'LAYER_DMA_AND_GUARDS_VERIFIED')),
         'graph must not create per-operation durable native events')
    if closed:
        need(phases.count('RESOURCE_CLOSE_INTENT') == phases.count('RESOURCES_CLOSED') == 1 and
             phases.index('PERFORMANCE_COMPACT_PUBLISHED') < phases.index('RESOURCE_CLOSE_INTENT') and
             phases.index('RESOURCE_CLOSE_INTENT') < phases.index('RESOURCES_CLOSED') == len(events)-1 and
             events[-1]['details'] == 'released_BO_count=4; exported client handles must already be closed; resident firmware unchanged',
             'four-BO native resource closure proof missing')
    else:
        need('RESOURCE_CLOSE_INTENT' not in phases and 'RESOURCES_CLOSED' not in phases,
             'owner must remain alive until its authenticated STOP')
    result.update(device_closed=closed, native_event_count=len(events))
    return result
