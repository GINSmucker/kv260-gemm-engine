"""Full INT64 mathematical Golden. No tile, exporter or quantizer reuse."""
import numpy as np

def gemm(x, w):
    if x.dtype != np.int8 or w.dtype != np.int8 or x.ndim != 2 or w.ndim != 2:
        raise ValueError("Golden requires INT8 two-dimensional matrices")
    if not x.flags.c_contiguous or not w.flags.c_contiguous or x.shape[1] != w.shape[0]:
        raise ValueError("Golden layout/shape mismatch")
    if x.shape[1] * 16384 > np.iinfo(np.int32).max:
        raise ValueError("INT32 prefix bound unsafe")
    wide = x.astype(np.int64) @ w.astype(np.int64)
    if np.any(wide > np.iinfo(np.int32).max) or np.any(wide < np.iinfo(np.int32).min):
        raise ValueError("Golden overflow")
    return wide.astype("<i4")
