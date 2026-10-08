import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {collectV3G1Timing} from '../ui/timing-v3-g1.mjs';
const directory=process.argv[2];assert(directory,'Actual B diagnostic output directory required');
const load=name=>readFileSync(path.join(directory,name),'utf8').trim().split('\n').map(JSON.parse);
const rows=load('timing.jsonl'),traces=load('trace.jsonl');
const result=collectV3G1Timing(rows,traces);
const mutations=[
  r=>r[0].request_id='another_task',r=>r[0].phases.B_receipt.wall_ns=-1,
  r=>r[0].phases.B_receipt.wall_ns=true,r=>r[0].thread_cpu_ns++,
  r=>r.pop(),r=>r[0].backend='pl_v2b',r=>r[1].extra=true
];
for(const change of mutations){const copy=structuredClone(rows);change(copy);assert.throws(()=>collectV3G1Timing(copy,traces));}
const different=structuredClone(traces);different[1].model_run_id='other';assert.throws(()=>collectV3G1Timing(rows,different));
console.log(JSON.stringify({...result,error_rejections:8,status:'PASS_ACTUAL_DIAGNOSTIC_ROWS'},null,2));
