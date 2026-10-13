"""Recompute quality from every saved 1000-logit record, without rerunning inference."""
import argparse
from pathlib import Path
import json
import numpy as np
from .common import read_json,sha,utc,write_json

def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--runs",nargs="+",type=Path,required=True);a=p.parse_args()
    splits=read_json(a.bundle/"splits.json")["splits"];reports=[]
    graph_shas={p.name:sha(p) for p in (a.bundle/"graphs").glob("*.onnx")}
    assert {r["path"] for r in splits["validation_fast"]}<={r["path"] for r in splits["validation_full"]}
    for directory in a.runs:
        summary=read_json(directory/"accuracy.json");raw=directory/"predictions.jsonl"
        assert sha(raw)==summary["predictions_sha256"]
        expected=splits[summary["split"]];counts={b:[0,0,0] for b in summary["backends"]};seen=set()
        labelset={r["label"] for r in expected};outside={b:0 for b in counts}
        with raw.open(encoding="utf-8") as f:
            for i,line in enumerate(f):
                row=json.loads(line);assert row["index"]==i and row["image"]==expected[i]
                path=row["image"]["path"];assert path not in seen;seen.add(path)
                inputs=set()
                for backend,r in row["results"].items():
                    y=np.asarray(r["logits"],dtype=np.float32)
                    assert y.shape==(1000,) and np.isfinite(y).all() and r["PL_starts"]==0 and r["fallback_count"]==0
                    top=np.argsort(-y,kind="stable")[:5].tolist()
                    assert top==[x["index"] for x in r["top5"]]
                    assert r["graph_sha256"]==graph_shas[r["graph"]]
                    assert r["quant_protocol_sha256"]==summary["protocol_sha256"]
                    inputs.add(r["input_tensor_sha256"])
                    label=row["image"]["label"];c=counts[backend]
                    c[0]+=int(top[0]==label);c[1]+=int(label in top);c[2]+=1;outside[backend]+=int(top[0] not in labelset)
                assert len(inputs)==1,"Paired backends did not receive identical tensor"
        assert len(seen)==len(expected)==summary["samples"]
        for b,c in counts.items():
            v=summary["backends"][b]
            assert c==[v["top1_correct"],v["top5_correct"],v["samples"]]
        reports.append({"run":str(directory),"status":"PASS_INDEPENDENT_RAW_RECOUNT","samples":len(seen),"counts":counts,
                        "predictions_sha256":sha(raw),"Top1_outside_Imagenette_label_subset":outside,"classes":1000})
        print(f"raw quality recount PASS: {directory.name} {len(seen)} images",flush=True)
    write_json(a.output/"accuracy_recount.json",{"status":"PASS","client_utc":utc(),"reports":reports,"PL":"NOT_RUN"})

if __name__=="__main__":main()
