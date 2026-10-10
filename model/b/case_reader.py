"""Strict B case loader, independently usable by the calculation workbench."""
from pathlib import Path
import numpy as np
from .common import read_json, sha, safe_path

SHAPES = {"head":(1,192,1000),"proj":(197,192,192),"qkv":(197,192,576),"fc1":(197,192,768),"fc2":(197,768,192)}

def validate_meta(case,layers):
    if case["schema"]!="b_model_case_v1" or case["layout"]!="compact_row_major" or case["mode"]!="dense_w8a8":
        raise ValueError("Unsupported B case schema/layout/mode")
    if tuple(case[k] for k in ("M","K","N"))!=SHAPES.get(case["family"]):
        raise ValueError("Case outside shape whitelist")
    names=[r["layer_id"] for r in layers]
    if len(names)!=49 or len(set(names))!=49:
        raise ValueError("Duplicate or missing layer identity")
    index=case["layer_index"]
    if not isinstance(index,int) or not 0<=index<49 or layers[index]["layer_id"]!=case["layer_id"]:
        raise ValueError("Wrong layer binding")
    layer=layers[index]
    if any(case[k]!=layer[k] for k in ("M","K","N","family")):
        raise ValueError("Wrong layer shape")
    if case["kind"]!="hand" and any(case[f]!=layer[f] for f in ("Wq","sw","bias")):
        raise ValueError("Wrong frozen model weight binding")
    shapes={"Xq":([case["M"],case["K"]],"|i1"),"Wq":([case["K"],case["N"]],"|i1"),
            "sx":([case["M"],1],"<f4"),"sw":([case["N"]],"<f4"),"bias":([case["N"]],"<f4"),
            "Y_golden":([case["M"],case["N"]],"<i4"),"postprocess_expected":([case["M"],case["N"]],"<f4")}
    for f,(shape,dtype) in shapes.items():
        value=case[f]
        if value["shape"]!=shape or value["dtype"]!=dtype or value["bytes"]!=int(np.prod(shape))*np.dtype(dtype).itemsize:
            raise ValueError("Case dtype/shape/bytes mismatch")

def load_case(bundle,case_id):
    bundle=Path(bundle);manifest=read_json(bundle/"layer_cases/manifest.json")
    cases=manifest["cases"];ids=[c["case_id"] for c in cases]
    if len(ids)!=len(set(ids)) or case_id not in ids:raise ValueError("Duplicate/missing case id")
    case=cases[ids.index(case_id)];layers=read_json(bundle/"layers.json");validate_meta(case,layers)
    arrays={}
    for field in ("Xq","Wq","sx","sw","bias","Y_golden","postprocess_expected"):
        value=case[field];p=safe_path(bundle,value["file"])
        if sha(p)!=value["sha256"] or p.stat().st_size!=value["bytes"]:raise ValueError("Case length/hash mismatch")
        arrays[field]=np.fromfile(p,dtype=value["dtype"]).reshape(value["shape"])
    if not all(np.isfinite(arrays[f]).all() for f in ("sx","sw","bias","postprocess_expected")):
        raise ValueError("Nonfinite float payload")
    if np.any(arrays["sx"]<=0) or np.any(arrays["sw"]<=0):raise ValueError("Nonpositive scale")
    if np.any(arrays["Xq"]==-128) or np.any(arrays["Wq"]==-128):raise ValueError("B protocol range violation")
    return case,arrays
