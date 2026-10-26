"""CPU-only byte layout and build-preflight tests."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import offline_checks as o
import build_arm as b

ROOT = Path(__file__).resolve().parent


class OfflineTests(unittest.TestCase):
    def test_dense_and_all_six_sparse_codes(self):
        pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
        for lo, hi in pairs:
            code = lo | hi << 2
            record = {'route': 'PL', 'k': 4, 'n': 2, 'mode': 1,
                      'w_data_offset': 0, 'w_data_bytes': 4, 'w_meta_offset': 0, 'w_meta_bytes': 1}
            decoded = o.decode_pl(record, bytes([128, 0, 7, 127]), bytes([code | code << 4]))
            want = np.zeros((4, 2), np.int8)
            want[lo] = [-128, 7]; want[hi] = [0, 127]
            np.testing.assert_array_equal(decoded, want)
            dense = {**record, 'mode': 0, 'w_data_bytes': 8, 'w_meta_bytes': 16}
            np.testing.assert_array_equal(o.decode_pl(dense, want.tobytes(), bytes(16)), want)

    def test_bad_codes_shapes_and_ranges_fail(self):
        record = {'route': 'PL', 'k': 4, 'n': 2, 'mode': 1,
                  'w_data_offset': 0, 'w_data_bytes': 4, 'w_meta_offset': 0, 'w_meta_bytes': 1}
        for changed, data, meta in (({}, bytes(4), b'\x00'), ({'mode': 2}, bytes(4), b'\x44'),
                                    ({'w_data_offset': 1}, bytes(4), b'\x44'),
                                    ({'k': True}, bytes(4), b'\x44'), ({'route': 'PS'}, bytes(4), b'\x44')):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                o.decode_pl({**record, **changed}, data, meta)

    def test_int8_negative_values_and_int64_accumulation(self):
        x = np.array([[-128, 127, -1, 2]], np.int8)
        w = np.array([[-128, 0], [127, 3], [-4, -2], [5, -7]], np.int8)
        np.testing.assert_array_equal(o.integer_golden(x, w), np.array([[32527, 369]], dtype='<i4'))
        with self.assertRaises(ValueError): o.integer_golden(x.astype(np.float32), w)
        x = np.full((1, 140000), -128, np.int8); w = x.T.copy()
        with self.assertRaisesRegex(ValueError, 'overflow'): o.integer_golden(x, w)

    def test_actual_model_preflight_does_not_run_a_compiler_or_hardware(self):
        with mock.patch.object(b.subprocess, 'run') as process:
            model = b.verify_model(ROOT / 'generated_model_v1')
        process.assert_not_called()
        self.assertEqual(model['routes']['PL_dense'], 24)
        self.assertEqual(model['routes']['PL_sparse24'], 12)
        self.assertFalse(model['hardware_executed'])

    def test_compile_refuses_windows_before_process_or_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            output = Path(temporary) / 'new_build'
            with mock.patch.object(b.os, 'name', 'nt'), mock.patch.object(b.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, 'ARM Linux'):
                    b.build(ROOT / 'generated_model_v1', output)
            process.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
