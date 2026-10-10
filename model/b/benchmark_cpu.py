"""B5 same-board CPU model timing; no PL speedup claim."""
import argparse
from pathlib import Path
import platform
import resource
import time
import numpy as np
from .common import utc,write_json
from .runtime import Runner

def telemetry():
    paths=[Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")]
    paths+=list(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    values={str(p):p.read_text().strip() for p in paths if p.exists()}
    values["ru_maxrss_KiB"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return values

def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--allow-unfrozen",action="store_true");a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    tensors=[np.load(a.bundle/f"regression/{i:02d}_input.npy") for i in range(20)]
    records=[]
    for backend in ("arm_fp32","arm_w8a8_reference","arm_custom_cpu"):
        runner=Runner(a.bundle,backend,allow_unfrozen=a.allow_unfrozen)
        before=telemetry();cold=runner.run(tensors[0],image_id="cold")
        for i in range(3):runner.run(tensors[i],image_id=f"warm{i}")
        samples=[]
        for i in range(30):
            r=runner.run(tensors[i%20],image_id=f"sample{i:02d}")
            samples.append({"index":i,"regression_image_index":i%20,"inference_ms":r["inference_ms"],"Top1":r["top5"][0]["index"]})
        vals=[s["inference_ms"] for s in samples]
        records.append({"backend":backend,"session_load_ms":runner.session_load_ms,"cold_first_inference_ms":cold["inference_ms"],
                        "warmup":3,"formal_samples":30,"p50_ms":float(np.median(vals)),"p95_ms":float(np.percentile(vals,95)),
                        "raw":samples,"before":before,"after":telemetry(),"scope":"complete ORT session.run on preprocessed tensor; excludes decode/session creation",
                        "threads":4,"inter_op_threads":1,"graph_optimization":"DISABLED","PL_starts":0})
        print(f"{backend}: p50={records[-1]['p50_ms']:.3f}ms p95={records[-1]['p95_ms']:.3f}ms",flush=True)
    write_json(a.output/"cpu_times.json",{"status":"PASS_MEASURED_CPU_ONLY","host_utc":utc(),"execution_host":platform.node(),"architecture":platform.machine(),
               "modes":records,"PL":"NOT_RUN","speedup":"NOT_EVALUATED","fair_CPU_before_PL_after":"NOT_RUN","kernel_cycles":"UNAVAILABLE"})

if __name__=="__main__":main()
