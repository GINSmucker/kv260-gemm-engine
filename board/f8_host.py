"""F8 request validation and chain lifecycle shared by model and XRT adapters.

The adapter owns an exclusive CU and all three BOs for the session lifetime.
It must obtain addresses/capacities/permissions from its runtime, not caller
integers. The native XRT service retains BO ownership across client failures.
Test adapters are software models only.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Protocol

import numpy as np

from .backend import (CapabilityError, HandleStateError, InputError,
                      TaskSpec, _validate_array, _validate_spec)


CAPABILITY_PATH = Path(__file__).resolve().parents[1] / "hardware/kv260_f8/capability.json"
# An ambiguous submission must retain BO ownership even if the caller drops its
# session reference. There is deliberately no software-only 'clear fault'.
_HELD_SESSIONS: dict[int, "F8Session"] = {}


@dataclass(frozen=True)
class BufferInfo:
    address: int
    capacity: int
    device_read: bool
    device_write: bool
    runtime_verified: bool
    offset: int = 0


class F8Adapter(Protocol):
    """Only this owner controls the CU. No simultaneous xrt::run/UIO control.

    All methods must return or raise within a bounded time. Own BOs until close;
    retain device/runtime handles and exclusive control while FAULT is latched.
    Identity must come from verified loaded-platform metadata, not user input.
    """

    interface_id: str
    ip_sha256: str
    capability_sha256: str
    control_owner: str
    exclusive: bool

    def buffers(self) -> dict[str, BufferInfo]: ...
    def write_input(self, name: str, array: np.ndarray) -> None: ...
    def sync_input(self, name: str) -> None: ...
    def sync_output(self) -> None: ...
    def read_output(self) -> bytes: ...
    def read32(self, offset: int) -> int: ...
    def write32(self, offset: int, value: int) -> None: ...
    def close(self) -> None: ...


class F8Session:
    """Minimal synchronous F8 controller; readiness depends on the adapter."""

    def __init__(self, adapter: F8Adapter):
        payload = CAPABILITY_PATH.read_bytes()
        self._cap = json.loads(payload)
        self._cap_hash = sha256(payload).hexdigest()
        self._adapter = adapter
        self._lock = Lock()
        self._state = "IDLE"
        self.last_done_control: int | None = None
        self._check_identity()

    @property
    def state(self) -> str:
        return self._state

    def _check_identity(self) -> None:
        expected = {
            "interface_id": self._cap["interface_id"],
            "ip_sha256": self._cap["hardware"]["ip_sha256"],
            "capability_sha256": self._cap_hash,
            "control_owner": self._cap["host"]["control_owner"],
        }
        if any(getattr(self._adapter, key, None) != value for key, value in expected.items()):
            raise CapabilityError("F8 interface, IP, capability hash or control owner mismatch")
        if self._adapter.exclusive is not True:
            raise CapabilityError("F8 requires an exclusive CU owner")

    def _validate(self, spec: TaskSpec, X: np.ndarray, W: np.ndarray) -> dict[str, BufferInfo]:
        self._check_identity()
        _validate_spec(spec)
        if [spec.M, spec.K, spec.N] not in self._cap["host"]["supported_shapes"]:
            raise CapabilityError("F8 accepts only (M,K,N)=(8,8,8); no implicit padding or tiling")
        _validate_array("X", X, (spec.M, spec.K))
        _validate_array("W", W, (spec.K, spec.N))
        buffers = self._adapter.buffers()
        if set(buffers) != {"X", "W", "Y"}:
            raise InputError("runtime must own exactly X, W and Y buffers")
        ranges = []
        for name, size in self._cap["host"]["buffer_bytes"].items():
            item = buffers[name]
            if not isinstance(item, BufferInfo) or item.runtime_verified is not True:
                raise InputError(f"{name}: missing runtime-verified BO")
            if (type(item.address) is not int or type(item.capacity) is not int or
                    item.address <= 0 or item.capacity < size or
                    item.address + item.capacity > 2**self._cap["hardware"]["address_bits"]):
                raise InputError(f"{name}: invalid device address or capacity")
            if (type(item.offset) is not int or item.offset != 0 or
                    item.address % self._cap["host"]["required_alignment_bytes"]):
                raise InputError(f"{name}: requires aligned allocation base with offset=0")
            if ((name in ("X", "W") and item.device_read is not True) or
                    (name == "Y" and item.device_write is not True)):
                raise InputError(f"{name}: incompatible device permissions")
            ranges.append((item.address, item.address + item.capacity))
        ranges.sort()
        if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
            raise InputError("F8 rejects aliasing allocation ranges")
        return buffers

    def _bit(self, name: str) -> int:
        return 1 << self._cap["control_bits"][name]

    def _read_control(self) -> int:
        value = self._adapter.read32(self._cap["registers"]["CTRL"])
        if type(value) is not int or not 0 <= value < 2**32:
            raise RuntimeError("invalid control register response")
        if value & self._bit("auto_restart"):
            raise RuntimeError("auto-restart is outside the F8 single-task contract")
        return value

    def _wait(self, predicate, timeout_s: float) -> int:
        deadline = monotonic() + timeout_s
        while True:
            value = self._read_control()
            if predicate(value):
                return value
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("F8 control deadline expired; BOs remain retained")
            sleep(min(0.001, remaining))

    def _idle(self, value: int) -> bool:
        return bool(value & self._bit("idle")) and not value & (self._bit("start") | self._bit("done"))

    def _fault(self) -> None:
        self._state = "FAULT"
        _HELD_SESSIONS[id(self)] = self

    def run(self, spec: TaskSpec, X: np.ndarray, W: np.ndarray,
            timeout_s: float = 1.0, cleanup_timeout_s: float = 0.1) -> np.ndarray:
        if not self._lock.acquire(blocking=False):
            raise HandleStateError("concurrent F8 calls are not supported")
        try:
            if self.state != "IDLE":
                raise HandleStateError(f"F8 session is {self.state}; run is prohibited")
            for value in (timeout_s, cleanup_timeout_s):
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise InputError("timeouts must be positive finite seconds")
            buffers = self._validate(spec, X, W)  # Before any MMIO or input writes.
            self.last_done_control = None
            _HELD_SESSIONS[id(self)] = self
            try:
                if not self._idle(self._read_control()):
                    raise RuntimeError("CU is not cleanly idle; stale done/start is not accepted")
                for name, array in (("X", X), ("W", W)):
                    self._adapter.write_input(name, array)
                    self._adapter.sync_input(name)
                registers = self._cap["registers"]
                self._adapter.write32(registers["GIE"], 0)
                self._adapter.write32(registers["IER"], 0)
                for name in ("X", "W", "Y"):
                    address = buffers[name].address
                    self._adapter.write32(registers[name]["low"], address & 0xFFFFFFFF)
                    self._adapter.write32(registers[name]["high"], address >> 32)
                # A failed Start write can still have reached hardware.
                self._state = "RUNNING"
                self._adapter.write32(registers["CTRL"], self._bit("start"))
                self.last_done_control = self._wait(lambda c: bool(c & self._bit("done")), timeout_s)
                self._state = "COMPLETED"
                try:
                    self._adapter.sync_output()
                    payload = self._adapter.read_output()
                    if not isinstance(payload, bytes) or len(payload) != self._cap["host"]["buffer_bytes"]["Y"]:
                        raise InputError("runtime must return exactly 256 output bytes")
                    result = np.frombuffer(payload, dtype="<i4").reshape(8, 8).copy()
                finally:
                    # Execute even when output sync, copying or later processing fails.
                    self._adapter.write32(registers["CTRL"], self._bit("continue"))
                    self._wait(self._idle, cleanup_timeout_s)
                    self._state = "IDLE"
                    _HELD_SESSIONS.pop(id(self), None)
                return result
            except BaseException:
                if self.state != "IDLE" or id(self) in _HELD_SESSIONS:
                    self._fault()
                raise
        finally:
            self._lock.release()

    def recover(self) -> dict:
        """Return to IDLE only after the real owner proves stopped and drained."""
        if not self._lock.acquire(blocking=False):
            raise HandleStateError("cannot recover an active F8 call")
        try:
            if self.state != "FAULT":
                raise HandleStateError("recovery requires FAULT")
            recover = getattr(self._adapter, "recover", None)
            if recover is None:
                raise HandleStateError("adapter has no platform recovery proof")
            evidence = recover()
            if not self._idle(self._read_control()):
                raise RuntimeError("recovered CU is not idle")
            self._state = "IDLE"
            _HELD_SESSIONS.pop(id(self), None)
            return evidence
        finally:
            self._lock.release()

    def close(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise HandleStateError("cannot close an active F8 call")
        try:
            if self.state == "CLOSED":
                return
            if self.state != "IDLE":
                raise HandleStateError("cannot release BOs before platform-proven quiescence")
            try:
                self._adapter.close()
            except BaseException:
                self._fault()
                raise
            self._state = "CLOSED"
        finally:
            self._lock.release()
