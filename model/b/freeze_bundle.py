"""Freeze reviewable B dense development package; never signs a C/PL gate."""
import argparse
from pathlib import Path
import shutil
import tarfile
import numpy as np
from .common import ROOT,read_json,sha,utc,write_json
from .runtime import verify_bundle
from .identity import BINDINGS

def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    if (a.bundle/"bundle_manifest.json").exists():raise FileExistsError("Never overwrite a frozen bundle")
    numeric=read_json(ROOT/"results/B/B2/numeric_freeze_20261001_01.json")
    for name,expected in numeric["files"].items():
        if sha(a.bundle/name)!=expected:raise ValueError("Numeric candidate changed after val")
    for src,dst in ((ROOT/"build/model_assets/deit_tiny/model.safetensors",a.bundle/"original/model.safetensors"),
                    (ROOT/"build/model_assets/deit_tiny/config.json",a.bundle/"original/config.json"),
                    (ROOT/"build/model_assets/b_deps/b_wheels/kv260_model_b-0.1.2-py3-none-any.whl",a.bundle/"install/kv260_model_b-0.1.2-py3-none-any.whl"),
                    (ROOT/"model/b/requirements-arm.txt",a.bundle/"install/requirements-arm.txt"),
                    (ROOT/"results/B/B0/b0_20260930_01/requirements_after.txt",a.bundle/"install/windows_environment_lock.txt")):
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    shutil.copytree(ROOT/"build/model_assets/b_deps/arm_wheels",a.bundle/"install/arm_wheels")
    shutil.copytree(ROOT/"build/model_assets/b_deps/ort_v1.22.1",a.bundle/"headers")
    for src in (ROOT/"model/b").rglob("*"):
        relative=src.relative_to(ROOT/"model/b")
        if src.is_file() and src.suffix in (".py",".cpp",".h",".toml",".txt",".json") and not any(v in relative.parts for v in ("build","__pycache__","kv260_model_b.egg-info")):
            dst=a.bundle/"source/model/b"/relative;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    evidence={
        "B0":"results/B/B0/b0_20260930_01/compatibility_probe.json",
        "B1":"results/B/B2/b2_20260930_01/fp32_regression.json",
        "B2":"results/B/B2/b2_20260930_01/quant_selection.json",
        "B3":"results/B/B3/b3_20260930_01/case_verification.json",
        "B4":"results/B/B4/b4_final_20261001_01/custom_regression.json",
        "Windows_fast_accuracy":"results/B/B5/accuracy_fast_20261001_01/accuracy.json",
        "Windows_full_accuracy":"results/B/B5/accuracy_full_20261001_01/accuracy.json",
        "numeric_freeze":"results/B/B2/numeric_freeze_20261001_01.json",
        "ARM_original_regression":"results/B/B5/arm_raw_20261001_01/b5_regression_20261001_01/custom_regression.json",
        "ARM_regression":"results/B/B5/arm_final_raw_20261001_01/b5_final_cpu_20261001_01/regression/custom_regression.json",
        "ARM_environment":"results/B/recovery/resume_20261001_01/arm_environment_final.json",
        "ARM_final_private_install":"results/B/B5/arm_final_raw_20261001_01/b5_final_cpu_20261001_01/completion.json",
        "ARM_fast_accuracy":"results/B/B5/arm_accuracy_fast_20261001_01/accuracy.json",
        "ARM_full_accuracy":"results/B/B5/arm_accuracy_full_20261001_01/accuracy.json",
        "ARM_times":"results/B/B5/arm_final_raw_20261001_01/b5_final_cpu_20261001_01/timing/cpu_times.json",
        "ARM_original_collected_integer_audit":"results/B/B5/collected_audit_20261001_01/collected_audit.json",
        "ARM_collected_integer_audit":"results/B/B5/collected_final_audit_20261001_01/collected_audit.json",
        "ARM_numeric_edges":"results/B/B5/arm_kernel_edges_20261001_01/numeric_edges.json",
        "Windows_numeric_edges":"results/B/B4/kernel_edges_20261001_01/numeric_edges.json",
        "quality_raw_recount":"results/B/B5/accuracy_recount_20261001_01/accuracy_recount.json",
    }
    ev={}
    for key,name in evidence.items():
        src=ROOT/name
        if not src.exists():raise FileNotFoundError("Required actual evidence missing: "+name)
        dst=a.bundle/"evidence"/(key+".json");dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        ev[key]={"path":name,"sha256":sha(src)}
    arm_refs=[];reference_root=(ROOT/evidence["ARM_regression"]).parent
    for i in range(20):
        ref={"image_index":i,"input_sha256":sha(a.bundle/f"regression/{i:02d}_input.npy")}
        for kind,filename in (("fp32","fp32_result.json"),("quant","semantic_result.json")):
            src=reference_root/f"image{i:02d}"/filename;r=read_json(src)
            dst=a.bundle/f"regression/arm/{i:02d}_{kind}_logits.npy";dst.parent.mkdir(parents=True,exist_ok=True)
            np.save(dst,np.asarray(r["logits"],dtype=np.float32).reshape(1,1000))
            ref[kind]={"file":dst.relative_to(a.bundle).as_posix(),"sha256":sha(dst),"source_result_sha256":sha(src),"graph_sha256":r["graph_sha256"]}
        arm_refs.append(ref)
    write_json(a.bundle/"regression/arm/manifest.json",{"schema":"b_arm_regression_reference_v1","images":20,"records":arm_refs,
               "scope":"Actual ARM same-host ORT references for future PL; root regression quant arrays retain Windows references and cross-host FAIL"})
    ar=read_json(ROOT/evidence["ARM_regression"]);wr=read_json(ROOT/evidence["B4"])
    for r in (ar,wr):
        if r["images"]!=20 or r["integer_outputs"]!=81719840 or r["actual_custom_calls"]!=980 or r["PL_starts"]!=0:
            raise ValueError("Required complete 20-image CPU evidence missing")
        if not all(x["custom_vs_same_host_semantic"]=="PASS" and x["cross_host_FP32"]=="PASS" for x in r["records"]):
            raise ValueError("Function parity gate failed; do not freeze a PASS")
    env=read_json(ROOT/evidence["ARM_environment"])
    if sha(a.bundle/"plugins/libquant_linear_arm.so")!=env["plugin_sha256"] or sha(ROOT/"model/b/custom_op/quant_linear.cpp")!=env["source_sha256"]:
        raise ValueError("Compiled ARM source/binary identity differs")
    installed=read_json(ROOT/evidence["ARM_final_private_install"])
    if installed["wheel_sha256"]!=sha(a.bundle/"install/kv260_model_b-0.1.2-py3-none-any.whl") or installed["plugin_sha256"]!=env["plugin_sha256"]:
        raise ValueError("Final ARM runtime identity differs")
    arm_quality_pass=all(read_json(ROOT/evidence[n])["status"]=="PASS" for n in ("ARM_fast_accuracy","ARM_full_accuracy"))
    windows_quality_pass=all(read_json(ROOT/evidence[n])["status"]=="PASS" for n in ("Windows_fast_accuracy","Windows_full_accuracy"))
    gates={"B0_READY":"PASS_LOCAL","B1_FP32_MODEL_READY":"PASS_LOCAL","B2_QUANT_PROTOCOL_FROZEN":"PASS_LOCAL",
           "B3_CASEPACK_READY":"PASS_LOCAL","B4_EXPORT_READY":"PASS_LOCAL",
           "B5_ARM_MODEL_READY":"PARTIAL_CROSS_HOST_QUANT_FAIL","B5_ARM_same_host_function_and_integer":"PASS",
           "B5_CROSS_HOST_QUANT":ar["cross_host_quant"],"B5_ACCURACY_WINDOWS_FAST_FULL":"PASS" if windows_quality_pass else "FAIL",
           "B5_ACCURACY_ARM_FAST_FULL":"PASS_CPU_ONLY" if arm_quality_pass else "FAIL","C_MODEL_BACKEND_READY":"NOT_RUN",
           "B6_FULL_PL_MODEL_PASS":"WAITING_C_BACKEND","B7_PL_EVALUATION":"NOT_RUN","B8_B9_hardware":"NOT_RUN_CONDITIONAL"}
    write_json(a.bundle/"GATE_SNAPSHOT.json",{"status":"DEVELOPMENT_FREEZE_WITH_REPORTED_OPEN_GATES","frozen_client_utc":utc(),"gates":gates,"evidence":ev,
               "cross_quant_note":"Windows and ARM nonlinear FP32 differences can cross dynamic RNE boundaries; no tolerance relaxed; ARM same-host quant/custom and exact integer validation passed",
               "actual_ARM_boot":env["boot_id"],"board_calendar":"unsynchronized, kept in ARM raw evidence; trusted client chronology is separate"})
    write_json(a.bundle/"hardware_bindings.json",{"A2_sha256":BINDINGS[next(iter(BINDINGS))],"A1_sha256":list(BINDINGS.values())[1],"paired_sha256":list(BINDINGS.values())[2],
               "driver_sha256":"cba324ac56c9bebe84fabbdf2635ea699b10ec718593cba7d44c4b004f782eca","tags":[0xe7333e5e,0x9c258169],"xclbin_uuid":"a1d6968d-c5b3-c896-a565-94ddf751a2c7","PL_in_this_B_session":"NOT_RUN",
               "binding_scope":"Expected A2 paired platform only; no current PL/driver observation"})
    (a.bundle/"RUN_ON_ARM.md").write_text("""# B dense development bundle

Frozen B software/model package. Cross-host quant logits FAIL is retained in GATE_SNAPSHOT.json. ARM integer/custom function passed; C bridge and PL remain NOT_RUN.

Private runtime: /home/ubuntu/fpga_project/model_b/.venv/bin/python
Installed wheel: install/kv260_model_b-0.1.2-py3-none-any.whl
Final bundle path: /home/ubuntu/fpga_project/model_b/releases/B_MODEL_BUNDLE_V1

```sh
cd /home/ubuntu/fpga_project/model_b
.venv/bin/python -B -m model.b.cli verify-bundle --bundle releases/B_MODEL_BUNDLE_V1
.venv/bin/python -B -m model.b.cli infer --bundle releases/B_MODEL_BUNDLE_V1 --image releases/B_MODEL_BUNDLE_V1/regression/00.JPEG --backend arm_fp32 --output runs/final_fp32_example.json
.venv/bin/python -B -m model.b.cli infer --bundle releases/B_MODEL_BUNDLE_V1 --image releases/B_MODEL_BUNDLE_V1/regression/00.JPEG --backend arm_custom_cpu --output runs/final_custom_example.json
.venv/bin/python -B -m model.b.cli run-layer-case --bundle releases/B_MODEL_BUNDLE_V1 --case-id l00_real --backend cpu_reference --output runs/final_layer_example.json
```

Use new output names on every run. The CPU paths have PL_starts=0. `arm_pl` requires explicit real C readiness, bridge and config; it fails without them and never switches to CPU. Source/header identities and all 49 weights are in this bundle. Three graphs retain 12 blocks, 197 tokens, 192 embedding, 3 heads, 224 input and all 1000 logits.

Inference timing means complete ORT session.run, excluding image preprocessing/session creation/bundle hash audit. The custom CPU is an integer semantic reference, not an optimized ARM INT8 performance baseline. No PL speedup is inferred.
""",encoding="utf-8")
    files={p.relative_to(a.bundle).as_posix():{"sha256":sha(p),"bytes":p.stat().st_size} for p in sorted(a.bundle.rglob("*")) if p.is_file()}
    manifest={"schema":"b_model_bundle_v1","candidate_id":"B_MODEL_BUNDLE_V1","model_candidate":"B_MODEL_DENSE_V1_20260930","status":"FROZEN_DEVELOPMENT_CANDIDATE_OPEN_GATES_RETAINED",
              "client_frozen_utc":utc(),"files":files,"model":"deit_tiny_patch16_224.fb_in1k","linear_count":49,"mode":"dense_w8a8","ORT":"1.22.1","standard_opset":18,"custom_domain_version":1,
              "model_revision":"80e968688553f219e4a86f940ed945a23709c16f","original_weight_sha256":BINDINGS["build/model_assets/deit_tiny/model.safetensors"],
              "architecture":{"blocks":12,"tokens":197,"embedding":192,"heads":3,"input":[1,3,224,224],"classes":1000},
              "runtime_wheel_version":"0.1.2","production_dependencies":{"numpy":"1.26.4","Pillow":"12.3.0","onnxruntime":"1.22.1"},
              "quant_protocol_sha256":sha(a.bundle/"quant_protocol.json"),"gates":gates,"C_and_PL":"NOT_RUN"}
    write_json(a.bundle/"bundle_manifest.json",manifest)
    verify_bundle(a.bundle)
    archive=a.output/"B_MODEL_BUNDLE_V1_20261001.tar.gz"
    def normalize(info):info.mtime=0;return info
    with tarfile.open(archive,"w:gz",compresslevel=1) as tf:tf.add(a.bundle,arcname="B_MODEL_BUNDLE_V1",filter=normalize)
    receipt={"status":"FROZEN_VERIFIED_DEVELOPMENT_PACKAGE","client_utc":utc(),"bundle":str(a.bundle.resolve()),"manifest_sha256":sha(a.bundle/"bundle_manifest.json"),"files":len(files),
             "archive_sha256":sha(archive),"archive_bytes":archive.stat().st_size,"archive":str(archive),"gates":gates}
    write_json(a.output/"bundle_freeze_receipt.json",receipt)
    print(f"bundle frozen: {len(files)} files, manifest SHA={receipt['manifest_sha256']}",flush=True)

if __name__=="__main__":main()
