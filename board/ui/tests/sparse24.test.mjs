import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {Sparse24Client,SPARSE24_MODEL,boardEndpoint,validateSparseResult} from '../sparse24-client.mjs';
import {createWorkbenchServer} from '../server.mjs';
import {livePrediction} from '../public/sparse24-live.js';

// The saved historical receipt supplies the wire shape; all transport tests use a local mock.
const saved=JSON.parse(await readFile(new URL('./fixtures/sparse24-success.json',import.meta.url),'utf8'));
const jpeg=Buffer.from([255,216,255,225,0,1,255,217]);
const digest=createHash('sha256').update(jpeg).digest('hex');
const id='a'.repeat(32);

async function board(t,{disconnect=false,wrongModel=false}={}) {
  let posts=0,gets=0;
  const status={schema:'opt50_live_demo_status_v1',boot_id:saved.boot_id,source_model_sha256:wrongModel?'0'.repeat(64):SPARSE24_MODEL,
    stage:'READY',busy:false,automatic_replay:false,selftest_completed_PL_calls:36,selftest_bitwise_mismatches:0};
  const receipt=()=>({...structuredClone(saved),request_id:id,image_sha256:digest});
  const server=http.createServer(async(req,res)=>{
    const send=value=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,value}));};
    if(req.url==='/api/status'){gets++;return send(status);}
    if(req.method==='POST'){
      posts++;assert.equal(req.url,'/api/infer/'+id);assert.equal(req.headers['content-type'],'image/jpeg');
      assert.equal(req.headers.origin,`http://${req.headers.host}`);
      const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(Buffer.concat(chunks),jpeg);
      if(disconnect)return res.destroy();
      return send({request_id:id,stage:'ADMITTED'});
    }
    if(req.url==='/api/result/'+id)return send(receipt());
    if(req.url==='/api/history')return send({records:[{request_id:id,stage:'PASS',boot_id:saved.boot_id,image_sha256:digest}]});
    res.writeHead(404);res.end('{}');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {endpoint:`http://127.0.0.1:${server.address().port}`,status,receipt,counts:()=>({posts,gets})};
}

test('Sparse24 accepts only explicit private IPv4 endpoints',()=>{
  assert.equal(boardEndpoint('http://192.168.137.2:8080'),'http://192.168.137.2:8080');
  for(const endpoint of ['https://192.168.1.2','http://example.com','http://8.8.8.8','http://user:pass@192.168.1.2','http://192.168.1.2/api','http://192.168.1.2/?x=1'])assert.throws(()=>boardEndpoint(endpoint));
});

test('Sparse24 rejects wrong identity, stale boot, changed input and incomplete completion proof',()=>{
  const expected={boot_id:saved.boot_id,image_sha256:saved.image_sha256};
  assert.equal(validateSparseResult(saved,saved.request_id,expected),saved);
  for(const change of [r=>r.source_model_sha256='0'.repeat(64),r=>r.boot_id='11111111-1111-1111-1111-111111111111',
    r=>r.image_sha256='0'.repeat(64),r=>r.native.postrun_buffer_checks_verified=false,r=>r.native.PL_sparse=23,
    r=>r.PL_logits_sha256='0'.repeat(64),r=>r.top5[0].index=r.top5[1].index,r=>r.top5.reverse()]){
    const value=structuredClone(saved);change(value);assert.throws(()=>validateSparseResult(value,saved.request_id,expected));
  }
});

test('connection is read-only; one image is submitted once and completed through the original ID',async t=>{
  const mock=await board(t),client=new Sparse24Client();
  await client.connect(mock.endpoint);assert.equal(mock.counts().posts,0);
  const response=await client.submit(id,jpeg,saved.boot_id);assert.equal(response.expected.image_sha256,digest);assert.equal(response.result.stage,'ADMITTED');
  await assert.rejects(client.submit(id,jpeg,saved.boot_id),/查询原编号/);assert.equal(mock.counts().posts,1);
  const result=await client.result(id);assert.equal(result.result.stage,'PASS');assert.equal(client.active,null);
  assert.equal((await client.history()).records.length,1);
  assert.equal(mock.counts().posts,1);
});

test('ambiguous POST disconnect is never replayed; GET can recover its completion',async t=>{
  const mock=await board(t,{disconnect:true}),client=new Sparse24Client();await client.connect(mock.endpoint);
  await assert.rejects(client.submit(id,jpeg,saved.boot_id),/只查询原编号/);
  assert.equal(client.active,id);assert.equal(mock.counts().posts,1);
  await assert.rejects(client.submit(id,jpeg,saved.boot_id),/不会重复发送/);
  assert.equal((await client.result(id)).result.stage,'PASS');assert.equal(mock.counts().posts,1);
});

test('wrong model, changed boot and malformed image stop before a hardware POST',async t=>{
  const wrong=await board(t,{wrongModel:true}),client=new Sparse24Client();await assert.rejects(client.connect(wrong.endpoint));assert.equal(wrong.counts().posts,0);
  const mock=await board(t);await client.connect(mock.endpoint);
  await assert.rejects(client.submit(id,jpeg,'11111111-1111-1111-1111-111111111111'),/启动已变化/);
  assert.equal(client.active,null);assert.equal(mock.counts().posts,0);
  await assert.rejects(client.submit(id,Buffer.from('not jpeg'),saved.boot_id));assert.equal(mock.counts().posts,0);
});

test('LAB proxy enforces origin, JPEG upload and returns validated real-result downloads',async t=>{
  const mock=await board(t),server=createWorkbenchServer().server;
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
  const origin=`http://127.0.0.1:${server.address().port}`;
  assert.equal((await (await fetch(origin+'/api/sparse24/connection')).json()).configured,false);assert.equal(mock.counts().gets,0);
  const payload={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:mock.endpoint})};
  assert.equal((await fetch(origin+'/api/sparse24/connect',{...payload,headers:{...payload.headers,Origin:'http://evil.example'}})).status,403);
  assert.equal(mock.counts().gets,0);
  assert.equal((await fetch(origin+'/api/sparse24/connect',payload)).status,200);assert.equal(mock.counts().posts,0);
  assert.equal((await fetch(origin+'/api/sparse24/infer/'+id,{method:'POST',headers:{'Content-Type':'text/plain'},body:jpeg})).status,415);
  const submitted=await fetch(origin+'/api/sparse24/infer/'+id,{method:'POST',headers:{'Content-Type':'image/jpeg','X-Sparse24-Boot':saved.boot_id},body:jpeg});
  assert.equal(submitted.status,202);assert.equal(mock.counts().posts,1);
  const result=await fetch(origin+'/api/sparse24/result/'+id+'?download=1');assert.equal(result.status,200);
  assert.match(result.headers.get('content-disposition'),/attachment/);assert.equal((await result.json()).result.native.PL_sparse,24);
  assert.equal((await fetch(origin+'/api/sparse24/result/'+id+'?image='+'0'.repeat(64))).status,200);
});

test('real result card has no synthetic fallback and labels proof and unclosed device correctly',()=>{
  const html=livePrediction(saved,{boot_id:saved.boot_id});
  assert.match(html,/tench/);assert.match(html,/Native 核验调用/);assert.match(html,/板端报告/);assert.match(html,/未声明关闭/);
  assert.doesNotMatch(livePrediction(null),/tench|合成概率/);
});
