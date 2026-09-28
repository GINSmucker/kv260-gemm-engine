"""Minimal four-method GEMM backend contract and an independent CPU implementation."""

from dataclasses import dataclass
from time import perf_counter_ns
from uuid import uuid4

import numpy as np


class InputError(ValueError):
    pass


class CapabilityError(ValueError):
    pass


class HandleStateError(RuntimeError):
    pass


class RuntimeNotReadyError(RuntimeError):
    pass


class ResultMismatchError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    M: int
    K: int
    N: int
    mode: str = "dense_w8a8"
    layout_id: str = "row_major_k_n_v1"


@dataclass
class CpuHandle:
    spec: TaskSpec
    W: np.ndarray | None
    prepare_ms: float
    released: bool = False


@dataclass(frozen=True)
class RunResult:
    Y: np.ndarray
    timings: dict
    run_id: str
    backend: str
    build_id: str


def _validate_spec(spec: TaskSpec) -> None:
    if not isinstance(spec, TaskSpec):
        raise InputError("task_spec must be a TaskSpec")
    for name in ("M", "K", "N"):
        value = getattr(spec, name)
        if type(value) is not int or value <= 0:
            raise InputError(f"{name} must be a positive integer")
        if value > 64:
            raise CapabilityError(f"{name}={value} exceeds this demo backend's limit of 64")
    if spec.K * 16384 > 2**31 - 1:
        raise CapabilityError("INT32 partial-sum safety bound exceeded")
    if spec.mode != "dense_w8a8" or spec.layout_id != "row_major_k_n_v1":
        raise CapabilityError("unsupported mode or layout")


def _validate_array(name: str, value: np.ndarray, shape: tuple[int, int]) -> None:
    if not isinstance(value, np.ndarray):
        raise InputError(f"{name} must be a NumPy array")
    if value.shape != shape or value.dtype != np.dtype("int8"):
        raise InputError(f"{name} must have shape {shape} and signed INT8 dtype")
    if not value.flags.c_contiguous:
        raise InputError(f"{name} must be C-contiguous; implicit copying is disabled")


class CpuReferenceBackend:
    """Software integration aid; never advertises PL execution."""

    def capabilities(self) -> dict:
        return {
            "backend": "cpu_reference",
            "ready": True,
            "mode": "dense_w8a8",
            "layout_id": "row_major_k_n_v1",
            "accepted_dimension_range": [1, 64],
            "validation_scope": "software_only; see test and run records for shapes and host",
            "build_id": "cpu_reference_v1",
            "pl_validated": False,
        }

    def prepare(self, task_spec: TaskSpec, weight_payload: np.ndarray) -> CpuHandle:
        start = perf_counter_ns()
        _validate_spec(task_spec)
        _validate_array("W", weight_payload, (task_spec.K, task_spec.N))
        weights = weight_payload.copy(order="C")
        weights.flags.writeable = False
        return CpuHandle(task_spec, weights, (perf_counter_ns() - start) / 1e6)

    def run(self, handle: CpuHandle, X_int8: np.ndarray) -> RunResult:
        if not isinstance(handle, CpuHandle) or handle.released or handle.W is None:
            raise HandleStateError("handle has been released or is invalid")
        spec = handle.spec
        _validate_array("X", X_int8, (spec.M, spec.K))
        start = perf_counter_ns()
        result = np.empty((spec.M, spec.N), dtype=np.int32)
        # Independent implementation path: Python integer multiply/accumulate,
        # rather than calling the project's NumPy-matmul Golden.
        for m in range(spec.M):
            for n in range(spec.N):
                acc = 0
                for k in range(spec.K):
                    acc += int(X_int8[m, k]) * int(handle.W[k, n])
                result[m, n] = acc
        elapsed_us = (perf_counter_ns() - start) / 1e3
        return RunResult(
            Y=result,
            timings={
                "prepare_ms": handle.prepare_ms,
                "cpu_call_us": elapsed_us,
                "board_call_us": None,
                "submit_wait_us": None,
                "kernel_us": None,
            },
            run_id=uuid4().hex,
            backend="cpu_reference",
            build_id="cpu_reference_v1",
        )

    def release(self, handle: CpuHandle) -> None:
        if not isinstance(handle, CpuHandle):
            raise HandleStateError("invalid handle")
        handle.W = None
        handle.released = True


def select_backend(name: str, *, manifest_path=None, socket_path=None):
    if name == "cpu_reference":
        return CpuReferenceBackend()
    if name == "fpga_f8" and manifest_path is not None and socket_path is not None:
        from .f8_backend import F8XrtBackend
        return F8XrtBackend(manifest_path, socket_path)
    raise RuntimeNotReadyError(
        f"{name} is not available: F8 requires an explicit verified bundle and live XRT socket"
    )
