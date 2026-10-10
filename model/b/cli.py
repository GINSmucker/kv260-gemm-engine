"""KV260 full DeiT CLI. Export modules are imported only by offline commands."""
import argparse
from pathlib import Path
import sys
import time
import traceback
import numpy as np
from .common import read_json, sha, utc, write_json

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    inf=sub.add_parser("infer",help="Complete 12-block DeiT inference on the selected host")
    inf.add_argument("--bundle",type=Path,required=True);src=inf.add_mutually_exclusive_group(required=True)
    src.add_argument("--image",type=Path);src.add_argument("--tensor",type=Path)
    inf.add_argument("--backend",required=True);inf.add_argument("--output",type=Path,required=True)
    inf.add_argument("--trace",type=Path);inf.add_argument("--dump",type=Path);inf.add_argument("--threads",type=int,default=4)
    inf.add_argument("--run-id");inf.add_argument("--image-id",default="img0000")
    inf.add_argument("--allow-unfrozen",action="store_true");inf.add_argument("--c-ready",type=Path)
    inf.add_argument("--bridge",type=Path);inf.add_argument("--bridge-config",type=Path)
    ev=sub.add_parser("evaluate",help="Paired full-1000-class labelled evaluation")
    ev.add_argument("--bundle",type=Path,required=True);ev.add_argument("--data-root",type=Path,required=True)
    ev.add_argument("--split",choices=["validation_fast","validation_full","selection"],required=True)
    ev.add_argument("--backends",nargs="+",required=True);ev.add_argument("--output-dir",type=Path,required=True)
    ev.add_argument("--allow-unfrozen",action="store_true");ev.add_argument("--limit",type=int);ev.add_argument("--threads",type=int,default=4)
    ev.add_argument("--c-ready",type=Path);ev.add_argument("--bridge",type=Path);ev.add_argument("--bridge-config",type=Path)
    ev.add_argument("--start-index",type=int,default=0);ev.add_argument("--end-index",type=int);ev.add_argument("--run-id")
    ver=sub.add_parser("verify-bundle");ver.add_argument("--bundle",type=Path,required=True)
    layer=sub.add_parser("run-layer-case",help="Complete layer with independent INT64 and an explicit ready-bound PL option")
    layer.add_argument("--bundle",type=Path,required=True);layer.add_argument("--case-id",required=True)
    layer.add_argument("--mode",default="dense_w8a8");layer.add_argument("--backend",choices=["cpu_reference","arm_pl"],default="cpu_reference")
    layer.add_argument("--output",type=Path,required=True)
    layer.add_argument("--c-ready",type=Path);layer.add_argument("--bridge",type=Path);layer.add_argument("--bridge-config",type=Path)
    layer.add_argument("--run-id");layer.add_argument("--image-id",default="case")
    a=p.parse_args()
    if a.command=="infer":
        from .runtime import Runner
        from .preprocess import preprocess
        if a.output.exists(): raise FileExistsError("Use a new result path")
        start=time.perf_counter_ns()
        tensor=preprocess(a.image) if a.image else np.load(a.tensor,allow_pickle=False)
        prep=(time.perf_counter_ns()-start)/1e6
        runner=Runner(a.bundle,a.backend,a.threads,a.allow_unfrozen,a.c_ready,a.bridge,a.bridge_config)
        trace=a.trace
        if runner.custom and trace is None: trace=a.output.with_suffix(".layers.jsonl")
        result=runner.run(tensor,a.run_id,a.image_id,trace,a.dump)
        result.update({"host_utc":utc(),"source_sha256":sha(a.image or a.tensor),"preprocess_or_tensor_load_ms":prep})
        write_json(a.output,result)
        print(f"{result['execution_kind']} PASS: Top1={result['top5'][0]['index']} inference={result['inference_ms']:.3f}ms PL starts={result['PL_starts']}",flush=True)
    elif a.command=="evaluate":
        from .evaluate import evaluate
        evaluate(a.bundle,a.data_root,a.split,a.output_dir,a.backends,a.threads,a.allow_unfrozen,a.limit,
                 a.c_ready,a.bridge,a.bridge_config,a.start_index,a.end_index,a.run_id)
    elif a.command=="run-layer-case":
        if a.mode!="dense_w8a8":raise ValueError("UNSUPPORTED: sparse/W4 require A4/A5 and C gates")
        if a.output.exists():raise FileExistsError("Use a new layer result path")
        if a.backend=="arm_pl":
            from .pl_case import dependencies
            dependencies(a.c_ready,a.bridge,a.bridge_config)
        from .case_reader import load_case
        from .golden_int64 import gemm
        case,arrays=load_case(a.bundle,a.case_id)
        begin=time.perf_counter_ns();reference=gemm(arrays["Xq"],arrays["Wq"]);elapsed=(time.perf_counter_ns()-begin)/1e6
        binding={};actual=reference
        if a.backend=="arm_pl":
            import uuid
            from .pl_case import run
            from .runtime import clean_id
            actual,binding=run(case,arrays,a.c_ready,a.bridge,a.bridge_config,clean_id(a.run_id or "case_"+uuid.uuid4().hex),clean_id(a.image_id))
        mismatch=int(np.sum(actual!=arrays["Y_golden"]))
        independent_mismatch=int(np.sum(actual!=reference))
        write_json(a.output,{"schema":"b_layer_case_result_v1","status":"PASS" if mismatch==0 and independent_mismatch==0 else "FAIL","case_id":a.case_id,
                   "layer_id":case["layer_id"],"M":case["M"],"K":case["K"],"N":case["N"],"mode":a.mode,"execution_kind":"FPGA_LAYER" if a.backend=="arm_pl" else "CPU_REFERENCE",
                   "integer_mismatches":mismatch,"independent_INT64_mismatches":independent_mismatch,"reference_elapsed_ms":elapsed,"timing_scope":"complete NumPy INT64 mathematical reference; not optimized ARM baseline",
                   "PL_starts":1 if a.backend=="arm_pl" else 0,"hardware_identity":binding or None,"host_utc":utc(),"supported_modes":["dense_w8a8"],"sparse_W4":"UNSUPPORTED"})
        if mismatch or independent_mismatch:raise ValueError("Layer mathematical mismatch")
        print(f"{a.backend} layer case {a.case_id}: {mismatch} integer mismatches")
    else:
        from .runtime import verify_bundle
        print(verify_bundle(a.bundle))

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        print(f"B_MODEL_ERROR: {type(e).__name__}: {e}",file=sys.stderr,flush=True)
        sys.exit(1)
