"""Closed compact lifecycle proof; file reads only, no device commands."""
from pathlib import Path
PROFILE = 'b24-real-input-low-overhead-v1'
PL = [i for i in range(48) if i % 4 != 1]
SPARSE = {i for i in range(48) if i % 4 in (2, 3)}


def need(ok, why):
    if not ok:
        raise ValueError(why)


def compact(value, boot_id, manifest):
    required = {'schema', 'profile', 'boot_id', 'source_model_sha256', 'run_id', 'START_commands_issued',
                'completed_PL_calls', 'PL_dense', 'PL_sparse', 'device_closed', 'native_END_completed',
                'guards_verified_after_graph', 'static_weights_verified_after_graph', 'postrun_buffer_checks_verified',
                'hot_path_fsync_count', 'hot_path_disk_events', 'poll_delay_us', 'deadline_seconds',
                'ram_event_capacity', 'ram_events', 'hot_operations_count',
                'graph_owner_sync_calls', 'graph_owner_sync_bytes', 'per_layer_input_sha_checks',
                'per_layer_guard_checks', 'per_layer_y_poison_writes', 'postrun_owner_sync_calls',
                'postrun_owner_sync_bytes', 'postrun_guard_checks', 'postrun_static_weight_sha_checks',
                'postrun_checks_started_ns', 'postrun_checks_completed_ns',
                'initial_weight_to_device_sync_calls', 'initial_weight_to_device_sync_bytes'}
    need(type(value) is dict and set(value) == required and value.get('schema') == 'opt50_performance_native_v2' and
         value.get('profile') == PROFILE and value.get('boot_id') == boot_id and
         value.get('source_model_sha256') == 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43' and
         value.get('run_id') == 1 and type(value['run_id']) is int and
         value.get('START_commands_issued') == value.get('completed_PL_calls') == 36 and
         value.get('PL_dense') == 12 and value.get('PL_sparse') == 24 and value.get('device_closed') is False and
         all(value.get(k) is True for k in ('native_END_completed', 'guards_verified_after_graph',
                                          'static_weights_verified_after_graph', 'postrun_buffer_checks_verified')) and
         value.get('hot_path_fsync_count') == value.get('hot_path_disk_events') == 0 and
         value.get('poll_delay_us') == 50 and value.get('deadline_seconds') == 120 and
         value.get('ram_event_capacity') == 256 and type(value.get('hot_operations_count')) is int and
         value['hot_operations_count'] >= 182, 'v2 native completion/counter/safety profile differs')
    allocation = lambda n: ((n+128+4095)//4096)*4096
    data_bytes = manifest['files']['pl_w_data.bin']['bytes']
    meta_bytes = manifest['files']['pl_w_meta.bin']['bytes']
    postrun_bytes = sum(allocation(x) for x in (113472, data_bytes, meta_bytes, 453888))
    initial_bytes = allocation(data_bytes)+allocation(meta_bytes)
    expected_counts = {'graph_owner_sync_calls': 0, 'graph_owner_sync_bytes': 0,
                       'per_layer_input_sha_checks': 0, 'per_layer_guard_checks': 0,
                       'per_layer_y_poison_writes': 0, 'postrun_owner_sync_calls': 4,
                       'postrun_owner_sync_bytes': postrun_bytes, 'postrun_guard_checks': 4,
                       'postrun_static_weight_sha_checks': 2,
                       'initial_weight_to_device_sync_calls': 2,
                       'initial_weight_to_device_sync_bytes': initial_bytes,
                       'hot_path_fsync_count': 0, 'hot_path_disk_events': 0}
    need(all(type(value[key]) is int and value[key] == expected for key, expected in expected_counts.items()),
         'v2 timed owner operations or fresh postrun checks differ')
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
             type(row['mode']) is int and row['mode'] == int(layer in SPARSE) and
             type(row['layer']) is int and type(row['command']) is int and
             type(row['monotonic_ns']) is int and row['monotonic_ns'] > last,
             'compact lifecycle route/order/timestamp differs')
        last = row['monotonic_ns']
    need(type(value['postrun_checks_started_ns']) is int and
         type(value['postrun_checks_completed_ns']) is int and
         last < value['postrun_checks_started_ns'] < value['postrun_checks_completed_ns'],
         'full buffer checks must complete strictly after END, outside Runner.run')
    return {'START_commands_issued': 36, 'completed_PL_calls': 36, 'PL_dense': 12, 'PL_sparse': 24,
            'native_END_completed': True, 'postrun_buffer_checks_verified': True,
            'guards_verified_after_graph': True, 'static_weights_verified_after_graph': True,
            'graph_owner_sync_calls': 0, 'graph_owner_sync_bytes': 0,
            'per_layer_input_sha_checks': 0, 'per_layer_guard_checks': 0, 'per_layer_y_poison_writes': 0,
            'postrun_owner_sync_calls': 4, 'postrun_owner_sync_bytes': postrun_bytes,
            'postrun_guard_checks': 4, 'postrun_static_weight_sha_checks': 2,
            'initial_weight_to_device_sync_calls': 2, 'initial_weight_to_device_sync_bytes': initial_bytes,
            'device_closed': False, 'hot_path_disk_events': 0,
            'hot_path_fsync_count': 0, 'compact_lifecycle_events': 182,
            'physical_PL_starts': 'NOT_INDEPENDENTLY_MEASURED',
            'postrun_buffer_checks_ms': (value['postrun_checks_completed_ns']-value['postrun_checks_started_ns'])/1_000_000,
            'native_model_interval_ms': (events[-1]['monotonic_ns']-events[0]['monotonic_ns'])/1_000_000}


def native_completion(c, execution, read_events, boot_id, manifest, closed=False):
    execution = Path(execution)
    result = compact(c.read_json(execution/'performance_native.json', private=True), boot_id, manifest)
    result['compact_receipt'] = c.identity(c.read_file(execution/'performance_native.json', private=True))
    events = read_events(c, execution/'native_events')
    phases = [row['phase'] for row in events]
    need(phases[0] == 'NATIVE_ENTERED' and phases.count('NATIVE_ENTERED') == phases.count('NATIVE_READY') == 1 and
         phases.count('PERFORMANCE_COMPACT_PUBLISHED') == 1 and
         phases.index('NATIVE_ENTERED') < phases.index('NATIVE_READY') and
         phases.index('NATIVE_READY') < phases.index('PERFORMANCE_COMPACT_PUBLISHED') and
         not any(phase in phases for phase in ('MODEL_BEGIN', 'MODEL_END_COMPLETED', 'MODEL_END_VERIFIED', 'DMA_ACTIVE_BEFORE_START',
                                               'START_WRITE_INTENT', 'START_WRITE_RETURNED', 'DMA_COMPLETION_PROVEN',
                                               'LAYER_COMPLETION_VERIFIED', 'LAYER_DMA_AND_GUARDS_VERIFIED')),
         'graph must not create per-operation durable native events')
    if closed:
        need(phases.count('RESOURCE_CLOSE_INTENT') == phases.count('RESOURCES_CLOSED') == 1 and
             phases.index('RESOURCE_CLOSE_INTENT') < phases.index('RESOURCES_CLOSED') == len(events)-1 and
             events[-1]['details'] == 'released_BO_count=4; exported client handles must already be closed; resident firmware unchanged',
             'four-BO native resource closure proof missing')
    else:
        need('RESOURCE_CLOSE_INTENT' not in phases and 'RESOURCES_CLOSED' not in phases,
             'owner must remain alive until its authenticated STOP')
    result.update(device_closed=closed, native_event_count=len(events))
    return result

