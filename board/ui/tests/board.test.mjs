import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { BoardClient, validateBoardJob } from '../board-client.mjs';
import { createWorkbenchServer } from '../server.mjs';

test('board reply binds input/run and rejects fixtures, malformed logits or PL claims',()=>{
  const id='board_123456789abc',sha='a'.repeat(64);
  const job={id,source:'BOARD',type:'inference',status:'SUCCEEDED',input_sha256:sha,result:{source:'BOARD',schema:'b_inference_result_v1',status:'PASS',execution_kind:'CPU_FP32',PL_starts:0,fallback_count:0,model_linear_count:49,logits:Array(1000).fill(0),top5:Array.from({length:5},(_,index)=>({index,name:'class_'+index,logit:0,probability:.001})),source_sha256:sha,run_id:id}};
  assert.doesNotThrow(()=>validateBoardJob(job));
  for(const changed of [{source:'DEMO'},{result:{...job.result,logits:[0]}},{result:{...job.result,source_sha256:'b'.repeat(64)}},{result:{...job.result,PL_starts:49}},{result:{...job.result,run_id:'other'}}])assert.throws(()=>validateBoardJob({...job,...changed}),e=>e.code==='BAD_BOARD_REPLY');
  assert.throws(()=>validateBoardJob({...job,result:{...job.result,top5:job.result.top5.map(x=>({...x,probability:.786}))}}),e=>e.code==='BAD_BOARD_REPLY');
});
test('fixed tunnel forbids external destinations and malformed board identity',async()=>{
  assert.throws(()=>new BoardClient('http://kv260:8080'));assert.throws(()=>new BoardClient('http://127.0.0.1:8741/secret'));
  const peer=http.createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({source:'DEMO',payload_verified:true}));});
  await new Promise(r=>peer.listen(0,'127.0.0.1',r));
  try{await assert.rejects(new BoardClient(`http://127.0.0.1:${peer.address().port}`).request('/health'),e=>e.code==='BAD_BOARD_IDENTITY');}finally{peer.closeAllConnections();await new Promise(r=>peer.close(r));}
});
test('PL reply requires all 49 actual receipts and the frozen owner identity',()=>{
  const id='board_123456789abc',digest='a'.repeat(64),boot='test_boot';
  const i={uid:1000,software_test:false,boot_id:boot,owner_pid:1234,tags:[0xe7333e5e,0x9c258169],uuid:'a1d6968d-c5b3-c896-a565-94ddf751a2c7',candidate_sha256:digest,window_manifest_sha256:digest,
    owner_sha256:'c6c2e3735860f8295873395b91cbd359b948a2d4789962522add9b76b7a446e7',driver_sha256:'cba324ac56c9bebe84fabbdf2635ea699b10ec718593cba7d44c4b004f782eca',B_manifest_sha256:'77680bacbcf6c46571fd6fc99c40778528dd366e950023b73058b6932515962e'};
  const layers=Array.from({length:49},(_,index)=>({layer_index:index,layer_id:'layer_'+index,model_run_id:id,image_id:'img0000',request_id:`${id}_img0000_l${index}`,starts_delta:1,starts_before:index,starts_after:index+1,owner_pid:1234,fallback_count:0,X_sha256:digest,W_sha256:digest,Y_sha256:digest}));
  const r={source:'BOARD',schema:'b_inference_result_v1',status:'PASS',execution_kind:'FPGA_MODEL',PL_starts:49,fallback_count:0,model_linear_count:49,observed_custom_calls:49,logits:Array(1000).fill(0),top5:Array.from({length:5},(_,index)=>({index,name:'class_'+index,logit:0,probability:.001})),source_sha256:digest,run_id:id,layers,actual_owner_identity:i,bundle_identity:{manifest_sha256:i.B_manifest_sha256},candidate_sha256:digest,window_manifest_sha256:digest,bridge_sha256:digest,bridge_config_sha256:digest,C_ready_sha256:digest};
  r.candidate_sha256='6d7e2162b8b066d741e50640cd40d2a95895458f859f34b2b8e346599e6cccd2';
  r.bridge_sha256='b5d09e4106c1fda58ca4fec9880b774b335e2e94c44be3580d94a1c5bfd50faa';
  r.C_ready_sha256='a85abd13250464ff6edfada7bac9ebe10007be7a2eed028449bc8edbb4d474f2';
  i.candidate_sha256=r.candidate_sha256;
  const job={id,source:'BOARD',type:'inference',status:'SUCCEEDED',options:{backend:'pl49'},boot_id:boot,input_sha256:digest,realPLStarts:49,result:r};
  assert.doesNotThrow(()=>validateBoardJob(job));
  for(const changed of [{layers:layers.slice(1)},{actual_owner_identity:{...i,software_test:true}},{actual_owner_identity:{...i,boot_id:'old'}},{actual_owner_identity:{...i,owner_sha256:digest}},{layers:layers.map((x,index)=>index===5?{...x,starts_delta:0}:x)},{fallback_count:1}])assert.throws(()=>validateBoardJob({...job,result:{...r,...changed}}),e=>e.code==='BAD_BOARD_REPLY');
  const guard={...r,
    candidate_sha256:'3449f437c8448a76310eb4dea39c273815278b665495db2317ce1e74ea793562',
    bridge_sha256:'d07dfe6496e9e0e6c5838ec239e46716eec04de016cc8a126e118eee7ddfc8f4',
    C_ready_sha256:'b6fe03685cc59164ed52278aed068473c68dbcc0368fcec87c9aea2be0fe7646',
    actual_owner_identity:{...i,owner_sha256:'89dac2ffae96ef647d41e00a4dd5d20f6c9ad97c033eff9dcfea101caa88affc',
      candidate_sha256:'3449f437c8448a76310eb4dea39c273815278b665495db2317ce1e74ea793562'}};
  assert.doesNotThrow(()=>validateBoardJob({...job,result:guard}));
  for(const key of ['candidate_sha256','bridge_sha256','C_ready_sha256']) {
    const mixed={...guard,[key]:digest,actual_owner_identity:{...guard.actual_owner_identity}};
    if(key==='candidate_sha256')mixed.actual_owner_identity.candidate_sha256=digest;
    assert.throws(()=>validateBoardJob({...job,result:mixed}),e=>e.code==='BAD_BOARD_REPLY');
  }
  for(const key of ['owner_sha256','driver_sha256','B_manifest_sha256']) {
    assert.throws(()=>validateBoardJob({...job,result:{...guard,
      actual_owner_identity:{...guard.actual_owner_identity,[key]:digest}}}),e=>e.code==='BAD_BOARD_REPLY');
  }
  const snapshot={...r,
    candidate_sha256:'410575d9fb64e120c54e0e7bf58cb099f055efe30686294f36b72d824eb2e58e',
    bridge_sha256:'bcb3969eeb6c21eccdb2d53b2a035537f8772d405bfc8ccb24a2ce33c7bc1400',
    C_ready_sha256:'356c19ae0ba45f7c85e771a513c5f2246c69688e067d60bf6eb4c291bfe64c45',
    actual_owner_identity:{...i,owner_sha256:'6da1d490a0379173b19b3197a3a04f4d54898f83df135193aaf6e7bd8bac10c8',
      candidate_sha256:'410575d9fb64e120c54e0e7bf58cb099f055efe30686294f36b72d824eb2e58e'}};
  assert.doesNotThrow(()=>validateBoardJob({...job,result:snapshot}));
  for(const key of ['candidate_sha256','bridge_sha256','C_ready_sha256']) {
    const mixed={...snapshot,[key]:guard[key],actual_owner_identity:{...snapshot.actual_owner_identity}};
    if(key==='candidate_sha256')mixed.actual_owner_identity.candidate_sha256=guard[key];
    assert.throws(()=>validateBoardJob({...job,result:mixed}),e=>e.code==='BAD_BOARD_REPLY');
  }
  for(const key of ['owner_sha256','driver_sha256','B_manifest_sha256']) {
    assert.throws(()=>validateBoardJob({...job,result:{...snapshot,
      actual_owner_identity:{...snapshot.actual_owner_identity,[key]:digest}}}),e=>e.code==='BAD_BOARD_REPLY');
  }
  assert.throws(()=>validateBoardJob({...job,result:{...guard,
    actual_owner_identity:{...guard.actual_owner_identity,owner_sha256:snapshot.actual_owner_identity.owner_sha256}}}),e=>e.code==='BAD_BOARD_REPLY');
  const shaOwner={...r,
    candidate_sha256:'1678b9dd914b672ac81e8dae9b8f2e506244b3c2e2ca48e99bea24a887b76ddd',
    bridge_sha256:'7872e29daae511fa5d0ea57d67685abc6beed00d124a432e9bd3f96e45d9f217',
    C_ready_sha256:'f8c9d906981e055a530c64b86a1da9eb28d862212c60e14b85d7f8b41012b559',
    actual_owner_identity:{...i,owner_sha256:'aa866241a69ed5b8d069464b4663ccca5e2a703adba0329d1dc8cf34ca8b258d',
      candidate_sha256:'1678b9dd914b672ac81e8dae9b8f2e506244b3c2e2ca48e99bea24a887b76ddd'}};
  assert.doesNotThrow(()=>validateBoardJob({...job,result:shaOwner}));
  for(const parent of [guard,snapshot]) {
    for(const key of ['candidate_sha256','bridge_sha256','C_ready_sha256']) {
      const mixed={...shaOwner,[key]:parent[key],actual_owner_identity:{...shaOwner.actual_owner_identity}};
      if(key==='candidate_sha256')mixed.actual_owner_identity.candidate_sha256=parent[key];
      assert.throws(()=>validateBoardJob({...job,result:mixed}),e=>e.code==='BAD_BOARD_REPLY');
    }
    assert.throws(()=>validateBoardJob({...job,result:{...shaOwner,
      actual_owner_identity:{...shaOwner.actual_owner_identity,owner_sha256:parent.actual_owner_identity.owner_sha256}}}),e=>e.code==='BAD_BOARD_REPLY');
  }
  for(const key of ['owner_sha256','driver_sha256','B_manifest_sha256']) {
    assert.throws(()=>validateBoardJob({...job,result:{...shaOwner,
      actual_owner_identity:{...shaOwner.actual_owner_identity,[key]:digest}}}),e=>e.code==='BAD_BOARD_REPLY');
  }
  const fullBo={...r,
    candidate_sha256:'f310b480b5546f6eb6e4814ad857879187e4046d211eab0de088af18fad4bec0',
    bridge_sha256:'dea28832645bd66718eb44c254848f12defe57474028e2636c2b19d77bf63d48',
    C_ready_sha256:'8df588686af2c6b19783df3fa1f7422ac168c6308206e9e8339b9feccb0e249e',
    actual_owner_identity:{...i,owner_sha256:'5027def50d442fe2608ac742044cacaddda0c542e0da431aa557dd8e25ae4823',
      candidate_sha256:'f310b480b5546f6eb6e4814ad857879187e4046d211eab0de088af18fad4bec0'}};
  assert.doesNotThrow(()=>validateBoardJob({...job,result:fullBo}));
  const originalSha={...r,
    candidate_sha256:'98c631580cf8d24ec49bd8bee32d2f948becaa500e4fe06faf857b61143bfd81',
    bridge_sha256:'01953e1b054adb165c49ee9894fdda9e5a33455dfcbda63058fe503f47983726',
    C_ready_sha256:'d0ef2c8ae686f08417aa4649e15eb0f6781194b8d497a5864f946fc0cd1b1a5a',
    actual_owner_identity:{...i,candidate_sha256:'98c631580cf8d24ec49bd8bee32d2f948becaa500e4fe06faf857b61143bfd81'}};
  assert.doesNotThrow(()=>validateBoardJob({...job,result:originalSha}));
  const cacheable={...r,
    candidate_sha256:'45183717bc4183c7cc6b580ecf20a64801bebbeb1eb2499bec2ff9d0f2047da9',
    bridge_sha256:'94dd50a045a3c49312a48995a9411c5ac16f44ab831e0a82c68113ea9f711b6c',
    C_ready_sha256:'41b534113e3611d130ee5716f89a26573b2a43e3a9aab499df99979c4f185dce',
    actual_owner_identity:{...i,owner_sha256:'9f59328e2f710cc6e6ed96d708dd0615228edc502ef1810f696de85665c20040',
      candidate_sha256:'45183717bc4183c7cc6b580ecf20a64801bebbeb1eb2499bec2ff9d0f2047da9'}};
  assert.doesNotThrow(()=>validateBoardJob({...job,result:cacheable}));
  for(const candidate of [r,originalSha,guard,snapshot,shaOwner,fullBo,cacheable]) {
    for(const other of [r,originalSha,guard,snapshot,shaOwner,fullBo,cacheable]) {
      if(candidate===other)continue;
      for(const key of ['candidate_sha256','bridge_sha256','C_ready_sha256']) {
        const mixed={...candidate,[key]:other[key],actual_owner_identity:{...candidate.actual_owner_identity}};
        if(key==='candidate_sha256')mixed.actual_owner_identity.candidate_sha256=other[key];
        assert.throws(()=>validateBoardJob({...job,result:mixed}),e=>e.code==='BAD_BOARD_REPLY');
      }
      if(candidate.actual_owner_identity.owner_sha256!==other.actual_owner_identity.owner_sha256) {
        assert.throws(()=>validateBoardJob({...job,result:{...candidate,
          actual_owner_identity:{...candidate.actual_owner_identity,owner_sha256:other.actual_owner_identity.owner_sha256}}}),e=>e.code==='BAD_BOARD_REPLY');
      }
    }
  }
  for(const key of ['owner_sha256','driver_sha256','B_manifest_sha256']) {
    assert.throws(()=>validateBoardJob({...job,result:{...fullBo,
      actual_owner_identity:{...fullBo.actual_owner_identity,[key]:digest}}}),e=>e.code==='BAD_BOARD_REPLY');
  }
  for(const key of ['owner_sha256','driver_sha256','B_manifest_sha256']) {
    assert.throws(()=>validateBoardJob({...job,result:{...cacheable,
      actual_owner_identity:{...cacheable.actual_owner_identity,[key]:digest}}}),e=>e.code==='BAD_BOARD_REPLY');
  }
});
test('proxy transfers actual image bytes and preserves board errors instead of falling back',async()=>{
  let posted=null;
  const peer=http.createServer(async(req,res)=>{res.setHeader('Content-Type','application/json');if(req.method==='POST'){let chunks=[];for await(const c of req)chunks.push(c);posted=JSON.parse(Buffer.concat(chunks));if(posted.options.backend==='pl49'){res.statusCode=409;return res.end(JSON.stringify({error:{code:'PL_NOT_READY',message:'No bridge'}}));}res.statusCode=201;return res.end(JSON.stringify({id:'board_123456789abc',source:'BOARD',status:'VALIDATING'}));}res.end(JSON.stringify({jobs:[]}));});
  await new Promise(r=>peer.listen(0,'127.0.0.1',r));const {server}=createWorkbenchServer({boardUrl:`http://127.0.0.1:${peer.address().port}`});await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const base=`http://127.0.0.1:${server.address().port}`,payload={source:'BOARD',type:'inference',options:{backend:'fp32',input:{name:'actual.png'}},image:{mime:'image/png',base64:'AAECAw=='}};
  try{
    const res=await fetch(base+'/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});assert.equal(res.status,201);assert.equal(posted.image.base64,payload.image.base64);assert.equal((await res.json()).source,'BOARD');
    const bad=await fetch(base+'/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,options:{backend:'pl49'}})});assert.equal(bad.status,409);assert.equal((await bad.json()).error.code,'PL_NOT_READY');assert.equal((await(await fetch(base+'/api/jobs')).json()).jobs.length,0);
    assert.equal((await fetch(base+'/api/serial/open',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,423);
  }finally{server.closeAllConnections();peer.closeAllConnections();await Promise.all([new Promise(r=>server.close(r)),new Promise(r=>peer.close(r))]);}
});
