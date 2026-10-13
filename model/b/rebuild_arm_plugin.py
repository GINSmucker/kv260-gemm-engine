"""Build a new B-only custom library using the existing ARM compiler."""
import argparse
from pathlib import Path
import platform
import subprocess
import sys
from model.b.common import read_json,sha,utc,write_json

def main():
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True)
    p.add_argument("--contracts",type=Path,required=True);p.add_argument("--headers",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    if platform.machine()!="aarch64":raise ValueError("ARM compiler identity required")
    if not str(a.output.resolve()).startswith("/home/ubuntu/fpga_project/model_b/"):raise ValueError("B private output required")
    a.output.mkdir(parents=True,exist_ok=True);plugin=a.output/"libquant_linear_arm.so"
    if plugin.exists():raise FileExistsError("Use a new build directory")
    cmd=["g++-13","-std=c++17","-O3","-shared","-fPIC","-fno-fast-math","-ffp-contract=off",
         "-I",str(a.headers),"-I",str(a.contracts),str(a.source),"-ldl","-o",str(plugin)]
    with (a.output/"build_custom_op.log").open("w") as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    write_json(a.output/"arm_environment_final.json",{"status":"PASS_BUILD_CPU_TEST_PENDING","host_utc":utc(),
               "boot_id":Path("/proc/sys/kernel/random/boot_id").read_text().strip(),"python":sys.version,"architecture":platform.machine(),
               "compiler":subprocess.check_output(["g++-13","--version"],text=True),"compile_command":cmd,
               "plugin_sha256":sha(plugin),"source_sha256":sha(a.source),"ABI_header_sha256":sha(a.contracts/"gemm_backend_v1.h"),
               "headers":read_json(a.headers/"headers_manifest.json"),
               "environment":subprocess.check_output([sys.executable,"-m","pip","freeze"],text=True),"PL":"NOT_RUN"})
    print("B final ARM custom plugin compiled; CPU validation follows",flush=True)

if __name__=="__main__":main()
