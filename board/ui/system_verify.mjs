// Real S4 Node proxy/exports. Browser proof is supplied only after actual UI use.
import { readFile, writeFile, mkdir, access, appendFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createWorkbenchServer } from './server.mjs';
import { validateBoardJob } from './board-client.mjs';
import { createEvidenceSink } from './evidence_sink.mjs';

const args=Object.fromEntries(process.argv.slice(2).reduce((pairs,value,index,all)=>index%2===0?[...pairs,[value,all[index+1]]]:pairs,[]));
const output=path.resolve(args['--output']);await mkdir(output,{recursive:true});
const requests=path.resolve(args['--requests']),browserFile=path.join(output,'browser_verification.json');
const digest=data=>createHash('sha256').update(data).digest('hex');
const exists=async name=>{try{await access(name);return true;}catch{return false;}};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const uiRoot=path.dirname(fileURLToPath(import.meta.url)),sourceHashes={};
// Generated test scratch data is not UI source and can contain legacy ACLs.
const excludedGeneratedPaths=['tests/.tmp'];
async function sourceNames(directory='',names=[]) {
  for(const entry of await readdir(path.join(uiRoot,directory),{withFileTypes:true})) {
    const name=path.posix.join(directory,entry.name);
    if(excludedGeneratedPaths.includes(name))continue;
    if(entry.isDirectory())await sourceNames(name,names);
    else if(entry.isFile())names.push(name);
  }
  return names;
}
for(const name of await sourceNames()){
  if(!/\.(mjs|js|html|css)$/.test(name))continue;
  const data=await readFile(path.join(uiRoot,name));sourceHashes[name.replaceAll('\\','/')]=digest(data);
  const copy=path.join(output,'ui_sources',name);await mkdir(path.dirname(copy),{recursive:true});await writeFile(copy,data,{flag:'wx'});
}
await writeFile(path.join(output,'ui_source_scope.json'),JSON.stringify({root:'ui',
  included_extensions:['mjs','js','html','css'],excluded_generated_paths:excludedGeneratedPaths,
  reason:'Generated test scratch directories are not source files.'},null,2),{flag:'wx'});
await writeFile(path.join(output,'ui_source_snapshot.json'),JSON.stringify(sourceHashes,null,2),{flag:'wx'});
const {server}=createWorkbenchServer({boardUrl:'http://127.0.0.1:8743'});
const httpObservations=[];
server.on('request',(req,res)=>res.once('finish',()=>{
  const record={method:req.method,url:req.url,status:res.statusCode,user_agent:req.headers['user-agent']??'',
    content_type:res.getHeader('Content-Type'),client_utc:new Date().toISOString()};
  httpObservations.push(record);
  void appendFile(path.join(output,'http_observations.jsonl'),JSON.stringify(record)+'\n');
}));
await new Promise(r=>server.listen(4174,'127.0.0.1',r));
console.log('S4 actual UI http://127.0.0.1:4174; waiting for real agent requests');
const started=Date.now();
let evidenceServer=null;
try {
  while(!await exists(requests)){assert(Date.now()-started<1800000);await sleep(500);}
  const contract=JSON.parse(await readFile(requests,'utf8'));
  const base='http://127.0.0.1:4174';
  const get=async suffix=>{const r=await fetch(base+suffix);if(!r.ok)throw new Error(await r.text());return r.json();};
  const health=await get('/api/board/health');assert(health.pl_ready && health.pl_identity.candidate_sha256===contract.candidate_sha256);
  const records=[];
  for(const row of contract.ui_jobs) {
    const job=await get('/api/jobs/'+row.id);validateBoardJob(job);
    const response=await fetch(base+'/api/jobs/'+row.id+'/export.json');assert(response.ok);
    const body=await response.text();assert.deepEqual(JSON.parse(body),job);
    const filename=row.id+'_export.json';await writeFile(path.join(output,filename),body);
    let csv=null;
    if(job.result?.layers){
      const response=await fetch(base+'/api/jobs/'+row.id+'/export.csv');assert(response.ok);csv=await response.text();
      const lines=csv.replace(/^\uFEFF/,'').split('\r\n');assert.equal(lines.length,50);
      assert(lines[0].includes('Y_sha256'));
      for(let index=0;index<49;index++)assert(lines[index+1].includes(job.result.layers[index].Y_sha256));
      await writeFile(path.join(output,row.id+'_layers.csv'),csv);
    }
    records.push({job_id:row.id,kind:row.kind,status:job.status,PL_starts:job.realPLStarts,json_file:filename,json_sha256:digest(body),csv_sha256:csv?digest(csv):null});
  }
  const preliminary={schema:'S4_windows_actual_proxy_exports_v1',status:'PASS_NODE_AWAIT_BROWSER',candidate_sha256:contract.candidate_sha256,window_manifest_sha256:contract.window_manifest_sha256,records,
    node:process.version,client_utc:new Date().toISOString()};
  await writeFile(path.join(output,'node_proxy_verification.json'),JSON.stringify(preliminary,null,2));
  evidenceServer=await createEvidenceSink(output,async (body,capture)=>{
    const observed=body.observations.join('\n');
    assert(observed.includes(body.browser_job_id) && observed.includes('SUCCEEDED'));
    assert(observed.includes('arm_pl') && observed.includes('49'));
    const live=await get('/api/jobs/'+body.browser_job_id);validateBoardJob(live);
    assert.equal(live.status,'SUCCEEDED');assert.equal(live.result.PL_starts,49);
    for(const ext of ['json','csv'])assert(httpObservations.some(r=>
      r.method==='GET' && r.url===`/api/jobs/${body.browser_job_id}/export.${ext}` && r.status===200 && /Mozilla/.test(r.user_agent)),
      'actual browser export not observed: '+ext);
    for(const ext of ['json','csv']){
      const response=await fetch(base+`/api/jobs/${body.browser_job_id}/export.${ext}`);assert(response.ok);
      const value=await response.text();
      if(ext==='json')assert.deepEqual(JSON.parse(value),live);
      else assert.equal(value.replace(/^\uFEFF/,'').split('\r\n').length,50);
      await writeFile(path.join(output,body.browser_job_id+'_export.'+ext),value,{flag:'wx'});
    }
    return {schema:'S4_actual_CUA_browser_v1',status:'PASS',candidate_sha256:contract.candidate_sha256,
      window_manifest_sha256:contract.window_manifest_sha256,browser_job_id:live.id,
      browser_real_PL_starts:49,observation_sha256:capture.observation_sha256,screenshot_sha256:capture.screenshot_sha256,
      screenshot_file:capture.screenshot_file,observation_file:capture.observation_file,
      browser_exports:['json','csv'],http_observations_file:'http_observations.jsonl',client_utc:new Date().toISOString()};
  });
  console.log('Actual Node job/JSON/49-row CSV verified. Browser can now run one real PL image.');
  const browserDeadline=Date.now()+540000;
  while(!await exists(browserFile)){assert(Date.now()<browserDeadline,'bounded browser proof deadline');await sleep(500);}
  const proof=JSON.parse(await readFile(browserFile,'utf8'));assert.equal(proof.status,'PASS');
  for(const [name,hash] of Object.entries(sourceHashes))assert.equal(digest(await readFile(path.join(uiRoot,name))),hash,'UI source changed during test');
  assert.equal(proof.candidate_sha256,contract.candidate_sha256);assert.equal(proof.window_manifest_sha256,contract.window_manifest_sha256);
  const job=await get('/api/jobs/'+proof.browser_job_id);validateBoardJob(job);assert.equal(job.result.PL_starts,49);
  const receipt={...preliminary,status:'PASS',browser_job_id:job.id,browser_verification_sha256:digest(await readFile(browserFile)),browser_log_sha256:proof.observation_sha256,
    browser_real_PL_starts:49,ui_source_snapshot_sha256:digest(await readFile(path.join(output,'ui_source_snapshot.json'))),client_utc:new Date().toISOString()};
  await writeFile(path.join(output,'windows_ui_result.json'),JSON.stringify(receipt,null,2));
  assert(/^system_[A-Za-z0-9_-]+$/.test(contract.window_id));
  await promisify(execFile)('C:/Windows/System32/OpenSSH/scp.exe',[
    path.join(output,'windows_ui_result.json'),
    `kv260:/home/ubuntu/fpga_project/model_b/runs/${contract.window_id}/windows_ui_result.json`
  ],{timeout:30000});
  console.log('S4 actual Node/browser proof saved and returned to the bounded board runner.');
  // Keep the reviewable UI open while the C runner receives this proof and drains.
  while(!await exists(path.join(output,'close_server'))){await sleep(500);}
} finally {
  server.closeAllConnections();await new Promise(r=>server.close(r));
  if(evidenceServer){evidenceServer.closeAllConnections();await new Promise(r=>evidenceServer.close(r));}
}
