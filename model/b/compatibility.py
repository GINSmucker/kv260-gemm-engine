"""B0 fixed IR/opset, standard quantization and custom-domain probe."""
import argparse
import importlib.metadata as md
from pathlib import Path
import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto as T
import onnxruntime as ort
from .common import ROOT, read_json, sha, utc, write_json

def session(path):
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    so.intra_op_num_threads = 4
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260930)
    x = rng.normal(size=(1, 192)).astype(np.float32)
    w = rng.normal(size=(192, 1000)).astype(np.float32)
    nodes = [helper.make_node("MatMul", ["X", "W"], ["Y"])]
    g = helper.make_graph(nodes, "B0_full_head", [helper.make_tensor_value_info("X", T.FLOAT, [1, 192])],
                          [helper.make_tensor_value_info("Y", T.FLOAT, [1, 1000])], [numpy_helper.from_array(w, "W")])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 18)])
    onnx.checker.check_model(m)
    onnx.save(m, a.output / "linear_probe.onnx")
    actual = session(a.output / "linear_probe.onnx").run(None, {"X": x})[0]
    assert np.allclose(actual, x @ w, atol=1e-4, rtol=1e-4)
    qnodes = [helper.make_node("Div", ["X", "s"], ["div"]), helper.make_node("Round", ["div"], ["round"]),
              helper.make_node("Clip", ["round", "lo", "hi"], ["Q"])]
    init = [numpy_helper.from_array(np.array([[0.5]], dtype=np.float32), "s"),
            numpy_helper.from_array(np.array(-127, dtype=np.float32), "lo"), numpy_helper.from_array(np.array(127, dtype=np.float32), "hi")]
    qm = helper.make_model(helper.make_graph(qnodes, "round_probe", [helper.make_tensor_value_info("X", T.FLOAT, [1, 8])],
             [helper.make_tensor_value_info("Q", T.FLOAT, [1, 8])], init), opset_imports=[helper.make_opsetid("", 18)])
    onnx.save(qm, a.output / "quant_probe.onnx")
    ties = np.array([[-63.75, -1.25, -0.75, -0.25, 0.25, 0.75, 1.25, 63.75]], dtype=np.float32)
    result = session(a.output / "quant_probe.onnx").run(None, {"X": ties})[0]
    assert np.array_equal(result, [[-127, -2, -2, 0, 0, 2, 2, 127]])
    custom = helper.make_node("QuantLinear", ["X", "W"], ["Y"], domain="com.kv260", layer_id="head", M=1, K=192, N=1000)
    cm = helper.make_model(helper.make_graph([custom], "custom_probe", list(g.input), list(g.output), list(g.initializer)),
                          opset_imports=[helper.make_opsetid("", 18), helper.make_opsetid("com.kv260", 1)])
    onnx.checker.check_model(cm)
    onnx.save(cm, a.output / "custom_domain_probe.onnx")
    assert onnx.load(a.output / "custom_domain_probe.onnx").graph.node[0].domain == "com.kv260"
    before = read_json(a.output / "environment_windows.json")["packages"]
    after = {d.metadata["Name"]: d.version for d in md.distributions()}
    assert all(after.get(k) == v for k, v in before.items())
    result = {"gate": "B0_READY", "status": "PASS", "host_utc": utc(), "run_id": a.output.name,
              "ort": ort.__version__, "onnx": onnx.__version__, "IR": m.ir_version, "opset": 18,
              "linear": "PASS (1,192,1000)", "RNE_Clip_broadcast": "PASS", "custom_domain_preservation": "PASS",
              "base_packages_unchanged": True, "ARM_custom_runtime": "NOT_RUN"}
    write_json(a.output / "compatibility_probe.json", result)
    (a.output / "requirements_after.txt").write_text("\n".join(f"{k}=={v}" for k, v in sorted(after.items())) + "\n", encoding="utf-8")
    print(result, flush=True)

if __name__ == "__main__":
    main()
