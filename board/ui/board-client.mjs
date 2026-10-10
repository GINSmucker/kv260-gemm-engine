import { AppError } from './domain.mjs';
import { isDeepStrictEqual } from 'node:util';
import { validateV2AIdentity } from './v2a-contract.mjs';

export class BoardClient {
  constructor(endpoint) {
    const url = new URL(endpoint);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Board URL must be the fixed loopback SSH tunnel');
    this.endpoint = url.origin;
    this.lastHealth = null;
  }
  async request(path, data) {
    let response;
    try { response = await fetch(this.endpoint + path, { method: data === undefined ? 'GET' : 'POST', headers: data === undefined ? {} : { 'Content-Type': 'application/json' }, ...(data === undefined ? {} : { body: JSON.stringify(data) }), signal: AbortSignal.timeout(15000) }); }
    catch { this.lastHealth = null; throw new AppError('BOARD_UNREACHABLE', '板端服务或SSH转发未连接。没有回退到演示。', 503); }
    let result;
    try { result = await response.json(); } catch { throw new AppError('BAD_BOARD_REPLY', '板端响应不是有效JSON。', 502); }
    if (!response.ok) throw new AppError(result.error?.code ?? 'BOARD_ERROR', result.error?.message ?? '板端拒绝请求。', response.status);
    if (path === '/health') {
      if (result.source !== 'BOARD' || !result.payload_verified || !['aarch64','arm64'].includes(result.architecture) || result.bundle_identity?.manifest_sha256 !== '77680bacbcf6c46571fd6fc99c40778528dd366e950023b73058b6932515962e') throw new AppError('BAD_BOARD_IDENTITY', '板端架构或冻结模型身份不匹配。', 502);
      this.lastHealth = result;
    }
    if (/^\/jobs\/board_/.test(path) || (path === '/jobs' && data)) validateBoardJob(result);
    return result;
  }
}

export function validateBoardJob(job) {
  if (job.source !== 'BOARD' || !/^board_[0-9a-f]{12}$/.test(job.id)) throw new AppError('BAD_BOARD_REPLY', '实际任务身份无效。', 502);
  if (job.status !== 'SUCCEEDED') return;
  const r = job.result;
  if (!r || r.source !== 'BOARD') throw new AppError('BAD_BOARD_REPLY', '缺少实际板端回执。', 502);
  const pl=job.options?.backend==='pl49';
  const v2a=r.actual_owner_identity?.schema==='kv260_gemm_actual_identity_v2a';
  const fail=message=>{throw new AppError('BAD_BOARD_REPLY',message,502);};
  if (!pl && r.PL_starts!==0) fail('CPU回执出现PL启动。');
  if (pl) {
    const expected=job.type==='inference'?49:job.type==='layer'?1:null,i=v2a?validateV2AIdentity(r,job):r.actual_owner_identity;
    if (expected===null || r.PL_starts!==expected || job.realPLStarts!==expected || r.fallback_count!==0 || !i || i.software_test!==false || (!v2a && i.uid!==1000) || i.boot_id!==job.boot_id || i.owner_pid<=0 || i.tags?.[0]!==0xe7333e5e || i.tags?.[1]!==0x9c258169 || i.uuid!=='a1d6968d-c5b3-c896-a565-94ddf751a2c7') fail('真实PL启动、owner或平台身份不一致。');
    if (!['candidate_sha256','window_manifest_sha256','bridge_sha256','bridge_config_sha256','C_ready_sha256'].every(k=>/^[a-f0-9]{64}$/.test(r[k]??'')) || i.candidate_sha256!==r.candidate_sha256 || i.window_manifest_sha256!==r.window_manifest_sha256) fail('PL候选或窗口摘要缺失。');
    // Additional owner is accepted only with its actual S0-S3 verified pairing.
    const originalOwner=i.owner_sha256==='c6c2e3735860f8295873395b91cbd359b948a2d4789962522add9b76b7a446e7'
      && ((r.candidate_sha256==='6d7e2162b8b066d741e50640cd40d2a95895458f859f34b2b8e346599e6cccd2'
        && r.bridge_sha256==='b5d09e4106c1fda58ca4fec9880b774b335e2e94c44be3580d94a1c5bfd50faa'
        && r.C_ready_sha256==='a85abd13250464ff6edfada7bac9ebe10007be7a2eed028449bc8edbb4d474f2')
      || (r.candidate_sha256==='98c631580cf8d24ec49bd8bee32d2f948becaa500e4fe06faf857b61143bfd81'
        && r.bridge_sha256==='01953e1b054adb165c49ee9894fdda9e5a33455dfcbda63058fe503f47983726'
        && r.C_ready_sha256==='d0ef2c8ae686f08417aa4649e15eb0f6781194b8d497a5864f946fc0cd1b1a5a'));
    const guardOwner=i.owner_sha256==='89dac2ffae96ef647d41e00a4dd5d20f6c9ad97c033eff9dcfea101caa88affc'
      && r.candidate_sha256==='3449f437c8448a76310eb4dea39c273815278b665495db2317ce1e74ea793562'
      && r.bridge_sha256==='d07dfe6496e9e0e6c5838ec239e46716eec04de016cc8a126e118eee7ddfc8f4'
      && r.C_ready_sha256==='b6fe03685cc59164ed52278aed068473c68dbcc0368fcec87c9aea2be0fe7646';
    const snapshotOwner=i.owner_sha256==='6da1d490a0379173b19b3197a3a04f4d54898f83df135193aaf6e7bd8bac10c8'
      && r.candidate_sha256==='410575d9fb64e120c54e0e7bf58cb099f055efe30686294f36b72d824eb2e58e'
      && r.bridge_sha256==='bcb3969eeb6c21eccdb2d53b2a035537f8772d405bfc8ccb24a2ce33c7bc1400'
      && r.C_ready_sha256==='356c19ae0ba45f7c85e771a513c5f2246c69688e067d60bf6eb4c291bfe64c45';
    const shaOwner=i.owner_sha256==='aa866241a69ed5b8d069464b4663ccca5e2a703adba0329d1dc8cf34ca8b258d'
      && r.candidate_sha256==='1678b9dd914b672ac81e8dae9b8f2e506244b3c2e2ca48e99bea24a887b76ddd'
      && r.bridge_sha256==='7872e29daae511fa5d0ea57d67685abc6beed00d124a432e9bd3f96e45d9f217'
      && r.C_ready_sha256==='f8c9d906981e055a530c64b86a1da9eb28d862212c60e14b85d7f8b41012b559';
    const fullBoOwner=i.owner_sha256==='5027def50d442fe2608ac742044cacaddda0c542e0da431aa557dd8e25ae4823'
      && r.candidate_sha256==='f310b480b5546f6eb6e4814ad857879187e4046d211eab0de088af18fad4bec0'
      && r.bridge_sha256==='dea28832645bd66718eb44c254848f12defe57474028e2636c2b19d77bf63d48'
      && r.C_ready_sha256==='8df588686af2c6b19783df3fa1f7422ac168c6308206e9e8339b9feccb0e249e';
    const cacheableOwner=i.owner_sha256==='9f59328e2f710cc6e6ed96d708dd0615228edc502ef1810f696de85665c20040'
      && r.candidate_sha256==='45183717bc4183c7cc6b580ecf20a64801bebbeb1eb2499bec2ff9d0f2047da9'
      && r.bridge_sha256==='94dd50a045a3c49312a48995a9411c5ac16f44ab831e0a82c68113ea9f711b6c'
      && r.C_ready_sha256==='41b534113e3611d130ee5716f89a26573b2a43e3a9aab499df99979c4f185dce';
    if(!(originalOwner||guardOwner||snapshotOwner||shaOwner||fullBoOwner||cacheableOwner||v2a) || i.driver_sha256!=='cba324ac56c9bebe84fabbdf2635ea699b10ec718593cba7d44c4b004f782eca' || i.B_manifest_sha256!=='77680bacbcf6c46571fd6fc99c40778528dd366e950023b73058b6932515962e' || r.bundle_identity?.manifest_sha256!==i.B_manifest_sha256) fail('冻结模型/native/驱动身份不一致。');
    if (job.type==='inference') {
      if (r.execution_kind!=='FPGA_MODEL' || r.layers?.length!==49 || r.observed_custom_calls!==49) fail('缺少49个实际层回执。');
      const ids=new Set();
      for(const [index,row] of r.layers.entries()) {
        if(row.layer_index!==index || row.model_run_id!==job.id || row.image_id!=='img0000' || row.request_id!==`${job.id}_img0000_l${index}` || ids.has(row.layer_id) || row.starts_delta!==1 || row.starts_after-row.starts_before!==1 || row.owner_pid!==i.owner_pid || row.fallback_count!==0 || !['X_sha256','W_sha256','Y_sha256'].every(k=>/^[a-f0-9]{64}$/.test(row[k]??''))) fail('PL逐层回执缺失、重复或属于其他任务。');
        if(index && row.starts_before!==r.layers[index-1].starts_after) fail('PL启动序列不连续。');
        if(v2a) {
          let observed;try{observed=JSON.parse(row.actual_identity_json);}catch{fail('V2A逐层身份JSON无效。');}
          if(row.schema!=='b_layer_trace_v2a' || row.backend!=='pl_v2a' || !isDeepStrictEqual(observed,r.actual_owner_identity))fail('V2A逐层身份改变。');
        }
        ids.add(row.layer_id);
      }
    } else {
      const p=r.receipt;
      if(r.execution_kind!=='FPGA_LAYER' || r.integer_mismatches!==0 || r.independent_INT64_mismatches!==0 || !p || p.one_start!==1 || p.done!==1 || p.kernel_error!==0 || p.guards_ok!==1 || p.inputs_unchanged!==1 || p.starts_after-p.starts_before!==1 || p.owner_pid!==i.owner_pid) fail('PL层数学或完成证据无效。');
    }
  }
  if (job.type === 'inference') {
    if (r.schema !== (v2a?'b_inference_result_v2a':'b_inference_result_v1') || r.status !== 'PASS' || !['CPU_FP32','CPU_REFERENCE','FPGA_MODEL'].includes(r.execution_kind) || r.fallback_count !== 0 || r.model_linear_count !== 49 || r.logits?.length !== 1000 || !r.logits.every(Number.isFinite) || r.top5?.length !== 5 || !/^[a-f0-9]{64}$/.test(r.source_sha256 ?? '') || r.source_sha256 !== job.input_sha256 || r.run_id !== job.id) throw new AppError('BAD_BOARD_REPLY', '模型结果、真实输入SHA或1000类logits不完整。', 502);
    const ranked=r.logits.map((value,index)=>({value,index})).sort((a,b)=>b.value-a.value||a.index-b.index).slice(0,5);
    const maximum=ranked[0].value,sum=r.logits.reduce((s,x)=>s+Math.exp(x-maximum),0);
    if (!r.top5.every((x,i)=>x.index===ranked[i].index && typeof x.name==='string' && x.logit===r.logits[x.index] && Number.isFinite(x.probability) && Math.abs(x.probability-Math.exp(x.logit-maximum)/sum)<1e-6)) throw new AppError('BAD_BOARD_REPLY','Top5排序或概率与实际1000logits不一致。',502);
  }
}
