"""B3 all 49 real/zero/bound cases and five complete-shape hand anchors."""
import argparse
import hashlib
from pathlib import Path
import numpy as np
from .common import ROOT, read_json, sha, utc, write_json, safe_path
from .validate_offline import diagnostic_graph, weights
from .golden_int64 import gemm
from .quantization import quantize, postprocess

def save(root, name, array):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    array.tofile(path)
    return {"file": name, "sha256": sha(path), "bytes": array.nbytes, "dtype": array.dtype.str, "shape": list(array.shape)}

def generate(bundle, output):
    output.mkdir(parents=True, exist_ok=True)
    diagnostic, names = diagnostic_graph(bundle, output)
    x = np.load(bundle / "regression/00_input.npy")
    data = diagnostic.run(names, {"image": x})
    layers = read_json(bundle / "layers.json")
    cases, results = [], []
    for i, l in enumerate(layers):
        w, sw, bias = weights(bundle, l)
        inp, ortq, sx, orti, ortf = data[i * 5:i * 5 + 5]
        flat = np.ascontiguousarray(inp.reshape(l["M"], l["K"]))
        q, nsx = quantize(flat, 1)
        assert np.array_equal(q, ortq.reshape(q.shape)) and np.array_equal(nsx, sx.reshape(nsx.shape)), "ORT/NumPy RNE mismatch"
        actual = gemm(q, w)
        assert np.array_equal(actual, orti.reshape(actual.shape)), "Float semantic integer GEMM not exact"
        assert np.array_equal(postprocess(actual, nsx, sw, bias), ortf.reshape(actual.shape)), "FP32 postprocessing mismatch"
        for kind, xq, xs in (("real", q, nsx), ("zero", np.zeros(q.shape, dtype=np.int8), np.ones(nsx.shape, dtype=np.float32)),
                             ("extreme", np.tile(np.where(np.arange(l["K"]) % 2 == 0,127,-127).astype(np.int8),(l["M"],1)), np.ones(nsx.shape,dtype=np.float32))):
            cid = f"l{i:02d}_{kind}"
            yi = gemm(xq, w)
            yf = postprocess(yi, xs, sw, bias)
            entries = {"Xq": save(bundle, f"layer_cases/{cid}/Xq.bin", xq),
                       "sx": save(bundle, f"layer_cases/{cid}/sx.bin", xs),
                       "Y_golden": save(bundle, f"layer_cases/{cid}/Y.bin", yi),
                       "postprocess_expected": save(bundle, f"layer_cases/{cid}/Yfloat.bin", yf)}
            cases.append({"schema": "b_model_case_v1", "case_id": cid, "layer_id": l["layer_id"], "layer_index": i,
                          "family": l["family"], "M": l["M"], "K": l["K"], "N": l["N"], "kind": kind,
                          "source": "regression/00_input.npy real propagated ORT quant network" if kind=="real" else "B independent deterministic input",
                          "Wq": l["Wq"], "sw": l["sw"], "bias": l["bias"], **entries,
                          "layout": "compact_row_major", "mode": "dense_w8a8", "Golden": "independent complete NumPy INT64; prefix bound checked"})
        results.append({"layer_id": l["layer_id"], "RNE_same_input": "PASS", "INT64_full_elements": int(actual.size),
                        "INT32_mismatches": 0, "postprocessing": "PASS_EXACT"})
        print(f"49-layer B Golden {i + 1}/49", flush=True)
    # Complete actual shapes, sparse hand arithmetic in a few entries; not new hardware sparse mode.
    for family in ("head", "proj", "qkv", "fc1", "fc2"):
        l = next(v for v in layers if v["family"] == family)
        xq = np.zeros((l["M"],l["K"]),dtype=np.int8)
        wq = np.zeros((l["K"],l["N"]),dtype=np.int8)
        xq[0,:3] = [1,-2,3]
        wq[:3,:2] = [[4,-5],[6,7],[-8,9]]
        expected = np.zeros((l["M"],l["N"]),dtype="<i4"); expected[0,:2]=[-32,8]
        assert np.array_equal(gemm(xq,wq),expected)
        cid = "hand_"+family
        xs=np.ones((l["M"],1),dtype=np.float32); sw=np.ones(l["N"],dtype=np.float32); bias=np.zeros(l["N"],dtype=np.float32)
        tensors={name:save(bundle,f"layer_cases/{cid}/{name}.bin",array) for name,array in
                 (("Xq",xq),("Wq",wq),("sx",xs),("sw",sw),("bias",bias),("Y_golden",expected),("postprocess_expected",expected.astype(np.float32)))}
        cases.append({"schema":"b_model_case_v1","case_id":cid,"layer_id":l["layer_id"],"layer_index":l["layer_index"],
                      "family":family,"M":l["M"],"K":l["K"],"N":l["N"],"kind":"hand","layout":"compact_row_major","mode":"dense_w8a8", **tensors})
    manifest={"schema":"b_model_casepack_v1","cases":cases,"case_count":len(cases),"layer_count":49,"created_utc":utc(),"PL":"NOT_RUN"}
    write_json(bundle/"layer_cases/manifest.json",manifest)
    write_json(output/"real_layer_numeric.json",{"status":"PASS","layers":results,"created_utc":utc(),"PL":"NOT_RUN"})
    return manifest

def verify(bundle, output):
    manifest=read_json(bundle/"layer_cases/manifest.json")
    records=[]
    for c in manifest["cases"]:
        arrays={}
        for field in ("Xq","Wq","sx","sw","bias","Y_golden","postprocess_expected"):
            t=c[field]; path=safe_path(bundle,t["file"])
            if sha(path)!=t["sha256"] or path.stat().st_size!=t["bytes"]:
                raise ValueError("case hash/length mismatch: "+c["case_id"])
            arrays[field]=np.fromfile(path,dtype=t["dtype"]).reshape(t["shape"])
        x,w=arrays["Xq"],arrays["Wq"]
        if x.dtype!=np.int8 or w.dtype!=np.int8 or x.shape!=(c["M"],c["K"]) or w.shape!=(c["K"],c["N"]) or c["layout"]!="compact_row_major":
            raise ValueError("case binding/transpose/layout mismatch")
        # Independent verifier directly computes INT64, does not use generation's gemm function.
        wide=x.astype(np.int64)@w.astype(np.int64)
        if np.any(np.abs(wide)>np.iinfo(np.int32).max): raise ValueError("overflow")
        mismatches=int(np.sum(wide!=arrays["Y_golden"]))
        if mismatches: raise ValueError("case integer mismatch")
        fp=(wide.astype(np.float32)*arrays["sx"]).astype(np.float32)
        fp=(fp*arrays["sw"]).astype(np.float32)
        fp=(fp+arrays["bias"]).astype(np.float32)
        if not np.array_equal(fp,arrays["postprocess_expected"]): raise ValueError("case float mismatch")
        records.append({"case_id":c["case_id"],"integer_outputs":wide.size,"mismatches":mismatches})
    result={"gate":"B3_CASEPACK_READY","status":"PASS","created_utc":utc(),"cases":len(records),"layers":49,
            "outputs":sum(r["integer_outputs"] for r in records),"records":records,"manifest_sha256":sha(bundle/"layer_cases/manifest.json"),"PL":"NOT_RUN"}
    write_json(output/"case_verification.json",result)
    print(f"B3 PASS: {len(records)} cases, {result['outputs']} exact outputs",flush=True)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--bundle",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--verify-only",action="store_true");a=p.parse_args()
    if not a.verify_only: generate(a.bundle,a.output)
    verify(a.bundle,a.output)
