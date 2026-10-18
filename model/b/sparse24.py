"""Deterministic K-axis 2:4 pruning and logical two-slot representation.

This module defines software tensors, not the A/B/C physical wire format.
"""
import numpy as np


def prune(weight):
    if weight.dtype != np.float32 or weight.ndim != 2 or weight.size == 0:
        raise ValueError("Expected nonempty FP32 W[K,N]")
    if weight.shape[0] % 4 or not np.isfinite(weight).all():
        raise ValueError("K must be divisible by four; weights must be finite")
    groups = weight.reshape(-1, 4, weight.shape[1]).transpose(0, 2, 1)
    positions = np.sort(np.argsort(-np.abs(groups), axis=2, kind="stable")[:, :, :2], axis=2).astype(np.uint8)
    mask = np.zeros(groups.shape, dtype=bool)
    np.put_along_axis(mask, positions, True, axis=2)
    dense = np.where(mask, groups, np.float32(0)).transpose(0, 2, 1).reshape(weight.shape)
    return np.ascontiguousarray(dense), positions, np.ascontiguousarray(mask.transpose(0, 2, 1).reshape(weight.shape))


def logical_pack(weight, positions):
    if weight.dtype != np.int8 or weight.ndim != 2 or weight.shape[0] % 4:
        raise ValueError("Expected INT8 W[K,N] with complete groups")
    validate_positions(positions, weight.shape[0] // 4, weight.shape[1])
    groups = weight.reshape(-1, 4, weight.shape[1]).transpose(0, 2, 1)
    values = np.take_along_axis(groups, positions, axis=2)
    if not np.array_equal(logical_unpack(values, positions), weight):
        raise ValueError("Unselected weight is nonzero")
    return np.ascontiguousarray(values)


def validate_positions(positions, groups, columns):
    if positions.dtype != np.uint8 or positions.shape != (groups, columns, 2):
        raise ValueError("Invalid position tensor")
    if np.any(positions >= 4) or np.any(positions[:, :, 0] >= positions[:, :, 1]):
        raise ValueError("Positions must be distinct, ascending, and in [0,3]")


def logical_unpack(values, positions):
    if values.dtype != np.int8 or values.ndim != 3 or values.shape[2] != 2:
        raise ValueError("Invalid two-slot values")
    g, n, _ = values.shape
    validate_positions(positions, g, n)
    result = np.zeros((g * 4, n), dtype=np.int8)
    columns = np.arange(n)[None, :]
    group_start = 4 * np.arange(g)[:, None]
    for slot in range(2):
        result[group_start + positions[:, :, slot], columns] = values[:, :, slot]
    return result


def slot_gemm(x, values, positions):
    """Software two-slot execution; checked against separate full INT64 GEMM."""
    if x.dtype != np.int8 or x.ndim != 2 or values.dtype != np.int8 or values.ndim != 3:
        raise ValueError("Expected INT8 input and values")
    g, n, slots = values.shape
    if slots != 2 or x.shape[1] != 4 * g:
        raise ValueError("Shape mismatch")
    validate_positions(positions, g, n)
    result = np.zeros((x.shape[0], n), dtype=np.int64)
    for group in range(g):
        for slot in range(2):
            result += x[:, 4 * group + positions[group, :, slot]].astype(np.int64) * values[group, :, slot].astype(np.int64)
    if np.any(result > np.iinfo(np.int32).max) or np.any(result < np.iinfo(np.int32).min):
        raise ValueError("INT32 overflow")
    return result.astype(np.int32)
