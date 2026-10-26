"""No real models or images: deterministic algorithm and guard tests."""
import itertools
import unittest

import numpy as np

from sparse24 import dense_golden, pack, prune, quantize_weight, slot_gemm, unpack


class Sparse24Tests(unittest.TestCase):
    def test_k_axis_and_ties(self):
        weight = np.array([[1, 9], [-4, 1], [4, -8], [-2, 3],
                           [0, -7], [0, 7], [0, 7], [0, 1]], np.float32)
        pruned, positions, mask = prune(weight)
        np.testing.assert_array_equal(positions,
                                      np.array([[[1, 2], [0, 2]], [[0, 1], [0, 1]]], np.uint8))
        np.testing.assert_array_equal(pruned, np.where(mask, weight, np.float32(0)))
        self.assertTrue(np.all(mask.reshape(2, 4, 2).sum(axis=1) == 2))

    def test_all_position_pairs(self):
        for a, b in itertools.combinations(range(4), 2):
            weight = np.zeros((4, 3), np.int8)
            weight[a] = [-128, 127, 0]
            weight[b] = [11, -13, 0]
            pos = np.tile(np.array([a, b], np.uint8), (1, 3, 1))
            values = pack(weight, pos)
            np.testing.assert_array_equal(unpack(values, pos), weight)
            x = np.array([[-128, -1, 0, 127], [127, 127, -128, -128]], np.int8)
            np.testing.assert_array_equal(slot_gemm(x, values, pos), dense_golden(x, weight))

    def test_zero_slots_are_legal(self):
        pruned, positions, mask = prune(np.zeros((8, 2), np.float32))
        quantized, scale = quantize_weight(pruned)
        np.testing.assert_array_equal(scale, np.ones((1, 2), np.float32))
        self.assertEqual(mask.sum(), 8)
        np.testing.assert_array_equal(unpack(pack(quantized, positions), positions), quantized)

    def test_round_nearest_even_and_signed_values(self):
        weight = np.array([[127], [0.5], [1.5], [-2.5], [-127], [-0.5], [-1.5], [2.5]], np.float32)
        q, scale = quantize_weight(weight)
        np.testing.assert_array_equal(scale, np.ones((1, 1), np.float32))
        np.testing.assert_array_equal(q[:, 0], [127, 0, 2, -2, -127, 0, -2, 2])

    def test_actual_model_shapes(self):
        rng = np.random.default_rng(20261021)
        for k, n in ((192, 576), (576, 192)):
            weight = rng.normal(size=(k, n)).astype(np.float32)
            pruned, positions, mask = prune(weight)
            q, _ = quantize_weight(pruned)
            values = pack(q, positions)
            np.testing.assert_array_equal(unpack(values, positions), q)
            self.assertEqual(int(mask.sum()) * 2, weight.size)
            x = rng.integers(-128, 128, (5, k), dtype=np.int8)
            np.testing.assert_array_equal(slot_gemm(x, values, positions), dense_golden(x, q))

    def test_invalid_inputs(self):
        for weight in (np.zeros((5, 2), np.float32), np.zeros((4, 2), np.float64),
                       np.full((4, 2), np.nan, np.float32), np.full((4, 2), np.inf, np.float32),
                       np.zeros((4, 0), np.float32)):
            with self.assertRaises(ValueError):
                prune(weight)
        q = np.ones((4, 1), np.int8)
        for positions in (np.array([[[1, 1]]], np.uint8), np.array([[[0, 4]]], np.uint8),
                          np.array([[[1, 0]]], np.uint8), np.array([[[0, 1]]], np.int32)):
            with self.assertRaises(ValueError):
                pack(q, positions)
        with self.assertRaises(ValueError):
            pack(q, np.array([[[0, 1]]], np.uint8))

    def test_int32_overflow_guard(self):
        # A complete full-K sum exceeding INT32 must be rejected, not wrapped.
        x = np.full((1, 140000), -128, np.int8)
        w = np.full((140000, 1), -128, np.int8)
        with self.assertRaises(ValueError):
            dense_golden(x, w)


if __name__ == "__main__":
    unittest.main(verbosity=2)
