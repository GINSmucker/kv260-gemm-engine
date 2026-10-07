"""Offline torch model; imports are isolated from ARM production modules."""
from pathlib import Path
import hashlib
import numpy as np
import torch
import timm
from safetensors.torch import load_file
from .common import ROOT, sha
from .quantization import quantize

WEIGHT_SHA = "21d4764d94f6c3ffdb6da3581115a0a1ee2d505537d96883b540e54766407c9e"

def original():
    torch.set_num_threads(4)
    torch.set_float32_matmul_precision("highest")
    path = ROOT / "build/model_assets/deit_tiny/model.safetensors"
    if sha(path) != WEIGHT_SHA:
        raise ValueError("Pinned model weight mismatch")
    model = timm.create_model("deit_tiny_patch16_224.fb_in1k", pretrained=False).eval()
    result = model.load_state_dict(load_file(str(path)), strict=True)
    assert not result.missing_keys and not result.unexpected_keys
    assert len(model.blocks) == 12 and model.embed_dim == 192 and model.head.out_features == 1000
    assert all(b.attn.num_heads == 3 for b in model.blocks)
    return model

def layer_table(model, tensor):
    layers, hooks = [], []
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear):
            def record(m, inputs, y, name=name):
                x = inputs[0]
                family = "head" if name == "head" else name.rsplit(".", 1)[1]
                layers.append({"layer_id": name, "layer_index": len(layers), "family": family,
                               "M": x.numel() // x.shape[-1], "K": m.in_features, "N": m.out_features,
                               "input_shape": list(x.shape), "output_shape": list(y.shape),
                               "pytorch_weight_shape": list(m.weight.shape), "weight_shape": [m.in_features, m.out_features],
                               "original_weight_sha256": hashlib.sha256(m.weight.detach().numpy().tobytes()).hexdigest(),
                               "weight_axis": 0, "activation_axis": -1})
            hooks.append(module.register_forward_hook(record))
    with torch.inference_mode():
        model(torch.from_numpy(tensor))
    for h in hooks:
        h.remove()
    assert len(layers) == 49 and sum(r["M"] * r["K"] * r["N"] for r in layers) == 1045949952
    return layers

class QuantLinear(torch.nn.Module):
    def __init__(self, source, layer, mode="row"):
        super().__init__()
        self.layer, self.mode, self.capture = layer, mode, None
        w, s = quantize(np.ascontiguousarray(source.weight.detach().numpy().T), 0)
        self.register_buffer("wq", torch.from_numpy(w.copy()))
        self.register_buffer("sw", torch.from_numpy(s.copy()))
        self.register_buffer("bias", source.bias.detach().clone() if source.bias is not None else torch.zeros(source.out_features))
        self.stats = {"calls": 0, "zero_rows": 0, "scale_min": float("inf"), "scale_max": 0.0, "at_limit": 0, "elements": 0}

    def forward(self, x):
        shape = x.shape
        flat = x.reshape(-1, self.wq.shape[0])
        if not bool(torch.isfinite(flat).all()):
            raise ValueError("Nonfinite activation")
        a = flat.abs().amax(dim=1, keepdim=True) if self.mode == "row" else flat.abs().amax().reshape(1, 1)
        sx = torch.where(a == 0, torch.ones_like(a), torch.clamp(a / 127.0, min=torch.finfo(torch.float32).tiny))
        xq = torch.clamp(torch.round(flat / sx), -127, 127)
        yi = xq @ self.wq.float()
        out = ((yi * sx) * self.sw) + self.bias
        st = self.stats
        st["calls"] += 1
        st["zero_rows"] += int((a == 0).sum())
        st["scale_min"] = min(st["scale_min"], float(sx.min()))
        st["scale_max"] = max(st["scale_max"], float(sx.max()))
        st["at_limit"] += int((xq.abs() == 127).sum())
        st["elements"] += xq.numel()
        if self.capture is not None:
            self.capture(self.layer, flat.numpy().copy(), xq.to(torch.int8).numpy().copy(), sx.numpy().copy(), yi.to(torch.int32).numpy().copy(), out.numpy().copy())
        return out.reshape(*shape[:-1], self.wq.shape[1])

def quant_model(layers, mode="row"):
    model = original()
    for layer in layers:
        name = layer["layer_id"]
        parent, leaf = name.rsplit(".", 1) if "." in name else ("", name)
        target = model.get_submodule(parent) if parent else model
        setattr(target, leaf, QuantLinear(model.get_submodule(name), layer, mode))
    return model.eval()
