"""Meaningful C1 checks against the frozen Golden and deliberately bad output."""

from pathlib import Path
import tempfile
import unittest

import numpy as np

from board.backend import (CpuReferenceBackend, HandleStateError, InputError,
                           ResultMismatchError, RuntimeNotReadyError, select_backend)
from board.cases import load_case, postprocess_toy, quantize_toy_inputs, verify_output
from golden.gemm_golden import gemm_int8_golden


ANCHOR = Path(__file__).resolve().parents[1] / "vectors/demo_integration/anchor"
TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "results/c_integration/test_tmp"


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = load_case(ANCHOR)

    def test_anchor_and_repeated_calls(self):
        backend = CpuReferenceBackend()
        handle = backend.prepare(self.case.spec, self.case.Wq)
        first = backend.run(handle, self.case.Xq)
        verify_output(self.case, first.Y)
        np.testing.assert_array_equal(first.Y, gemm_int8_golden(self.case.Xq, self.case.Wq))
        changed = self.case.Xq.copy()
        changed[0, 0] += 1
        second = backend.run(handle, changed)
        self.assertFalse(np.array_equal(first.Y, second.Y))
        np.testing.assert_array_equal(first.Y, [[4, -11], [8, 6]])
        backend.release(handle)
        backend.release(handle)
        with self.assertRaises(HandleStateError):
            backend.run(handle, self.case.Xq)

    def test_fault_injection_is_detected(self):
        corrupted = self.case.Y_ref_int32.copy()
        corrupted[0, 1] += 1
        with self.assertRaisesRegex(ResultMismatchError, r"mismatch at \(0,1\)"):
            verify_output(self.case, corrupted)

    def test_model_consumes_returned_values(self):
        X_requant, W_requant = quantize_toy_inputs(self.case)
        np.testing.assert_array_equal(X_requant, self.case.Xq)
        np.testing.assert_array_equal(W_requant, self.case.Wq)
        model = postprocess_toy(self.case, self.case.Y_ref_int32)
        np.testing.assert_array_equal(model, [[1.0, -0.59375], [1.5, -0.0625]])
        changed = self.case.Y_ref_int32.copy()
        changed[0, 0] += 1
        self.assertEqual(postprocess_toy(self.case, changed)[0, 0], 1.125)

    def test_rejects_implicit_layout_copy(self):
        backend = CpuReferenceBackend()
        noncontiguous = self.case.Wq.T
        with self.assertRaisesRegex(InputError, "C-contiguous"):
            backend.prepare(self.case.spec.__class__(2, 2, 3), noncontiguous)

    def test_rectangular_extremes_and_weight_replacement(self):
        from board.backend import TaskSpec
        spec = TaskSpec(3, 5, 4)
        X = np.array([[-128, 127, 0, -1, 2], [1, -2, 3, -4, 5],
                      [0, 0, -128, 127, 1]], dtype=np.int8)
        W = np.array([[127, -128, 1, 0], [-1, 2, 3, 4], [0, -1, 127, -128],
                      [5, 6, -7, 8], [-128, 0, 1, 127]], dtype=np.int8)
        backend = CpuReferenceBackend()
        handle = backend.prepare(spec, W)
        first = backend.run(handle, X).Y
        np.testing.assert_array_equal(first, gemm_int8_golden(X, W))
        W[0, 0] = 0  # prepare owns a snapshot, not the caller's mutable array.
        np.testing.assert_array_equal(backend.run(handle, X).Y, first)
        backend.release(handle)
        replacement = backend.prepare(spec, W)
        second = backend.run(replacement, X).Y
        np.testing.assert_array_equal(second, gemm_int8_golden(X, W))
        self.assertFalse(np.array_equal(first, second))
        backend.release(replacement)

    def test_payload_hash_is_checked(self):
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temp:
            target = Path(temp).resolve()
            if not target.is_relative_to(TEST_TMP_ROOT.resolve()):
                raise RuntimeError("temporary test directory escaped the workspace")
            (target / "manifest.json").write_bytes((ANCHOR / "manifest.json").read_bytes())
            payload = bytearray((ANCHOR / "tensors.npz").read_bytes())
            payload[-1] ^= 1
            (target / "tensors.npz").write_bytes(payload)
            with self.assertRaisesRegex(InputError, "SHA-256 mismatch"):
                load_case(target)

    def test_fpga_selection_never_falls_back_to_cpu(self):
        with self.assertRaises(RuntimeNotReadyError):
            select_backend("fpga_xrt")


if __name__ == "__main__":
    unittest.main()
