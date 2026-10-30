import test from 'node:test';
import assert from 'node:assert/strict';
import {statistics,comparison,comparisonChart,routeMap} from '../public/charts.js';
import {presentationData} from '../presentation.mjs';
import {createWorkbenchServer} from '../server.mjs';
import {boardBenchmark} from '../public/live-view.js';

test('percentiles retain the slow sample and do not mutate recorded order',()=>{
  const values=[12,1000,10,14];
  const s=statistics(values);
  assert.deepEqual(values,[12,1000,10,14]);
  assert.equal(s.p50,13);assert.equal(s.max,1000);assert.ok(Math.abs(s.p95-852.1)<1e-9);
  assert.equal(statistics([]),null);assert.equal(statistics([12,NaN]),null);assert.equal(statistics([-1,12]),null);
});
test('reference switch uses the selected paired path and preserves a regression',()=>{
  const samples=[{index:1,fp32Ms:20,w8a8Ms:8,plMs:10},{index:2,fp32Ms:24,w8a8Ms:10,plMs:12}];
  assert.equal(comparison(samples,'fp32').ratio,2);
  const w8=comparison(samples,'w8');assert.equal(w8.cpu.p50,9);assert.equal(w8.saved,-2);assert.ok(w8.reduction<0);
  assert.equal(comparison([{fp32Ms:1,plMs:2}],'w8'),null);
});
test('two measured curves retain every point, keyboard values and zero-duration edge cases',()=>{
  const samples=Array.from({length:3},(_,i)=>({index:i+1,fp32Ms:i===2?1000:20,w8a8Ms:15+i,plMs:10+i}));
  const svg=comparisonChart(samples,'fp32');
  assert.equal((svg.match(/<polyline /g)||[]).length,2);assert.equal((svg.match(/class="curve-pair"/g)||[]).length,3);
  assert.ok(svg.includes('1000.000'));assert.ok(svg.includes('全部 3 个正式样本'));
  assert.doesNotMatch(comparisonChart([{index:1,fp32Ms:0,w8a8Ms:0,plMs:0}]),/NaN|Infinity/);
});
test('saved presentation binds real receipts and exposes the complete fixed route',async()=>{
  const data=await presentationData();
  assert.equal(data.source,'SAVED_MEASUREMENTS');assert.equal(data.live,false);
  assert.equal(data.candidate,'AB_H576_OPT45_PS_GELU_20261016');
  assert.equal(data.benchmark.result.samples.length,3);assert.equal(data.formal.length,2);
  assert.equal(data.quality.full3925.samples,3925);assert.equal(data.stability.graphs,5000);
  const routes=data.inference.result.route_manifest;
  assert.equal(routes.length,49);assert.equal(routes.filter(x=>x.backend==='PL').length,36);
  const html=routeMap(routes);assert.equal((html.match(/class="route-cell /g)||[]).length,49);
  assert.ok(html.includes('head，CPU'));assert.ok(html.includes('BLOCK 11'));
});
test('read-only showcase is served without a board connection or a running model',async()=>{
  const {server}=createWorkbenchServer();
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const response=await fetch(`http://127.0.0.1:${server.address().port}/api/presentation`);
    assert.equal(response.status,200);const data=await response.json();assert.equal(data.live,false);
    assert.equal(data.benchmark.status,'SUCCEEDED');assert.equal(data.sources.length,4);
    const json=await fetch(`http://127.0.0.1:${server.address().port}/api/presentation/benchmark.json`);
    assert.match(json.headers.get('content-disposition'),/attachment/);assert.deepEqual(await json.json(),data.benchmark);
    const csv=await fetch(`http://127.0.0.1:${server.address().port}/api/presentation/benchmark.csv`);
    const rows=(await csv.text()).trim().split('\r\n');assert.equal(rows.length,4);assert.match(rows[1],/SAVED_MEASUREMENTS/);
  } finally { server.closeAllConnections();await new Promise(resolve=>server.close(resolve)); }
});
test('H576 live view uses completed receipts and does not plot pending or failed work',async()=>{
  const data=await presentationData();
  const state={boardHealth:{model_protocol:'h576_v5'},warmup:1,runs:3,benchmarkReference:'w8'};
  const html=boardBenchmark(state,data.benchmark,{busy:false,run:'<button>RUN</button>'});
  assert.equal((html.match(/<polyline /g)||[]).length,2);assert.match(html,/144.39/);
  const pending=boardBenchmark(state,{status:'RUNNING'},{busy:true,run:''});
  assert.doesNotMatch(pending,/<polyline /);assert.match(pending,/disabled/);
  const failed=boardBenchmark(state,{status:'FAILED',error:{message:'连接中断'}},{busy:false,run:''});
  assert.doesNotMatch(failed,/<polyline /);assert.match(failed,/连接中断/);
});
