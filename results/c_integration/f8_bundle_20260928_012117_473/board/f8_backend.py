"""The four-method C interface backed exclusively by the KV260 F8 XRT service."""
from dataclasses import dataclass
from functools import wraps
from threading import Lock
from time import perf_counter_ns
from uuid import uuid4

import numpy as np

from .backend import (CapabilityError, HandleStateError, RuntimeNotReadyError, RunResult, TaskSpec,
                      _validate_array, _validate_spec)
from .f8_host import F8Session
from .f8_xrt import F8XrtAdapter


@dataclass
class F8Handle:
    spec: TaskSpec
    W: np.ndarray | None
    prepare_ms: float
    owner: object
    released: bool = False


def exclusive_call(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        if not self._lock.acquire(blocking=False):
            raise HandleStateError("concurrent F8 backend call rejected")
        try:
            return method(self, *args, **kwargs)
        finally:
            self._lock.release()
    return call


class F8XrtBackend:
    def __init__(self, manifest_path, socket_path):
        self.adapter = F8XrtAdapter(manifest_path, socket_path)
        try:
            status = self.adapter.status()
            if (status["quarantined"] or status["submitted"] or status["errors"] or
                    not status["ctrl"] & 4 or status["ctrl"] & 0x83 or not status["quiet"] or
                    any(status[k] for k in ("x_pending", "w_pending", "y_pending"))):
                raise RuntimeNotReadyError("F8 owner requires explicit recovery")
        except BaseException:
            self.adapter._disconnect()
            raise
        self.session = F8Session(self.adapter)
        self._handle = None
        self._lock = Lock()

    def capabilities(self):
        return {"backend": "fpga_f8", "ready": self.session.state == "IDLE",
                "supported_shapes": [[8, 8, 8]], "mode": "dense_w8a8",
                "layout_id": "row_major_k_n_v1", "cpu_fallback": False,
                "build_id": self.adapter._manifest["build_fingerprint"],
                "runtime_owner_pid": self.adapter.owner_pid,
                "pl_validation": "see board result records; construction is not a GEMM test"}

    @exclusive_call
    def prepare(self, task_spec, weight_payload):
        start = perf_counter_ns()
        _validate_spec(task_spec)
        if (task_spec.M, task_spec.K, task_spec.N) != (8, 8, 8):
            raise CapabilityError("F8 supports only 8x8x8")
        _validate_array("W", weight_payload, (8, 8))
        if self._handle is not None or self.session.state != "IDLE":
            raise HandleStateError("F8 permits one prepared handle on an idle session")
        weights = weight_payload.copy(order="C")
        weights.flags.writeable = False
        self._handle = F8Handle(task_spec, weights, (perf_counter_ns()-start)/1e6, self)
        return self._handle

    def _check(self, handle):
        if handle is not self._handle or handle.owner is not self or handle.released or handle.W is None:
            raise HandleStateError("invalid or released F8 handle")

    @exclusive_call
    def run(self, handle, X_int8):
        self._check(handle)
        _validate_array("X", X_int8, (8, 8))
        start = perf_counter_ns()
        result = self.session.run(handle.spec, X_int8, handle.W)
        board_us = (perf_counter_ns()-start)/1e3
        status = self.adapter.status()
        return RunResult(result,
                         {"prepare_ms": handle.prepare_ms, "board_call_us": board_us,
                          "cpu_call_us": None, "submit_wait_us": None, "kernel_us": None,
                          "axi_status": status}, uuid4().hex, "fpga_f8",
                         self.adapter._manifest["build_fingerprint"])

    @exclusive_call
    def release(self, handle):
        if isinstance(handle, F8Handle) and handle.owner is self and handle.released:
            return
        self._check(handle)
        if self.session.state != "IDLE":
            raise HandleStateError("F8 is not idle; recover before release")
        handle.W = None
        handle.released = True
        self._handle = None

    @exclusive_call
    def recover(self):
        return self.session.recover()

    @exclusive_call
    def close(self):
        if self._handle is not None:
            raise HandleStateError("release the prepared handle before close")
        self.session.close()
