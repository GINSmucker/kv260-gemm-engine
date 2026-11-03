import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {finalData} from './final-data.mjs';

const ROOT=new URL('../../',import.meta.url);
const hash=raw=>createHash('sha256').update(raw).digest('hex');
const need=(ok,message)=>{if(!ok)throw Error(message);};
const digest=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const close=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<1e-7;

export function validateSavedEvidence(manifest,files,final) {
  need(manifest.schema==='kv260_roadshow_saved_evidence_v1'&&manifest.source==='SAVED_BOARD_EVIDENCE'&&
    manifest.live===false&&manifest.new_board_execution===false&&manifest.standalone_layer_receipt_available===false,'Saved evidence scope differs');
  need(manifest.paired_archive_sha256===final.metrics.source_evidence.sha256,'Paired archive identity differs');
  for(const [key,spec] of Object.entries(manifest.files)) {
    const raw=files.get(key);
    need(raw&&raw.length===spec.bytes&&hash(raw)===spec.sha256,'Saved file identity differs: '+key);
  }
  const json=key=>JSON.parse(files.get(key).toString('utf8'));
  const inference=manifest.inference.map(spec=>{
    const report=json(spec.report),receipt=spec.receipt_field?report[spec.receipt_field]:report;
    need(receipt.stage==='PASS'&&receipt.execution_source==='REAL_FPGA_OPT50_SPARSE24'&&
      receipt.source_model_sha256===final.metrics.model_sha256.Sparse24,'Saved inference model/result differs');
    need(digest(receipt.image_sha256)&&receipt.image_sha256===hash(files.get(spec.image))&&
      digest(receipt.input_sha256)&&/^[0-9a-f]{32}$/.test(receipt.request_id)&&
      /^[0-9a-f-]{36}$/.test(receipt.boot_id),'Saved image/request binding differs');
    need(digest(receipt.CPU_logits_sha256)&&receipt.CPU_logits_sha256===receipt.PL_logits_sha256&&
      receipt.bitwise_mismatches===0,'Saved CPU/PL digest differs');
    const native=receipt.native;
    need(native.PL_dense===12&&native.PL_sparse===24&&native.START_commands_issued===36&&
      native.completed_PL_calls===36&&native.native_END_verified===true&&
      native.postrun_buffer_checks_verified===true&&native.device_closed===receipt.device_closed,'Saved completion proof differs');
    need(['Runner_run_ms','CPU_reference_ms','preprocessing_ms','request_work_ms'].every(k=>Number.isFinite(receipt[k])&&receipt[k]>0),'Saved inference timing differs');
    need(receipt.top5.length===5&&new Set(receipt.top5.map(x=>x.index)).size===5&&receipt.top5.every((row,i)=>
      Number.isInteger(row.index)&&row.index>=0&&row.index<1000&&typeof row.name==='string'&&
      Number.isFinite(row.logit)&&Number.isFinite(row.probability)&&row.probability>=0&&row.probability<=1&&
      (!i||receipt.top5[i-1].probability>=row.probability)),'Saved Top5 differs');
    return {...spec,receipt};
  });
  need(inference.length===2&&new Set(inference.map(x=>x.id)).size===2,'Saved inference coverage differs');
  const layers=json('layers').filter(layer=>layer.route==='PL');
  need(layers.length===36&&new Set(layers.map(x=>x.index)).size===36,'Layer specification differs');
  const sample=final.samples.find(row=>row.image_1based===manifest.layer_sample_1based);
  need(Boolean(sample),'Saved layer sample is outside paired195');
  const observations=['Dense','Sparse24'].map(model=>{
    const pending=json(model+'-pending'),accuracy=json(model+'-accuracy'),commit=json(model+'-commit');
    need(commit.closed===true&&commit.files['pending.json'].sha256===hash(files.get(model+'-pending'))&&
      commit.files['accuracy.json'].sha256===hash(files.get(model+'-accuracy')),'Saved layer commit differs');
    need(pending.sample.id===sample.id&&pending.sample.image.sha256===sample.image_sha256&&
      pending.sample.input.sha256===sample.input_sha256&&accuracy.id===sample.id&&
      accuracy.image_sha256===sample.image_sha256&&accuracy.input_sha256===sample.input_sha256&&
      accuracy.bitwise_mismatches===0&&accuracy.compared_logits===1000&&accuracy.completed_PL_calls===36&&
      close(accuracy.inference_ms,sample[model==='Dense'?'Dense_PS_PL_ms':'Sparse_PS_PL_ms']),'Saved layer sample/accuracy differs');
    const rows=pending.owner_metrics.layers;
    need(rows.length===36&&pending.native.completed_PL_calls===36&&pending.native.native_END_verified===true&&
      pending.native.postrun_buffer_checks_verified===true&&pending.owner_metrics.completed_PL_calls===36&&
      pending.native.PL_dense===(model==='Dense'?36:12)&&pending.native.PL_sparse===(model==='Dense'?0:24),'Saved layer completion differs');
    for(const [i,row] of rows.entries())need(row.layer_index===layers[i].index&&
      row.mode===(model==='Dense'?0:layers[i].mode)&&Number.isFinite(row.owner_Run_dispatch_ms)&&row.owner_Run_dispatch_ms>0,
      'Saved layer sequence/mode differs');
    need(JSON.stringify(pending.owner_metrics.layer_call_sequence)===JSON.stringify(layers.map(x=>x.index)),'Saved layer call sequence differs');
    return {model,pending,accuracy,commit};
  });
  const layerCase=json('layer-case');
  need(layerCase.layer_id==='blocks.0.mlp.fc1'&&layerCase.M===197&&layerCase.K===192&&layerCase.N===576&&
    layerCase.status==='INPUTS_READY_NOT_BOARD_TEST','Layer case identity differs');
  const verification=files.get('verification').toString('utf8');
  const publishedLayerAcceptance=/24层单层Dense\/Sparse真实数据一致\s*\|\s*PASS/.test(verification);
  return {source:'SAVED_BOARD_EVIDENCE',live:false,hardwareContacted:false,new_board_execution:false,
    source_revision:manifest.source_revision,archive_sha256:manifest.paired_archive_sha256,
    files:manifest.files,inference,layer:{sample,layers,observations,case:layerCase,
      published_acceptance_pass:publishedLayerAcceptance,standalone_receipt_available:false}};
}

export async function loadSavedEvidence() {
  const manifest=JSON.parse(await readFile(new URL('results/roadshow/manifest.json',ROOT),'utf8'));
  const files=new Map(await Promise.all(Object.entries(manifest.files).map(async([key,spec])=>{
    need(typeof spec.path==='string'&&(/^(results|board)\//.test(spec.path)||spec.path==='docs/verification.md'),'Invalid evidence path');
    need(!spec.path.includes('..')&&!/[\\:\0]/.test(spec.path),'Invalid evidence path');
    return [key,await readFile(new URL(spec.path,ROOT))];
  })));
  return {data:validateSavedEvidence(manifest,files,await finalData()),files};
}
