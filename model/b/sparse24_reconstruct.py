"""Fixed-mask ridge reconstruction of retained weights, offline only."""
import numpy as np


def reconstruct(weight, mask, gram):
    if weight.dtype != np.float32 or mask.dtype != bool or weight.shape != mask.shape:
        raise ValueError("Expected matching FP32 weight and boolean mask")
    k,n = weight.shape
    if gram.shape != (k,k) or not np.isfinite(gram).all() or not np.isfinite(weight).all():
        raise ValueError("Invalid Gram matrix or weight")
    if k % 4 or not np.all(mask.reshape(-1,4,n).sum(axis=1)==2):
        raise ValueError("Mask must retain two positions per K quartet")
    damping = 1e-4 * max(float(np.trace(gram))/k,1e-12)
    rhs = gram @ weight.astype(np.float64)
    result = np.zeros(weight.shape,np.float32)
    residual = 0.0
    for col in range(n):
        keep = np.flatnonzero(mask[:,col])
        system = gram[np.ix_(keep,keep)].copy()
        system.flat[::len(keep)+1] += damping
        target = rhs[keep,col]+damping*weight[keep,col].astype(np.float64)
        value = np.linalg.solve(system,target)
        residual = max(residual,float(np.linalg.norm(system@value-target)/max(np.linalg.norm(target),1e-12)))
        result[keep,col] = value.astype(np.float32)
    if not np.isfinite(result).all() or residual > 1e-8:
        raise ValueError("Reconstruction solve failed numerical verification")
    return result,{"lambda":damping,"max_relative_residual":residual,"max_abs_weight":float(np.max(np.abs(result)))}
