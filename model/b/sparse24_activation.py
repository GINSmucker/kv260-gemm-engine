"""Offline activation statistics and deterministic K-axis two-slot masks."""
from itertools import combinations

import numpy as np

PAIRS = np.array(list(combinations(range(4), 2)), dtype=np.uint8)


def prune_activation(weight, gram, method):
    if weight.dtype != np.float32 or weight.ndim != 2 or weight.shape[0] % 4:
        raise ValueError("Expected FP32 W[K,N], K divisible by four")
    g = weight.shape[0] // 4
    if gram.shape != (g, 4, 4) or not np.isfinite(gram).all() or not np.isfinite(weight).all():
        raise ValueError("Invalid statistics or weights")
    w = weight.reshape(g, 4, -1).transpose(0, 2, 1).astype(np.float64)
    if method == "wanda":
        score = np.abs(w) * np.sqrt(np.maximum(np.diagonal(gram, axis1=1, axis2=2), 0))[:, None, :]
        pos = np.sort(np.argsort(-score, axis=2, kind="stable")[:, :, :2], axis=2).astype(np.uint8)
    elif method == "gram":
        errors = []
        for pair in PAIRS:
            d = w.copy()
            d[:, :, pair] = 0
            errors.append(np.einsum("gni,gij,gnj->gn", d, gram, d, optimize=False))
        pos = PAIRS[np.argmin(np.stack(errors, axis=-1), axis=-1)]
    else:
        raise ValueError("Unknown scoring method")
    mask = np.zeros(w.shape, dtype=bool)
    np.put_along_axis(mask, pos, True, axis=2)
    mask = np.ascontiguousarray(mask.transpose(0, 2, 1).reshape(weight.shape))
    return np.where(mask, weight, np.float32(0)), pos, mask


def gram_sum(x):
    x = x.reshape(-1, x.shape[-1]).astype(np.float64).reshape(-1, x.shape[-1] // 4, 4)
    return np.einsum("tgi,tgj->gij", x, x, optimize=False), x.shape[0]
