"""B1 full network parity and B4 graph construction (quality not yet frozen)."""
import argparse
import hashlib
from pathlib import Path
import traceback
import numpy as np
import torch
import timm
from PIL import Image
from .common import ROOT, read_json, sha, utc, write_json
from .preprocess import preprocess, SPEC
from .reference_model import original, layer_table, WEIGHT_SHA, quant_model
from .compatibility import session
from .export_onnx import export_fp32, rewrite, audit
from .quantization import PROTOCOL

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bundle", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    (a.bundle / "graphs").mkdir(parents=True, exist_ok=True)
    image = ROOT / "build/model_assets/deit_tiny/sample.png"
    x = preprocess(image)
    model = original()
    transform = timm.data.create_transform(**timm.data.resolve_model_data_config(model), is_training=False)
    with Image.open(image) as im:
        tx = transform(im.convert("RGB")).unsqueeze(0).numpy()
    assert np.array_equal(tx, x), "Preprocessing differs from timm"
    write_json(a.bundle / "preprocess.json", SPEC)
    layers = layer_table(model, x)
    params = {name: {"shape": list(t.shape), "dtype": str(t.dtype), "sha256": hashlib.sha256(t.numpy().tobytes()).hexdigest()}
              for name, t in model.state_dict().items()}
    write_json(a.bundle / "model_identity.json", {"model": "deit_tiny_patch16_224.fb_in1k", "revision": "80e968688553f219e4a86f940ed945a23709c16f",
               "weights_sha256": WEIGHT_SHA, "parameters": params, "parameter_count": sum(p.numel() for p in model.parameters()),
               "blocks": 12, "tokens": 197, "embedding": 192, "heads": 3, "classes": 1000, "linear_count": 49,
               "state_dict_strict": "PASS", "missing_keys": 0, "unexpected_keys": 0})
    with torch.inference_mode():
        fp = model(torch.from_numpy(x)).numpy()
    graph = export_fp32(model, x, a.bundle / "graphs/model_fp32.onnx")
    with torch.inference_mode():
        unfused = model(torch.from_numpy(x)).numpy()
    ort_y = session(a.bundle / "graphs/model_fp32.onnx").run(None, {"image": x})[0]
    np.save(a.output / "sample_input.npy", x)
    np.save(a.output / "sample_fp32_logits.npy", fp)
    np.save(a.output / "sample_ort_logits.npy", ort_y)
    parity = {"gate": "B1_FP32_MODEL_READY", "status": "PASS", "host_utc": utc(), "run_id": a.output.name,
              "original_vs_unfused_max_abs": float(np.max(np.abs(fp - unfused))), "torch_vs_ORT_max_abs": float(np.max(np.abs(fp - ort_y))),
              "atol": 1e-4, "rtol": 1e-4, "IR": graph.ir_version, "opset": 18, "preprocess_sample_exact": True,
              "accuracy": "NOT_RUN", "regression_20": "PENDING_DATA", "layer_count": len(layers)}
    if not np.allclose(fp, unfused, atol=1e-4, rtol=1e-4) or not np.allclose(fp, ort_y, atol=1e-4, rtol=1e-4):
        parity["status"] = "FAIL"
        write_json(a.output / "fp32_parity.json", parity)
        raise ValueError("Frozen FP32 parity gate failed")
    rewrite(a.bundle / "graphs/model_fp32.onnx", layers, a.bundle)
    write_json(a.output / "graph_audit.json", audit(a.bundle))
    write_json(a.bundle / "quant_protocol.json", {**PROTOCOL, "freeze_status": "PENDING_TRAIN_SELECTION"})
    qmodel = quant_model(layers)
    with torch.inference_mode():
        expected = qmodel(torch.from_numpy(x)).numpy()
    qort = session(a.bundle / "graphs/model_w8a8_reference.onnx").run(None, {"image": x})[0]
    np.save(a.output / "sample_quant_torch_logits.npy", expected)
    np.save(a.output / "sample_quant_ort_logits.npy", qort)
    qparity = {"status": "PASS" if np.allclose(expected, qort, atol=1e-4, rtol=1e-4) else "FAIL",
               "max_abs": float(np.max(np.abs(expected - qort))), "atol": 1e-4, "rtol": 1e-4, "pl": "NOT_RUN"}
    write_json(a.output / "quant_graph_sample_parity.json", qparity)
    write_json(a.output / "fp32_parity.json", parity)
    if qparity["status"] != "PASS":
        raise ValueError("Quant graph parity failed")
    print({"FP32": parity, "quant_sample": qparity}, flush=True)

if __name__ == "__main__":
    main()
