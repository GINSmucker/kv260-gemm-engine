"""Actual ARM CLI rejection probes, with no C backend or PL loading."""
import argparse
from pathlib import Path
import subprocess
import sys
from model.b.common import sha, utc, write_json

def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    base=[sys.executable,"-B","-m","model.b.cli"]
    probes=[
        ("missing_C_ready",["infer","--bundle",str(a.bundle),"--image",str(a.bundle/"regression/00.JPEG"),
                            "--backend","arm_pl","--output",str(a.output/"must_not_exist_pl.json")],"NOT_READY"),
        ("sparse_unsupported",["run-layer-case","--bundle",str(a.bundle),"--case-id","l00_real",
                               "--mode","sparse24","--output",str(a.output/"must_not_exist_sparse.json")],"UNSUPPORTED"),
        ("W4_unsupported",["run-layer-case","--bundle",str(a.bundle),"--case-id","l00_real",
                           "--mode","w4a8","--output",str(a.output/"must_not_exist_w4.json")],"UNSUPPORTED")
    ]
    records=[]
    for name,args,expected in probes:
        r=subprocess.run(base+args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        log=a.output/(name+".log");log.write_bytes(r.stdout)
        if r.returncode==0 or expected.encode() not in r.stdout:raise ValueError("CLI failed to reject "+name)
        records.append({"name":name,"exit_code":r.returncode,"expected_error":expected,"log_sha256":sha(log)})
    assert not list(a.output.glob("must_not_exist*"))
    write_json(a.output/"cli_errors.json",{"status":"PASS_CPU_CLI_REJECTION_ONLY","host_utc":utc(),"records":records,
               "PL":"NOT_RUN","C_receipt_rejection_and_safety":"NOT_RUN; C responsibility"})
    print("ARM CLI rejects missing C readiness and unsupported enhancements",flush=True)

if __name__=="__main__":main()
