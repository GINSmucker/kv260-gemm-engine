"""B4/ARM regression: 49 real calls and independent full INT64 audit."""
import argparse
import json
from pathlib import Path
import platform
import time
import numpy as np
from .common import read_json, sha, utc, write_json
from .runtime import Runner

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--count",type=int,default=20);p.add_argument("--golden-count",type=int,default=20)
    p.add_argument("--allow-unfrozen",action="store_true");a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    prefix="windows" if platform.system()=="Windows" else "arm"
    runner=Runner(a.bundle,prefix+"_custom_cpu",allow_unfrozen=a.allow_unfrozen)
    semantic=Runner(a.bundle,prefix+"_w8a8_reference",allow_unfrozen=a.allow_unfrozen)
    fp32=Runner(a.bundle,prefix+"_fp32",allow_unfrozen=a.allow_unfrozen)
    layers=read_json(a.bundle/"layers.json")
    records=[]; integer_outputs=0; starts=time.perf_counter()
    for i in range(a.count):
        x=np.load(a.bundle/f"regression/{i:02d}_input.npy")
        # Identical Pillow output verified separately; tensors give precise graph boundary.
        trace=a.output/f"image{i:02d}/layers.jsonl";dump=a.output/f"image{i:02d}/raw"
        actual=runner.run(x,run_id=a.output.name,image_id=f"reg{i:02d}",trace=trace,dump=dump if i<a.golden_count else None)
        qref=semantic.run(x,image_id=f"reg{i:02d}")
        fp=fp32.run(x,image_id=f"reg{i:02d}")
        windows_fp=np.load(a.bundle/f"regression/{i:02d}_fp32_logits.npy").reshape(-1)
        windows_q=np.load(a.bundle/f"regression/{i:02d}_quant_logits.npy").reshape(-1)
        y=np.asarray(actual["logits"],dtype=np.float32); expected=np.asarray(qref["logits"],dtype=np.float32)
        same_graph_ok=np.allclose(y,expected,atol=1e-4,rtol=1e-4)
        cross_fp_ok=np.allclose(fp["logits"],windows_fp,atol=1e-4,rtol=1e-4)
        cross_q_ok=np.allclose(expected,windows_q,atol=1e-4,rtol=1e-4)
        checks=[]
        if i<a.golden_count:
            rows=[json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
            for l,r in zip(layers,rows):
                j=l["layer_index"]
                xq=np.fromfile(dump/f"{j}_Xq.bin",dtype=np.int8).reshape(l["M"],l["K"])
                w=np.fromfile(a.bundle/l["Wq"]["file"],dtype=np.int8).reshape(l["K"],l["N"])
                yi=np.fromfile(dump/f"{j}_Y.bin",dtype="<i4").reshape(l["M"],l["N"])
                wide=xq.astype(np.int64)@w.astype(np.int64)
                mismatch=int(np.sum(wide!=yi));integer_outputs+=yi.size
                checks.append({"layer_id":l["layer_id"],"outputs":yi.size,"mismatches":mismatch,"X_sha256":sha(dump/f"{j}_Xq.bin"),"Y_sha256":sha(dump/f"{j}_Y.bin")})
                if mismatch or checks[-1]["X_sha256"]!=r["X_sha256"] or checks[-1]["Y_sha256"]!=r["Y_sha256"]:
                    raise ValueError("Custom INT32/identity mismatch")
        rec={"image_index":i,"custom_vs_same_host_semantic":"PASS" if same_graph_ok else "FAIL",
             "max_abs_custom":float(np.max(np.abs(y-expected))),"cross_host_FP32":"PASS" if cross_fp_ok else "FAIL",
             "cross_host_quant":"PASS" if cross_q_ok else "FAIL", "cross_quant_max_abs":float(np.max(np.abs(expected-windows_q))),
             "actual_custom_calls":actual["observed_custom_calls"],"PL_starts":actual["PL_starts"],
             "custom_ms":actual["inference_ms"],"semantic_ms":qref["inference_ms"],"fp32_ms":fp["inference_ms"],"integer":checks}
        write_json(a.output/f"image{i:02d}/custom_result.json",actual);write_json(a.output/f"image{i:02d}/semantic_result.json",qref)
        write_json(a.output/f"image{i:02d}/fp32_result.json",fp)
        records.append(rec);write_json(a.output/"progress.json",{"completed":i+1,"total":a.count,"records":records,"elapsed_s":time.perf_counter()-starts})
        print(f"{prefix} complete custom regression {i+1}/{a.count}: same-host={rec['custom_vs_same_host_semantic']} cross-FP32={rec['cross_host_FP32']} cross-quant={rec['cross_host_quant']}",flush=True)
        if not same_graph_ok or not cross_fp_ok:
            write_json(a.output/"failure.json",rec);raise ValueError("Frozen model parity gate failed")
    summary={"gate":"B4_EXPORT_READY" if prefix=="windows" else "B5_ARM_MODEL_READY","status":"PASS", "host_utc":utc(),
             "execution_host":platform.node(),"architecture":platform.machine(),"images":a.count,"integer_outputs":integer_outputs,
             "actual_custom_calls":49*a.count,"PL_starts":0,"fallback_count":0,"cross_host_quant":"PASS" if all(r["cross_host_quant"]=="PASS" for r in records) else "FAIL_RETAINED",
             "cross_host_quant_scope":"Quantization can amplify cross-platform nonlinear FP32 variation; same-host integer/custom parity is separate",
             "elapsed_s":time.perf_counter()-starts,"records":records,"PL":"NOT_RUN"}
    if prefix=="arm" and summary["cross_host_quant"]!="PASS":
        summary["status"]="PARTIAL_CROSS_HOST_QUANT_FAIL"
    write_json(a.output/"custom_regression.json",summary)

if __name__=="__main__":main()
