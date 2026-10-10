"""Explicit V2A complete inference CLI, never auto-select old PL ABI."""
import argparse
from pathlib import Path
import time
import numpy as np
from model.b.common import sha,utc,write_json
from model.b.runtime_v2a import Runner
from model.b.preprocess import preprocess
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    source=p.add_mutually_exclusive_group(required=True);source.add_argument('--image',type=Path);source.add_argument('--tensor',type=Path)
    p.add_argument('--backend',choices=['arm_pl_v2a','arm_custom_cpu_v2a','windows_custom_cpu_v2a'],required=True)
    p.add_argument('--plugin-v2a',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--trace',type=Path,required=True)
    p.add_argument('--dump',type=Path);p.add_argument('--threads',type=int,default=4);p.add_argument('--run-id');p.add_argument('--image-id',default='img0000')
    for key in ['c-ready','bridge','bridge-config']:p.add_argument('--'+key,type=Path)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError('Use a new result path')
    begin=time.perf_counter_ns();tensor=preprocess(a.image) if a.image else np.load(a.tensor,allow_pickle=False);prep=(time.perf_counter_ns()-begin)/1e6
    runner=Runner(a.bundle,a.backend,a.threads,False,a.c_ready,a.bridge,a.bridge_config,a.plugin_v2a)
    result=runner.run(tensor,a.run_id,a.image_id,a.trace,a.dump);result.update(host_utc=utc(),source_sha256=sha(a.image or a.tensor),preprocess_or_tensor_load_ms=prep)
    write_json(a.output,result);print(result['execution_kind'],result['status'],result['inference_ms'],result['PL_starts'],flush=True)
if __name__=='__main__':main()
