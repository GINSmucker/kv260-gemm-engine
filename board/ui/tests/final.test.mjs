import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {finalData,validateFinalData} from '../final-data.mjs';
import {createFinalServer} from '../final-server.mjs';

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

test('final server serves actual artifacts and downloads; historical demo remains separate',async t=>{
  const server=createFinalServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const origin=`http://127.0.0.1:${server.address().port}`;
  for(const path of ['/','/final.js','/final.css','/api/final','/api/final/paired.csv','/api/final/timing.json','/api/final/comparison.csv','/architecture/report/software.svg','/architecture/software/software.html','/legacy/']) {
    const response=await fetch(origin+path);assert.equal(response.status,200,path);assert.ok((await response.text()).length>100,path);
  }
  assert.equal((await fetch(origin+'/api/final',{method:'POST'})).status,405);
  assert.equal((await fetch(origin+'/api/final/paired.csv')).headers.get('content-type'),'text/csv; charset=utf-8');
  assert.equal((await fetch(origin+'/api/board/health')).status,423);
  assert.equal((await fetch(origin+'/architecture/%2e%2e%2f%2e%2e%2fREADME.md')).status,404);
  assert.match((await fetch(origin+'/architecture/software/software.html')).headers.get('content-security-policy'),/script-src 'self' 'unsafe-inline'/);
});
