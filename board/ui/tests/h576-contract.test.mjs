import test from 'node:test';
import assert from 'node:assert/strict';
import {validateBoardJob} from '../board-client.mjs';
import {H576_MANIFEST} from '../h576-contract.mjs';

function graph() {
  const id='board_123456789abc',sha='a'.repeat(64),boot='boot';
  return {id,source:'BOARD',status:'SUCCEEDED',type:'inference',options:{backend:'pl49'},boot_id:boot,input_sha256:sha,realPLStarts:36,result:{model_protocol:'h576_v5',schema:'b_inference_result_h576_v5',source:'BOARD',status:'PASS',run_id:id,source_sha256:sha,boot_id:boot,bundle_identity:{manifest_sha256:H576_MANIFEST},fallback_count:0,model_linear_count:49,PL_starts:36,logits:Array(1000).fill(0),top5:Array.from({length:5},(_,index)=>({index,name:'class',logit:0,probability:.001})),hardware_binding:{package_manifest_sha256:H576_MANIFEST,boot_id:boot,owner_pid:123,observer_tags:[0xd9b17a2f,0x770261c5],uuid:'0245b248-109b-6bf7-73c7-16b629abe9a1'},route_manifest:Array.from({length:49},(_,layer_index)=>({layer_index,backend:layer_index<48&&layer_index%4!==1?'PL':'CPU',evidence:'FROZEN_ROUTE_COMPLETED_GRAPH'}))}};
}
test('H576 accepts its fixed mixed route and rejects incorrect model or hardware',()=>{
  validateBoardJob(graph());
  for(const change of [j=>j.result.bundle_identity.manifest_sha256='b'.repeat(64),j=>j.result.hardware_binding.uuid='wrong',j=>j.result.PL_starts=49,j=>j.result.route_manifest[1].backend='PL',j=>j.result.top5[0].probability=.5]) {
    const j=graph();change(j);assert.throws(()=>validateBoardJob(j));
  }
});
test('H576 layer reports prefix work and rejects CPU-route PL claims',()=>{
  const j=graph();j.type='layer';j.options={backend:'pl49',layerIndex:3};
  Object.assign(j.result,{target_layer:3,target_PL_starts:1,PL_starts:3,integer_mismatches:0,independent_INT64_mismatches:0});
  validateBoardJob(j);j.options.layerIndex=1;j.result.target_layer=1;assert.throws(()=>validateBoardJob(j));
});
