// Explicit live-board regression; not run by npm test.
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
const root=path.resolve('results/UI/ui_board_20261001_01'),base='http://127.0.0.1:4173';
await mkdir(root,{recursive:true});
const evidence={schema:'ui_board_verification_v1',client_utc:new Date().toISOString(),jobs:[],checks:[],source:'ACTUAL_KV260_ARM',PL_starts:0};
async function api(route,body){const res=await fetch(base+route,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},...(body?{body:JSON.stringify(body)}:{})});const data=await res.json();if(!res.ok){const e=new Error(data.error?.message);e.code=data.error?.code;throw e;}return data;}
async function untilDone(id){for(let i=0;i<600;i++){const j=await api('/api/jobs/'+id);if(['SUCCEEDED','FAILED','CANCELLED'].includes(j.status)){await writeFile(path.join(root,id+'.json'),JSON.stringify(j,null,2));evidence.jobs.push({id:j.id,type:j.type,status:j.status,backend:j.options.backend,top1:j.result?.top5?.[0],input_sha256:j.input_sha256,PL_starts:j.realPLStarts});return j;}await new Promise(r=>setTimeout(r,100));}throw new Error('Task not completed within bounded poll');}
async function photo(name){const data=await readFile(path.join(root,'fixtures',name));return {name,image:{mime:'image/jpeg',base64:data.toString('base64')},sha:createHash('sha256').update(data).digest('hex')};}
async function infer(pic,backend='fp32'){const j=await api('/api/jobs',{source:'BOARD',type:'inference',options:{backend,scenario:'normal',input:{name:pic.name,kind:'upload'}},image:pic.image});const done=await untilDone(j.id);assert.equal(done.status,'SUCCEEDED');assert.equal(done.result.source_sha256,pic.sha);assert.equal(done.result.logits.length,1000);assert.equal(done.result.PL_starts,0);assert.equal(done.result.run_id,j.id);return done;}
try {
  evidence.health=await api('/api/board/health');assert.equal(evidence.health.payload_verified,true);
  const dog=await photo('dog.JPEG'),church=await photo('church.JPEG');
  const a=await infer(dog),repeat=await infer(dog),b=await infer(church),q=await infer(dog,'w8a8'),custom=await infer(dog,'custom_cpu');
  assert.deepEqual(a.result.logits,repeat.result.logits);assert.notDeepEqual(a.result.logits,b.result.logits);assert.notEqual(a.result.top5[0].index,b.result.top5[0].index);
  assert.equal(custom.result.observed_custom_calls,49);assert.deepEqual(q.result.logits,custom.result.logits);
  evidence.checks.push('RAW_IMAGE_SHA_MATCH','SAME_INPUT_1000_LOGITS_EXACT','DIFFERENT_IMAGE_LOGITS_AND_TOP1_DIFFER','ARM_CUSTOM_49_CALLS','SAME_ARM_QUANT_CUSTOM_LOGITS_EXACT');
  const layer=await untilDone((await api('/api/jobs',{source:'BOARD',type:'layer',options:{backend:'fp32',layerIndex:48,case:'real'}})).id);assert.equal(layer.status,'SUCCEEDED');assert.equal(layer.result.integer_mismatches,0);evidence.checks.push('ACTUAL_HEAD_INT64_ZERO_DIFFERENCE');
  const bench=await untilDone((await api('/api/jobs',{source:'BOARD',type:'benchmark',options:{backend:'fp32',runs:3,warmup:1,input:{name:dog.name}},image:dog.image})).id);assert.equal(bench.status,'SUCCEEDED');assert.equal(bench.result.samples.length,3);assert.equal(bench.result.timingsSource,'BOARD_CPU_MEASURED');
  const csv=await(await fetch(base+`/api/jobs/${bench.id}/export.csv`)).text();assert.equal(csv.split('\r\n').length,4);assert.ok(csv.includes('BOARD_CPU'));await writeFile(path.join(root,'actual_benchmark.csv'),csv);evidence.checks.push('ACTUAL_PAIRED_CPU_SAMPLES_CSV');
  const cancelled=await api('/api/jobs',{source:'BOARD',type:'benchmark',options:{backend:'fp32',runs:3,warmup:5,input:{name:dog.name}},image:dog.image});
  await assert.rejects(api('/api/jobs',{source:'BOARD',type:'inference',options:{backend:'fp32'},image:dog.image}),e=>e.code==='DEVICE_BUSY');
  await api('/api/jobs/'+cancelled.id+'/cancel',{});const stopped=await untilDone(cancelled.id);assert.equal(stopped.status,'CANCELLED');assert.equal(stopped.result,null);evidence.checks.push('ONE_ACTIVE_BOARD_TASK','CANCEL_AT_SOFTWARE_BOUNDARY_NO_RESULT');
  await assert.rejects(api('/api/jobs',{source:'BOARD',type:'inference',options:{backend:'pl49'},image:dog.image}),e=>e.code==='PL_NOT_READY');evidence.checks.push('PL_REJECTED_WITHOUT_FALLBACK');
  const bad=await untilDone((await api('/api/jobs',{source:'BOARD',type:'inference',options:{backend:'fp32'},image:{mime:'image/jpeg',base64:'bm90IGFuIGltYWdl'}})).id);assert.equal(bad.status,'FAILED');assert.equal(bad.result,null);evidence.checks.push('INVALID_IMAGE_REJECTED_ON_BOARD');
  evidence.status='PASS_BOARD_CPU';evidence.final_health=await api('/api/board/health');assert.equal(evidence.final_health.active_job,null);
} catch(error){evidence.status='FAIL';evidence.error={message:error.message,stack:error.stack};process.exitCode=1;}
await writeFile(path.join(root,'verification.json'),JSON.stringify(evidence,null,2));
console.log(JSON.stringify({status:evidence.status,checks:evidence.checks,jobs:evidence.jobs,error:evidence.error?.message},null,2));
