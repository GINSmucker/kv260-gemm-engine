"""Full INT64 mathematical Golden. No tile, exporter or quantizer reuse."""
import numpy as np

def gemm(x, w):
    if x.dtype != np.int8 or w.dtype != np.int8 or x.ndim != 2 or w.ndim != 2:
        raise ValueError("Golden requires INT8 two-dimensional matrices")
    if not x.flags.c_contiguous or not w.flags.c_contiguous or x.shape[1] != w.shape[0]:
        raise ValueError("Golden layout/shape mismatch")
    if x.shape[1] * 16384 > np.iinfo(np.int32).max:
        raise ValueError("INT32 prefix bound unsafe")
    wide = x.astype(np.int64) @ w.astype(np.int64)
    if np.any(wide > np.iinfo(np.int32).max) or np.any(wide < np.iinfo(np.int32).min):
        raise ValueError("Golden overflow")
    return wide.astype("<i4")


def main():
    import argparse, hashlib, json
    from pathlib import Path
    parser=argparse.ArgumentParser(description="Complete public INT64 head Golden; no FPGA")
    parser.add_argument("--check",type=Path,required=True)
    args=parser.parse_args()
    manifest=json.loads((args.check.parent/"manifest.json").read_bytes())
    case=manifest["cases"][0]
    if hashlib.sha256(args.check.read_bytes()).hexdigest()!=case["payload_sha256"]:
        raise ValueError("Case archive changed")
    with np.load(args.check,allow_pickle=False) as values:
        for name,spec in case["arrays"].items():
            data=values[name]
            if list(data.shape)!=spec["shape"] or str(data.dtype)!=spec["dtype"] or hashlib.sha256(data.tobytes()).hexdigest()!=spec["sha256"]:
                raise ValueError("Array identity changed: "+name)
        actual=gemm(values["Xq"],values["Wq"])
        if not np.array_equal(actual,values["Y_golden"]):raise ValueError("Complete integer mismatch")
    print(json.dumps({"status":"PASS_COMPLETE_PUBLIC_HEAD","outputs":int(actual.size),"M":1,"K":192,"N":1000,"PL_starts":0}))

if __name__=="__main__":main()
