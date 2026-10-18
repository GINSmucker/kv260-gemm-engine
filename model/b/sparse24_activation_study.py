"""Bounded software-only activation-aware sparse model evaluation."""
import argparse
import hashlib
import json
import re
import shutil
import tarfile
import time
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper as NH

from .common import ROOT, read_json, write_json, sha, utc
from .preprocess import preprocess
from .quantization import quantize, postprocess
from .sparse24 import prune, logical_pack, logical_unpack, slot_gemm
from .golden_int64 import gemm
from .sparse24_activation import prune_activation, gram_sum
from .sparse24_study import BUNDLE, DATA, graph, session, evaluate

OLD = ROOT / "results/B/B8/sparse24_20261012_01"
LAYERS = read_json(BUNDLE / "layers.json")
INDICES = [r["layer_index"] for r in LAYERS if r["family"] in ("qkv", "fc1", "fc2")]


def order(row, seed):
    return hashlib.sha256((seed + row["image_sha256"]).encode()).hexdigest(), row["path"]


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    inventory = read_json(OLD / "dataset_inventory.json")
    splits = read_json(BUNDLE / "splits.json")["splits"]
    selected = read_json(OLD / "selection.json")
    known = {r["path"]: r for rows in inventory.values() for r in rows}
    pattern = re.compile(r'train[/\\]+n[0-9]{8}[/\\]+[^\s"\\/<>]+\.(?:JPEG|jpg|png)', re.I)
    references = {}

    def scan(name, content):
        paths = sorted({p.replace("\\\\", "/").replace("\\", "/") for p in pattern.findall(content)})
        paths = [p for p in paths if p in known]
        if paths:
            references[name] = sorted(set(references.get(name, [])) | set(paths))

    skip = {"dataset_inventory.json", "evidence_manifest.json", "selection.json", "splits.json"}
    for base in (ROOT / "results/B", ROOT / "results/model_planning"):
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".jsonl", ".csv", ".txt", ".md") and path.name not in skip:
                scan(path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8-sig", errors="replace"))
    archive = ROOT.parent / (ROOT.name + "_归档") / "cleanup_20261001_223045"
    for name in ("b_history.tar.gz", "b_deployment.tar.gz"):
        print("AUDIT_ARCHIVE", name, flush=True)
        with tarfile.open(archive / name, "r|gz") as package:
            for member in package:
                p = Path(member.name)
                if member.isfile() and p.suffix in (".json", ".jsonl", ".csv", ".txt", ".md") and p.name not in skip:
                    with package.extractfile(member) as stream:
                        # JSONL references can be large; lines avoid materializing predictions.
                        for line in stream:
                            scan(name + ":" + member.name, line.decode("utf-8-sig", errors="replace"))
    excluded = {r["image_sha256"] for rows in splits.values() for r in rows}
    excluded.update(r["image_sha256"] for r in selected)
    extra = {known[p]["image_sha256"] for paths in references.values() for p in paths} - excluded
    excluded.update(extra)
    unused = [r for r in inventory["train"] if r["image_sha256"] not in excluded]
    quality = []
    classes = sorted({r["wnid"] for r in unused})
    for cls in classes:
        rows = sorted((r for r in unused if r["wnid"] == cls), key=lambda r: order(r, "sparse24_activation_quality_v1"))
        if len(rows) < 200:
            raise ValueError("Insufficient untouched class: " + cls)
        quality.extend(rows[:200])
    qsha = {r["image_sha256"] for r in quality}
    train = [r for r in unused if r["image_sha256"] not in qsha]
    fit, local = [], []
    for cls in sorted({r["wnid"] for r in splits["calibration"]}):
        rows = sorted((r for r in splits["calibration"] if r["wnid"] == cls), key=lambda r: order(r, "sparse24_activation_fit_v1"))
        fit.extend(rows[:len(rows)//2])
        local.extend(rows[len(rows)//2:])
    while len(fit) < 128:
        fit.append(local.pop(0))
    assert len(fit) == len(local) == 128
    roles = {"fit": fit, "local": local, "quality": quality, "recovery_train": train, "selection": selected, "regression": splits["regression"], "validation": splits["validation_full"]}
    seen = set()
    for name, rows in roles.items():
        hashes = {r["image_sha256"] for r in rows}
        assert len(hashes) == len(rows) and not seen & hashes
        seen.update(hashes)
        write_json(out / (name + ".json"), rows)
    # Reuse the exact cached selection tensors and immutable baseline evidence.
    assert sha(out / "selection.json") == sha(OLD / "selection.json")
    originals = read_json(OLD / "sensitivity.json")["candidates"]
    pilots = []
    for family in ("qkv", "fc1", "fc2"):
        rows = [r for r in originals if len(r["indices"]) == 1 and LAYERS[r["indices"][0]]["family"] == family]
        rows.sort(key=lambda r: (-r["results"]["w8a8"]["top1_correct"], r["results"]["w8a8"]["logits_mse"], r["indices"][0]))
        pilots.extend([rows[0]["indices"][0], rows[6]["indices"][0]])
    write_json(out / "data_audit.json", {"utc": utc(), "status": "PASS", "scope": ["results/B", "results/model_planning", "b_history.tar.gz", "b_deployment.tar.gz"], "C_private": "NOT_ACCESSED", "references": references, "additional_used_content": sorted(extra), "counts": {k:len(v) for k,v in roles.items()}, "roles_content_disjoint": True, "quality_predictions": "NOT_RUN", "inventory_sha256": sha(OLD / "dataset_inventory.json")})
    rules = ROOT / "docs/model/KV260_2比4稀疏W8A8开发与验收计划.md"
    shutil.copyfile(rules, out / "approved_protocol.md")
    sources = {}
    for path in sorted((ROOT / "model/b").glob("*.py")):
        target = out / "source" / path.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, target)
        sources[path.name] = sha(path)
    write_json(out / "protocol.json", {"utc": utc(), "status": "FROZEN_BEFORE_CANDIDATE_PREDICTIONS", "rules_sha256": sha(out / "approved_protocol.md"), "sources": sources, "pilots": pilots, "max_untrained":22, "max_trained":3, "selection_max_lost":5, "formal_max_drop_pp":1.0, "formal_candidates_max":1, "prefixes":[2,4,8,12,18,24], "roles":{k:sha(out/(k+".json")) for k in roles}, "selection_inputs_sha256":sha(OLD/"selection_inputs.npy"), "baseline_logits_sha256":sha(OLD/"selection_results/baseline/fp32_logits.npy")})
    print("DATA_FROZEN", {k:len(v) for k,v in roles.items()}, "pilots", pilots, flush=True)


def capture(graph_bytes):
    model = onnx.load_from_string(graph_bytes)
    names = []
    for i in INDICES:
        l = LAYERS[i]
        names.append(l["graph_input"])
        model.graph.output.append(onnx.helper.make_tensor_value_info(names[-1], onnx.TensorProto.FLOAT, l["input_shape"]))
    return session(model.SerializeToString()), names


def checked_input(row):
    p = DATA / row["path"]
    if sha(p) != row["image_sha256"]:
        raise ValueError("Image changed: " + row["path"])
    return preprocess(p)


def collect(out, rows, graph_bytes, label):
    engine, names = capture(graph_bytes)
    plain = session(graph_bytes)
    totals = {i:np.zeros((LAYERS[i]["K"]//4,4,4),np.float64) for i in INDICES}
    counts = {i:0 for i in INDICES}
    for n,row in enumerate(rows):
        x = checked_input(row)
        values = engine.run(None,{"image":x})
        if n < 3:
            np.testing.assert_array_equal(values[0],plain.run(None,{"image":x})[0])
        for i,v in zip(INDICES,values[1:]):
            part,count = gram_sum(v)
            totals[i] += part
            counts[i] += count
        if (n+1)%32 == 0:
            print("STATS",label,n+1,flush=True)
    folder = out / label
    folder.mkdir(exist_ok=True)
    stats = {}
    for i in INDICES:
        stats[i] = totals[i]/counts[i]
        np.save(folder / f"gram_{i:02d}.npy",stats[i])
    write_json(folder / "summary.json", {"utc":utc(),"images":len(rows),"tokens":counts,"graph_sha256":hashlib.sha256(graph_bytes).hexdigest(),"logits_exact_probes":3,"source":"FP32 actual Linear inputs; uncentered float64"})
    return stats


def weights(out, stats, method):
    initializers = {t.name:NH.to_array(t) for t in onnx.load(BUNDLE/"graphs/model_fp32.onnx").graph.initializer}
    records = []
    rng = np.random.default_rng(20261012)
    for i in INDICES:
        w = np.ascontiguousarray(initializers[LAYERS[i]["layer_id"]+".weight"].T)
        p,pos,mask = prune(w) if method == "magnitude" else prune_activation(w,stats[i],method)
        p2,pos2,m2 = prune(w) if method == "magnitude" else prune_activation(w,stats[i],method)
        assert np.array_equal(pos,pos2) and np.array_equal(p,p2)
        q,s = quantize(p,0)
        v = logical_pack(q,pos)
        np.testing.assert_array_equal(logical_unpack(v,pos),q)
        x = rng.integers(-127,128,(LAYERS[i]["M"],LAYERS[i]["K"]),dtype=np.int8)
        reference = gemm(x,q)
        np.testing.assert_array_equal(slot_gemm(x,v,pos),reference)
        folder = out/"weights"/f"{i:02d}"
        folder.mkdir(parents=True)
        for name,value in (("pruned_fp32",p),("positions",pos),("mask",mask),("Wq",q),("sw",s),("values",v),("integer_input",x),("integer_reference",reference)):
            np.save(folder/(name+".npy"),value)
        records.append({"index":i,"files":{f.name:sha(f) for f in folder.iterdir()},"outputs":int(reference.size),"differences":0,"zero_slots":int(np.sum(v==0))})
    write_json(out/"weight_verification.json",{"status":"PASS","method":method,"layers":records})


def local_errors(out):
    base = (BUNDLE/"graphs/model_fp32.onnx").read_bytes()
    engine,_ = capture(base)
    init = {t.name:NH.to_array(t) for t in onnx.load_from_string(base).graph.initializer}
    originals = {i:np.ascontiguousarray(init[LAYERS[i]["layer_id"]+".weight"].T) for i in INDICES}
    parameters = {}
    for i,w in originals.items():
        q,s = quantize(w,0)
        bias = init[LAYERS[i]["layer_id"]+".bias"]
        parameters[i] = (w,q,s,bias)
    candidates = {m:{i:(np.load(out/m/"weights"/f"{i:02d}"/"pruned_fp32.npy"),np.load(out/m/"weights"/f"{i:02d}"/"Wq.npy"),np.load(out/m/"weights"/f"{i:02d}"/"sw.npy")) for i in INDICES} for m in ("wanda","gram")}
    sums = {m:{i:np.zeros(4,np.float64) for i in INDICES} for m in candidates}
    for n,row in enumerate(read_json(out/"local.json")):
        activations = engine.run(None,{"image":checked_input(row)})[1:]
        for i,x in zip(INDICES,activations):
            x = x.reshape(-1,LAYERS[i]["K"])
            w,q,s,bias = parameters[i]
            xq,sx = quantize(x,1)
            # Integer products are exactly representable below 2^24 at these shapes.
            xd = xq.astype(np.float32)
            yf = x@w+bias
            yq = postprocess((xd@q.astype(np.float32)).astype(np.int32),sx,s,bias)
            for m in candidates:
                p,pq,ps = candidates[m][i]
                pf = x@p+bias
                py = postprocess((xd@pq.astype(np.float32)).astype(np.int32),sx,ps,bias)
                sums[m][i] += [np.sum((pf.astype(float)-yf)**2),np.sum(yf.astype(float)**2),np.sum((py.astype(float)-yq)**2),np.sum(yq.astype(float)**2)]
        if (n+1)%16 == 0:
            print("LOCAL",n+1,flush=True)
    result = {m:[{"index":i,"fp32_error":float(v[0]/max(v[1],1e-12)),"w8a8_error":float(v[2]/max(v[3],1e-12)),"sums":v.tolist()} for i,v in sums[m].items()] for m in sums}
    write_json(out/"local_errors.json",{"methods":result,"input":"same original FP32 layer inputs; W8 local quantization, not propagated W8 quality","samples":128})


def study(out):
    protocol = read_json(out/"protocol.json")
    for name,digest in protocol["sources"].items():
        if sha(ROOT/"model/b"/name) != digest:
            raise ValueError("Source changed after freeze: "+name)
    stats = collect(out,read_json(out/"fit.json"),(BUNDLE/"graphs/model_fp32.onnx").read_bytes(),"original_stats")
    for method in ("wanda","gram"):
        weights(out/method,stats,method)
        shutil.copyfile(out/"selection.json",out/method/"selection.json")
    local_errors(out)
    tensors = np.load(OLD/"selection_inputs.npy",mmap_mode="r")
    labels = np.array([r["label"] for r in read_json(out/"selection.json")])
    baseline = np.load(OLD/"selection_results/baseline/fp32_logits.npy")
    base_correct = int(np.sum(np.argmax(baseline,axis=1)==labels))
    assert base_correct == 764
    summaries = []

    def candidate(root,name,indices):
        indices = sorted(indices)
        result = {m:evaluate(root,name,indices,m,tensors,labels,baseline)[0] for m in ("fp32","w8a8")}
        row = {"name":name,"root":root.relative_to(out).as_posix(),"indices":indices,"results":result,"eligible":all(base_correct-r["top1_correct"] <= 5 for r in result.values()),"max_lost":max(base_correct-r["top1_correct"] for r in result.values()),"removed_MAC":sum(LAYERS[i]["M"]*LAYERS[i]["K"]*LAYERS[i]["N"]//2 for i in indices)}
        summaries.append(row)
        assert len(summaries) <= 22
        write_json(out/"sensitivity.json",{"baseline_correct":base_correct,"candidates":summaries,"quality":"NOT_RUN"})
        return row

    pilots = {}
    for method in ("wanda","gram"):
        pilots[method] = [candidate(out/method,f"{method}_single_{i:02d}",[i]) for i in protocol["pilots"]]
    def method_key(m):
        return (-sum(r["eligible"] for r in pilots[m]),-sum(r["results"]["w8a8"]["top1_correct"] for r in pilots[m]),sum(r["results"]["w8a8"]["logits_mse"] for r in pilots[m]),m!="wanda")
    method = min(pilots,key=method_key)
    errors = read_json(out/"local_errors.json")["methods"][method]
    ranked = sorted(errors,key=lambda r:(r["fp32_error"]/(LAYERS[r["index"]]["M"]*LAYERS[r["index"]]["K"]*LAYERS[r["index"]]["N"]//2),r["index"]))
    ranking = [r["index"] for r in ranked]
    write_json(out/"method_selection.json",{"method":method,"keys":{m:method_key(m) for m in pilots},"layer_ranking":ranking})
    combos = [candidate(out/method,f"{method}_prefix_{n:02d}",ranking[:n]) for n in protocol["prefixes"]]
    def quality_key(r):
        return (r["max_lost"],-r["removed_MAC"],r["results"]["w8a8"]["logits_mse"],r["name"])
    def eligible_key(r):
        return (-r["removed_MAC"],-r["results"]["w8a8"]["top1_correct"],r["results"]["w8a8"]["logits_mse"],r["name"])
    anchor = min((r for r in combos if len(r["indices"])>=8),key=quality_key)
    ordered = [i for i in ranking if i in anchor["indices"]]
    removed = ordered[:-2]
    swapped = removed+[i for i in ranking if i not in anchor["indices"]][:2]
    combos.append(candidate(out/method,method+"_remove2",removed))
    combos.append(candidate(out/method,method+"_swap2",swapped))
    control = out/"magnitude"
    control.mkdir()
    shutil.copyfile(out/"selection.json",control/"selection.json")
    for i in ranking[:8]:
        shutil.copytree(OLD/"weights"/f"{i:02d}",control/"weights"/f"{i:02d}")
    combos.append(candidate(control,"magnitude_same8",ranking[:8]))
    good = [r for r in combos if r["eligible"]]
    best = min(good,key=eligible_key) if good else min(combos,key=quality_key)
    refreshed = collect(out,read_json(out/"fit.json")+read_json(out/"local.json"),graph(out/best["root"],best["indices"],"fp32"),"refreshed_stats")
    refresh_root = out/"refresh"
    weights(refresh_root,refreshed,"magnitude" if best["root"] == "magnitude" else method)
    shutil.copyfile(out/"selection.json",refresh_root/"selection.json")
    combos.append(candidate(refresh_root,"activation_refresh",best["indices"]))
    good = sorted((r for r in summaries if r["eligible"] and len(r["indices"])>=2),key=eligible_key)
    near = [r for r in combos if len(r["indices"])>=8 and r["max_lost"]<=20]
    training = bool(not any(len(r["indices"])>=8 for r in good) and near)
    write_json(out/"untrained_decision.json",{"utc":utc(),"candidates":len(summaries),"selected_method":method,"eligible_multilayer":[r["name"] for r in good],"arm_selection_max2":[r["name"] for r in good[:2]],"training_cost_probe_required":training,"training_origin":min(near,key=quality_key)["name"] if training else None,"training_reason":"NO_8_LAYER_ELIGIBLE_AND_WITHIN_2PP" if training else ("8_LAYER_TARGET_MET" if any(len(r["indices"])>=8 for r in good) else "NO_8_LAYER_COMBINATION_WITHIN_2PP"),"quality":"NOT_RUN","hardware":"NOT_RUN"})
    print("UNTRAINED_COMPLETE",len(summaries),"training",training,"eligible",[r["name"] for r in good],flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action",choices=("prepare","study"))
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    {"prepare":prepare,"study":study}[args.action](args.output.resolve())


if __name__ == "__main__":
    main()
