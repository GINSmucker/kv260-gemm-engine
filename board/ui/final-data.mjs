import {readFile} from 'node:fs/promises';

const ROOT = new URL('../../', import.meta.url);
const columns = ['Dense_CPU_ms','Dense_PS_PL_ms','Sparse_CPU_ms','Sparse_PS_PL_ms'];
const parts = ['start_to_completion_ms','native_prepare_ms','native_verify_ms','between_calls_ms','runner_boundary_ms'];
const need = (value, message) => { if (!value) throw Error(message); };
const near = (a,b) => Number.isFinite(a) && Math.abs(a-b)<1e-7;

export function validateFinalData(metrics, csv, timing) {
  const lines = csv.trim().split(/\r?\n/).map(line=>line.split(','));
  const header = lines.shift();
  need(header.join(',')==='image_1based,id,label,image_sha256,input_sha256,'+columns.join(','), 'Unexpected paired CSV columns');
  const samples = lines.map((line,index)=>{
    need(line.length===header.length, 'Malformed paired row');
    const row=Object.fromEntries(header.map((name,i)=>[name,line[i]]));
    row.image_1based=Number(row.image_1based);
    need(row.image_1based===index+306, 'Matched image order differs');
    for(const name of columns) { row[name]=Number(row[name]); need(Number.isFinite(row[name]) && row[name]>0,'Invalid duration'); }
    return row;
  });
  need(samples.length===195 && metrics.status==='PASS' && metrics.images_accuracy===500 && metrics.latency_images===195, 'Final evidence scope differs');
  for(const name of columns) {
    const values=samples.map(row=>row[name]).sort((a,b)=>a-b);
    const percentile=p=>{const at=p*(values.length-1),lo=Math.floor(at),hi=Math.ceil(at);return values[lo]+(values[hi]-values[lo])*(at-lo);};
    need(near(values.reduce((a,b)=>a+b,0)/195,metrics.latency[name].mean_ms) &&
         near(percentile(.5),metrics.latency[name].median_ms) && near(percentile(.95),metrics.latency[name].p95_ms), 'CSV and published distributions differ');
  }
  need(timing.status==='PASS' && timing.archive_sha256===metrics.source_evidence.sha256 &&
       timing.samples_per_model===195 && String(timing.image_range_1based)==='306,500' &&
       timing.new_board_execution===false && timing.pure_PL_cycles_measured===false, 'Timing provenance differs');
  for(const name of ['Dense','Sparse24']) {
    const model=timing.models[name],key=name==='Dense'?'Dense_PS_PL_ms':'Sparse_PS_PL_ms';
    need(model.images.length===195 && model.layers.length===36 && near(model.mean_ms.runner_ms,metrics.latency[key].mean_ms), 'Timing sample coverage differs');
    for(const [i,row] of model.images.entries()) {
      need(row.image_1based===i+306 && row.id===samples[i].id && near(row.runner_ms,samples[i][key]) &&
           parts.every(part=>Number.isFinite(row[part]) && row[part]>=0) &&
           near(parts.reduce((sum,part)=>sum+row[part],0),row.runner_ms), 'Timing partition differs');
    }
    for(const part of parts) need(near(model.mean_ms[part],model.images.reduce((sum,row)=>sum+row[part],0)/195),'Timing mean differs');
  }
  return {source:'SAVED_BOARD_EVIDENCE',live:false,metrics,samples,timing};
}

export async function finalData() {
  const [metrics,csv,timing]=await Promise.all([
    readFile(new URL('results/board500/metrics.json',ROOT),'utf8'),
    readFile(new URL('results/board500/paired195_cpu_pl.csv',ROOT),'utf8'),
    readFile(new URL('results/board500/timing_breakdown.json',ROOT),'utf8')
  ]);
  return validateFinalData(JSON.parse(metrics),csv,JSON.parse(timing));
}
