"""Actual paired quality runs with per-image logits and resumable progress."""
from pathlib import Path
import json
import time
import numpy as np
from .common import read_json, sha, utc, write_json
from .preprocess import preprocess
from .runtime import Runner

def evaluate(bundle, data_root, split, output, backends, threads=4, allow_unfrozen=False, limit=None,
             c_ready=None, bridge=None, bridge_config=None, start_index=0, end_index=None, run_id=None):
    bundle, output, data_root = Path(bundle), Path(output), Path(data_root)
    protocol = read_json(bundle / "quant_protocol.json")
    if protocol["freeze_status"] != "FROZEN_AFTER_TRAIN_SELECTION":
        raise ValueError("Quantization must freeze before val prediction")
    splits = read_json(bundle / "splits.json")
    all_rows = splits["splits"][split]
    end_index=len(all_rows) if end_index is None else end_index
    if not 0<=start_index<end_index<=len(all_rows):raise ValueError("Invalid split start/end range")
    rows = all_rows[start_index:end_index]
    if limit:
        rows = rows[:limit]
        end_index=start_index+len(rows)
    if not rows:raise ValueError("Empty evaluation range")
    if len(backends)!=len(set(backends)):raise ValueError("Duplicate evaluation backend")
    output.mkdir(parents=True, exist_ok=True)
    raw = output / "predictions.jsonl"
    if raw.exists():
        raise FileExistsError("Use a new evaluation run directory")
    from .runtime import clean_id
    import uuid
    run_id=clean_id(run_id or 'eval_'+uuid.uuid4().hex)
    runners = [Runner(bundle, b, threads, allow_unfrozen,c_ready,bridge,bridge_config) for b in backends]
    counters = {b: {"top1_correct":0,"top5_correct":0,"samples":0,"times_ms":[]} for b in backends}
    failures, records = [], []
    start = time.perf_counter()
    with raw.open("w", encoding="utf-8") as f:
        for local_index, row in enumerate(rows):
            i=start_index+local_index
            path = data_root / row["path"]
            if sha(path) != row["image_sha256"]:
                raise ValueError("Image hash mismatch")
            t0=time.perf_counter_ns(); x=preprocess(path); pre=(time.perf_counter_ns()-t0)/1e6
            record={"index":i,"image":row,"preprocess_ms":pre,"results":{}}
            for runner in runners:
                trace=output/'traces'/f'{i:05d}_{runner.backend}.jsonl' if runner.custom else None
                try:
                    result=runner.run(x,run_id=run_id,image_id=f"eval{i:05d}",trace=trace)
                except Exception as e:
                    record.update(status='FAIL',failed_backend=runner.backend,error=type(e).__name__+': '+str(e))
                    f.write(json.dumps(record,ensure_ascii=True,allow_nan=False)+'\n');f.flush()
                    write_json(output/'progress.json',{'status':'FAIL','failed_index':i,'completed':local_index,'total':len(rows),'error':record['error']})
                    raise
                top=[v["index"] for v in result["top5"]]
                c=counters[runner.backend]; c["top1_correct"]+=int(top[0]==row["label"]); c["top5_correct"]+=int(row["label"] in top)
                c["samples"]+=1;c["times_ms"].append(result["inference_ms"])
                record["results"][runner.backend]=result
            first, last = backends[0], backends[-1]
            if record["results"][first]["top5"][0]["index"] != record["results"][last]["top5"][0]["index"]:
                failures.append({"image":row,"reference_top1":record["results"][first]["top5"][0],"candidate_top1":record["results"][last]["top5"][0]})
            f.write(json.dumps(record,ensure_ascii=True,allow_nan=False)+"\n"); f.flush()
            if (local_index+1)%25==0 or local_index+1==len(rows):
                write_json(output/"progress.json",{"status":"RUNNING","completed":local_index+1,"total":len(rows),"elapsed_s":time.perf_counter()-start,"host_utc":utc()})
                print(f"{split}: {local_index+1}/{len(rows)} in {time.perf_counter()-start:.1f}s",flush=True)
    for c in counters.values():
        c["top1_percent"]=100*c["top1_correct"]/c["samples"];c["top5_percent"]=100*c["top5_correct"]/c["samples"]
        c["inference_p50_ms"]=float(np.median(c["times_ms"]));c["inference_p95_ms"]=float(np.percentile(c["times_ms"],95))
    drop=counters[backends[0]]["top1_percent"]-counters[backends[-1]]["top1_percent"]
    summary={"gate":"B5_ACCURACY","status":"PASS" if drop<=1.0 else "FAIL", "split":split,"samples":len(rows),"host_utc":utc(),
             "top1_drop_pp":drop,"max_drop_pp":1.0,"backends":counters,"prediction_classes":1000,"failures":failures,
             "predictions_sha256":sha(raw),"splits_sha256":sha(bundle/"splits.json"),"protocol_sha256":sha(bundle/"quant_protocol.json"),
             "elapsed_s":time.perf_counter()-start,"PL":"RUN" if 'arm_pl' in backends else "NOT_RUN",
             "run_id":run_id,"start_index":start_index,"end_index":end_index,"split_total":len(all_rows),
             "range_scope":"FULL_SPLIT" if start_index==0 and end_index==len(all_rows) else "WINDOW_RANGE",
             "full_is_superset_of_fast":True,"quality_scope":"Imagenette labelled ImageNet subset; full 1000-logit argmax"}
    write_json(output/"accuracy.json",summary);write_json(output/"progress.json",{"status":"COMPLETE","completed":len(rows),"total":len(rows),"host_utc":utc()})
    print(f"quality {summary['status']}: Top1 drop {drop:.6f}pp",flush=True)
    return summary
