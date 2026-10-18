import { AppError } from './domain.mjs';

export const H576_MANIFEST = 'c18de455de083363d0b73a7b526f5262a5cb2371c8e91b5900a4447328694a76';
export function validateH576Result(job) {
  const r=job.result, fail=()=>{throw new AppError('BAD_BOARD_REPLY','H576模型、实际路由或结果身份不一致。',502);};
  if(r?.model_protocol!=='h576_v5' || r.source!=='BOARD' || r.status!=='PASS'
    || r.bundle_identity?.manifest_sha256!==H576_MANIFEST || r.boot_id!==job.boot_id || r.fallback_count!==0)fail();
  if(r.PL_starts>0) {
    const b=r.hardware_binding;
    if(!b || b.package_manifest_sha256!==H576_MANIFEST || b.boot_id!==job.boot_id
      || b.observer_tags?.[0]!==0xd9b17a2f || b.observer_tags?.[1]!==0x770261c5
      || b.uuid!=='0245b248-109b-6bf7-73c7-16b629abe9a1' || !Number.isInteger(b.owner_pid) || b.owner_pid<=0)fail();
  }
  if(job.type==='benchmark') {
    if(r.samples?.length!==job.options.runs || !r.samples.every(x=>[x.fp32Ms,x.w8a8Ms].every(y=>Number.isFinite(y)&&y>0)
      && (r.PL_starts===0?x.plMs===null:Number.isFinite(x.plMs)&&x.plMs>0))
      || (r.PL_starts!==0&&r.PL_starts!==36*(job.options.runs+job.options.warmup)))fail();
    return;
  }
  const pl=job.options?.backend==='pl49';
  if(job.type==='layer') {
    const index=job.options.layerIndex, count=index-Math.floor((index+3)/4)+1;
    if(r.integer_mismatches!==0 || r.independent_INT64_mismatches!==0 || r.target_layer!==index
      || (pl && (index===48 || index%4===1 || r.target_PL_starts!==1 || r.PL_starts!==count))
      || (!pl && r.PL_starts!==0))fail();
    return;
  }
  if(r.schema!=='b_inference_result_h576_v5' || r.run_id!==job.id || r.source_sha256!==job.input_sha256
    || !/^[a-f0-9]{64}$/.test(r.source_sha256??'') || r.model_linear_count!==49 || r.logits?.length!==1000
    || !r.logits.every(Number.isFinite) || r.PL_starts!==(pl?36:0) || job.realPLStarts!==r.PL_starts)fail();
  if(pl && (r.route_manifest?.length!==49 || r.route_manifest.filter(x=>x.backend==='PL').length!==36
    || r.route_manifest.some((x,i)=>x.layer_index!==i || x.backend!==((i!==48&&i%4!==1)?'PL':'CPU') || x.evidence!=='FROZEN_ROUTE_COMPLETED_GRAPH')))fail();
  const ranked=r.logits.map((value,index)=>({value,index})).sort((a,b)=>b.value-a.value||a.index-b.index).slice(0,5);
  const total=r.logits.reduce((s,x)=>s+Math.exp(x-ranked[0].value),0);
  if(r.top5?.length!==5 || !r.top5.every((x,i)=>x.index===ranked[i].index && x.logit===ranked[i].value
    && typeof x.name==='string' && Math.abs(x.probability-Math.exp(x.logit-ranked[0].value)/total)<1e-6))fail();
}
