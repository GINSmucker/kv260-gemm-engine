"""New external timing run against the immutable bundle, including AMS hwmon."""
import argparse
from pathlib import Path
import platform
import resource
import numpy as np
from model.b.runtime import Runner
from model.b.common import utc,write_json

def telemetry():
    paths=[Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")]
    paths+=list(Path("/sys/class/hwmon").glob("hwmon*/temp*_input"))
    values={str(p):p.read_text().strip() for p in paths if p.exists()}
    values["ru_maxrss_KiB"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    values["fd_count"]=len(list(Path("/proc/self/fd").iterdir()))
    values["VmRSS"]=[s for s in Path("/proc/self/status").read_text().splitlines() if s.startswith("VmRSS:")][0]
    return values

def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    if (a.output/"cpu_times.json").exists():raise FileExistsError("Use a new run")
    tensors=[np.load(a.bundle/f"regression/{i:02d}_input.npy") for i in range(20)]
    records=[]
    for backend in ("arm_fp32","arm_w8a8_reference","arm_custom_cpu"):
        runner=Runner(a.bundle,backend);before=telemetry();cold=runner.run(tensors[0])
        for i in range(3):runner.run(tensors[i])
        samples=[]
        for i in range(30):
            pre=telemetry();r=runner.run(tensors[i%20]);post=telemetry()
            samples.append({"index":i,"regression_image_index":i%20,"inference_ms":r["inference_ms"],
                            "top1":r["top5"][0]["index"],"before":pre,"after":post})
        vals=[r["inference_ms"] for r in samples]
        records.append({"backend":backend,"session_load_ms":runner.session_load_ms,"cold_first_inference_ms":cold["inference_ms"],
                        "warmup":3,"formal_samples":30,"p50_ms":float(np.median(vals)),"p95_ms":float(np.percentile(vals,95)),
                        "raw":samples,"before":before,"after":telemetry(),"threads":4,"PL_starts":0,
                        "scope":"complete ORT session.run on the same preprocessed 20-image cycle; excludes telemetry, decode and session creation"})
        print(f"{backend}: p50={records[-1]['p50_ms']:.3f} p95={records[-1]['p95_ms']:.3f}ms with hwmon",flush=True)
    write_json(a.output/"cpu_times.json",{"status":"PASS_MEASURED_CPU_ONLY","host_utc":utc(),"execution_host":platform.node(),
               "bundle_identity":runner.identity,"modes":records,"PL":"NOT_RUN","speedup":"NOT_EVALUATED",
               "temperature_source":"hwmon temp*_input raw sysfs values; AMS channel labels not assumed",
               "prior_run":"b5_final_cpu_20261001_01/timing: thermal_zone path did not supply temperature; original raw retained"})

if __name__=="__main__":main()
