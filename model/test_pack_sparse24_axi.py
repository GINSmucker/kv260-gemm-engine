"""Independent synthetic contract tests for SPLIT128; no model or board access."""
from itertools import combinations
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

import pack_sparse24_axi as wire


ERRORS = (ValueError, TypeError, OverflowError)
PAIRS = tuple(combinations(range(4), 2))


def independent_weight(values, positions):
    """Slow scalar reconstruction, independent of the production decoder."""
    groups, columns, _ = values.shape
    result = np.zeros((4 * groups, columns), dtype=np.int8)
    for group in range(groups):
        for column in range(columns):
            for slot in range(2):
                result[4 * group + int(positions[group, column, slot]), column] = \
                    values[group, column, slot]
    return result


def independent_blobs(values, positions):
    """The contract expressed using scalar byte/nibble indexes, not pack APIs."""
    records = values.shape[0] * values.shape[1]
    data = bytearray(16 * ((2 * records + 15) // 16))
    meta = bytearray(16 * (((records + 1) // 2 + 15) // 16))
    columns = values.shape[1]
    for record in range(records):
        group, column = divmod(record, columns)
        for slot in range(2):
            data[2 * record + slot] = int(values[group, column, slot]) & 255
        p0, p1 = map(int, positions[group, column])
        meta[record // 2] |= (p0 | (p1 << 2)) << (4 * (record % 2))
    return bytes(data), bytes(meta)


def slots(groups, columns):
    count = groups * columns
    positions = np.array([PAIRS[i % 6] for i in range(count)], dtype=np.uint8)
    positions = positions.reshape(groups, columns, 2)
    values = ((np.arange(2 * count, dtype=np.int32) * 37) % 256 - 128)
    return values.astype(np.int8).reshape(groups, columns, 2), positions


class TestWireByteContract(unittest.TestCase):
    def test_format_id_and_alignment(self):
        self.assertEqual(wire.FORMAT_ID, "H576_SPARSE24_SPLIT128_V1")
        for original, expected in ((0, 0), (1, 16), (15, 16), (16, 16),
                                   (17, 32), (31, 32), (32, 32)):
            self.assertEqual(wire.align16(original), expected)
        for bad in (-1, 1.5, "16", True):
            with self.subTest(bad=bad), self.assertRaises(ERRORS):
                wire.align16(bad)

    def test_document_fixed_byte_vector_and_signed_results(self):
        x = np.array([[1, 2, 3, 4]], dtype=np.int8)
        values = np.array([[[-5, 7], [3, -2]]], dtype=np.int8)
        positions = np.array([[[1, 3], [0, 2]]], dtype=np.uint8)
        weight = np.array([[0, 3], [-5, 0], [0, -2], [7, 0]], dtype=np.int8)
        data, meta = wire.pack_sparse(values, positions)
        self.assertEqual(wire.pack_int8(x), bytes.fromhex("01020304") + bytes(12))
        self.assertEqual(data, bytes.fromhex("fb0703fe") + bytes(12))
        self.assertEqual(meta, bytes.fromhex("8d") + bytes(15))
        restored_values, restored_positions, restored_w = wire.unpack_sparse(data, meta, 4, 2)
        np.testing.assert_array_equal(restored_values, values)
        np.testing.assert_array_equal(restored_positions, positions)
        np.testing.assert_array_equal(restored_w, weight)
        expected = np.array([[18, -3]], dtype=np.int32)
        np.testing.assert_array_equal(wire.golden_dense_int32(x, weight), expected)
        np.testing.assert_array_equal(wire.emulate_axi(wire.pack_int8(x), data, meta, 1, 4, 2, 1), expected)
        self.assertEqual(wire.pack_int32(expected), bytes.fromhex("12000000fdffffff") + bytes(8))

    def test_all_six_position_codes_and_retained_zero_slots(self):
        positions = np.array(PAIRS, dtype=np.uint8).reshape(1, 6, 2)
        values = np.array([[[-128, 127], [0, 0], [-1, 0], [0, -128],
                            [127, -127], [-6, 9]]], dtype=np.int8)
        data, meta = wire.pack_sparse(values, positions)
        self.assertEqual(meta[:3], bytes.fromhex("84 9c ed"))
        self.assertEqual((data, meta), independent_blobs(values, positions))
        actual_values, actual_positions, actual_w = wire.unpack_sparse(data, meta, 4, 6)
        np.testing.assert_array_equal(actual_values, values)
        np.testing.assert_array_equal(actual_positions, positions)
        np.testing.assert_array_equal(actual_w, independent_weight(values, positions))
        x = np.array([[-128, -1, 0, 127], [127, 0, -128, -1]], dtype=np.int8)
        expected = x.astype(np.int64) @ actual_w.astype(np.int64)
        np.testing.assert_array_equal(wire.emulate_axi(wire.pack_int8(x), data, meta, 2, 4, 6, 1), expected)

    def test_values_and_metadata_beat_transitions_7_8_and_31_32(self):
        values, positions = slots(1, 35)
        data, meta = wire.pack_sparse(values, positions)
        self.assertEqual((data, meta), independent_blobs(values, positions))
        for record in (7, 8, 31, 32, 34):
            with self.subTest(record=record):
                self.assertEqual(data[2 * record:2 * record + 2],
                                 bytes(int(v) & 255 for v in values[0, record]))
                code = (meta[record // 2] >> (4 * (record % 2))) & 15
                self.assertEqual(code, int(positions[0, record, 0]) | (int(positions[0, record, 1]) << 2))
        np.testing.assert_array_equal(wire.unpack_sparse(data, meta, 4, 35)[2],
                                      independent_weight(values, positions))

    def test_odd_record_count_zero_high_nibble_and_final_beat(self):
        for count in (1, 3, 7, 9, 15, 17, 31, 33):
            with self.subTest(count=count):
                values, positions = slots(1, count)
                data, meta = wire.pack_sparse(values, positions)
                self.assertEqual(meta[(count - 1) // 2] >> 4, 0)
                self.assertEqual(data[2 * count:], bytes(len(data) - 2 * count))
                logical_meta = (count + 1) // 2
                self.assertEqual(meta[logical_meta:], bytes(len(meta) - logical_meta))
                # Real t=0 has code 4; padding code 0 must not be decoded as a record.
                np.testing.assert_array_equal(wire.unpack_sparse(data, meta, 4, count)[1], positions)

    def test_no_row_or_group_padding_and_non_contiguous_input(self):
        x = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int8)
        self.assertEqual(wire.pack_int8(x), bytes((1, 2, 3, 4, 5, 6)) + bytes(10))
        strided = np.arange(12, dtype=np.int8).reshape(2, 6)[:, ::2]
        self.assertFalse(strided.flags.c_contiguous)
        self.assertEqual(wire.pack_int8(strided), bytes((0, 2, 4, 6, 8, 10)) + bytes(10))
        values, positions = slots(2, 3)
        data, meta = wire.pack_sparse(values, positions)
        self.assertEqual(len(data), 16)  # Not two separately padded G rows.
        self.assertEqual((data, meta), independent_blobs(values, positions))
        np.testing.assert_array_equal(wire.unpack_int8(wire.pack_int8(strided), 2, 3), strided)

    def test_int32_little_endian_boundaries_and_tail_lanes(self):
        array = np.array([[0, -1, -2147483648], [2147483647, 18, -3]], dtype=np.int32)
        packed = wire.pack_int32(array)
        reference = b"".join(int(v).to_bytes(4, "little", signed=True) for v in array.flat) + bytes(8)
        self.assertEqual(packed, reference)
        np.testing.assert_array_equal(wire.unpack_int32(packed, 2, 3), array)


class TestWireInvalidInputs(unittest.TestCase):
    def test_invalid_positions_and_shapes(self):
        values, positions = slots(1, 2)
        bad_positions = []
        for pair in ((0, 0), (3, 1), (0, 4), (4, 5)):
            bad = positions.copy()
            bad[0, 0] = pair
            bad_positions.append(bad)
        bad_positions += [positions.astype(np.int32), positions[:, :, :1], positions[:, :1]]
        for bad in bad_positions:
            with self.subTest(shape=bad.shape, dtype=bad.dtype), self.assertRaises(ERRORS):
                wire.pack_sparse(values, bad)
        for bad in (values.astype(np.uint8), values.astype(np.int32), values[:, :, :1],
                    values.reshape(1, 4), np.empty((0, 2, 2), dtype=np.int8)):
            with self.subTest(shape=bad.shape, dtype=bad.dtype), self.assertRaises(ERRORS):
                wire.pack_sparse(bad, positions)

    def test_each_illegal_real_metadata_code_is_rejected(self):
        values, positions = slots(1, 2)
        data, metadata = wire.pack_sparse(values, positions)
        for code in set(range(16)) - {4, 8, 12, 9, 13, 14}:
            bad = bytearray(metadata)
            bad[0] = (bad[0] & 240) | code
            with self.subTest(code=code), self.assertRaises(ERRORS):
                wire.unpack_sparse(data, bytes(bad), 4, 2)

    def test_short_and_long_buffers_are_rejected(self):
        values, positions = slots(1, 3)
        data, meta = wire.pack_sparse(values, positions)
        for bad in (data[:-1], data + bytes(16)):
            with self.subTest(kind="values", length=len(bad)), self.assertRaises(ERRORS):
                wire.unpack_sparse(bad, meta, 4, 3)
        for bad in (meta[:-1], meta + bytes(16)):
            with self.subTest(kind="metadata", length=len(bad)), self.assertRaises(ERRORS):
                wire.unpack_sparse(data, bad, 4, 3)
        for decoder, size in ((wire.unpack_int8, 1), (wire.unpack_int32, 4)):
            correct = bytes(16)
            for bad in (correct[:-1], correct + bytes(16)):
                with self.subTest(size=size, length=len(bad)), self.assertRaises(ERRORS):
                    decoder(bad, 1, 3)

    def test_nonzero_padding_and_unused_high_nibble_rejected(self):
        values, positions = slots(1, 3)
        data, meta = wire.pack_sparse(values, positions)
        bad_data = bytearray(data)
        bad_data[-1] = 1
        with self.assertRaises(ERRORS):
            wire.unpack_sparse(bytes(bad_data), meta, 4, 3)
        for offset, added in ((-1, 1), (1, 0x40)):
            bad_meta = bytearray(meta)
            bad_meta[offset] |= added
            with self.subTest(offset=offset), self.assertRaises(ERRORS):
                wire.unpack_sparse(data, bytes(bad_meta), 4, 3)
        for decoder in (wire.unpack_int8, wire.unpack_int32):
            dirty = bytearray(16)
            dirty[-1] = 1
            with self.subTest(decoder=decoder.__name__), self.assertRaises(ERRORS):
                decoder(bytes(dirty), 1, 1)

    def test_wrong_matrix_dtype_shape_or_empty_input(self):
        for bad in (np.ones((2, 3), dtype=np.uint8), np.ones((2, 3), dtype=np.int32),
                    np.ones(6, dtype=np.int8), np.ones((1, 2, 3), dtype=np.int8),
                    np.empty((0, 3), dtype=np.int8)):
            with self.subTest(shape=bad.shape, dtype=bad.dtype), self.assertRaises(ERRORS):
                wire.pack_int8(bad)
        for bad in (np.ones((2, 3), dtype=np.int64), np.ones(6, dtype=np.int32),
                    np.empty((2, 0), dtype=np.int32)):
            with self.subTest(shape=bad.shape, dtype=bad.dtype), self.assertRaises(ERRORS):
                wire.pack_int32(bad)
        for rows, columns in ((0, 1), (1, 0), (-1, 2), (True, 2), (1.5, 2)):
            with self.subTest(rows=rows, columns=columns), self.assertRaises(ERRORS):
                wire.unpack_int8(bytes(16), rows, columns)
        for k, n in ((0, 1), (3, 2), (4, 0), (-4, 2), (4, True)):
            with self.subTest(k=k, n=n), self.assertRaises(ERRORS):
                wire.unpack_sparse(bytes(16), bytes(16), k, n)


class TestGoldenAndEmulation(unittest.TestCase):
    def test_dense_ignores_metadata_object_completely(self):
        class ForbiddenMetadata:
            def __bytes__(self):
                raise AssertionError("Dense read metadata")
            def __len__(self):
                raise AssertionError("Dense measured metadata")
            def __getitem__(self, key):
                raise AssertionError("Dense indexed metadata")
        x = np.array([[1, -2, 3], [-128, 127, 0]], dtype=np.int8)
        weight = np.array([[1, 2, 3, 4, 5], [5, 4, 3, 2, 1], [-1, -2, -3, -4, -5]], dtype=np.int8)
        expected = (x.astype(np.int64) @ weight.astype(np.int64)).astype(np.int32)
        actual = wire.emulate_axi(wire.pack_int8(x), wire.pack_int8(weight),
                                  ForbiddenMetadata(), 2, 3, 5, 0)
        self.assertEqual(actual.dtype, np.dtype(np.int32))
        np.testing.assert_array_equal(actual, expected)

    def test_dense_and_sparse_match_independent_full_k_golden(self):
        rng = np.random.default_rng(20261022)
        for m, k, n in ((1, 4, 1), (3, 8, 5), (5, 12, 17), (2, 4, 33),
                        (3, 20, 7), (1, 16, 9)):
            values, positions = slots(k // 4, n)
            weight = independent_weight(values, positions)
            x = rng.integers(-128, 128, size=(m, k), dtype=np.int16).astype(np.int8)
            expected = (x.astype(np.int64) @ weight.astype(np.int64)).astype(np.int32)
            data, meta = wire.pack_sparse(values, positions)
            with self.subTest(shape=(m, k, n)):
                np.testing.assert_array_equal(wire.golden_dense_int32(x, weight), expected)
                np.testing.assert_array_equal(wire.emulate_axi(wire.pack_int8(x), data, meta, m, k, n, 1), expected)
                np.testing.assert_array_equal(wire.emulate_axi(wire.pack_int8(x), wire.pack_int8(weight), None, m, k, n, 0), expected)

    def test_golden_checks_dtype_shape_and_actual_int32_overflow(self):
        for x, w in ((np.ones((1, 4), dtype=np.uint8), np.ones((4, 2), dtype=np.int8)),
                     (np.ones((1, 4), dtype=np.int8), np.ones((4, 2), dtype=np.int32)),
                     (np.ones((1, 3), dtype=np.int8), np.ones((4, 2), dtype=np.int8)),
                     (np.ones(4, dtype=np.int8), np.ones((4, 2), dtype=np.int8))):
            with self.assertRaises(ERRORS):
                wire.golden_dense_int32(x, w)
        # Exact 2^31 positive result: must reject, not wrap to INT32_MIN.
        x = np.full((1, 131072), -128, dtype=np.int8)
        w = np.full((131072, 1), -128, dtype=np.int8)
        with self.assertRaises(ERRORS):
            wire.golden_dense_int32(x, w)

    def test_illegal_modes_and_sparse_incomplete_k_rejected(self):
        for mode in (-1, 2, "1", True):
            with self.subTest(mode=mode), self.assertRaises(ERRORS):
                wire.emulate_axi(bytes(16), bytes(16), bytes(16), 1, 4, 1, mode)
        with self.assertRaises(ERRORS):
            wire.emulate_axi(bytes(16), bytes(16), bytes(16), 1, 3, 1, 1)

    def test_emulator_independently_rejects_real_illegal_code_and_dirty_input_tail(self):
        values, positions = slots(1, 3)
        data, meta = wire.pack_sparse(values, positions)
        x = wire.pack_int8(np.array([[1, 2, 3, 4]], dtype=np.int8))
        illegal_meta = bytearray(meta)
        illegal_meta[0] &= 240
        with self.assertRaises(ERRORS):
            wire.emulate_axi(x, data, bytes(illegal_meta), 1, 4, 3, 1)
        dirty_x = bytearray(x)
        dirty_x[-1] = 1
        with self.assertRaises(ERRORS):
            wire.emulate_axi(bytes(dirty_x), data, meta, 1, 4, 3, 1)


class TestHostBoundary(unittest.TestCase):
    def test_buffer_lengths_formula_and_dense_metadata_placeholder(self):
        sparse = wire.buffer_layout(3, 12, 5, 1)
        expected = {"X": (36, 48, 3), "W_data": (30, 32, 2),
                    "W_meta": (8, 16, 1), "Y": (60, 64, 4)}
        for name, (logical, allocated, beats) in expected.items():
            self.assertEqual(sparse[name]["logical_bytes"], logical)
            self.assertEqual(sparse[name]["allocated_bytes"], allocated)
            self.assertEqual(sparse[name]["beat_count"], beats)
        dense = wire.buffer_layout(2, 3, 5, 0)
        self.assertEqual(dense["W_data"]["logical_bytes"], 15)
        self.assertEqual(dense["W_meta"]["logical_bytes"], 0)
        self.assertEqual(dense["W_meta"]["allocated_bytes"], 16)
        self.assertEqual(dense["W_meta"]["beat_count"], 1)

    def test_validate_good_call_and_shapes_whitelist(self):
        layout = wire.buffer_layout(3, 12, 5, 1)
        addresses = dict(X=0x1000, W_data=0x2000, W_meta=0x3000, Y=0x4000)
        lengths = {name: item["allocated_bytes"] for name, item in layout.items()}
        self.assertIsNone(wire.validate_call(3, 12, 5, 1, addresses, lengths, {(3, 12, 5)}))
        bigger_lengths = {name: length + 16 for name, length in lengths.items()}
        self.assertIsNone(wire.validate_call(3, 12, 5, 1, addresses, bigger_lengths))
        with self.assertRaises(ERRORS):
            wire.validate_call(3, 12, 5, 1, addresses, lengths, {(1, 4, 2)})

    def test_validate_rejects_bad_addresses_short_lengths_overlap_and_wrap(self):
        layout = wire.buffer_layout(3, 12, 5, 1)
        addresses = dict(X=0x1000, W_data=0x2000, W_meta=0x3000, Y=0x4000)
        lengths = {name: item["allocated_bytes"] for name, item in layout.items()}
        for bad_address in (0, -64, 0x1001, 1 << 64, True, 4096.0):
            bad = dict(addresses, X=bad_address)
            with self.subTest(address=bad_address), self.assertRaises(ERRORS):
                wire.validate_call(3, 12, 5, 1, bad, lengths)
        for name in layout:
            bad = dict(lengths)
            bad[name] -= 1
            with self.subTest(short=name), self.assertRaises(ERRORS):
                wire.validate_call(3, 12, 5, 1, addresses, bad)
        with self.assertRaises(ERRORS):
            wire.validate_call(3, 12, 5, 1, dict(addresses, Y=addresses["X"]), lengths)
        with self.assertRaises(ERRORS):
            wire.validate_call(3, 12, 5, 1, dict(addresses, X=(1 << 64) - 64),
                               dict(lengths, X=128))
        with self.assertRaises(ERRORS):
            wire.validate_call(3, 12, 5, 1, {k: v for k, v in addresses.items() if k != "W_meta"}, lengths)
        with self.assertRaises(ERRORS):
            wire.validate_call(3, 12, 5, 1, addresses, dict(lengths, W_meta=0))

    def test_conservative_accumulation_bound_and_size_overflow(self):
        with self.assertRaises(ERRORS):
            wire.buffer_layout(1, 131072, 1, 0)
        with self.assertRaises(ERRORS):
            wire.buffer_layout(1 << 63, 4, 4, 1)

    def test_export_refuses_existing_directory_before_reading_missing_source(self):
        with tempfile.TemporaryDirectory(prefix="test_split128_") as directory:
            root = Path(directory)
            output = root / "candidates" / "existing"
            output.mkdir(parents=True)
            sentinel = output / "keep.bin"
            sentinel.write_bytes(b"DO NOT OVERWRITE")
            with mock.patch.object(wire, "ROOT", root):
                with self.assertRaises((ValueError, FileExistsError)):
                    wire.export_package(root / "nonexistent_candidate", output)
            self.assertEqual(sentinel.read_bytes(), b"DO NOT OVERWRITE")
            self.assertEqual(sorted(p.name for p in output.iterdir()), ["keep.bin"])


class TestPackageRegression(unittest.TestCase):
    def test_layer_source_metadata_valid_and_each_missing_or_corrupt_field_rejected(self):
        source = {
            "inventory": {"layer_index": 26, "layer_id": "blocks.6.mlp.fc1",
                          "family": "fc1", "shape_MKN": [197, 192, 576]},
            "weight_source": "weights/layer_26.npz",
            "case_source": "cases/layer_26.npz",
            "weight_sha256": "a" * 64,
            "case_sha256": "b" * 64,
        }
        layer = {
            "layer_index": 26, "layer_id": "blocks.6.mlp.fc1", "family": "fc1",
            "shape_MKN": [197, 192, 576],
            "source_weight_npz": "weights/layer_26.npz",
            "source_case_npz": "cases/layer_26.npz",
            "source_weight_sha256": "a" * 64,
            "source_case_sha256": "b" * 64,
        }
        source_before, layer_before = copy.deepcopy(source), copy.deepcopy(layer)
        self.assertIsNone(wire._verify_layer_metadata(layer, source))
        self.assertEqual(source, source_before)
        self.assertEqual(layer, layer_before)
        for name in layer:
            missing = copy.deepcopy(layer)
            del missing[name]
            with self.subTest(field=name, fault="missing"), self.assertRaises(ERRORS):
                wire._verify_layer_metadata(missing, source)
            polluted = copy.deepcopy(layer)
            polluted[name] = {"layer_index": 27, "shape_MKN": [197, 576, 192]}.get(name, "CORRUPTED")
            with self.subTest(field=name, fault="corrupt"), self.assertRaises(ERRORS):
                wire._verify_layer_metadata(polluted, source)

    def test_export_success_with_broken_stdout_still_exits_zero_and_writes_no_failure(self):
        manifest = {"status": "PASS_SYNTHETIC_COMMIT", "layers": [], "cases": []}
        with tempfile.TemporaryDirectory(prefix="test_split128_stdout_") as directory:
            root = Path(directory)
            output = root / "candidates" / "new_package"
            candidate = root / "source_candidate"
            with mock.patch.object(wire, "export_package", return_value=manifest) as export, \
                    mock.patch.object(wire, "_write_json") as write_json, \
                    mock.patch("builtins.print", side_effect=BrokenPipeError("closed stdout")) as present:
                result = wire.main(["export", "--candidate", str(candidate), "--output-dir", str(output)])
            self.assertEqual(result, 0)
            export.assert_called_once_with(candidate, output)
            present.assert_called_once()
            write_json.assert_not_called()
            self.assertFalse((output / "failure.json").exists())


if __name__ == "__main__":
    unittest.main()
