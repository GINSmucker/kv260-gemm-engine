"""Small public development checks on the real F8 XRT chain, never sealed acceptance."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import time

import numpy as np

from .backend import TaskSpec, select_backend
from .f8_xrt import F8XrtAdapter


def golden(X, W):
    # Only the development checker calculates a reference. Backend results must
    # be returned from native XRT BO reads and are compared element-by-element.
    return np.array([[sum(int(X[i,k])*int(W[k,j]) for k in range(8))
                      for j in range(8)] for i in range(8)], dtype=np.int32)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    log = args.output.open("x", encoding="utf-8")
    def emit(event, data):
        line = json.dumps({"event": event, "monotonic_ns": time.monotonic_ns(), "data": data})
        log.write(line+"\n"); log.flush(); print(line, flush=True)
    backend = None
    try:
        backend = select_backend("fpga_f8", manifest_path=args.manifest, socket_path=args.socket)
        emit("runtime", {"capabilities": backend.capabilities(),
                         "buffers": {k: vars(v) for k,v in backend.adapter.buffers().items()},
                         "status": backend.adapter.status()})
        sequence = np.arange(-32,32,dtype=np.int8).reshape(8,8)
        mixed = ((np.arange(64,dtype=np.int16)*17+11)%256-128).astype(np.int8).reshape(8,8)
        cases = [("zero", np.zeros((8,8),dtype=np.int8), sequence),
                 ("identity", sequence, np.eye(8,dtype=np.int8)),
                 ("extreme", np.full((8,8),-128,dtype=np.int8), np.full((8,8),-128,dtype=np.int8)),
                 ("signed_nonsymmetric", sequence, mixed)]
        checked = 0
        retained = []
        for name, X, W in cases:
            handle = backend.prepare(TaskSpec(8,8,8), W)
            for repeat in range(2):
                current = X if repeat == 0 else np.ascontiguousarray(X[:,::-1])
                result = backend.run(handle, current)
                expected = golden(current, W)
                np.testing.assert_array_equal(result.Y, expected)
                for previous, original in retained:
                    np.testing.assert_array_equal(previous, original)
                retained.append((result.Y, result.Y.copy()))
                checked += result.Y.size
                emit("gemm", {"case": name, "repeat": repeat, "status": "PASS",
                              "Y": result.Y.tolist(), "timings": result.timings,
                              "output_sha256": sha256(result.Y.tobytes()).hexdigest()})
            backend.release(handle)
        backend.close(); backend = None
        # A genuine PL task is submitted, then only its Python client socket is
        # closed before consuming Done. The native owner must survive, quarantine,
        # and explicitly prove idle/drained after Continue before another task.
        orphan = F8XrtAdapter(args.manifest, args.socket)
        old_pid = orphan.owner_pid
        for name in ("X", "W"):
            orphan.write_input(name, sequence); orphan.sync_input(name)
        for name, base in (("X",0x10),("W",0x1c),("Y",0x28)):
            address = orphan.buffers()[name].address
            orphan.write32(base,address&0xffffffff); orphan.write32(base+4,address>>32)
        orphan.write32(0,1)
        orphan._disconnect()
        time.sleep(0.02)
        evidence = orphan.recover()
        assert orphan.owner_pid == old_pid
        emit("client_disconnect_recovery", {"status":"PASS", "owner_pid":old_pid, "proof":evidence})
        orphan.close()
        backend = select_backend("fpga_f8", manifest_path=args.manifest, socket_path=args.socket)
        handle = backend.prepare(TaskSpec(8,8,8), mixed)
        result = backend.run(handle, sequence)
        np.testing.assert_array_equal(result.Y, golden(sequence,mixed))
        emit("gemm", {"case":"after_recovery", "repeat":0, "status":"PASS",
                      "Y":result.Y.tolist(), "timings":result.timings,
                      "output_sha256":sha256(result.Y.tobytes()).hexdigest()})
        backend.release(handle); backend.close(); backend = None
        emit("result", {"status":"PASS", "compared_tasks":9, "compared_outputs":checked+64,
                        "disconnect_tasks":1, "acceptance":"NOT_RUN",
                        "kernel_latency_measured":False})
        return 0
    except BaseException as exc:
        emit("failure", {"type":type(exc).__name__,"message":str(exc),
                         "session_state": backend.session.state if backend else None})
        # No blanket close/kill/reset here. The independent native owner retains
        # all BOs if the hardware state is uncertain.
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
