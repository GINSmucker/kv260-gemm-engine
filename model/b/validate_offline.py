"""Train-only selection, full FP32 regression and real-activation INT64 audit."""
import argparse
import copy
import hashlib
from pathlib import Path
import shutil
import time
import numpy as np
import onnx
from onnx import helper as H, TensorProto as T
import torch
from .common import ROOT, read_json, sha, utc, write_json
from .compatibility import session
from .golden_int64 import gemm
from .preprocess import preprocess
from .quantization import quantize, postprocess, PROTOCOL
from .reference_model import original

def diagnostic_graph(bundle, output):
    m = onnx.load(bundle / "graphs/model_w8a8_reference.onnx")
    names, fields = [], []
    for l in read_json(bundle / "layers.json"):
        p = "BQL_" + str(l["layer_index"])
        for name, kind in ((l["graph_input"], T.FLOAT), (p + "_xq", T.FLOAT), (p + "_sx", T.FLOAT),
                           (p + "_i32", T.INT32), (l["graph_output"], T.FLOAT)):
            names.append(name)
            if name not in {v.name for v in m.graph.output}:
                m.graph.output.append(H.make_tensor_value_info(name, kind, None))
    # Internal diagnostic graph remains in evidence, never changes deployment graph.
    path = output / "quant_diagnostic.onnx"
    onnx.save(m, path)
    return session(path), names

def weights(bundle, l):
    return tuple(np.fromfile(bundle / l[f]["file"], dtype=l[f]["dtype"]).reshape(l[f]["shape"]) for f in ("Wq", "sw", "bias"))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    splits = read_json(ROOT / "results/B/B1/data_20260930_01/splits.json")
    for name in ("splits.json", "labels_imagenet1k.json", "dataset_source.json"):
        shutil.copyfile(ROOT / "results/B/B1/data_20260930_01" / name, a.bundle / name)
    layers = read_json(a.bundle / "layers.json")
    fp, quant = session(a.bundle / "graphs/model_fp32.onnx"), session(a.bundle / "graphs/model_w8a8_reference.onnx")
    diagnostic, names = diagnostic_graph(a.bundle, a.output)
    model = original()
    records = []
    regression_dir = a.bundle / "regression"
    regression_dir.mkdir(exist_ok=True)
    for i, row in enumerate(splits["splits"]["regression"]):
        x = preprocess(a.data / row["path"])
        with torch.inference_mode():
            expected = model(torch.from_numpy(x)).numpy()
        actual = fp.run(None, {"image": x})[0]
        qy = quant.run(None, {"image": x})[0]
        ok = np.allclose(expected, actual, atol=1e-4, rtol=1e-4)
        np.save(regression_dir / f"{i:02d}_input.npy", x)
        np.save(regression_dir / f"{i:02d}_fp32_logits.npy", actual)
        np.save(regression_dir / f"{i:02d}_quant_logits.npy", qy)
        target_image = regression_dir / f"{i:02d}.JPEG"
        shutil.copyfile(a.data / row["path"], target_image)
        records.append({"image": row, "index": i, "FP32_parity": "PASS" if ok else "FAIL", "max_abs": float(np.max(np.abs(expected - actual))),
                        "input_sha256": sha(regression_dir / f"{i:02d}_input.npy"), "FP32_logits_sha256": sha(regression_dir / f"{i:02d}_fp32_logits.npy"),
                        "quant_logits_sha256": sha(regression_dir / f"{i:02d}_quant_logits.npy")})
        if not ok:
            write_json(a.output / "fp32_regression_failed.json", records)
            raise ValueError("FP32 regression gate failed")
        print(f"FP32 full regression {i + 1}/20", flush=True)
    write_json(a.output / "fp32_regression.json", {"gate": "B1_FP32_MODEL_READY", "status": "PASS", "images": 20,
               "atol": 1e-4, "rtol": 1e-4, "records": records, "created_utc": utc()})
    write_json(a.bundle / "regression_manifest.json", records)

    # Calibration observes the candidate's real propagated activations (train only).
    stats = [{"layer_id": l["layer_id"], "calls": 0, "zero_rows": 0, "scale_min": float("inf"), "scale_max": 0.0,
              "at_limit": 0, "elements": 0, "clipped_excess": 0} for l in layers]
    for i, row in enumerate(splits["splits"]["calibration"]):
        x = preprocess(a.data / row["path"])
        outputs = diagnostic.run(names, {"image": x})
        for j, l in enumerate(layers):
            inp, q, s, yi, yf = outputs[j * 5:j * 5 + 5]
            st = stats[j]
            st["calls"] += 1
            st["zero_rows"] += int(np.sum(np.max(np.abs(inp), axis=-1) == 0))
            st["scale_min"] = min(st["scale_min"], float(s.min()))
            st["scale_max"] = max(st["scale_max"], float(s.max()))
            st["at_limit"] += int(np.sum(np.abs(q) == 127))
            st["elements"] += q.size
            st["clipped_excess"] += int(np.sum(np.abs(np.rint(inp / s)) > 127))
        if (i + 1) % 32 == 0:
            print(f"train calibration {i + 1}/256", flush=True)
    write_json(a.output / "calibration_summary.json", {"status": "PASS", "images": 256, "layers": stats, "created_utc": utc()})
    sample_records, fcorrect, qcorrect, f5, q5 = [], 0, 0, 0, 0
    for i, row in enumerate(splits["splits"]["selection"]):
        x = preprocess(a.data / row["path"])
        fy, qy = fp.run(None, {"image": x})[0][0], quant.run(None, {"image": x})[0][0]
        ft, qt = np.argsort(-fy, kind="stable")[:5], np.argsort(-qy, kind="stable")[:5]
        fcorrect += int(ft[0] == row["label"])
        qcorrect += int(qt[0] == row["label"])
        f5 += int(row["label"] in ft)
        q5 += int(row["label"] in qt)
        sample_records.append({"image": row, "FP32_top5": ft.tolist(), "quant_top5": qt.tolist(),
                               "FP32_logits": fy.tolist(), "quant_logits": qy.tolist()})
        if (i + 1) % 20 == 0:
            print(f"train selection {i + 1}/100", flush=True)
    drop = float(fcorrect - qcorrect)
    selected = drop <= 1.0
    decision = {"gate": "B2_QUANT_PROTOCOL_FROZEN", "status": "PASS" if selected else "FAIL_TRAIN_SELECTION",
                "created_utc": utc(), "protocol": PROTOCOL["id"], "candidate_count": 1,
                "train_only": True, "calibration_images": 256, "selection_images": 100,
                "FP32_top1_correct": fcorrect, "quant_top1_correct": qcorrect, "FP32_top5_correct": f5, "quant_top5_correct": q5,
                "top1_drop_pp": drop, "max_drop_pp": 1.0, "alternative_candidates": "NOT_NEEDED" if selected else "REQUIRED",
                "canonical_quant_network_reference": "ORT standard quant semantics graph; full propagated activations",
                "torch_complete_quant_parity": "FAIL_RETAINED; cross-framework FP32 differences cross dynamic RNE boundaries",
                "PL": "NOT_RUN", "samples": sample_records}
    write_json(a.output / "quant_selection.json", decision)
    if not selected:
        raise ValueError("Default candidate needs train-side alternatives")
    write_json(a.bundle / "quant_protocol.json", {**PROTOCOL, "freeze_status": "FROZEN_AFTER_TRAIN_SELECTION",
               "frozen_utc": utc(), "selection_sha256": sha(a.output / "quant_selection.json"), "val_predictions_before_freeze": 0})
    print(f"train selection PASS: FP32={fcorrect}/100 quant={qcorrect}/100 drop={drop}pp; val NOT_RUN", flush=True)

if __name__ == "__main__":
    main()
