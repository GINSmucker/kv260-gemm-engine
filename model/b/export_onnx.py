"""Complete FP32 export and exact weight-identity Linear graph replacement."""
import copy
from pathlib import Path
import numpy as np
import onnx
from onnx import helper as H, numpy_helper as NH, TensorProto as T
import torch
from .common import read_json, sha, write_json
from .quantization import quantize, PROTOCOL

def export_fp32(model, tensor, path):
    # Unfused is the same attention expression; original/fused parity is tested.
    for block in model.blocks:
        block.attn.fused_attn = False
    torch.onnx.export(model, torch.from_numpy(tensor), str(path), input_names=["image"], output_names=["logits"],
                      opset_version=18, dynamo=False, do_constant_folding=False,
                      training=torch.onnx.TrainingMode.EVAL)
    m = onnx.load(path)
    onnx.checker.check_model(m)
    assert m.ir_version <= 10
    return m

def rewrite(fp32_path, layers, bundle):
    base = onnx.load(fp32_path)
    producers = {y: n for n in base.graph.node for y in n.output}
    consumers = {}
    for n in base.graph.node:
        for x in n.input:
            consumers.setdefault(x, []).append(n)
    arrays = {t.name: NH.to_array(t) for t in base.graph.initializer}
    plans, remove = {}, set()
    new_init = []
    for layer in layers:
        name = layer["layer_id"]
        weight_name = name + ".weight"
        bias_name = name + ".bias"
        original = arrays[weight_name]
        assert original.shape == (layer["N"], layer["K"])
        import hashlib
        assert hashlib.sha256(original.tobytes()).hexdigest() == layer["original_weight_sha256"]
        wq, sw = quantize(np.ascontiguousarray(original.T), 0)
        bias = arrays[bias_name]
        layer_dir = bundle / "weights" / f"{layer['layer_index']:02d}"
        layer_dir.mkdir(parents=True, exist_ok=True)
        for field, a in (("Wq", wq), ("sw", sw.reshape(-1)), ("bias", bias)):
            path = layer_dir / (field + ".bin")
            a.tofile(path)
            layer[field] = {"file": path.relative_to(bundle).as_posix(), "sha256": sha(path), "bytes": path.stat().st_size,
                            "dtype": a.dtype.str, "shape": list(a.shape)}
        # Only accept the exact exported Linear forms from this frozen weight.
        first = consumers[weight_name]
        if name == "head" and len(first) == 1 and first[0].op_type == "Gemm":
            node = first[0]
            attrs = {a.name: H.get_attribute_value(a) for a in node.attribute}
            assert attrs.get("transB", 0) == 1 and attrs.get("alpha", 1) == 1 and attrs.get("beta", 1) == 1
            x, output, drop = node.input[0], node.output[0], [node]
        else:
            assert len(first) == 1 and first[0].op_type == "Transpose"
            tr = first[0]
            mm = consumers[tr.output[0]]
            assert len(mm) == 1 and mm[0].op_type == "MatMul"
            mm = mm[0]
            adds = consumers[mm.output[0]]
            assert len(adds) == 1 and adds[0].op_type == "Add" and bias_name in adds[0].input
            x, output, drop = mm.input[0], adds[0].output[0], [tr, mm, adds[0]]
        prefix = "BQL_" + str(layer["layer_index"])
        layer["graph_input"], layer["graph_output"] = x, output
        for n in drop:
            remove.add(n.name)
        qname, sname, bname = prefix + "_Wq", prefix + "_sw", prefix + "_bias"
        new_init += [NH.from_array(wq, qname), NH.from_array(sw.reshape(-1), sname), NH.from_array(bias, bname)]
        custom = H.make_node("QuantLinear", [x, qname, sname, bname], [output], domain="com.kv260", name=prefix,
                    layer_id=name, layer_index=layer["layer_index"], family=layer["family"], M=layer["M"], K=layer["K"], N=layer["N"],
                    quant_protocol_id=PROTOCOL["id"], W_sha256=layer["Wq"]["sha256"], output_shape=layer["output_shape"])
        plans[drop[-1].name] = (custom, prefix, x, output, wq, sw.reshape(-1), bias)
    custom_model, reference = copy.deepcopy(base), copy.deepcopy(base)
    for model, is_custom in ((custom_model, True), (reference, False)):
        nodes, extra = [], []
        for node in model.graph.node:
            if node.name in plans:
                c, p, x, out, wq, sw, bias = plans[node.name]
                if is_custom:
                    nodes.append(c)
                else:
                    def const(suffix, a):
                        name = p + suffix
                        extra.append(NH.from_array(np.array(a), name))
                        return name
                    axes = const("_axes", np.array([-1], dtype=np.int64))
                    q = const("_127", np.float32(127))
                    lo = const("_lo", np.float32(-127))
                    one = const("_one", np.float32(1))
                    zero = const("_zero", np.float32(0))
                    tiny = const("_tiny", np.float32(np.finfo(np.float32).tiny))
                    wf = const("_Wf", wq.astype(np.float32))
                    sf = const("_sf", sw)
                    bf = const("_bf", bias)
                    def op(kind, inputs, suffix, **kw):
                        target = p + suffix
                        nodes.append(H.make_node(kind, inputs, [target], name=target, **kw))
                        return target
                    ab = op("Abs", [x], "_abs")
                    mx = op("ReduceMax", [ab, axes], "_max", keepdims=1)
                    z = op("Equal", [mx, zero], "_iszero")
                    div = op("Div", [mx, q], "_scaled")
                    floor = op("Max", [div, tiny], "_floor")
                    sx = op("Where", [z, one, floor], "_sx")
                    xd = op("Div", [x, sx], "_xd")
                    rnd = op("Round", [xd], "_rne")
                    xq = op("Clip", [rnd, lo, q], "_xq")
                    yi = op("MatMul", [xq, wf], "_yi")
                    i32 = op("Cast", [yi], "_i32", to=T.INT32)
                    f = op("Cast", [i32], "_f", to=T.FLOAT)
                    t1 = op("Mul", [f, sx], "_t1")
                    t2 = op("Mul", [t1, sf], "_t2")
                    nodes.append(H.make_node("Add", [t2, bf], [out], name=p + "_output"))
            elif node.name not in remove:
                nodes.append(node)
        del model.graph.node[:]
        model.graph.node.extend(nodes)
        used = {s for n in nodes for s in n.input}
        retained = [t for t in model.graph.initializer if t.name in used]
        del model.graph.initializer[:]
        model.graph.initializer.extend(retained + (new_init if is_custom else extra))
        if is_custom:
            model.opset_import.append(H.make_opsetid("com.kv260", 1))
        onnx.checker.check_model(model)
    onnx.save(custom_model, bundle / "graphs/model_w8a8_pl.onnx")
    onnx.save(reference, bundle / "graphs/model_w8a8_reference.onnx")
    write_json(bundle / "layers.json", layers)
    return custom_model, reference

def audit(bundle):
    layers = read_json(bundle / "layers.json")
    m = onnx.load(bundle / "graphs/model_w8a8_pl.onnx")
    nodes = [n for n in m.graph.node if n.domain == "com.kv260"]
    assert len(nodes) == 49
    table = {l["layer_id"]: l for l in layers}
    seen = []
    for n in nodes:
        a = {x.name: H.get_attribute_value(x) for x in n.attribute}
        name = a["layer_id"].decode()
        assert name not in seen and name in table
        l = table[name]
        assert all(a[k] == l[k] for k in ("M", "K", "N"))
        assert a["W_sha256"].decode() == l["Wq"]["sha256"]
        seen.append(name)
    assert seen == [l["layer_id"] for l in layers]
    return {"status": "PASS", "custom_nodes": 49, "unique_layer_ids": 49,
            "attention_dynamic_MatMul_retained": sum(n.op_type == "MatMul" for n in m.graph.node),
            "PatchEmbed_Conv_retained": sum(n.op_type == "Conv" for n in m.graph.node),
            "model_path": "12 complete blocks, unchanged attention and nonlinearity; full head 1000"}
