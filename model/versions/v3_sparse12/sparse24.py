"""K-axis 2:4 logical tensors; deliberately NOT a hardware wire format.

Rule follows model/b/sparse24.py and model/b/quantization.py at repository
commit 030f011b46afdec3c6493356e5b5db39ca09a5a5. W is always [K,N].
"""
import numpy as np


def require(ok, message):
    if not ok:
        raise ValueError(message)


def prune(weight):
    require(weight.dtype == np.float32 and weight.ndim == 2 and weight.size > 0,
            "Expected nonempty FP32 W[K,N]")
    require(weight.shape[0] % 4 == 0 and np.isfinite(weight).all(),
            "K must be divisible by four; weights must be finite")
    groups = weight.reshape(-1, 4, weight.shape[1]).transpose(0, 2, 1)
    positions = np.sort(
        np.argsort(-np.abs(groups), axis=2, kind="stable")[:, :, :2], axis=2
    ).astype(np.uint8)
    group_mask = np.zeros(groups.shape, dtype=bool)
    np.put_along_axis(group_mask, positions, True, axis=2)
    mask = np.ascontiguousarray(group_mask.transpose(0, 2, 1).reshape(weight.shape))
    return np.ascontiguousarray(np.where(mask, weight, np.float32(0))), positions, mask


def quantize_weight(weight):
    require(weight.dtype == np.float32 and weight.ndim == 2 and weight.size > 0
            and np.isfinite(weight).all(), "Expected nonempty finite FP32 W[K,N]")
    maxima = np.max(np.abs(weight), axis=0, keepdims=True)
    scale = np.where(
        maxima == 0, np.float32(1),
        np.maximum(maxima / np.float32(127), np.float32(np.finfo(np.float32).tiny)),
    ).astype(np.float32)
    values = np.clip(np.rint(weight / scale), -127, 127).astype(np.int8)
    return np.ascontiguousarray(values), np.ascontiguousarray(scale)


def validate_positions(positions, groups, columns):
    require(positions.dtype == np.uint8 and positions.shape == (groups, columns, 2),
            "Expected UINT8 positions[K/4,N,2]")
    require(np.all(positions < 4) and np.all(positions[:, :, 0] < positions[:, :, 1]),
            "Positions must be distinct, ascending, and in [0,3]")


def unpack(values, positions):
    require(values.dtype == np.int8 and values.ndim == 3 and values.shape[2] == 2
            and values.shape[0] > 0 and values.shape[1] > 0, "Invalid two-slot values")
    g, n, _ = values.shape
    validate_positions(positions, g, n)
    output = np.zeros((g * 4, n), dtype=np.int8)
    columns = np.arange(n)[None, :]
    base = 4 * np.arange(g)[:, None]
    for slot in range(2):
        output[base + positions[:, :, slot], columns] = values[:, :, slot]
    return output


def pack(weight, positions):
    require(weight.dtype == np.int8 and weight.ndim == 2 and weight.size > 0
            and weight.shape[0] % 4 == 0, "Expected INT8 W[K,N] with complete groups")
    validate_positions(positions, weight.shape[0] // 4, weight.shape[1])
    groups = weight.reshape(-1, 4, weight.shape[1]).transpose(0, 2, 1)
    values = np.ascontiguousarray(np.take_along_axis(groups, positions, axis=2))
    require(np.array_equal(unpack(values, positions), weight), "Unselected weight is nonzero")
    return values


def checked_int32(output):
    limits = np.iinfo(np.int32)
    require(np.all(output >= limits.min) and np.all(output <= limits.max), "INT32 overflow")
    return output.astype(np.int32)


def dense_golden(x, weight):
    """Independent full-K multiplication; never uses masks or slot positions."""
    require(x.dtype == weight.dtype == np.int8 and x.ndim == weight.ndim == 2
            and x.shape[1] == weight.shape[0], "Expected matching INT8 matrices")
    return checked_int32(x.astype(np.int64) @ weight.astype(np.int64))


def slot_gemm(x, values, positions):
    require(x.dtype == np.int8 and x.ndim == 2 and values.dtype == np.int8
            and values.ndim == 3 and values.shape[2] == 2, "Expected INT8 input and slots")
    g, n, _ = values.shape
    require(g > 0 and n > 0 and x.shape[1] == 4 * g, "Shape mismatch")
    validate_positions(positions, g, n)
    result = np.zeros((x.shape[0], n), dtype=np.int64)
    for group in range(g):
        for slot in range(2):
            result += (
                x[:, 4 * group + positions[group, :, slot]].astype(np.int64)
                * values[group, :, slot].astype(np.int64)
            )
    return checked_int32(result)
