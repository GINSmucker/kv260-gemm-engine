"""Strict G1 diagnostic timing collector. Never supplies production readiness."""


def validate(rows, traces):
    if len(rows) != 49 or len(traces) != 49:
        raise ValueError('49 complete layer timing and trace rows required')
    names = {'B_input', 'B_quantize', 'B_prepare', 'B_CPU_REFERENCE',
             'B_receipt', 'B_dequantize', 'B_trace_export', 'B_dump_export'}
    identity = (traces[0].get('model_run_id'), traces[0].get('image_id'))
    keys = {'schema', 'layer_index', 'request_id', 'backend', 'pre_terminal_export_wall_ns',
            'thread_cpu_ns', 'terminal_export_cost', 'phases'}
    for index, (row, trace) in enumerate(zip(rows, traces)):
        if set(row) != keys or (trace.get('model_run_id'), trace.get('image_id')) != identity:
            raise ValueError('Exact timing keys and one model/image identity required')
        if (row.get('schema') != 'b_layer_timing_v3_g1' or row.get('layer_index') != index
                or type(row.get('layer_index')) is not int or row.get('request_id') != trace.get('request_id')
                or trace.get('schema') != 'b_layer_trace_v3_g1' or trace.get('layer_index') != index
                or trace.get('starts_delta') != 0 or trace.get('fallback_count') != 0
                or row.get('backend') != 'cpu_reference' or trace.get('backend') != 'cpu_reference'
                or row.get('terminal_export_cost') != 'ENCLOSING_SESSION_RUN'):
            raise ValueError('Wrong candidate/CPU execution/layer/request timing identity')
        if row['request_id'] != f'{identity[0]}_{identity[1]}_l{index}':
            raise ValueError('Derived model/image/layer request ID required')
        phases = row.get('phases')
        if not isinstance(phases, dict) or set(phases) != names:
            raise ValueError('Exact mutually exclusive timing phases required')
        for value in phases.values():
            if not isinstance(value, dict) or set(value) != {'wall_ns', 'thread_cpu_ns'}:
                raise ValueError('Exact phase clock keys required')
            if any(type(n) is not int or not 0 <= n <= 2**53 - 1 for n in value.values()):
                raise ValueError('Bounded integer clock values required')
        for total in ('pre_terminal_export_wall_ns', 'thread_cpu_ns'):
            if type(row.get(total)) is not int or not 0 <= row[total] <= 2**53 - 1:
                raise ValueError('Bounded integer totals required')
        if (sum(v['wall_ns'] for v in phases.values()) != row['pre_terminal_export_wall_ns']
                or sum(v['thread_cpu_ns'] for v in phases.values()) != row['thread_cpu_ns']):
            raise ValueError('Timing partition does not close')
    return {'status': 'PASS_DIAGNOSTIC_SCHEMA_ONLY', 'layers': 49, 'PL_starts': 0,
            'production_capability': False, 'observer_overhead': 'NOT_QUANTIFIED'}
