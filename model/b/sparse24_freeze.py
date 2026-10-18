"""Freeze the selected software candidate and verify complete graph integers."""
import argparse
import shutil
import tarfile
from pathlib import Path
import numpy as np
import onnx
from onnx import helper as H, TensorProto as T, numpy_helper as NH
from .common import ROOT, read_json, sha, write_json, utc
from .sparse24_study import BUNDLE, graph, session
from .golden_int64 import gemm


def freeze(study):
    decision = read_json(study / "selection_decision.json")
    if decision["status"] != "CANDIDATE_SELECTED":
        raise ValueError("No quality candidate; do not access formal validation")
    protocol = read_json(study / "study_protocol.json")
    for name, key in (("sparse24_study.py", "source_sha256"), ("sparse24.py", "pruning_source_sha256")):
        if sha(Path(__file__).with_name(name)) != protocol[key]:
            raise ValueError("Selection implementation identity changed")
    destination = study / "candidate"
    destination.mkdir(exist_ok=False)
    selected = decision["candidate"]
    layers = read_json(BUNDLE / "layers.json")
    pruning_evidence = read_json(study / "pruning_verification.json")["layers"]
    modes = []
    for layer in layers:
        index = layer["layer_index"]
        sparse = index in selected["indices"]
        entry = {"layer_id": layer["layer_id"], "layer_index": index, "family": layer["family"],
                 "shape": [layer[k] for k in ("M", "K", "N")], "mode": "sparse24_w8a8" if sparse else "dense_w8a8",
                 "software_execution": "CPU_DENSE_PRUNED_REFERENCE", "PL": "NOT_READY",
                 "planned_target": "PL" if layer["family"] in ("qkv","fc1","fc2") else "CPU"}
        source = study / "weights" / f"{index:02d}"
        entry["weights"] = ({p.name: sha(p) for p in source.glob("*.npy") if not p.name.startswith("integer_")}
                            if sparse else {k: layer[k]["sha256"] for k in ("Wq", "sw", "bias")})
        if sparse:
            target = destination / "weights" / f"{index:02d}"
            target.mkdir(parents=True)
            for name in entry["weights"]:
                if sha(source / name) != pruning_evidence[index]["files"][name]:
                    raise ValueError("Pruned logical tensor identity changed")
                shutil.copyfile(source / name, target / name)
            entry["logical_weight_directory"] = target.relative_to(destination).as_posix()
        modes.append(entry)
    write_json(destination / "layer_modes.json", modes)
    for mode in ("fp32", "w8a8"):
        path = destination / ("pruned_"+mode+".onnx")
        path.write_bytes(graph(study, selected["indices"], mode))
        if sha(path) != selected["results"][mode]["graph_sha256"]:
            raise ValueError("Graph differs from the selected candidate")
    instrumented = onnx.load(destination / "pruned_w8a8.onnx")
    weights = {t.name: NH.to_array(t) for t in instrumented.graph.initializer}
    for layer in layers:
        i = layer["layer_index"]
        shape = layer["input_shape"]
        instrumented.graph.output.append(H.make_tensor_value_info(f"BQL_{i}_xq", T.FLOAT, shape))
        instrumented.graph.output.append(H.make_tensor_value_info(f"BQL_{i}_i32", T.INT32, layer["output_shape"]))
    engine = session(instrumented.SerializeToString())
    plain = session((destination / "pruned_w8a8.onnx").read_bytes())
    inputs = np.load(study / "selection_inputs.npy", mmap_mode="r")
    # Fixed train-side indices; no validation image is used for this proof.
    evidence = []
    integer_folder = study / "selected_integer"
    integer_folder.mkdir()
    total = 0
    for image in (0,499,999):
        results = engine.run(None, {"image": inputs[image:image+1]})
        np.testing.assert_array_equal(results[0], plain.run(None, {"image": inputs[image:image+1]})[0])
        for i, layer in enumerate(layers):
            xqf, yi = results[1+2*i:3+2*i]
            assert np.array_equal(xqf, np.rint(xqf)) and np.max(np.abs(xqf)) <= 127
            x = xqf.astype(np.int8).reshape(layer["M"], layer["K"])
            wf = weights[f"BQL_{i}_Wf"]
            assert np.array_equal(wf, wf.astype(np.int8))
            w = wf.astype(np.int8)
            expected = (np.load(study / "weights" / f"{i:02d}" / "Wq.npy") if i in selected["indices"]
                        else np.fromfile(BUNDLE / layer["Wq"]["file"], dtype=np.int8).reshape(layer["K"],layer["N"]))
            np.testing.assert_array_equal(w, expected)
            ref = gemm(x, w)
            np.testing.assert_array_equal(yi.reshape(ref.shape), ref)
            case = integer_folder / f"image{image:03d}_layer{i:02d}.npz"
            np.savez(case, Xq=x, actual=yi.reshape(ref.shape), reference=ref)
            total += ref.size
            evidence.append({"image_index": image, "layer_index": i, "outputs": ref.size, "differences": 0, "file": case.name, "sha256": sha(case)})
    write_json(study / "selected_integer_verification.json", {"status": "PASS", "cases": evidence, "integer_outputs": total,
               "graph_sha256": sha(destination / "pruned_w8a8.onnx"), "reference": "independent full INT64", "PL": "NOT_RUN"})
    write_json(destination / "pruning_protocol.json", {"schema": "sparse24_software_pruning_v1", "candidate_id": "B_SPARSE24_W8A8_V1",
               "algorithm": "FP32 absolute magnitude descending; stable K index ties; two ascending retained slots; quantize after mask",
               "position_values": "zero slot values are legal", "physical_format": "NOT_FROZEN",
               "quality_max_drop_pp": 1.0, "selection_candidate": selected["name"], "selection_indices": selected["indices"],
               "selection_sha256": sha(study / "selection.json"), "decision_sha256": sha(study / "selection_decision.json"),
               "study_protocol_sha256": sha(study / "study_protocol.json"), "integer_evidence_sha256": sha(study / "selected_integer_verification.json")})
    write_json(destination / "freeze.json", {"status": "FROZEN_BEFORE_VAL", "utc": utc(), "candidate_id": "B_SPARSE24_W8A8_V1",
               "files": {p.relative_to(destination).as_posix(): sha(p) for p in destination.rglob("*") if p.is_file()},
               "quality_runner_sha256": sha(Path(__file__).with_name("sparse24_quality.py")),
               "splits_sha256": sha(BUNDLE / "splits.json"), "quant_protocol_sha256": sha(BUNDLE / "quant_protocol.json"),
               "original_fp32_sha256": sha(BUNDLE / "graphs/model_fp32.onnx"), "validation_predictions": 0})
    archive = study / "arm_quality_package.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for p in destination.iterdir():
            tf.add(p, arcname="candidate/"+p.name)
        for name in ("__init__.py", "common.py", "preprocess.py", "sparse24_quality.py"):
            tf.add(Path(__file__).with_name(name), arcname="b/"+name)
    write_json(study / "arm_package.json", {"sha256": sha(archive), "bytes": archive.stat().st_size,
               "freeze_sha256": sha(destination / "freeze.json"), "PL": "NOT_RUN"})
    print("FREEZE_PASS", selected["name"], selected["indices"], total, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    freeze(parser.parse_args().study.resolve())
