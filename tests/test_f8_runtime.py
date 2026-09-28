"""Development transport/lifetime tests. No PL correctness claim is made."""
import socket
from threading import Lock, Thread
import unittest

from board.backend import HandleStateError, RuntimeNotReadyError, TaskSpec, select_backend
from board.f8_host import F8Session, _HELD_SESSIONS
from board.f8_xrt import F8XrtAdapter
from test_f8_host import ModelAdapter
import numpy as np


class RuntimeBoundaryTests(unittest.TestCase):
    def socket_adapter(self, timeout=0.2):
        client, server = socket.socketpair()
        client.settimeout(timeout)
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        adapter = object.__new__(F8XrtAdapter)
        adapter._socket = client
        adapter._timeout = timeout
        adapter._closed = False
        adapter._lock = Lock()
        return adapter, server

    def test_fragmented_reply_and_device_error_not_substituted_with_cpu(self):
        adapter, server = self.socket_adapter()
        def owner():
            server.recv(100)
            for data in (b"OK ", b"123 ", b"0\n"):
                server.sendall(data)
            server.recv(100)
            server.sendall(b"OK 123 4\n")
        task = Thread(target=owner)
        task.start()
        self.assertEqual(adapter.read32(0), 123)
        with self.assertRaisesRegex(RuntimeError, "AXI/status error"):
            adapter.read32(0)
        task.join(1)
        self.assertFalse(task.is_alive())

    def test_timeout_discards_stream_and_rejects_followup(self):
        adapter, server = self.socket_adapter(0.01)
        with self.assertRaises(TimeoutError):
            adapter._rpc("WRITE 0 1")
        self.assertIsNone(adapter._socket)
        # The peer is not a child process of the Python client and remains open.
        self.assertIn(b"WRITE 0 1", server.recv(100))
        with self.assertRaises(HandleStateError):
            adapter._rpc("READ 0")

    def test_native_error_is_propagated(self):
        adapter, server = self.socket_adapter()
        server.sendall(b"ERR AXI transactions remain outstanding\n")
        with self.assertRaisesRegex(RuntimeError, "outstanding"):
            adapter._rpc("RECOVER")

    def test_fault_without_recovery_proof_retains_session(self):
        model = ModelAdapter()
        model.hang = True
        session = F8Session(model)
        X = np.zeros((8,8), dtype=np.int8)
        with self.assertRaises(TimeoutError):
            session.run(TaskSpec(8,8,8), X, X, timeout_s=0.001)
        with self.assertRaises(HandleStateError):
            session.recover()
        self.assertEqual(session.state, "FAULT")
        self.assertIn(id(session), _HELD_SESSIONS)
        _HELD_SESSIONS.pop(id(session))  # Test model owns no real resources.

    def test_recovery_failure_stays_fault_and_success_checks_ctrl(self):
        model = ModelAdapter()
        session = F8Session(model)
        session._fault()
        self.addCleanup(_HELD_SESSIONS.pop, id(session), None)
        def failure():
            raise RuntimeError("outstanding")
        model.recover = failure
        with self.assertRaises(RuntimeError):
            session.recover()
        self.assertEqual(session.state, "FAULT")
        model.recover = lambda: {"proof": "software model only"}
        model.ctrl = 2
        with self.assertRaises(RuntimeError):
            session.recover()
        self.assertEqual(session.state, "FAULT")
        model.ctrl = 4
        session.recover()
        self.assertEqual(session.state, "IDLE")
        self.assertNotIn(id(session), _HELD_SESSIONS)

    def test_backend_requires_explicit_runtime_configuration(self):
        for kwargs in ({}, {"manifest_path": "missing.json"}, {"socket_path": "missing.sock"}):
            with self.assertRaises(RuntimeNotReadyError):
                select_backend("fpga_f8", **kwargs)


if __name__ == "__main__":
    unittest.main()
