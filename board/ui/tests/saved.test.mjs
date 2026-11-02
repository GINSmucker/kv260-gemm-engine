import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {loadSavedEvidence,validateSavedEvidence} from '../saved-data.mjs';
import {finalData} from '../final-data.mjs';
import {createWorkbenchServer} from '../server.mjs';
import {savedInference,savedLayer,savedBenchmark,savedTabs} from '../public/saved-view.js';

const manifest=JSON.parse(await readFile(new URL('../../../results/roadshow/manifest.json',import.meta.url),'utf8'));
const final=await finalData();
const loaded=await loadSavedEvidence();
const pin=raw=>createHash('sha256').update(raw).digest('hex');

function changeJSON(key,mutate) {
  const copy=structuredClone(manifest),files=new Map(loaded.files),value=JSON.parse(files.get(key));
  mutate(value);
  const raw=Buffer.from(JSON.stringify(value));files.set(key,raw);
  copy.files[key].bytes=raw.length;copy.files[key].sha256=pin(raw);
  return {copy,files};
}

test('saved evidence binds real photos, model, completion and committed 36-layer traces',()=>{
  const data=loaded.data;
  assert.equal(data.inference.length,2);assert.equal(data.hardwareContacted,false);assert.equal(data.live,false);
  assert.equal(data.inference[0].receipt.top5[0].name,'acoustic guitar');
  assert.equal(data.layer.observations[1].pending.owner_metrics.layers[1].mode,1);
  assert.equal(data.layer.standalone_receipt_available,false);
  assert.equal(data.layer.published_acceptance_pass,true);
});

test('saved reader rejects changed photo bytes, model, input and fabricated completion',()=>{
  const altered=new Map(loaded.files);altered.set('guitar-image',Buffer.from('different picture'));
  assert.throws(()=>validateSavedEvidence(manifest,altered,final),/identity/);
  for(const mutate of [r=>r.source_model_sha256='f'.repeat(64),r=>r.image_sha256='0'.repeat(64),r=>r.native.completed_PL_calls=35,r=>r.bitwise_mismatches=1]) {
    const {copy,files}=changeJSON('guitar-report',mutate);
    assert.throws(()=>validateSavedEvidence(copy,files,final),/differs/);
  }
  const {copy,files}=changeJSON('Dense-pending',r=>r.owner_metrics.layers[1].mode=1);
  assert.throws(()=>validateSavedEvidence(copy,files,final),/commit/);
});

test('saved views keep historical scope, unavailable evidence and standalone limits visible',()=>{
  const data=loaded.data;
  const inference=savedInference(data),layer=savedLayer(data),benchmark=savedBenchmark(data,final);
  assert.match(inference,/acoustic guitar/);assert.match(inference,/136\.516 ms/);assert.match(inference,/设备常驻/);
  assert.match(layer,/blocks\.0\.mlp\.fc1/);assert.match(layer,/原始结果JSON未包含/);assert.match(layer,/非单层Golden计数/);
  assert.match(benchmark,/143\.494 ms/);assert.match(benchmark,/136\.588 ms/);assert.match(benchmark,/195 对/);
  assert.match(savedInference(data,new URLSearchParams('sample=missing')),/存档不可用/);
  assert.match(savedLayer(null),/不会显示DEMO/);
  assert.match(savedTabs('inference','live'),/view=live/);
  const portrait=savedInference(data,new URLSearchParams('sample=portrait'));
  assert.match(portrait,/bow tie/);assert.doesNotMatch(portrait,/acoustic guitar/);
});

test('saved HTTP APIs are read-only, serve exact files and reject unknown keys and cross-origin',async t=>{
  const {server,store}=createWorkbenchServer();
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const base=`http://127.0.0.1:${server.address().port}`;
  const response=await fetch(base+'/api/saved');assert.equal(response.status,200);
  assert.equal((await response.json()).source,'SAVED_BOARD_EVIDENCE');
  const image=await fetch(base+'/api/saved/files/guitar-image');
  assert.equal(image.headers.get('content-type'),'image/jpeg');
  assert.equal(pin(Buffer.from(await image.arrayBuffer())),manifest.files['guitar-image'].sha256);
  assert.equal((await fetch(base+'/api/saved',{method:'POST'})).status,405);
  assert.equal((await fetch(base+'/api/saved/files/not-a-file')).status,404);
  assert.equal((await fetch(base+'/api/saved/files/manifest.json')).status,404);
  assert.equal((await fetch(base+'/api/saved',{headers:{Origin:'https://other.invalid'}})).status,403);
  assert.match((await fetch(base+'/api/saved/files/guitar-report?download=1')).headers.get('content-disposition'),/attachment/);
  assert.equal(store.jobs.size,0);
});
