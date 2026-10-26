"""Create and verify a new H576 PL36 2:4 research candidate, offline only."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import tarfile
import traceback

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, helper, numpy_helper

from sparse24 import dense_golden, pack, prune, quantize_weight, require, slot_gemm, unpack


ARCHIVE_SHA256 = "c29a006fe472fd2bfb545d7bbf77f34a5090ff9315043cdd7e5830e518d61f7c"
FP32_SHA256 = "b07d7279aecaf64b7907c6849ad8bcce4acaafeeb1110bfdd1de2d5b4843fb21"
W8_SHA256 = "f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415"
ROOT = "v5_h576_ui_20261017_01/"
PL_FAMILIES = {"qkv", "fc1", "fc2"}
SEED = 20261021


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def write_json(path, value):
    # New artifacts only: accidental reuse/overwrite is an error.
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def arrays(model):
    result = {item.name: numpy_helper.to_array(item) for item in model.graph.initializer}
    require(len(result) == len(model.graph.initializer), "Duplicate initializer names")
    return result


def read_source(archive):
    require(sha_file(archive) == ARCHIVE_SHA256, "Unexpected source archive SHA256")
    wanted = {"assets/h576_fp32.onnx", "assets/h576_w8.onnx", "bundle/layers.json"}
    wanted.update(f"bundle/weights/{i:02d}_Wq.bin" for i in range(49))
    wanted.update(f"assets/layer_inputs/{i}_Xq.bin" for i in range(49))
    payload = {}
    # Read known regular files in memory; never extract arbitrary archive paths.
    with tarfile.open(archive, "r|gz") as stream:
        for member in stream:
            name = member.name.removeprefix(ROOT)
            if not member.name.startswith(ROOT) or name not in wanted:
                continue
            require(member.isfile() and 0 < member.size < 64 * 1024 * 1024,
                    "Invalid source member: " + member.name)
            require(name not in payload, "Duplicate source member: " + name)
            payload[name] = stream.extractfile(member).read()
    require(set(payload) == wanted, "Source archive is missing required members")
    require(sha_bytes(payload["assets/h576_fp32.onnx"]) == FP32_SHA256, "FP32 identity mismatch")
    require(sha_bytes(payload["assets/h576_w8.onnx"]) == W8_SHA256, "W8 identity mismatch")
    fp32 = onnx.load_model_from_string(payload["assets/h576_fp32.onnx"])
    w8 = onnx.load_model_from_string(payload["assets/h576_w8.onnx"])
    onnx.checker.check_model(fp32)
    onnx.checker.check_model(w8)
    layers = json.loads(payload["bundle/layers.json"])
    require(len(layers) == 49 and [l["layer_index"] for l in layers] == list(range(49)),
            "Unexpected layer inventory")
    require(Counter(l["family"] for l in layers) ==
            Counter(qkv=12, proj=12, fc1=12, fc2=12, head=1), "Unexpected families")
    fp = arrays(fp32)
    quantized = arrays(w8)
    for layer in layers:
        i, k, n = (layer[key] for key in ("layer_index", "K", "N"))
        original = fp[layer["layer_id"] + ".weight"]
        require(original.shape == (n, k) and original.dtype == np.float32,
                f"Unexpected FP32 layout at layer {i}")
        require(sha_bytes(original.tobytes()) == layer["original_weight_sha256"],
                f"Original FP32 weight mismatch at layer {i}")
        q, scale = quantize_weight(np.ascontiguousarray(original.T))
        data = payload["bundle/" + layer["Wq"]["file"]]
        require(q.tobytes() == data and sha_bytes(data) == layer["Wq"]["sha256"],
                f"Original dense quantization mismatch at layer {i}")
        require(np.array_equal(q.astype(np.float32), quantized[f"BQL_{i}_Wf"])
                and np.array_equal(scale.ravel(), quantized[f"BQL_{i}_sf"]),
                f"Original graph quantization mismatch at layer {i}")
        require(sha_bytes(scale.tobytes()) == layer["numeric_identity"]["sw"]
                and sha_bytes(quantized[f"BQL_{i}_bf"].tobytes()) == layer["numeric_identity"]["bias"],
                f"Original scale/bias identity mismatch at layer {i}")
        x = np.frombuffer(payload[f"assets/layer_inputs/{i}_Xq.bin"], np.int8)
        require(x.size == layer["M"] * k and np.all(x >= -127), f"Invalid Xq at layer {i}")
    return payload, layers, fp32, w8


def replace_initializers(model, replacements):
    changed = set()
    for tensor in model.graph.initializer:
        if tensor.name in replacements:
            tensor.CopyFrom(numpy_helper.from_array(replacements[tensor.name], tensor.name))
            changed.add(tensor.name)
    require(changed == set(replacements), "Missing replacement initializers")
    onnx.checker.check_model(model)


def compare_unmodified(source_data, candidate, expected_changes):
    source = onnx.load_model_from_string(source_data)
    # Strip initializer contents to ensure that graph topology and metadata are unchanged.
    old = {item.name: item.SerializeToString() for item in source.graph.initializer}
    new = {item.name: item.SerializeToString() for item in candidate.graph.initializer}
    require(old.keys() == new.keys(), "Initializer inventory changed")
    require({name for name in old if old[name] != new[name]} == expected_changes,
            "Unexpected initializer changes")
    skeleton = onnx.ModelProto()
    skeleton.CopyFrom(candidate)
    del source.graph.initializer[:]
    del skeleton.graph.initializer[:]
    require(source.SerializeToString() == skeleton.SerializeToString(), "Graph topology or metadata changed")


def candidate_protocol():
    return {
        "schema": "h576_sparse24_logical_v1",
        "status": "RESEARCH_CANDIDATE_NOT_QUALITY_ACCEPTED",
        "selected_families": sorted(PL_FAMILIES),
        "grouping": "For each output n, W[4*g:4*g+4,n]; W is C-order [K,N]",
        "selection": "FP32 abs descending, stable ascending K-index ties; keep two slots",
        "quantization": "AFTER mask; FP32 column absmax/127; nearest-even; [-127,127]; zero point 0",
        "zero_column_scale": 1.0,
        "minimum_scale": float(np.finfo(np.float32).tiny),
        "positions": {"dtype": "uint8", "shape": ["K/4", "N", 2],
                      "meaning": "two distinct ascending offsets in [0,3]"},
        "values": {"dtype": "int8", "shape": ["K/4", "N", 2]},
        "decode": "Wq[4*g + positions[g,n,s], n] = values[g,n,s]; others zero",
        "zero_slot_values": "LEGAL: exactly two retained slots, not necessarily two nonzero INT8 values",
        "physical_wire_format": "NOT_FROZEN; no bit-packed metadata or AXI layout is defined here",
        "storage_warning": "Values alone use half the dense INT8 bytes; two UINT8 offsets add the other half",
        "onnx_execution": "CPU_DENSE_PRUNED_REFERENCE; dense MatMul, not a sparse performance kernel",
        "bias_cpu_layers_attention": "UNCHANGED_INITIALIZERS_AND_TOPOLOGY",
        "activation_snapshots": "Original dense-model intermediate inputs; per-layer arithmetic tests only",
        "quality": "NOT_RUN; no real-image predictions, no training or fine-tuning",
        "board": "NOT_RUN; not interchangeable with frozen runtime/weights identities",
        "reference_rule": "GINSmucker/kv260-gemm-engine@030f011b46afdec3c6493356e5b5db39ca09a5a5:model/b/sparse24.py",
    }


def create_candidate(archive, output):
    require(not output.exists(), "Output exists; choose a new directory (no overwrite)")
    payload, layers, fp32, w8 = read_source(archive)
    output.mkdir(parents=True, exist_ok=False)
    try:
        (output / "weights").mkdir()
        (output / "cases").mkdir()
        fp = arrays(fp32)
        quantized = arrays(w8)
        fp_replacements, q_replacements, records = {}, {}, []
        selected_elements = retained_slots = total_outputs = 0
        rng = np.random.default_rng(SEED)
        for layer in layers:
            i, m, k, n = (layer[key] for key in ("layer_index", "M", "K", "N"))
            record = {"layer_index": i, "layer_id": layer["layer_id"], "family": layer["family"],
                      "shape_MKN": [m, k, n], "source_weight_sha256": layer["original_weight_sha256"]}
            if layer["family"] not in PL_FAMILIES:
                records.append({**record, "mode": "dense_w8a8_unchanged", "target": "CPU"})
                continue
            original = np.ascontiguousarray(fp[layer["layer_id"] + ".weight"].T)
            wp, positions, mask = prune(original)
            wq, sw = quantize_weight(wp)
            values = pack(wq, positions)
            np.testing.assert_array_equal(unpack(values, positions), wq)
            require(np.all(mask.reshape(k // 4, 4, n).sum(axis=1) == 2), "Invalid mask")
            require(np.all(wq[~mask] == 0), "Quantization regrew a masked value")
            x_bytes = payload[f"assets/layer_inputs/{i}_Xq.bin"]
            x = np.frombuffer(x_bytes, np.int8).reshape(m, k)
            reference = dense_golden(x, wq)
            np.testing.assert_array_equal(slot_gemm(x, values, positions), reference)
            # Extra signed extremes, zeros and a reproducible synthetic input.
            edge = np.vstack([np.zeros(k, np.int8), np.full(k, -128, np.int8),
                              np.full(k, 127, np.int8),
                              rng.integers(-128, 128, size=(3, k), dtype=np.int8)])
            edge_y = dense_golden(edge, wq)
            np.testing.assert_array_equal(slot_gemm(edge, values, positions), edge_y)
            folder = output / "weights" / f"layer_{i:02d}.npz"
            np.savez(folder, W_fp32=wp, Wq=wq, sw=sw, mask=mask, values=values,
                     positions=positions, bias=quantized[f"BQL_{i}_bf"])
            case = output / "cases" / f"layer_{i:02d}.npz"
            np.savez(case, Xq=x, Y_int32=reference, edge_Xq=edge, edge_Y_int32=edge_y)
            fp_replacements[layer["layer_id"] + ".weight"] = np.ascontiguousarray(wp.T)
            q_replacements[f"BQL_{i}_Wf"] = wq.astype(np.float32)
            q_replacements[f"BQL_{i}_sf"] = sw.ravel()
            energy = np.sum(original.astype(np.float64) ** 2)
            removed = np.sum((original.astype(np.float64) - wp.astype(np.float64)) ** 2)
            record.update(mode="sparse24_w8a8_logical", target="PL_RESEARCH_NOT_READY",
                          weights=folder.relative_to(output).as_posix(),
                          integer_case=case.relative_to(output).as_posix(),
                          snapshot_x_sha256=sha_bytes(x_bytes), retained_slots=int(mask.sum()),
                          int8_nonzero_values=int(np.count_nonzero(wq)), integer_mismatches=0,
                          removed_weight_energy_fraction=float(removed / energy) if energy else 0.0)
            records.append(record)
            selected_elements += original.size
            retained_slots += int(mask.sum())
            total_outputs += reference.size + edge_y.size
            print(f"PRUNED layer={i:02d} {layer['layer_id']} shape={k}x{n} integer_mismatches=0", flush=True)

        replace_initializers(fp32, fp_replacements)
        replace_initializers(w8, q_replacements)
        compare_unmodified(payload["assets/h576_fp32.onnx"], fp32, set(fp_replacements))
        # Max magnitudes are retained, so weight scales normally remain byte-identical.
        old_q = arrays(onnx.load_model_from_string(payload["assets/h576_w8.onnx"]))
        changed_q = {name for name, value in q_replacements.items()
                     if not np.array_equal(old_q[name], value)}
        compare_unmodified(payload["assets/h576_w8.onnx"], w8, changed_q)
        onnx.save_model(fp32, output / "pruned_fp32.onnx")
        onnx.save_model(w8, output / "pruned_w8a8_reference.onnx")
        write_json(output / "protocol.json", candidate_protocol())
        write_json(output / "layers.json", records)
        require(sha_file(archive) == ARCHIVE_SHA256, "Source changed during pruning")
        manifest = {
            "schema": "h576_sparse24_candidate_v1",
            "status": "PRUNING_AND_LAYER_ARITHMETIC_PASS_QUALITY_NOT_RUN",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": {"archive_sha256": ARCHIVE_SHA256, "fp32_sha256": FP32_SHA256,
                       "w8_sha256": W8_SHA256, "original_archive_modified": False},
            "environment": {"python": sys.version.split()[0], "numpy": np.__version__,
                            "onnx": onnx.__version__, "onnxruntime": ort.__version__,
                            "platform": platform.platform(), "seed": SEED},
            "implementation": {name: sha_file(Path(__file__).with_name(name))
                               for name in ("prune_h576.py", "sparse24.py", "test_sparse24.py")},
            "summary": {"layers": 49, "pruned_layers": len(fp_replacements),
                        "unchanged_cpu_layers": 49 - len(fp_replacements),
                        "selected_dense_elements": selected_elements,
                        "retained_slots": retained_slots, "pruned_slot_fraction": 0.5,
                        "checked_layer_output_elements": total_outputs, "integer_mismatches": 0},
            "acceptance": {"accuracy": "NOT_RUN", "fine_tuning": "NOT_RUN",
                           "hardware_format": "NOT_FROZEN", "HLS_RTL_PL": "NOT_RUN",
                           "speedup": "NOT_MEASURED"},
            "files": {p.relative_to(output).as_posix(): {"sha256": sha_file(p), "bytes": p.stat().st_size}
                      for p in sorted(output.rglob("*")) if p.is_file()},
        }
        write_json(output / "candidate.json", manifest)
        print(json.dumps(manifest["summary"], indent=2), flush=True)
    except BaseException:
        write_json(output / "failure.json", {"status": "FAIL", "traceback": traceback.format_exc()})
        raise


def ort_session(model):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(model.SerializeToString(), options,
                                providers=["CPUExecutionProvider"])


def verify_candidate(archive, output):
    require(not (output / "verification.json").exists(), "Verification exists; preserve the prior receipt")
    manifest = json.loads((output / "candidate.json").read_text(encoding="utf-8"))
    require(manifest["source"]["archive_sha256"] == ARCHIVE_SHA256, "Candidate source mismatch")
    for name, identity in manifest["files"].items():
        path = (output / name).resolve()
        require(path.is_relative_to(output.resolve()) and path.is_file(), "Invalid artifact path")
        require(path.stat().st_size == identity["bytes"] and sha_file(path) == identity["sha256"],
                "Candidate artifact identity changed: " + name)
    for name, digest in manifest["implementation"].items():
        require(sha_file(Path(__file__).with_name(name)) == digest, "Implementation identity changed: " + name)
    payload, layers, base_fp, base_q = read_source(archive)
    fp = onnx.load(output / "pruned_fp32.onnx")
    qmodel = onnx.load(output / "pruned_w8a8_reference.onnx")
    fp_arrays, q_arrays = arrays(fp), arrays(qmodel)
    original_fp, original_q = arrays(base_fp), arrays(base_q)
    fp_changes, q_changes, layer_evidence = set(), set(), []
    records = json.loads((output / "layers.json").read_text(encoding="utf-8"))
    require([r["layer_index"] for r in records] == list(range(49)), "Candidate layer inventory mismatch")
    total_integers = 0
    for layer, record in zip(layers, records):
        i, m, k, n = (layer[key] for key in ("layer_index", "M", "K", "N"))
        require(record["layer_id"] == layer["layer_id"] and record["family"] == layer["family"],
                "Layer identity changed")
        if layer["family"] not in PL_FAMILIES:
            continue
        with np.load(output / "weights" / f"layer_{i:02d}.npz", allow_pickle=False) as weights:
            wp, wq, scale = weights["W_fp32"], weights["Wq"], weights["sw"]
            positions, mask, values = weights["positions"], weights["mask"], weights["values"]
            # Independent Python ranking checks every group, including tie handling.
            original = np.ascontiguousarray(original_fp[layer["layer_id"] + ".weight"].T)
            require(mask.dtype == np.bool_ and mask.shape == (k, n), "Mask layout mismatch")
            expected_positions = np.empty((k // 4, n, 2), np.uint8)
            for g in range(k // 4):
                for column in range(n):
                    order = sorted(range(4), key=lambda s: (-abs(float(original[4*g+s, column])), s))
                    expected_positions[g, column] = sorted(order[:2])
            np.testing.assert_array_equal(positions, expected_positions)
            expected_mask = np.zeros((k, n), bool)
            for slot in range(2):
                expected_mask[4 * np.arange(k//4)[:, None] + positions[:, :, slot],
                              np.arange(n)[None, :]] = True
            np.testing.assert_array_equal(mask, expected_mask)
            np.testing.assert_array_equal(wp, np.where(mask, original, np.float32(0)))
            expected_q, expected_scale = quantize_weight(wp)
            np.testing.assert_array_equal(wq, expected_q)
            np.testing.assert_array_equal(scale, expected_scale)
            np.testing.assert_array_equal(unpack(values, positions), wq)
            np.testing.assert_array_equal(weights["bias"], original_q[f"BQL_{i}_bf"])
            np.testing.assert_array_equal(fp_arrays[layer["layer_id"] + ".weight"], wp.T)
            np.testing.assert_array_equal(q_arrays[f"BQL_{i}_Wf"], wq.astype(np.float32))
            np.testing.assert_array_equal(q_arrays[f"BQL_{i}_sf"], scale.ravel())
            with np.load(output / "cases" / f"layer_{i:02d}.npz", allow_pickle=False) as case:
                require(case["Xq"].shape == (m, k) and
                        case["Xq"].tobytes() == payload[f"assets/layer_inputs/{i}_Xq.bin"],
                        "Snapshot input identity mismatch")
                for x_key, y_key in (("Xq", "Y_int32"), ("edge_Xq", "edge_Y_int32")):
                    reference = dense_golden(case[x_key], wq)
                    np.testing.assert_array_equal(case[y_key], reference)
                    np.testing.assert_array_equal(slot_gemm(case[x_key], values, positions), reference)
                    total_integers += reference.size
        fp_changes.add(layer["layer_id"] + ".weight")
        for name in (f"BQL_{i}_Wf", f"BQL_{i}_sf"):
            if not np.array_equal(original_q[name], q_arrays[name]):
                q_changes.add(name)
        layer_evidence.append({"layer_index": i, "mask_and_ranking": "PASS", "integer_mismatches": 0})
        print(f"VERIFIED layer={i:02d} ranking/mask/quantization/slots/golden", flush=True)
    compare_unmodified(payload["assets/h576_fp32.onnx"], fp, fp_changes)
    compare_unmodified(payload["assets/h576_w8.onnx"], qmodel, q_changes)

    # Whole-graph smoke and new, propagated intermediate activations. These are NOT images.
    engine_fp = ort_session(fp)
    engine_q = ort_session(qmodel)
    instrumented = onnx.ModelProto()
    instrumented.CopyFrom(qmodel)
    selected = [l for l in layers if l["family"] in PL_FAMILIES]
    for layer in selected:
        i = layer["layer_index"]
        instrumented.graph.output.append(helper.make_tensor_value_info(
            f"BQL_{i}_xq", TensorProto.FLOAT, layer["input_shape"]))
        instrumented.graph.output.append(helper.make_tensor_value_info(
            f"BQL_{i}_i32", TensorProto.INT32, layer["output_shape"]))
    onnx.checker.check_model(instrumented)
    engine_instrumented = ort_session(instrumented)
    rng = np.random.default_rng(SEED)
    synthetic = [("zero", np.zeros((1, 3, 224, 224), np.float32)),
                 ("seeded_normal", rng.normal(0, 0.25, (1, 3, 224, 224)).astype(np.float32))]
    graph_evidence = []
    for name, image in synthetic:
        fp_logits = engine_fp.run(None, {"image": image})[0]
        q_logits = engine_q.run(None, {"image": image})[0]
        values_out = engine_instrumented.run(None, {"image": image})
        require(fp_logits.shape == q_logits.shape == (1, 1000)
                and np.isfinite(fp_logits).all() and np.isfinite(q_logits).all(), "Invalid graph output")
        np.testing.assert_array_equal(values_out[0], q_logits)
        for j, layer in enumerate(selected):
            i, k, n = (layer[key] for key in ("layer_index", "K", "N"))
            xqf, actual = values_out[1+2*j:3+2*j]
            require(np.array_equal(xqf, np.rint(xqf)) and np.all(np.abs(xqf) <= 127),
                    "Invalid graph quantized activation")
            xq = xqf.astype(np.int8).reshape(-1, k)
            wq = q_arrays[f"BQL_{i}_Wf"].astype(np.int8)
            golden = dense_golden(xq, wq)
            np.testing.assert_array_equal(actual.reshape(-1, n), golden)
            total_integers += golden.size
            if j % 12 == 0:
                print(f"GRAPH {name} integer-check layer={i:02d}", flush=True)
        # Preserve numerical outputs, not benchmark timings or quality scores.
        np.savez(output / f"smoke_{name}.npz", input=image, fp32_logits=fp_logits, w8a8_logits=q_logits)
        graph_evidence.append({"input": name, "input_sha256": sha_bytes(image.tobytes()),
                               "logits": 1000, "finite": True,
                               "instrumented_logits_unchanged": True,
                               "checked_pruned_layers": len(selected), "integer_mismatches": 0})
    require(sha_file(archive) == ARCHIVE_SHA256, "Source archive changed")
    result = {
        "schema": "h576_sparse24_verification_v1",
        "status": "PASS_STRUCTURE_AND_SOFTWARE_ARITHMETIC_ONLY",
        "candidate_manifest_sha256": sha_file(output / "candidate.json"),
        "pruned_layers": len(layer_evidence), "unchanged_cpu_layers": 13,
        "checked_integer_elements": total_integers, "integer_mismatches": 0,
        "layer_checks": layer_evidence, "whole_graph_smoke": graph_evidence,
        "cpu_layers_biases_other_initializers_and_graph_topology": "UNCHANGED",
        "real_image_accuracy": "NOT_RUN", "fine_tuning": "NOT_RUN",
        "HLS_RTL_PL": "NOT_RUN", "physical_format": "NOT_FROZEN",
        "baseline_accuracy_and_speedup_inherited": False,
        "source_archive_unchanged": True,
        "verification_outputs": {p.name: sha_file(p) for p in sorted(output.glob("smoke_*.npz"))},
    }
    write_json(output / "verification.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("layer_checks",)}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prune", "verify"))
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.prefix == sys.base_prefix:
        parser.error("Use the project's .venv Python")
    action = create_candidate if args.action == "prune" else verify_candidate
    action(args.archive.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
