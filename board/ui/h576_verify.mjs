import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,readFile,writeFile,access,appendFile} from 'node:fs/promises';
import path from 'node:path';
import {createWorkbenchServer} from './server.mjs';
import {validateBoardJob} from './board-client.mjs';
import {H576_MANIFEST} from './h576-contract.mjs';

const run=process.argv[2];assert(/^v5_h576_ui_\d{8}_\d+$/.test(run));
const out=path.resolve('results/model_planning',run,'local_ui');await mkdir(out,{recursive:true});
const sha=data=>createHash('sha256').update(data).digest('hex');
const exists=async p=>{try{await access(p);return true;}catch{return false;}};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const {server}=createWorkbenchServer({boardUrl:'http://127.0.0.1:8743'});
const observed=[];
server.on('request',(req,res)=>res.once('finish',()=>{
  const row={method:req.method,url:req.url,status:res.statusCode,user_agent:req.headers['user-agent']??'',client_utc:new Date().toISOString()};
  observed.push(row);void appendFile(path.join(out,'http.jsonl'),JSON.stringify(row)+'\n');
}));
await new Promise(r=>server.listen(4174,'127.0.0.1',r));
console.log('H576 UI http://127.0.0.1:4174; waiting for bounded board UI');
const base='http://127.0.0.1:4174';
async function json(url){const r=await fetch(base+url);const text=await r.text();assert(r.ok,text);return JSON.parse(text);}
let health;const deadline=Date.now()+3600000;
while(Date.now()<deadline){
  try{health=await json('/api/board/health');if(health.model_protocol==='h576_v5'&&health.pl_ready)break;}catch{}
  await pause(2000);
}
assert(health?.bundle_identity.manifest_sha256===H576_MANIFEST&&health.pl_ready);
let jobs=[];
while(Date.now()<deadline){
  jobs=(await json('/api/jobs')).jobs;
  if(jobs.some(j=>j.status==='SUCCEEDED'&&j.type==='benchmark')&&jobs.filter(j=>j.status==='SUCCEEDED'&&j.type==='inference').length>=6)break;
  await pause(1000);
}
const records=[];
for(const job of jobs){
  validateBoardJob(job);
  if(job.status!=='SUCCEEDED')continue;
  const response=await fetch(base+`/api/jobs/${job.id}/export.json`);assert(response.ok);
  const text=await response.text();assert.deepEqual(JSON.parse(text),job);await writeFile(path.join(out,job.id+'.json'),text);
  if(job.result.route_manifest||job.result.samples){
    const response=await fetch(base+`/api/jobs/${job.id}/export.csv`);assert(response.ok);
    const text=await response.text();await writeFile(path.join(out,job.id+'.csv'),text);
    if(job.result.route_manifest)assert.equal(text.replace(/^\uFEFF/,'').split('\r\n').length,50);
  }
  records.push({id:job.id,type:job.type,PL_starts:job.result.PL_starts,json_sha256:sha(text)});
}
await writeFile(path.join(out,'node_ready.json'),JSON.stringify({status:'PASS_PROXY_AWAIT_REAL_BROWSER',records,health},null,2));
console.log('Actual H576 jobs and exports verified; ready for browser action.');
const observation=path.join(out,'browser_observation.json');
while(!await exists(observation)){assert(Date.now()<deadline);await pause(500);}
const proof=JSON.parse(await readFile(observation,'utf8'));
assert(proof.status==='OBSERVED_REAL_H576_BROWSER'&&proof.route_text.includes('36PL')&&proof.route_text.includes('13CPU'));
const job=await json('/api/jobs/'+proof.job_id);validateBoardJob(job);
assert.equal(job.status,'SUCCEEDED');assert.equal(job.result.PL_starts,36);
for(const ext of ['json','csv'])assert(observed.some(r=>r.url===`/api/jobs/${job.id}/export.${ext}`&&r.status===200&&/Mozilla/.test(r.user_agent)),'browser export missing '+ext);
const files={};
for(const name of ['browser_observation.json',...(proof.screenshot_file?[proof.screenshot_file]:[])])files[name]=sha(await readFile(path.join(out,name)));
const result={status:'PASS_REAL_UI_BROWSER_AND_EXPORTS',package_manifest_sha256:H576_MANIFEST,browser_job_id:job.id,PL_starts:36,route_rows:49,records,evidence:files,client_utc:new Date().toISOString()};
await writeFile(path.join(out,'result.json'),JSON.stringify(result,null,2));
await writeFile(path.join(out,'finish_ui.json'),JSON.stringify(result,null,2));
await promisify(execFile)('C:/Windows/System32/OpenSSH/scp.exe',[path.join(out,'finish_ui.json'),`kv260:/home/ubuntu/fpga_project/model_b/c_backend/protocol_preparation/${run}/finish_ui.json`],{timeout:30000});
console.log('PASS_REAL_UI_BROWSER_AND_EXPORTS; board may safely close.');
while(!await exists(path.join(out,'close_server'))){await pause(500);}
server.closeAllConnections();await new Promise(r=>server.close(r));
