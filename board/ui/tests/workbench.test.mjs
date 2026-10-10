import test from 'node:test';
import assert from 'node:assert/strict';
import { JobStore, LAYERS, POLICY, validateProfile } from '../domain.mjs';
import { createWorkbenchServer } from '../server.mjs';

const inference = (scenario = 'normal') => ({ source: 'DEMO', type: 'inference', options: { backend: 'pl49', scenario, sample: 'bridge', input: { name: 'local-demo.svg', kind: 'builtin' } } });
function virtualStore() { let now = 100000; return { store: new JobStore(() => now), advance: ms => now += ms }; }
test('model catalog covers five complete shape families and 49 unique layers', () => {
  assert.equal(LAYERS.length, 49); assert.equal(new Set(LAYERS.map(x => x.id)).size, 49);
  assert.deepEqual(Object.fromEntries(['qkv','proj','fc1','fc2','head'].map(f => [f, LAYERS.filter(x => x.family === f).length])), { qkv:12,proj:12,fc1:12,fc2:12,head:1 });
  assert.equal(LAYERS.reduce((sum,x) => sum + x.M*x.K*x.N, 0), 1045949952);
  assert.equal(POLICY.hardwareIOEnabled, false);
});
test('complete simulated model creates 1000 logits and never starts physical PL', () => {
  const {store,advance}=virtualStore();const job=store.create(inference());
  advance(2000);assert.equal(store.get(job.id).status,'RUNNING');
  advance(5000);const final=store.get(job.id);
  assert.equal(final.status,'SUCCEEDED');assert.equal(final.layerCompleted,49);assert.equal(final.result.logits.length,1000);
  assert.equal(final.realPLStarts,0);assert.equal(final.result.realPLStarts,0);assert.equal(final.result.layerTrace.length,49);
  assert.ok(Math.abs(final.result.logits.reduce((sum,x)=>sum+Math.exp(x),0)-1)<1e-10);
});
test('one task in flight, rejection leaves first task intact', () => {
  const {store}=virtualStore();const first=store.create(inference());
  assert.throws(()=>store.create(inference()),e=>e.code==='DEVICE_BUSY');assert.equal(store.active,first.id);assert.equal(store.jobs.size,1);
});
test('stop transitions through CANCELLING and never fabricates a completed result', () => {
  const {store,advance}=virtualStore();const job=store.create(inference());advance(1900);
  assert.equal(store.cancel(job.id).status,'CANCELLING');advance(200);
  const end=store.get(job.id);assert.equal(end.status,'CANCELLED');assert.equal(end.result,null);assert.equal(store.active,null);
  assert.equal(store.cancel(job.id).status,'CANCELLED');
});
test('disconnect and invalid input preserve failure without retry or result', () => {
  for(const scenario of ['disconnect','invalid_input']){const {store,advance}=virtualStore();const job=store.create(inference(scenario));advance(4000);const failed=store.get(job.id);assert.equal(failed.status,'FAILED');assert.equal(failed.result,null);assert.equal(failed.realPLStarts,0);assert.equal(store.jobs.size,1);}
});
test('layer and benchmark outputs are synthetic and independently identified', () => {
  const {store,advance}=virtualStore();let job=store.create({source:'DEMO',type:'layer',options:{layerIndex:48,case:'extreme'}});advance(2000);let r=store.get(job.id).result;
  assert.equal(r.layer.family,'head');assert.equal(r.outputs,1000);assert.equal(r.integerCheck,'SIMULATED_ZERO_DIFFERENCE');assert.equal(r.realPLStarts,0);
  job=store.create({source:'DEMO',type:'benchmark',options:{runs:6,warmup:2}});advance(3000);r=store.get(job.id).result;
  assert.equal(r.samples.length,6);assert.ok(r.p95Ms>=r.p50Ms);assert.ok(Math.abs(r.p50Ms-61.195)<1e-9);assert.equal(r.accuracy,null);assert.equal(r.timingsSource,'SYNTHETIC_FIXTURE');
});
test('physical and malformed tasks fail before creation', () => {
  const {store}=virtualStore();assert.throws(()=>store.create({...inference(),source:'BOARD'}),e=>e.code==='HARDWARE_LOCKED');
  assert.throws(()=>store.create({source:'DEMO',type:'layer',options:{layerIndex:49,case:'real'}}),e=>e.code==='BAD_LAYER');
  assert.throws(()=>store.create({source:'DEMO',type:'benchmark',options:{runs:999,warmup:0}}),e=>e.code==='BAD_RUNS');assert.equal(store.jobs.size,0);
});
test('saved profiles are validated data only and secrets are rejected',()=>{
  assert.equal(validateProfile({kind:'network',endpoint:'http://kv260:8080'}).connection,'HARDWARE_LOCKED');
  assert.throws(()=>validateProfile({kind:'network',endpoint:'http://user:secret@kv260:8080'}),e=>e.code==='BAD_PROFILE');
  assert.equal(validateProfile({kind:'serial',port:'com11',baud:115200}).port,'COM11');
  assert.throws(()=>validateProfile({kind:'serial',port:'COM0',baud:115200}),e=>e.code==='BAD_PROFILE');
});
test('diagnostics never contact a device or permit shell commands',()=>{
  const {store}=virtualStore();const snapshot=store.diagnostics('owner_status');assert.equal(snapshot.hardwareContacted,false);assert.equal(snapshot.ownerState,'NOT_QUERIED');
  assert.throws(()=>store.diagnostics('sudo reboot'),e=>e.code==='BAD_ACTION');
});
test('HTTP integration serves app, enforces CSP and hardware lock, handles jobs and rejects cross-origin',async()=>{
  let now=200000;const {server}=createWorkbenchServer({clock:()=>now});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  const post=(route,data,headers={})=>fetch(base+route,{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(data)});
  try{
    const home=await fetch(base+'/');assert.equal(home.status,200);assert.ok((await home.text()).includes('KV260 LAB'));assert.ok(home.headers.get('content-security-policy').includes("connect-src 'self'"));
    for(const asset of ['/app.js','/styles.css','/tokens.css','/assets/bridge.svg'])assert.equal((await fetch(base+asset)).status,200);
    for(const route of ['/api/connect/network','/api/serial/open','/api/board/status']){const res=await post(route,{});assert.equal(res.status,423);assert.equal((await res.json()).hardwareContacted,false);}
    const real=await post('/api/jobs',{...inference(),source:'BOARD'});assert.equal(real.status,423);
    const csrf=await post('/api/jobs',inference(),{Origin:'https://unrelated.example'});assert.equal(csrf.status,403);
    const profile=await post('/api/profiles',{kind:'network',endpoint:'http://kv260:8080'});assert.equal((await profile.json()).hardwareContacted,false);
    const res=await post('/api/jobs',inference());assert.equal(res.status,201);const job=await res.json();now+=7000;
    const completed=await(await fetch(base+`/api/jobs/${job.id}`)).json();assert.equal(completed.status,'SUCCEEDED');assert.equal(completed.result.realPLStarts,0);
    const jsonExport=await fetch(base+`/api/jobs/${job.id}/export.json`);assert.ok(jsonExport.headers.get('content-disposition').includes('attachment'));assert.equal((await jsonExport.json()).result.logits.length,1000);
    assert.equal((await fetch(base+`/api/jobs/${job.id}/export.csv`)).status,409);
    const bench=await(await post('/api/jobs',{source:'DEMO',type:'benchmark',options:{runs:6,warmup:2}})).json();now+=3000;
    const csvExport=await fetch(base+`/api/jobs/${bench.id}/export.csv`);assert.equal(csvExport.status,200);const csvText=await csvExport.text();assert.equal(csvText.split('\r\n').length,7);assert.ok(csvText.includes('SYNTHETIC_FIXTURE'));assert.ok(csvExport.headers.get('content-disposition').endsWith('.csv"'));
    assert.equal((await fetch(base+'/server.mjs')).status,404);assert.equal((await fetch(base+'/api/jobs/demo_missing')).status,404);
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
