"""Frozen FP32 W8/A8 semantics, independent of the hardware implementation."""
import numpy as np

PROTOCOL = {
    "schema": "b_quant_protocol_v1", "id": "B_W8A8_ROW_DYNAMIC_V1", "mode": "dense_w8a8",
    "weight_scale": "per_output_channel_absmax", "activation_scale": "dynamic_per_row_absmax",
    "zero_point": 0, "qmin": -127, "qmax": 127, "rounding": "nearest_even",
    "scale_dtype": "float32", "scale_min": float(np.finfo(np.float32).tiny), "zero_scale": 1.0,
    "layout": "compact_row_major_X_MK_W_KN_Y_MN", "byte_order": "little",
    "postprocess": "FP32(FP32(FP32(Yi32)*sx)*sw)+FP32(bias); separate rounded operations",
    "max_K": 768, "integer_prefix_bound": 768 * 127 * 127,
    "quality_top1_max_drop_pp": 1.0, "logits_atol": 1e-4, "logits_rtol": 1e-4,
    "FPGA_bias_scale": False,
}

def quantize(v, axis):
    v = np.asarray(v)
    if v.dtype != np.float32 or not np.isfinite(v).all() or v.size == 0:
        raise ValueError("Quantization requires nonempty finite FP32")
    a = np.max(np.abs(v), axis=axis, keepdims=True)
    s = np.where(a == 0, np.float32(1), np.maximum(a / np.float32(127), np.float32(np.finfo(np.float32).tiny)))
    q = np.clip(np.rint(v / s), -127, 127).astype(np.int8)
    return np.ascontiguousarray(q), np.ascontiguousarray(s, dtype=np.float32)

def postprocess(y, sx, sw, bias):
    if y.dtype != np.dtype("int32") or not all(np.isfinite(a).all() for a in (sx, sw, bias)):
        raise ValueError("Invalid postprocessing payload")
    if np.any(sx <= 0) or np.any(sw <= 0):
        raise ValueError("Scale must be positive")
    return np.add(np.multiply(np.multiply(y.astype(np.float32), sx, dtype=np.float32), sw, dtype=np.float32), bias, dtype=np.float32)
