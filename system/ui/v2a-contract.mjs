// Explicit versioned UI receipt validation. The S4 window also requires the
// same candidate's independent S3 gate; these pins come from actual S1/S2.
import { AppError } from './domain.mjs';

export const V2A_PINS=Object.freeze({
  candidate_sha256:'8c0e3516beb1a372c68c833ba8c9808a81666183c72641f993e0ef6189ea5303',
  owner_sha256:'a8cdc9dadbebf455b9b2dd77706d788d74297bd189d605d554ae46ad1e243c8f',
  bridge_sha256:'2152ebd94a12c9f49b76362bdc920d2187fe4d4990a63a35845e226e4c490e18',
  ready_sha256:'1431503c44c6f899bbf5949d84324b016d38d0bee8c937f11c8afc3a7afdd91f',
  B_tools_wheel_sha256:'06c71de29a9e9f26163a3b92362eb400efc5adde297e15f9d05aaaffce1387d2',
  driver_sha256:'cba324ac56c9bebe84fabbdf2635ea699b10ec718593cba7d44c4b004f782eca',
  driver_srcversion:'9806AB60AF7AE94483CDDC9',
  platform_manifest_sha256:'326c68b0041140afb7a04d707a6b8114ed866be2a959bb538e139a4aad9888b9',
  A2_gate_sha256:'a2c1e1f93c81d275488ebf87ae813a12cbd7931674a0f6f21578301ba81ba80d',
  B_manifest_sha256:'77680bacbcf6c46571fd6fc99c40778528dd366e950023b73058b6932515962e',
  uuid:'a1d6968d-c5b3-c896-a565-94ddf751a2c7',tag0:0xe7333e5e,tag1:0x9c258169
});

export function validateV2AIdentity(result,job) {
  const fail=()=>{throw new AppError('BAD_BOARD_REPLY','V2A版本、完整owner身份或已验证候选绑定不一致。',502);};
  const actual=result.actual_owner_identity,i=actual?.identity;
  if(!i || actual.schema!=='kv260_gemm_actual_identity_v2a' || actual.abi_version!==2 || actual.wire_version!==2
      || actual.scope!=='B_SYSTEM_VERIFIED' || actual.software_test!==false
      || result.ABI_version!==2 || result.wire_version!==2
      || result.schema!==(job.type==='layer'?'b_layer_case_result_v2a':'b_inference_result_v2a'))fail();
  const outer=['abi_version','bo','identity','schema','scope','software_test','window_manifest_sha256','wire_version'];
  if(JSON.stringify(Object.keys(actual).sort())!==JSON.stringify(outer))fail();
  const sizes=[151552,192512,606208];
  if(!Array.isArray(actual.bo) || actual.bo.length!==3)fail();
  for(const [index,bo] of actual.bo.entries()) {
    if(!bo || JSON.stringify(Object.keys(bo).sort())!==JSON.stringify(['address','bytes','high_address'])
        || !Number.isSafeInteger(bo.address) || bo.address<=0 || bo.address%64!==0
        || bo.bytes!==sizes[index] || !Number.isSafeInteger(bo.address+bo.bytes)
        || !(bo.address+bo.bytes<=0x80000000 || (bo.address>=0x800000000 && bo.address+bo.bytes<=0x880000000))
        || bo.high_address!==(bo.address>=2**32))fail();
    if(actual.bo.slice(0,index).some(other=>bo.address<other.address+other.bytes && other.address<bo.address+bo.bytes))fail();
  }
  const keys=[...Object.keys(V2A_PINS),'boot_id','window_id','owner_pid','owner_birth_ticks'].sort();
  if(JSON.stringify(Object.keys(i).sort())!==JSON.stringify(keys))fail();
  if(Object.entries(V2A_PINS).some(([k,v])=>i[k]!==v) || i.boot_id!==job.boot_id
      || typeof i.window_id!=='string' || !/^system_v2a_[A-Za-z0-9_]+$/.test(i.window_id)
      || !Number.isSafeInteger(i.owner_pid) || i.owner_pid<=0 || !Number.isSafeInteger(i.owner_birth_ticks) || i.owner_birth_ticks<=0)fail();
  if(result.candidate_sha256!==i.candidate_sha256 || result.bridge_sha256!==i.bridge_sha256
      || result.C_ready_sha256!==i.ready_sha256 || actual.window_manifest_sha256!==result.window_manifest_sha256
      || result.v2a_plugin_sha256!=='b1fca00bc36c5318717f04d149f3fa6ca1dd664aac8bd84aa8a080fe5d9e1893')fail();
  // A local view for common checks; the original nested receipt is untouched.
  // UID belongs to the validated config/socket peer, not this wire receipt.
  return {...i,software_test:actual.software_test,tags:[i.tag0,i.tag1],window_manifest_sha256:actual.window_manifest_sha256};
}
