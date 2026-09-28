"""Development tests of host logic with a software CU model; never board PASS."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re
import unittest

import numpy as np

from board.backend import (CapabilityError, HandleStateError, InputError,
                           RuntimeNotReadyError, TaskSpec, select_backend)
from board.f8_host import BufferInfo, CAPABILITY_PATH, F8Session, _HELD_SESSIONS


ROOT = Path(__file__).resolve().parents[1]
SPEC = TaskSpec(8, 8, 8)


class ModelAdapter:
    """Independent sticky-done control model; output data are scripted, not GEMM."""

    def __init__(self):
        payload = CAPABILITY_PATH.read_bytes()
        capability = json.loads(payload)
        self.interface_id = capability["interface_id"]
        self.ip_sha256 = capability["hardware"]["ip_sha256"]
        self.capability_sha256 = sha256(payload).hexdigest()
        self.control_owner = "exclusive_host_chain"
        self.exclusive = True
        self.info = {
            "X": BufferInfo(0x800001000, 64, True, False, True),
            "W": BufferInfo(0x800002000, 64, True, False, True),
            "Y": BufferInfo(0x800003000, 256, False, True, True),
        }
        self.events = []
        self.registers = {}
        self.ctrl = 4
        self.polls = 0
        self.ack_polls = 0
        self.starts = 0
        self.closed = 0
        self.hang = False
        self.stuck_done = False
        self.fail_start = False
        self.output_failure = None
        self.output = np.arange(64, dtype="<i4").tobytes()
        self.on_input = None

    def buffers(self):
        return self.info.copy()

    def write_input(self, name, array):
        self.events.append(("input", name, array.copy()))
        if self.on_input:
            self.on_input()

    def sync_input(self, name):
        self.events.append(("sync_in", name))

    def sync_output(self):
        self.events.append(("sync_out",))
        if self.output_failure == "sync":
            raise OSError("injected output sync failure")

    def read_output(self):
        self.events.append(("read_out",))
        if self.output_failure == "read":
            raise OSError("injected output read failure")
        return self.output

    def read32(self, offset):
        if offset != 0:
            raise AssertionError("only CTRL should be read")
        if self.ctrl == 0 and not self.hang:
            self.polls += 1
            if self.polls >= 2:
                self.ctrl = 2  # Reading this bit does not acknowledge completion.
        if self.ack_polls and not self.stuck_done:
            self.ack_polls -= 1
            if self.ack_polls == 0:
                self.ctrl = 4
        self.events.append(("read32", offset, self.ctrl))
        return self.ctrl

    def write32(self, offset, value):
        self.events.append(("write32", offset, value))
        self.registers[offset] = value
        if offset == 0 and value == 1:
            if self.ctrl != 4:
                raise AssertionError("start requires idle with cleared done")
            for name, low, high in (("X", 0x10, 0x14), ("W", 0x1C, 0x20), ("Y", 0x28, 0x2C)):
                if (self.registers[high] << 32) | self.registers[low] != self.info[name].address:
                    raise AssertionError("incomplete 64-bit address before start")
            self.starts += 1
            self.ctrl = 0
            self.polls = 0
            if self.fail_start:
                raise OSError("ambiguous Start write")
        elif offset == 0 and value == 16:
            if self.ctrl != 2:
                raise AssertionError("continue without completion")
            self.ack_polls = 2  # Host must observe deassertion, not assume it.

    def close(self):
        self.events.append(("close",))
        self.closed += 1


class F8HostTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ModelAdapter()
        self.session = F8Session(self.adapter)
        self.X = np.arange(64, dtype=np.int8).reshape(8, 8)
        self.W = np.eye(8, dtype=np.int8)

    def tearDown(self):
        # Software models own no DMA memory. This is not a production recovery API.
        _HELD_SESSIONS.pop(id(self.session), None)

    def run_task(self, **kwargs):
        return self.session.run(SPEC, self.X, self.W, **kwargs)

    def test_shape_dtype_layout_mode_rejected_before_io(self):
        cases = [
            (TaskSpec(2, 3, 2), self.X, self.W),
            (TaskSpec(17, 33, 15), self.X, self.W),
            (TaskSpec(8, 8, 7), self.X, self.W),
            (TaskSpec(True, 8, 8), self.X, self.W),
            (TaskSpec(8, 8, 8, mode="sparse"), self.X, self.W),
            (SPEC, self.X.astype(np.uint8), self.W),
            (SPEC, self.X.T, self.W),
            (SPEC, self.X, self.W.astype(np.int32)),
        ]
        for spec, X, W in cases:
            with self.subTest(spec=spec, dtype=str(X.dtype)), self.assertRaises((InputError, CapabilityError)):
                self.session.run(spec, X, W)
            self.assertEqual(self.adapter.events, [])
            self.assertEqual(self.session.state, "IDLE")

    def test_invalid_buffers_rejected_before_io(self):
        original = self.adapter.info.copy()
        cases = [
            ("X", {"capacity": 63}), ("Y", {"capacity": 255}),
            ("X", {"address": 0}), ("X", {"address": -64}),
            ("W", {"address": 0x800002004}),
            ("Y", {"address": 2**64 - 64}),
            ("X", {"address": True}), ("W", {"offset": 64}),
            ("X", {"runtime_verified": False}), ("Y", {"device_write": False}),
            ("W", {"device_read": False}),
            ("Y", {"address": original["X"].address}),
            ("W", {"address": original["X"].address}),
        ]
        for name, changes in cases:
            self.adapter.info = original.copy()
            self.adapter.info[name] = replace(original[name], **changes)
            with self.subTest(name=name, changes=changes), self.assertRaises(InputError):
                self.run_task()
            self.assertEqual(self.adapter.events, [])

    def test_identity_and_control_ownership_mismatch_rejected(self):
        for name, wrong in (("interface_id", "D64"), ("ip_sha256", "wrong"),
                            ("capability_sha256", "wrong"), ("control_owner", "runtime_scheduler"),
                            ("exclusive", False)):
            original = getattr(self.adapter, name)
            setattr(self.adapter, name, wrong)
            with self.subTest(name=name), self.assertRaises(CapabilityError):
                self.run_task()
            self.assertEqual(self.adapter.events, [])
            setattr(self.adapter, name, original)

    def test_repeated_calls_reconfigure_all_addresses_and_acknowledge_done(self):
        first = self.run_task()
        np.testing.assert_array_equal(first, np.arange(64, dtype=np.int32).reshape(8, 8))
        self.assertEqual(self.session.last_done_control, 2)
        for name, item in self.adapter.info.items():
            self.adapter.info[name] = replace(item, address=item.address + 0x100000000)
        self.X.fill(-128)
        self.W.fill(127)
        self.adapter.output = np.full((8, 8), -130048, dtype="<i4").tobytes()
        second = self.run_task()
        np.testing.assert_array_equal(second, np.full((8, 8), -130048, dtype=np.int32))
        self.assertEqual(first[0, 1], 1)
        self.assertEqual(self.adapter.starts, 2)
        self.assertEqual(self.session.state, "IDLE")
        self.assertEqual(self.adapter.ctrl, 4)
        controls = [event[2] for event in self.adapter.events if event[:2] == ("write32", 0)]
        self.assertEqual(controls, [1, 16, 1, 16])
        kinds = [event[:2] for event in self.adapter.events]
        self.assertLess(kinds.index(("sync_in", "X")), kinds.index(("write32", 0x10)))
        self.assertLess(kinds.index(("sync_in", "W")), kinds.index(("write32", 0x10)))
        self.assertLess(kinds.index(("sync_out",)), kinds.index(("read_out",)))

    def test_output_software_failures_still_acknowledge_and_allow_next_call(self):
        for failure in ("sync", "read", "short"):
            self.adapter.output_failure = failure
            self.adapter.output = b"short" if failure == "short" else bytes(256)
            with self.subTest(failure=failure), self.assertRaises((OSError, InputError)):
                self.run_task()
            self.assertEqual(self.session.state, "IDLE")
            self.assertEqual(self.adapter.ctrl, 4)
            self.assertEqual(self.session.last_done_control, 2)
        self.adapter.output_failure = None
        self.adapter.output = bytes(256)
        self.run_task()

    def assert_fault_is_quarantined(self):
        self.assertEqual(self.session.state, "FAULT")
        self.assertIs(_HELD_SESSIONS[id(self.session)], self.session)
        before = len(self.adapter.events)
        with self.assertRaises(HandleStateError):
            self.run_task()
        with self.assertRaises(HandleStateError):
            self.session.close()
        self.assertEqual(len(self.adapter.events), before)
        self.assertEqual(self.adapter.closed, 0)

    def test_timeout_retains_buffers_and_blocks_resubmission_and_close(self):
        self.adapter.hang = True
        with self.assertRaises(TimeoutError):
            self.run_task(timeout_s=0.002)
        self.assertIsNone(self.session.last_done_control)
        self.assertNotIn(("write32", 0, 16), self.adapter.events)
        self.assert_fault_is_quarantined()

    def test_uncertain_start_write_retains_buffers(self):
        self.adapter.fail_start = True
        with self.assertRaises(OSError):
            self.run_task()
        self.assertEqual(self.adapter.starts, 1)
        self.assert_fault_is_quarantined()

    def test_continue_not_clearing_done_enters_fault(self):
        self.adapter.stuck_done = True
        with self.assertRaises(TimeoutError):
            self.run_task(cleanup_timeout_s=0.002)
        self.assertEqual(self.session.last_done_control, 2)
        self.assert_fault_is_quarantined()

    def test_stale_done_and_auto_restart_are_never_reused(self):
        for ctrl in (2, 0x84, 1):
            self.adapter.ctrl = ctrl
            with self.subTest(ctrl=ctrl), self.assertRaises(RuntimeError):
                self.run_task()
            self.assertEqual(self.adapter.starts, 0)
            self.assert_fault_is_quarantined()
            _HELD_SESSIONS.pop(id(self.session))  # Discard only the software model.
            self.session = F8Session(self.adapter)

    def test_concurrent_call_and_close_are_rejected(self):
        def competing_call():
            with self.assertRaises(HandleStateError):
                self.run_task()
            with self.assertRaises(HandleStateError):
                self.session.close()
        self.adapter.on_input = competing_call
        self.run_task()
        self.assertEqual(self.adapter.starts, 1)

    def test_invalid_timeout_is_rejected_before_io(self):
        for timeout in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout), self.assertRaises(InputError):
                self.run_task(timeout_s=timeout)
            self.assertEqual(self.adapter.events, [])

    def test_close_is_idempotent_and_prohibits_later_run(self):
        self.run_task()
        self.session.close()
        self.session.close()
        self.assertEqual(self.adapter.closed, 1)
        with self.assertRaises(HandleStateError):
            self.run_task()

    def test_fpga_backend_remains_unavailable_without_real_adapter(self):
        for name in ("fpga_xrt", "fpga_f8"):
            with self.assertRaises(RuntimeNotReadyError):
                select_backend(name)


class F8ReleaseContractTests(unittest.TestCase):
    def test_capability_matches_published_ip_registers_and_widths(self):
        cap = json.loads(CAPABILITY_PATH.read_bytes())
        release = ROOT / "results/external_ip_review_20260927/release"
        self.assertEqual(sha256((release / "gemm_top.zip").read_bytes()).hexdigest(), cap["hardware"]["ip_sha256"])
        ip = release / "ip"
        top = (ip / "hdl/verilog/gemm_top.v").read_text(encoding="utf-8")
        control = (ip / "hdl/verilog/gemm_top_control_s_axi.v").read_text(encoding="utf-8")
        header = (ip / "drivers/gemm_top_v1_0/src/xgemm_top_hw.h").read_text(encoding="utf-8")
        for index, name in enumerate(("X", "W", "Y")):
            for kind, expected in (("DATA", cap["hardware"]["external_axi_data_bits"][name]),
                                   ("ADDR", cap["hardware"]["address_bits"])):
                match = re.search(rf"C_M_AXI_GMEM{index}_{kind}_WIDTH\s*=\s*(\d+)", top)
                self.assertEqual(int(match[1]), expected)
            for index2, half in enumerate(("low", "high")):
                match = re.search(rf"ADDR_{name}_DATA_{index2}\s*=\s*6'h([0-9a-f]+)", control)
                actual = int(match[1], 16)
                self.assertEqual(cap["registers"][name][half], actual)
                self.assertLessEqual(actual + 4, cap["hardware"]["control_aperture_bytes"])
        for name, exported in (("CTRL", "AP_CTRL"), ("GIE", "GIE"), ("IER", "IER"), ("ISR", "ISR")):
            match = re.search(rf"#define XGEMM_TOP_CONTROL_ADDR_{exported}\s+(0x[0-9a-f]+)", header)
            self.assertEqual(cap["registers"][name], int(match[1], 16))
        self.assertRegex(header, r"bit 1\s+- ap_done \(Read\)")
        self.assertRegex(header, r"bit 4\s+- ap_continue \(Read/Write/SC\)")
        self.assertEqual(int(re.search(r"C_S_AXI_CONTROL_ADDR_WIDTH\s*=\s*(\d+)", top)[1]), 6)
        self.assertFalse(cap["validation"]["ready_for_board"])


if __name__ == "__main__":
    unittest.main()
