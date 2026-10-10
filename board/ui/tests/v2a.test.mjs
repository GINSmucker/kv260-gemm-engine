import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { V2A_PINS, validateV2AIdentity } from '../v2a-contract.mjs';
import { validateBoardJob } from '../board-client.mjs';

// Synthetic validator fixtures only; no fixture is a board or gate receipt.
function layerFixture() {
  const digest='a'.repeat(64),boot='unit-test-boot',id='board_012345abcdef';
  const identity={...V2A_PINS,boot_id:boot,window_id:'system_v2a_unit_01',owner_pid:1234,owner_birth_ticks:100};
  return {id,source:'BOARD',type:'layer',status:'SUCCEEDED',boot_id:boot,options:{backend:'pl49'},realPLStarts:1,
    result:{schema:'b_layer_case_result_v2a',source:'BOARD',ABI_version:2,wire_version:2,PL_starts:1,fallback_count:0,
      candidate_sha256:identity.candidate_sha256,bridge_sha256:identity.bridge_sha256,C_ready_sha256:identity.ready_sha256,
      window_manifest_sha256:digest,bridge_config_sha256:digest,v2a_plugin_sha256:'b1fca00bc36c5318717f04d149f3fa6ca1dd664aac8bd84aa8a080fe5d9e1893',
      bundle_identity:{manifest_sha256:identity.B_manifest_sha256},execution_kind:'FPGA_LAYER',integer_mismatches:0,independent_INT64_mismatches:0,
      actual_owner_identity:{schema:'kv260_gemm_actual_identity_v2a',abi_version:2,wire_version:2,
        bo:[{address:1148977152,bytes:151552,high_address:false},{address:1150550016,bytes:192512,high_address:false},{address:1159725056,bytes:606208,high_address:false}],
        scope:'B_SYSTEM_VERIFIED',software_test:false,identity,window_manifest_sha256:digest},
      receipt:{one_start:1,done:1,kernel_error:0,guards_ok:1,inputs_unchanged:1,starts_before:2,starts_after:3,owner_pid:1234}}};
}

test('V2A layer validator rejects version, owner and candidate mixing',()=>{
  assert.doesNotThrow(()=>validateBoardJob(layerFixture()));
  const mutations=[
    j=>j.result.ABI_version=1,j=>j.result.wire_version=1,j=>j.result.schema='b_layer_case_result_v1',
    j=>j.result.actual_owner_identity.software_test=true,j=>j.result.actual_owner_identity.scope='C_S2_VALIDATION_ONLY',
    j=>j.result.actual_owner_identity.identity.owner_birth_ticks='100',j=>j.result.actual_owner_identity.identity.extra=true,
    j=>j.result.actual_owner_identity.identity.driver_sha256='0'.repeat(64),j=>j.result.actual_owner_identity.identity.owner_pid=0,
    j=>j.result.C_ready_sha256='0'.repeat(64),j=>j.result.v2a_plugin_sha256='0'.repeat(64),
    j=>j.result.receipt.guards_ok=0,j=>j.result.receipt.owner_pid++,j=>j.boot_id='another-boot'
  ];
  for(const change of mutations){const j=layerFixture();change(j);assert.throws(()=>validateBoardJob(j),e=>e.code==='BAD_BOARD_REPLY');}
});

test('actual S3 img00 identity and all 49 original layer identities replay without fabricated uid',()=>{
  const root=new URL('./fixtures/',import.meta.url);
  const actual=JSON.parse(readFileSync(new URL('img00.json',root),'utf8'));
  const job={type:'inference',boot_id:actual.actual_owner_identity.identity.boot_id};
  assert.equal('uid' in actual.actual_owner_identity,false);
  assert.doesNotThrow(()=>validateV2AIdentity(actual,job));
  const rows=readFileSync(new URL('img00.layers.jsonl',root),'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(rows.length,49);
  for(const row of rows) {
    const identity=JSON.parse(row.actual_identity_json);
    assert.deepEqual(identity,actual.actual_owner_identity);
    assert.doesNotThrow(()=>validateV2AIdentity({...actual,actual_owner_identity:identity},job));
  }
  const mutations=[
    r=>r.actual_owner_identity.uid=1000,r=>delete r.actual_owner_identity.bo,
    r=>r.actual_owner_identity.bo[0].bytes--,r=>r.actual_owner_identity.bo[0].high_address=true,
    r=>r.actual_owner_identity.bo[0].address++,r=>r.actual_owner_identity.bo[1].address=r.actual_owner_identity.bo[0].address,
    r=>r.actual_owner_identity.bo[0].extra=0,
    r=>r.actual_owner_identity.identity.driver_sha256='0'.repeat(64),r=>r.ABI_version=1,
    r=>r.actual_owner_identity.window_manifest_sha256='0'.repeat(64)
  ];
  for(const mutate of mutations){const bad=structuredClone(actual);mutate(bad);assert.throws(()=>validateV2AIdentity(bad,job),e=>e.code==='BAD_BOARD_REPLY');}
});

test('V2A full-image validator binds every original nested receipt',()=>{
  const j=layerFixture(),r=j.result,digest='a'.repeat(64);j.type='inference';j.input_sha256=digest;j.realPLStarts=49;
  Object.assign(r,{schema:'b_inference_result_v2a',status:'PASS',execution_kind:'FPGA_MODEL',PL_starts:49,observed_custom_calls:49,
    model_linear_count:49,source_sha256:digest,run_id:j.id,logits:Array(1000).fill(0),
    top5:Array.from({length:5},(_,index)=>({index,name:'class_'+index,logit:0,probability:.001})),
    layers:Array.from({length:49},(_,index)=>({schema:'b_layer_trace_v2a',backend:'pl_v2a',layer_index:index,layer_id:'layer_'+index,
      model_run_id:j.id,image_id:'img0000',request_id:`${j.id}_img0000_l${index}`,starts_delta:1,starts_before:index,starts_after:index+1,
      owner_pid:1234,fallback_count:0,X_sha256:digest,W_sha256:digest,Y_sha256:digest,actual_identity_json:JSON.stringify(r.actual_owner_identity)}))});
  assert.doesNotThrow(()=>validateBoardJob(j));
  for(const change of [
    j=>j.result.layers[24].actual_identity_json='{}',j=>j.result.layers[24].schema='b_layer_trace_v1',
    j=>j.result.layers[24].backend='pl',j=>j.result.layers[24].starts_before++,j=>j.result.layers.pop()
  ]){const bad=structuredClone(j);change(bad);assert.throws(()=>validateBoardJob(bad),e=>e.code==='BAD_BOARD_REPLY');}
});
