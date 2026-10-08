// Offline diagnostic collector. It supplies no hardware capability or ready.
import assert from 'node:assert/strict';
export function collectV3G1Timing(rows, traces) {
  assert.equal(rows.length,49);assert.equal(traces.length,49);
  const phases=['B_input','B_quantize','B_prepare','B_CPU_REFERENCE','B_receipt','B_dequantize','B_trace_export','B_dump_export'].sort();
  const integer=n=>Number.isSafeInteger(n)&&n>=0;
  for(let i=0;i<49;i++) {
    const row=rows[i],trace=traces[i];
    assert.deepEqual(Object.keys(row).sort(),['schema','layer_index','request_id','backend','pre_terminal_export_wall_ns',
      'thread_cpu_ns','terminal_export_cost','phases'].sort());
    assert.equal(row.schema,'b_layer_timing_v3_g1');assert.equal(trace.schema,'b_layer_trace_v3_g1');
    assert.equal(row.layer_index,i);assert.equal(trace.layer_index,i);
    assert.equal(row.request_id,trace.request_id);assert.equal(typeof row.request_id,'string');
    assert.equal(row.request_id,`${trace.model_run_id}_${trace.image_id}_l${i}`);
    assert.equal(trace.model_run_id,traces[0].model_run_id);assert.equal(trace.image_id,traces[0].image_id);
    assert.equal(row.backend,'cpu_reference');assert.equal(trace.backend,'cpu_reference');
    assert.equal(trace.starts_delta,0);assert.equal(trace.fallback_count,0);
    assert.equal(row.terminal_export_cost,'ENCLOSING_SESSION_RUN');
    assert.deepEqual(Object.keys(row.phases).sort(),phases);
    let wall=0,cpu=0;
    for(const value of Object.values(row.phases)) {
      assert.deepEqual(Object.keys(value).sort(),['thread_cpu_ns','wall_ns']);
      assert(integer(value.wall_ns)&&integer(value.thread_cpu_ns));wall+=value.wall_ns;cpu+=value.thread_cpu_ns;
    }
    assert(integer(wall)&&integer(cpu)&&integer(row.pre_terminal_export_wall_ns)&&integer(row.thread_cpu_ns));
    assert.equal(wall,row.pre_terminal_export_wall_ns);assert.equal(cpu,row.thread_cpu_ns);
  }
  return Object.freeze({schema:'v3_g1_diagnostic_collection',layers:49,execution_source:'CPU_REFERENCE',
    PL_starts:0,production_capability:false,observer_overhead:'NOT_QUANTIFIED',fair_performance:'NOT_RUN'});
}
