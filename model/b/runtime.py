"""Production ARM/Windows ORT inference. No torch, timm or ONNX import."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import threading
import time
import uuid
import sys
import numpy as np
import onnxruntime as ort
from .common import read_json, safe_path, sha
from .preprocess import preprocess
from . import __version__

BACKENDS = ("windows_fp32", "windows_w8a8_reference", "windows_custom_cpu", "arm_fp32",
            "arm_w8a8_reference", "arm_custom_cpu", "arm_pl", "cpu_fault_test")
_EXECUTION_LOCK = threading.RLock()
_PL_BINDING = None

def verify_bundle(bundle, allow_unfrozen=False):
    bundle = Path(bundle).resolve()
    path = bundle / "bundle_manifest.json"
    if not path.exists():
        if allow_unfrozen:
            return {"status": "DEVELOPMENT_UNFROZEN", "manifest_sha256": None}
        raise ValueError("Model bundle has not been frozen")
    manifest = read_json(path)
    aliases = set()
    for name, value in manifest["files"].items():
        p = safe_path(bundle, name)
        if str(p).casefold() in aliases:
            raise ValueError("Duplicate/alias payload")
        aliases.add(str(p).casefold())
        if p.stat().st_size != value["bytes"] or sha(p) != value["sha256"]:
            raise ValueError("Bundle payload hash/length mismatch: " + name)
    return {"status": "PASS", "manifest_sha256": sha(path), "candidate_id": manifest["candidate_id"]}

def clean_id(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("Unsafe run/image id")
    return value

class Runner:
    def __init__(self, bundle, backend, threads=4, allow_unfrozen=False, c_ready=None, bridge=None, bridge_config=None):
        if backend not in BACKENDS:
            raise ValueError("Unsupported backend")
        architecture = platform.machine().lower()
        if backend.startswith("arm_") and architecture not in ("aarch64", "arm64"):
            raise ValueError("ARM backend must execute on ARM")
        if backend.startswith("windows_") and platform.system() != "Windows":
            raise ValueError("Windows backend must execute on Windows")
        self.bundle, self.backend = Path(bundle).resolve(), backend
        self.identity = verify_bundle(self.bundle, allow_unfrozen)
        self.layers = read_json(self.bundle / "layers.json")
        if len(self.layers) != 49 or len({l["layer_id"] for l in self.layers}) != 49:
            raise ValueError("Model requires 49 unique Linear layers")
        self.labels = read_json(self.bundle / "labels_imagenet1k.json")
        self.custom = backend.endswith("custom_cpu") or backend in ("arm_pl", "cpu_fault_test")
        self.plugin_sha256 = None
        self.threads = threads
        self.backend_env = "pl" if backend == "arm_pl" else "cpu_fault_test" if backend == "cpu_fault_test" else "cpu_reference"
        self.bridge_env = {}
        self.c_binding = {}
        if backend == "arm_pl":
            if not c_ready or not bridge or not bridge_config:
                raise ValueError("NOT_READY: explicit C readiness, bridge and configuration required")
            ready = read_json(c_ready)
            if ready.get("status") != "PASS" or ready.get("abi_version") != 1:
                raise ValueError("NOT_READY: C has not passed the model bridge gate")
            if ready.get("bridge_sha256") != sha(bridge):
                raise ValueError("BAD_IDENTITY: C bridge binary hash")
            if ready.get("A2_gate_sha256") != "a2c1e1f93c81d275488ebf87ae813a12cbd7931674a0f6f21578301ba81ba80d":
                raise ValueError("BAD_IDENTITY: C A2 binding")
            self.bridge_env = {"KV260_GEMM_BRIDGE": str(Path(bridge).resolve()),
                               "KV260_GEMM_CONFIG": Path(bridge_config).read_text(encoding="utf-8")}
            config=read_json(bridge_config)
            self.c_binding={"C_ready_sha256":sha(c_ready),"bridge_sha256":sha(bridge),"bridge_config_sha256":sha(bridge_config),
                            "candidate_sha256":config.get("candidate_sha256"),"window_manifest_sha256":config.get("window_manifest_sha256")}
            # The C plugin keeps one attached owner for the process lifetime.
            binding = (sha(c_ready), sha(bridge), sha(bridge_config))
            global _PL_BINDING
            with _EXECUTION_LOCK:
                if _PL_BINDING is not None and _PL_BINDING != binding:
                    raise ValueError("BAD_IDENTITY: one process must retain one C bridge/owner binding")
                _PL_BINDING = binding
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        if self.custom:
            name = "quant_linear_windows.dll" if platform.system() == "Windows" else "libquant_linear_arm.so"
            self.plugin_sha256 = sha(self.bundle / "plugins" / name)
            so.register_custom_ops_library(str(self.bundle / "plugins" / name))
            graph = "model_w8a8_pl.onnx"
        else:
            graph = "model_fp32.onnx" if backend.endswith("fp32") else "model_w8a8_reference.onnx"
        start = time.perf_counter_ns()
        self.session = ort.InferenceSession(str(self.bundle / "graphs" / graph), so, providers=["CPUExecutionProvider"])
        self.session_load_ms = (time.perf_counter_ns() - start) / 1e6
        self.graph, self.graph_sha256 = graph, sha(self.bundle / "graphs" / graph)
        self.protocol_sha256 = sha(self.bundle / "quant_protocol.json")

    def run(self, tensor, run_id=None, image_id="img0000", trace=None, dump=None):
        # The custom library reads process environment. Keep mode and evidence IDs
        # together even when several CPU/PL Runner objects share a process.
        with _EXECUTION_LOCK:
            os.environ["KV260_B_BACKEND"] = self.backend_env
            os.environ.update(self.bridge_env)
            return self._run(tensor, run_id, image_id, trace, dump)

    def _run(self, tensor, run_id=None, image_id="img0000", trace=None, dump=None):
        x = np.asarray(tensor)
        if x.dtype != np.float32 or x.shape != (1,3,224,224) or not np.isfinite(x).all():
            raise ValueError("Inference requires finite FP32 NCHW [1,3,224,224]")
        run_id = clean_id(run_id or ("b_" + uuid.uuid4().hex))
        image_id = clean_id(image_id)
        os.environ["KV260_B_RUN_ID"], os.environ["KV260_B_IMAGE_ID"] = run_id, image_id
        if trace:
            trace = Path(trace).resolve()
            trace.parent.mkdir(parents=True, exist_ok=True)
            if trace.exists():
                raise FileExistsError("Use a new layer trace path")
        if dump:
            dump = Path(dump).resolve()
            dump.mkdir(parents=True, exist_ok=False)
        os.environ["KV260_B_TRACE_FILE"] = str(trace) if trace else ""
        os.environ["KV260_B_DUMP_DIR"] = str(dump) if dump else ""
        start = time.perf_counter_ns()
        logits = self.session.run(None, {"image": np.ascontiguousarray(x)})[0]
        elapsed = (time.perf_counter_ns() - start) / 1e6
        if logits.shape != (1,1000) or not np.isfinite(logits).all():
            raise ValueError("Invalid model logits")
        rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace and trace.exists() else []
        if self.custom and trace:
            if [r["layer_id"] for r in rows] != [l["layer_id"] for l in self.layers]:
                raise ValueError("Missing/reordered actual QuantLinear calls")
            for r in rows:
                expected_request = f"{run_id}_{image_id}_l{r['layer_index']}"
                if r.get("model_run_id") != run_id or r.get("image_id") != image_id or r.get("request_id") != expected_request:
                    raise ValueError("BAD_IDENTITY: layer evidence belongs to another model request")
        pl_starts = sum(r["starts_delta"] for r in rows)
        if self.backend == "arm_pl" and (not trace or len(rows) != 49 or pl_starts != 49):
            raise ValueError("Full PL inference needs 49 actual starts with trace")
        actual_owner_identity = None
        if self.backend == "arm_pl":
            identities = [json.loads(r["actual_identity_json"]) for r in rows]
            if not isinstance(identities[0], dict) or any(v != identities[0] for v in identities):
                raise ValueError("BAD_IDENTITY: changed/malformed C owner identity")
            actual_owner_identity = identities[0]
            if actual_owner_identity.get("software_test") is not False:
                raise ValueError("BAD_IDENTITY: software mock is not a production PL backend")
        y = logits[0]
        indices = np.argsort(-y, kind="stable")[:5]
        exp = np.exp(y - np.max(y), dtype=np.float32)
        probabilities = exp / np.sum(exp, dtype=np.float32)
        result = {"schema": "b_inference_result_v1", "status": "PASS", "run_id": run_id, "image_id": image_id,
                  "backend": self.backend, "execution_host": platform.node(), "architecture": platform.machine(),
                  "execution_kind": "FPGA_MODEL" if self.backend == "arm_pl" else "CPU_REFERENCE" if not self.backend.endswith("fp32") else "CPU_FP32",
                  "ORT": ort.__version__, "threads": self.threads, "graph": self.graph, "graph_sha256": self.graph_sha256,
                  "b_runtime_version": __version__, "python_executable": sys.executable, "custom_plugin_sha256": self.plugin_sha256,
                  "bundle_identity": self.identity, "quant_protocol_sha256": self.protocol_sha256,
                  "input_tensor_sha256": hashlib.sha256(x.tobytes()).hexdigest(), "logits": y.tolist(),
                  "top5": [{**self.labels[int(i)], "logit": float(y[i]), "probability": float(probabilities[i])} for i in indices],
                  "probability_definition": "stable FP32 softmax of all 1000 logits; not calibrated confidence",
                  "model_linear_count": 49, "observed_custom_calls": len(rows) if self.custom and trace else None,
                  "PL_starts": pl_starts, "fallback_count": 0, "kernel_cycles": None,
                  "kernel_cycles_reason": "UNAVAILABLE", "session_load_ms": self.session_load_ms,
                  "inference_ms": elapsed, "timing_scope": "ORT session.run complete model; excludes preprocessing/session creation",
                  "layer_trace": str(trace) if trace else None,
                  "actual_owner_identity": actual_owner_identity,
                  "quantize_us_sum": sum(r["quantize_us"] for r in rows) if rows else None,
                  "gemm_api_us_sum": sum(r["gemm_api_us"] for r in rows) if rows else None,
                  "dequantize_us_sum": sum(r["dequantize_us"] for r in rows) if rows else None}
        result.update(self.c_binding)
        return result
