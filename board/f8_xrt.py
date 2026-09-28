"""Verified Python client for the persistent native XRT/BO owner on KV260."""
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import struct
from threading import Lock
from time import monotonic

import numpy as np

from .backend import CapabilityError, HandleStateError, InputError, RuntimeNotReadyError
from .f8_host import BufferInfo, CAPABILITY_PATH


def verify_bundle(path: Path) -> dict:
    path = path.resolve()
    manifest = json.loads(path.read_bytes())
    if manifest.get("schema") != "f8_runtime_bundle_v1":
        raise CapabilityError("unsupported F8 runtime bundle")
    for relative, expected in manifest["files"].items():
        item = (path.parent / relative).resolve()
        if not item.is_relative_to(path.parent):
            raise CapabilityError("bundle file escapes bundle directory")
        if sha256(item.read_bytes()).hexdigest() != expected:
            raise CapabilityError(f"bundle hash mismatch: {relative}")
    local_cap = sha256(CAPABILITY_PATH.read_bytes()).hexdigest()
    if local_cap != manifest["capability_sha256"]:
        raise CapabilityError("installed capability differs from bundle")
    return manifest


class F8XrtAdapter:
    control_owner = "exclusive_host_chain"
    exclusive = True

    def __init__(self, manifest_path: str | Path, socket_path: str | Path, rpc_timeout_s=2.0):
        if os.name != "posix":
            raise RuntimeNotReadyError("F8 XRT client must run on the KV260 Linux host")
        self._manifest_path = Path(manifest_path).resolve()
        self._manifest = verify_bundle(self._manifest_path)
        self._socket_path = str(socket_path)
        self._timeout = float(rpc_timeout_s)
        if not 0 < self._timeout <= 10:
            raise InputError("RPC timeout must be in (0,10] seconds")
        self._socket = None
        self._lock = Lock()
        self._closed = False
        self.last_recovery = None
        self._connect()
        try:
            self._verify_owner()
        except BaseException:
            self._disconnect()
            raise
        cap = json.loads(CAPABILITY_PATH.read_bytes())
        self.interface_id = cap["interface_id"]
        self.ip_sha256 = cap["hardware"]["ip_sha256"]
        self.capability_sha256 = self._manifest["capability_sha256"]

    def _connect(self):
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        peer.settimeout(self._timeout)
        try:
            peer.connect(self._socket_path)
        except BaseException:
            peer.close()
            raise
        self._socket = peer

    def _disconnect(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _rpc(self, command: str) -> str:
        if self._closed or self._socket is None:
            raise HandleStateError("XRT owner disconnected; explicit recovery required")
        if not self._lock.acquire(blocking=False):
            raise HandleStateError("concurrent XRT RPC rejected")
        try:
            try:
                deadline = monotonic() + self._timeout
                self._socket.settimeout(self._timeout)
                self._socket.sendall(command.encode("ascii") + b"\n")
                reply = bytearray()
                while not reply.endswith(b"\n"):
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("XRT RPC deadline expired")
                    self._socket.settimeout(remaining)
                    part = self._socket.recv(1)
                    if not part or len(reply) > 4096:
                        raise ConnectionError("invalid or disconnected XRT owner response")
                    reply.extend(part)
            except (OSError, ConnectionError):
                # The native owner remains alive and retains all BOs. Reusing a
                # stream after a timeout could consume a stale response.
                self._disconnect()
                raise
            line = reply.decode("ascii").rstrip("\n")
            if line.startswith("ERR "):
                raise RuntimeError(line[4:])
            if not line.startswith("OK "):
                self._disconnect()
                raise RuntimeError("invalid XRT response prefix")
            return line[3:]
        finally:
            self._lock.release()

    def _verify_owner(self):
        info = self._rpc("INFO").split()
        if len(info) != 11:
            raise CapabilityError("invalid native owner information")
        tag0, tag1 = map(int, info[:2])
        if [tag0, tag1] != self._manifest["hardware_build_tags"] or info[2] != self._manifest["xclbin_uuid"]:
            raise CapabilityError("loaded hardware/metadata identity differs from bundle")
        pid = int(info[3])
        peer_pid, peer_uid, _ = struct.unpack("3i", self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if pid != peer_pid or peer_uid != os.geteuid():
            raise CapabilityError("native owner peer credentials differ")
        actual = sha256(Path(f"/proc/{pid}/exe").read_bytes()).hexdigest()
        if actual != self._manifest["files"]["f8_xrt_service"]:
            raise CapabilityError("native owner executable differs from bundle")
        self.owner_pid = pid
        self._buffers = {name: BufferInfo(int(info[4 + 2*i]), int(info[5 + 2*i]),
                                         name != "Y", name == "Y", True)
                         for i, name in enumerate(("X", "W", "Y"))}

    def buffers(self):
        return dict(self._buffers)

    def write_input(self, name, array):
        if name not in ("X", "W") or not isinstance(array, np.ndarray) or array.shape != (8, 8) or array.dtype != np.int8 or not array.flags.c_contiguous:
            raise InputError("XRT input must be a contiguous 8x8 signed INT8 array")
        self._rpc(f"INPUT {0 if name == 'X' else 1} {array.tobytes().hex()}")

    def sync_input(self, name):
        if name not in ("X", "W"):
            raise InputError("unknown input")
        self._rpc(f"SYNCIN {0 if name == 'X' else 1}")

    def sync_output(self):
        self._rpc("SYNCOUT")

    def read_output(self):
        payload = bytes.fromhex(self._rpc("OUTPUT"))
        if len(payload) != 256:
            raise RuntimeError("invalid XRT output length")
        return payload

    def read32(self, offset):
        ctrl, errors = map(int, self._rpc(f"READ {offset}").split())
        if errors:
            raise RuntimeError(f"F8 AXI/status error latch: 0x{errors:x}")
        return ctrl

    def write32(self, offset, value):
        if type(offset) is not int or type(value) is not int or not 0 <= value < 2**32:
            raise InputError("invalid MMIO request")
        self._rpc(f"WRITE {offset} {value}")

    def status(self):
        keys = ("ctrl", "errors", "x_pending", "w_pending", "y_pending", "x_ar", "x_r",
                "w_ar", "w_r", "y_aw", "y_w", "y_b", "quiet", "submitted", "quarantined")
        values = list(map(int, self._rpc("STATUS").split()))
        if len(values) != len(keys):
            raise RuntimeError("invalid status response")
        return dict(zip(keys, values))

    def recover(self):
        if self._closed:
            raise HandleStateError("adapter is closed")
        if self._socket is None:
            self._connect()
        self._verify_owner()
        before = self._rpc("RECOVER")
        after = self.status()
        if (after["errors"] or after["submitted"] or after["quarantined"] or
                any(after[k] for k in ("x_pending", "w_pending", "y_pending")) or
                not after["quiet"] or not after["ctrl"] & 4 or after["ctrl"] & 0x83):
            raise RuntimeError("native recovery did not establish a stopped and drained CU")
        self.last_recovery = {"before": before, "after": after}
        return self.last_recovery

    def close(self):
        if self._closed:
            return
        self._rpc("DETACH")
        self._disconnect()
        self._closed = True
