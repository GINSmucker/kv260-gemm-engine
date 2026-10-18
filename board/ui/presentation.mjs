import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const ROOT=new URL('../../',import.meta.url);
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const json=async path=>JSON.parse(await readFile(new URL(path,ROOT),'utf8'));
let cached;
export function presentationData() {
  if(!cached)cached=load().catch(error=>{cached=null;throw error;});
  return cached;
}
async function load() {
  const index=await json('results/model_planning/h576_closeout_20261016_01/delivery_index.json');
  const sources=[];
  async function evidence(key) {
    const spec=index.evidence[key];
    if(!spec||!spec.path.startsWith('results/')||spec.path.includes('..'))throw Error('Missing presentation evidence');
    const bytes=await readFile(new URL(spec.path,ROOT));
    if(digest(bytes)!==spec.sha256)throw Error('Presentation evidence identity mismatch');
    sources.push({name:key,path:spec.path,sha256:spec.sha256});
    return JSON.parse(bytes);
  }
  const [fair,quality,endurance,chrome]=await Promise.all(['performance','quality','C_endurance','Chrome_real_PL_desktop'].map(evidence));
  const chromeRoot=new URL('results/model_planning/'+chrome.run_id+'/',ROOT);
  async function exported(name) {
    const bytes=await readFile(new URL('local_chrome/'+name,chromeRoot));
    if(digest(bytes)!==chrome.browser_files[name])throw Error('Presentation export identity mismatch');
    return JSON.parse(bytes);
  }
  const [benchmark,inference]=await Promise.all([exported('benchmark.json'),exported('pl.json')]);
  if(benchmark.status!=='SUCCEEDED'||inference.status!=='SUCCEEDED'||benchmark.result.bundle_identity.manifest_sha256!==index.package_manifest_sha256||inference.result.bundle_identity.manifest_sha256!==index.package_manifest_sha256)throw Error('Presentation candidate mismatch');
  return {
    source:'SAVED_MEASUREMENTS',live:false,model:index.model,hardware:index.hardware,candidate:index.candidate_id,
    manifest:index.package_manifest_sha256,
    formal:fair.rounds.map(round=>({round:round.round,pl:round.PL,original:round.CPU.original_fp32.faster_bracket,w8:round.CPU.h576_w8.faster_bracket,speedupOriginal:round.CPU.original_fp32.speedup_p50,speedupW8:round.CPU.h576_w8.speedup_p50})),
    quality:quality.results,stability:{graphs:endurance.formal_long_graphs,loads:endurance.formal_loads,times:endurance.times_ms,client:endurance.resource_results.client,owner:endurance.resource_results.owner},
    benchmark,inference,sources,
    scope:'已保存的实板回执；浏览此页不连接板卡，不启动推理。交互样本、正式性能、既有质量与有限稳定性分别展示。'
  };
}
