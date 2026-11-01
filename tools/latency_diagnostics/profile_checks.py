"""Read-only immutable bundle and durable-log profiling. No XRT or FPGA calls."""
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import sys
import time

sys.path.insert(0,'/opt/opt50-demo-v1/private')
import model_runtime as runtime
import common

os.sched_setaffinity(0,{0})
root=Path('/var/lib/opt50-demo-v1/model')
bundle=runtime.verify_bundle(root)
profile=cProfile.Profile();profile.enable()
samples=[]
for _ in range(5):
    began=time.perf_counter_ns();cpu=time.process_time_ns()
    bundle.unchanged(root)
    samples.append({'wall_ms':(time.perf_counter_ns()-began)/1e6,'cpu_ms':(time.process_time_ns()-cpu)/1e6})
profile.disable();stream=io.StringIO();pstats.Stats(profile,stream=stream).sort_stats('cumtime').print_stats(14)
diagnostics=Path(__file__).resolve().parent
target=diagnostics/'fsync-files';target.mkdir(exist_ok=True)
fsync=[]
for i in range(20):
    began=time.perf_counter_ns();cpu=time.process_time_ns()
    common.create(target/(str(time.monotonic_ns())+'.json'),b'{"check":"read_only_diagnostic"}\n')
    fsync.append({'wall_ms':(time.perf_counter_ns()-began)/1e6,'cpu_ms':(time.process_time_ns()-cpu)/1e6})
value={'unchanged':samples,'unchanged_cprofile':stream.getvalue(),'durable_create':fsync,'affinity':list(os.sched_getaffinity(0))}
(diagnostics/'checks.json').write_text(json.dumps(value,indent=2))
print(json.dumps(value))
