import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {finalData,validateFinalData} from '../final-data.mjs';
import {createFinalServer} from '../final-server.mjs';
import {finalOverview,finalPerformance,finalArchitecture,pairedChart,liveServicePerformance} from '../public/final-lab.js';

const csv=await readFile(new URL('../../../results/board500/paired195_cpu_pl.csv',import.meta.url),'utf8');

test('final data binds all 195 pairs and disjoint timing partitions to published metrics',async()=>{
  const data=await finalData();
  assert.equal(data.samples.length,195);
  assert.equal(data.live,false);
  assert.equal(data.timing.models.Sparse24.images.length,195);
  assert.equal(data.metrics.CPU_PL_bitwise_mismatches.Sparse24,0);
});

test('mismatched CSV, missing receipts and changed timing means are rejected',async()=>{
  const data=await finalData();
  assert.throws(()=>validateFinalData(data.metrics,csv.replace('144.793439','44.793439'),data.timing),/distributions/);
  const missing=structuredClone(data.timing);missing.models.Dense.images.pop();
  assert.throws(()=>validateFinalData(data.metrics,csv,missing),/coverage/);
  const changed=structuredClone(data.timing);changed.models.Dense.mean_ms.start_to_completion_ms+=1;
  assert.throws(()=>validateFinalData(data.metrics,csv,changed),/mean/);
});

test('LAB entry aliases share the final dashboard and serve verified downloads',async t=>{
  const server=createFinalServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const index=await (await fetch(origin+'/')).text();
  assert.match(index,/KV260 LAB/);assert.match(index,/\/final-lab.css/);assert.match(index,/\/app.js/);
  for(const path of ['/legacy/','/legacy','/index.html','/final.html']) assert.equal(await (await fetch(origin+path)).text(),index,path);
  for(const path of ['/app.js','/final-lab.js','/final-lab.css','/api/final','/api/final/paired.csv','/api/final/timing.json','/api/final/comparison.csv','/api/final/live-latency.json','/architecture/report/software.svg','/architecture/software/software.html']) {
    const response=await fetch(origin+path);assert.equal(response.status,200,path);assert.ok((await response.text()).length>100,path);
  }
  assert.equal((await fetch(origin+'/api/final',{method:'POST'})).status,405);
  assert.equal((await fetch(origin+'/api/final/paired.csv')).headers.get('content-type'),'text/csv; charset=utf-8');
  assert.equal((await fetch(origin+'/api/board/health')).status,423);
  assert.equal((await fetch(origin+'/architecture/%2e%2e%2f%2e%2e%2fREADME.md')).status,404);
  assert.match((await fetch(origin+'/architecture/software/software.html')).headers.get('content-security-policy'),/script-src 'self' 'unsafe-inline'/);
});

test('live request comparison retains original dimensions, complete latency and the slower first request',async()=>{
  const report=JSON.parse(await readFile(new URL('../../../results/live_demo/python_service_20261101.json',import.meta.url),'utf8'));
  const html=liveServicePerformance(report);
  assert.equal(report.image.resized_before_upload,false);
  assert.equal(report.comparison.warm_samples_per_phase,5);
  assert.equal(report.validation.cross_version_inputs_and_all_outputs_byte_identical,true);
  assert.match(html,/完整请求（本机往返）/);assert.match(html,/固定 100 ms 查询/);
  assert.match(html,/首张含初始化/);assert.match(html,/未改善/);
  assert.match(html,/Native \/ 配置不变/);
  assert.equal(liveServicePerformance(null),'');
});

test('LAB final views retain all 195 points and distinguish historical evidence and timing boundaries',async()=>{
  const data=await finalData(),overview=finalOverview(data),performance=finalPerformance(data),architecture=finalArchitecture(data);
  assert.match(overview,/136\.588/);assert.match(overview,/2\.218/);assert.match(overview,/71\.4/);
  assert.doesNotMatch(overview,/full3925|5000 图/);
  for(const series of ['pl','cpu']) {
    const chart=pairedChart(data,series,500);
    const curves=[...chart.svg.matchAll(/<polyline [^>]*points="([^"]+)"/g)];
    assert.equal(curves.length,2);for(const curve of curves)assert.equal(curve[1].split(' ').length,195);
    const row=data.samples.at(-1),key=series==='pl'?'Sparse_PS_PL_ms':'Sparse_CPU_ms';
    assert.ok(chart.detail.includes(row[key].toFixed(3)));
  }
  assert.match(performance,/计时外验收成本/);assert.match(performance,/不是纯 PL 周期/);
  assert.equal((architecture.match(/报告 SVG/g)||[]).length,4);
  assert.match(architecture,/批末关闭会话/);assert.match(architecture,/非新生成的 Vivado 截图/);
  assert.match(finalOverview(null),/最终数据暂不可用/);
});
