"""Actual custom CPU RNE/zero/subnormal/extreme-INT8 and rejection probes."""
import argparse
import copy
import json
import os
from pathlib import Path
import platform
import numpy as np
import onnxruntime as ort
from model.b.common import read_json,sha,utc,write_json

def make_graph(bundle,path):
    import onnx
    from onnx import helper as h,TensorProto as t
    original=onnx.load(bundle/"graphs/model_w8a8_pl.onnx")
    node=copy.deepcopy([n for n in original.graph.node if n.op_type=="QuantLinear"][-1])
    keep=set(node.input[1:]);node.input[0]="X"
    initializers=[copy.deepcopy(v) for v in original.graph.initializer if v.name in keep]
    g=h.make_graph([node],"B_head_custom_numeric_probe",[h.make_tensor_value_info("X",t.FLOAT,[1,192])],
                   [h.make_tensor_value_info(node.output[0],t.FLOAT,[1,1000])],initializers)
    m=h.make_model(g,opset_imports=[h.make_opsetid("",18),h.make_opsetid("com.kv260",1)]);m.ir_version=10
    onnx.checker.check_model(m);onnx.save(m,path)

def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--graph",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--plugin",type=Path);p.add_argument("--generate",action="store_true");a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    if a.generate:make_graph(a.bundle,a.graph)
    plugin=a.plugin or a.bundle/"plugins"/("quant_linear_windows.dll" if platform.system()=="Windows" else "libquant_linear_arm.so")
    s=ort.SessionOptions();s.intra_op_num_threads=1;s.inter_op_num_threads=1
    s.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL;s.register_custom_ops_library(str(plugin.resolve()))
    sess=ort.InferenceSession(str(a.graph),s,providers=["CPUExecutionProvider"])
    head=read_json(a.bundle/"layers.json")[-1]
    w=np.fromfile(a.bundle/head["Wq"]["file"],dtype=np.int8).reshape(192,1000)
    x=np.zeros((1,192),dtype=np.float32);x[0,:9]=[-2.5,-1.5,-.5,0,0,.5,1.5,2.5,127]
    tie=np.zeros((1,192),dtype=np.int8);tie[0,:9]=[-2,-2,0,0,0,0,2,2,127]
    tiny=np.zeros_like(x);tiny[0,0]=np.nextafter(np.float32(0),np.float32(1))
    cases=[("zero",np.zeros_like(x),np.zeros_like(tie),np.float32(1)),
           ("RNE_ties",x,tie,np.float32(1)),("subnormal",tiny,np.zeros_like(tie),np.float32(2**-126)),
           ("all_positive_127",np.full_like(x,127),np.full_like(tie,127),np.float32(1)),
           ("all_negative_127",np.full_like(x,-127),np.full_like(tie,-127),np.float32(1))]
    records=[]
    os.environ["KV260_B_BACKEND"]="cpu_reference"
    for name,inp,expected,sx in cases:
        d=a.output/name;d.mkdir();raw=d/"raw";raw.mkdir()
        os.environ.update({"KV260_B_RUN_ID":"kernel_edges","KV260_B_IMAGE_ID":name,
                           "KV260_B_TRACE_FILE":str((d/"trace.jsonl").resolve()),"KV260_B_DUMP_DIR":str(raw.resolve())})
        y=sess.run(None,{"X":inp})[0]
        q=np.fromfile(raw/"48_Xq.bin",dtype=np.int8).reshape(1,192)
        scales=np.fromfile(raw/"48_sx.bin",dtype="<f4")
        yi=np.fromfile(raw/"48_Y.bin",dtype="<i4").reshape(1,1000)
        np.testing.assert_array_equal(q,expected);np.testing.assert_array_equal(scales,[sx])
        np.testing.assert_array_equal(yi,expected.astype(np.int64)@w.astype(np.int64))
        assert np.isfinite(y).all()
        records.append({"case":name,"status":"PASS","integer_outputs":1000,"Xq_sha256":sha(raw/"48_Xq.bin"),"Y_sha256":sha(raw/"48_Y.bin")})
    for name,inp in (("nan",np.full_like(x,np.nan)),("positive_inf",np.full_like(x,np.inf)),
                     ("negative_inf",np.full_like(x,-np.inf)),("wrong_shape",x[:,:191]),("wrong_dtype",x.astype(np.float64))):
        os.environ["KV260_B_TRACE_FILE"]=str((a.output/(name+".must_not_exist.jsonl")).resolve());os.environ["KV260_B_DUMP_DIR"]=""
        try:sess.run(None,{"X":inp})
        except Exception as e:
            (a.output/(name+".log")).write_text(str(e),encoding="utf-8")
            records.append({"case":name,"status":"PASS_REJECTED","error":type(e).__name__})
        else:raise ValueError("Custom CPU failed to reject "+name)
    assert not list(a.output.glob("*.must_not_exist.jsonl"))
    write_json(a.output/"numeric_edges.json",{"status":"PASS_CPU_NUMERIC_PROBES_ONLY","host_utc":utc(),"graph_sha256":sha(a.graph),
               "plugin_sha256":sha(plugin),"cases":records,"integer_outputs":5000,"PL":"NOT_RUN"})
    print("actual custom CPU: five numeric and five rejection probes PASS",flush=True)

if __name__=="__main__":main()
