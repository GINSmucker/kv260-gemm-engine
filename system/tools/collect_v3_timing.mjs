// Read candidate-generated diagnostic JSONL, retaining exact input SHA.
// This offline seam supplies no production capability or authentication.
import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {collectV3G1Timing} from '../ui/timing-v3-g1.mjs';
const [timingFile,traceFile,outputFile]=process.argv.slice(2);
if(!timingFile||!traceFile||!outputFile)throw new Error('Usage: node tools/collect_v3_timing.mjs timing.jsonl trace.jsonl output.json');
const read=p=>{const raw=readFileSync(p);return {sha256:createHash('sha256').update(raw).digest('hex'),rows:raw.toString('utf8').trim().split('\n').map(JSON.parse)};};
const timing=read(timingFile),trace=read(traceFile);
const result={...collectV3G1Timing(timing.rows,trace.rows),timing_sha256:timing.sha256,trace_sha256:trace.sha256};
writeFileSync(outputFile,JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(result,null,2));
