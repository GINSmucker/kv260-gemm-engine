"""SPLIT128 v1 pack/unpack, AXI-byte emulation and independent integer golden.

Only NumPy + the Python standard library are required. No ONNX execution,
training, downloads, hardware access or changes to the source candidate.
Export writes an exclusively NEW directory; verify performs no file writes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
from numbers import Integral
from pathlib import Path
import platform
import sys
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parent
FORMAT_ID = 'H576_SPARSE24_SPLIT128_V1'
FORMAT_DOC = 'docs/sparse24_axi_format_v0.1.md'
FORMAT_DOC_SHA256 = 'a5fd29d02914c1cf5b2cfa61f19d6373594d70ed3277cd4f2d404e53206bdbb4'
CANDIDATE_SHA256 = '86ef8803d667ef8fd2e959822abaa4e84d7655f28c381b452b0b19e8f8ae8528'
SELECTED = (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47)
STATUS = 'PASS_PYTHON_AXI_FORMAT_AND_INTEGER_GOLDEN_NOT_HLS'
UINT64_MAX = (1 << 64) - 1
INT32_MAX = (1 << 31) - 1
BEAT_BYTES = 16


def require(ok, message):
    if not ok:
        raise ValueError(message)


def integer(value, name, minimum=0):
    require(isinstance(value, Integral) and not isinstance(value, (bool, np.bool_)),
            name + ' must be an integer, not bool/float')
    value = int(value)
    require(minimum <= value <= UINT64_MAX, name + ' outside allowed range')
    return value


def align16(n):
    n = integer(n, 'length')
    result = ((n + 15) // 16) * 16
    require(result <= UINT64_MAX, 'Aligned length overflows UINT64')
    return result


def buffer_layout(m, k, n, mode):
    m, k, n = (integer(v, name, 1) for v, name in ((m, 'M'), (k, 'K'), (n, 'N')))
    mode = integer(mode, 'mode')
    require(mode in (0, 1), 'mode must be Dense=0 or Sparse24=1')
    require(k * 16384 <= INT32_MAX, 'K exceeds conservative INT32 accumulation bound')
    require(mode == 0 or k % 4 == 0, 'Sparse K must be divisible by 4')
    t = (k // 4) * n
    sizes = {'X': m * k, 'W_data': k * n if mode == 0 else 2 * t,
             'W_meta': 0 if mode == 0 else (t + 1) // 2, 'Y': 4 * m * n}
    result = {}
    for name, logical in sizes.items():
        require(logical <= UINT64_MAX, 'Buffer length overflows UINT64')
        allocated = 16 if name == 'W_meta' and mode == 0 else align16(logical)
        result[name] = {'logical_bytes': logical, 'allocated_bytes': allocated,
                        'beat_count': allocated // 16, 'alignment_bytes': 64}
    return result


def validate_call(m, k, n, mode, addresses, lengths, supported_shapes=None):
    """Validate a proposed Host descriptor; does not allocate/touch physical RAM.

    This conservative checker forbids overlapping BO regions. A None whitelist
    validates only the software format, NOT an unspecified hardware capacity.
    """
    layout = buffer_layout(m, k, n, mode)
    if supported_shapes is not None:
        require((int(m), int(k), int(n)) in supported_shapes, 'Shape not supported by this kernel')
    require(set(addresses) == set(lengths) == set(layout), 'Incomplete/unknown buffer descriptor')
    regions = []
    for name, info in layout.items():
        address = integer(addresses[name], name + ' address', 1)
        size = integer(lengths[name], name + ' allocation', 1)
        require(address % 64 == 0, name + ' address must be 64-byte aligned')
        require(size >= info['allocated_bytes'], name + ' allocation is too short')
        end = address + size
        require(end - 1 <= UINT64_MAX, name + ' address range overflows UINT64')
        regions.append((address, end, name))
    for a, b in itertools.combinations(regions, 2):
        require(a[1] <= b[0] or b[1] <= a[0], 'Overlapping buffer regions: ' + a[2] + '/' + b[2])


def matrix(value, dtype, name):
    require(isinstance(value, np.ndarray) and value.dtype == np.dtype(dtype)
            and value.ndim == 2 and min(value.shape) > 0, name + ' has wrong dtype/shape')
    return value


def _padding(raw):
    return raw + bytes(align16(len(raw)) - len(raw))


def _blob(blob, logical, name):
    require(isinstance(blob, (bytes, bytearray, memoryview)), name + ' must be bytes-like')
    blob = bytes(blob)
    require(len(blob) == align16(logical), name + ' must have the exact canonical padded length')
    require(not any(blob[logical:]), name + ' tail padding must be zero')
    return blob


def pack_int8(array2d):
    matrix(array2d, np.int8, 'INT8 matrix')
    return _padding(array2d.tobytes(order='C'))


def unpack_int8(blob, rows, cols):
    rows, cols = integer(rows, 'rows', 1), integer(cols, 'cols', 1)
    logical = rows * cols
    require(logical <= UINT64_MAX, 'INT8 matrix length overflow')
    blob = _blob(blob, logical, 'INT8 matrix')
    return np.frombuffer(blob, np.int8, count=logical).reshape(rows, cols).copy()


def pack_int32(array2d):
    matrix(array2d, np.int32, 'INT32 matrix')
    return _padding(array2d.astype('<i4', copy=False).tobytes(order='C'))


def unpack_int32(blob, rows, cols):
    rows, cols = integer(rows, 'rows', 1), integer(cols, 'cols', 1)
    logical = 4 * rows * cols
    require(logical <= UINT64_MAX, 'INT32 matrix length overflow')
    blob = _blob(blob, logical, 'INT32 matrix')
    return np.frombuffer(blob, '<i4', count=rows * cols).reshape(rows, cols).astype(np.int32)


def _slots(values, positions):
    require(isinstance(values, np.ndarray) and values.dtype == np.int8
            and values.ndim == 3 and values.shape[-1] == 2
            and min(values.shape[:2]) > 0, 'Expected INT8 values[G,N,2]')
    require(isinstance(positions, np.ndarray) and positions.dtype == np.uint8
            and positions.shape == values.shape, 'Expected UINT8 positions[G,N,2]')
    require(np.all(positions < 4) and np.all(positions[..., 0] < positions[..., 1]),
            'Positions must be distinct, ascending offsets 0..3')


def pack_sparse(values, positions):
    _slots(values, positions)
    buffer_layout(1, 4 * values.shape[0], values.shape[1], 1)
    codes = (positions[..., 0] | (positions[..., 1] << 2)).ravel(order='C')
    metadata = np.zeros((codes.size + 1) // 2, np.uint8)
    metadata[:] = codes[::2]
    metadata[:codes[1::2].size] |= codes[1::2] << 4
    return _padding(values.tobytes(order='C')), _padding(metadata.tobytes())


def _decode_records(data_blob, meta_blob, k, n):
    """Use explicit 128-bit words/bit positions, independent of byte-array packing."""
    layout = buffer_layout(1, k, n, 1)
    k, n = int(k), int(n)
    total = (k // 4) * n
    data = _blob(data_blob, layout['W_data']['logical_bytes'], 'Sparse W_data')
    meta = _blob(meta_blob, layout['W_meta']['logical_bytes'], 'Sparse W_meta')
    require(total % 2 == 0 or meta[total // 2] >> 4 == 0, 'Unused high metadata nibble must be zero')
    words_v = [int.from_bytes(data[i:i+16], 'little') for i in range(0, len(data), 16)]
    words_p = [int.from_bytes(meta[i:i+16], 'little') for i in range(0, len(meta), 16)]
    values = np.empty((k // 4, n, 2), np.int8)
    positions = np.empty((k // 4, n, 2), np.uint8)
    for t in range(total):
        code = (words_p[t // 32] >> (4 * (t % 32))) & 15
        p0, p1 = code & 3, (code >> 2) & 3
        require(p0 < p1, 'Invalid position code at record ' + str(t))
        pair = (words_v[t // 8] >> (16 * (t % 8))) & 65535
        a, b = pair & 255, (pair >> 8) & 255
        g, col = divmod(t, n)
        values[g, col] = [a - 256 if a >= 128 else a, b - 256 if b >= 128 else b]
        positions[g, col] = [p0, p1]
    return values, positions


def unpack_sparse(data_blob, meta_blob, k, n):
    values, positions = _decode_records(data_blob, meta_blob, k, n)
    k, n = int(k), int(n)
    weight = np.zeros((k, n), np.int8)
    groups, columns = 4 * np.arange(k // 4)[:, None], np.arange(n)[None, :]
    for s in (0, 1):
        weight[groups + positions[..., s], columns] = values[..., s]
    return values, positions, weight


def _int32(output):
    limits = np.iinfo(np.int32)
    require(np.all(output >= limits.min) and np.all(output <= limits.max), 'INT32 overflow; no wrapping/saturation')
    return output.astype(np.int32)


def golden_dense_int32(x, w):
    """Full-K INT64 oracle: reads ORIGINAL X/W arrays; never reads slots/AXI bytes."""
    matrix(x, np.int8, 'Golden X')
    matrix(w, np.int8, 'Golden W')
    require(x.shape[1] == w.shape[0], 'Golden X/W K mismatch')
    buffer_layout(x.shape[0], x.shape[1], w.shape[1], 0)
    return _int32(x.astype(np.int64) @ w.astype(np.int64))


def emulate_axi(x_blob, w_data_blob, w_meta_blob, m, k, n, mode):
    """Python AXI-word decoder + arithmetic, NOT an RTL or performance simulator.

    Dense mode deliberately never inspects w_meta_blob, not even its length.
    Sparse computes selected-X gather/MACs, not a full reconstructed-W matmul.
    """
    buffer_layout(m, k, n, mode)
    m, k, n, mode = map(int, (m, k, n, mode))
    x = unpack_int8(x_blob, m, k).astype(np.int64)
    if mode == 0:
        w = unpack_int8(w_data_blob, k, n).astype(np.int64)
        return _int32(x @ w)
    values, positions = _decode_records(w_data_blob, w_meta_blob, k, n)
    output = np.zeros((m, n), np.int64)
    for g in range(k // 4):
        for s in (0, 1):
            output += x[:, 4 * g + positions[g, :, s]] * values[g, :, s].astype(np.int64)
    return _int32(output)


def sha_file(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _json_read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write_json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def _write_bytes(path, value):
    with Path(path).open('xb') as stream:
        stream.write(value)


def _inside(root, name):
    require(isinstance(name, str) and not Path(name).is_absolute(), 'Artifact name must be relative')
    path = (root / name).resolve(strict=True)
    require(path.is_relative_to(root.resolve()) and path.is_file(), 'Artifact escapes its package')
    return path


def _check_files(root, records):
    require(isinstance(records, dict) and records, 'Empty artifact manifest')
    for name, record in records.items():
        path = _inside(root, name)
        require(path.stat().st_size == record['bytes'] and sha_file(path) == record['sha256'],
                'Artifact identity mismatch: ' + name)


def _source(candidate):
    candidate = candidate.resolve(strict=True)
    path = candidate / 'candidate.json'
    require(sha_file(path) == CANDIDATE_SHA256, 'Not the frozen c062 source candidate')
    manifest = _json_read(path)
    require(manifest['schema'] == 'h576_sparse24_mlp12_candidate_v1'
            and manifest['status'] == 'EXPORTED_AND_REPRODUCED_DEVELOPMENT_ONLY'
            and manifest['selection'] == 'c062' and manifest['layer_indices'] == list(SELECTED),
            'Unexpected source candidate/selection/status')
    _check_files(candidate, manifest['files'])
    inventory = _json_read(candidate / 'layers.json')
    require(len(inventory) == 49 and {r['layer_index'] for r in inventory} == set(range(49)),
            'Source must contain the exact 49-layer inventory')
    require([r['layer_index'] for r in inventory if r['weight_changed']] == list(SELECTED),
            'Source selected layer inventory differs')
    layers = []
    for i in SELECTED:
        row = next(r for r in inventory if r['layer_index'] == i)
        expected_shape = [197, 192, 576] if i % 4 == 2 else [197, 576, 192]
        family = 'fc1' if i % 4 == 2 else 'fc2'
        require(row['shape_MKN'] == expected_shape and row['family'] == family
                and row['layer_id'] == f'blocks.{i // 4}.mlp.{family}', 'Unexpected selected layer')
        weight_name, case_name = f'weights/layer_{i:02d}.npz', f'cases/layer_{i:02d}.npz'
        require(row['weights'] == weight_name and row['integer_case'] == case_name, 'Unexpected source NPZ path')
        with np.load(_inside(candidate, weight_name), allow_pickle=False) as stored:
            tensors = {name: stored[name] for name in stored.files}
        require(set(tensors) == {'W_fp32', 'Wq', 'sw', 'bias', 'mask', 'positions', 'values'},
                'Unexpected source weight fields')
        matrix(tensors['Wq'], np.int8, 'Source Wq')
        require(tensors['Wq'].shape == tuple(expected_shape[1:]), 'Source W shape mismatch')
        _slots(tensors['values'], tensors['positions'])
        k, n = tensors['Wq'].shape
        require(tensors['values'].shape == (k // 4, n, 2), 'Source slots shape mismatch')
        require(tensors['mask'].dtype == np.bool_ and tensors['mask'].shape == (k, n), 'Source mask dtype/shape')
        expected_mask = np.zeros((k, n), dtype=bool)
        reconstructed = np.zeros((k, n), np.int8)
        for s in (0, 1):
            kk = 4 * np.arange(k // 4)[:, None] + tensors['positions'][..., s]
            nn = np.arange(n)[None, :]
            expected_mask[kk, nn] = True
            reconstructed[kk, nn] = tensors['values'][..., s]
        require(np.array_equal(expected_mask, tensors['mask'])
                and np.array_equal(reconstructed, tensors['Wq']), 'Source slots/mask do not reconstruct Wq')
        with np.load(_inside(candidate, case_name), allow_pickle=False) as saved:
            require(set(saved.files) == {'Xq', 'Y_int32', 'edge_Xq', 'edge_Y_int32'}, 'Unexpected source case fields')
            cases = {name: saved[name] for name in saved.files}
        for prefix in ('', 'edge_'):
            x, y = cases[prefix + 'Xq'], cases[prefix + 'Y_int32']
            matrix(x, np.int8, 'Source Xq')
            matrix(y, np.int32, 'Source INT32 reference')
            expected_rows = 197 if prefix == '' else 6
            require(x.shape == (expected_rows, k) and y.shape == (expected_rows, n), 'Source case shape mismatch')
        layers.append({'inventory': row, 'tensors': tensors, 'cases': cases,
                       'weight_source': weight_name, 'case_source': case_name,
                       'weight_sha256': manifest['files'][weight_name]['sha256'],
                       'case_sha256': manifest['files'][case_name]['sha256']})
    return manifest, layers


def _tiny():
    x = np.array([[1, 2, 3, 4]], np.int8)
    w = np.array([[0, 3], [-5, 0], [0, -2], [7, 0]], np.int8)
    values = np.array([[[-5, 7], [3, -2]]], np.int8)
    positions = np.array([[[1, 3], [0, 2]]], np.uint8)
    return x, w, values, positions


def synthetic_checks():
    x, w, values, positions = _tiny()
    data, meta = pack_sparse(values, positions)
    require(pack_int8(x) == bytes.fromhex('01 02 03 04') + bytes(12), 'Document X byte vector mismatch')
    require(data == bytes.fromhex('FB 07 03 FE') + bytes(12)
            and meta == bytes.fromhex('8D') + bytes(15), 'Document sparse byte vector mismatch')
    golden = golden_dense_int32(x, w)
    require(pack_int32(golden) == bytes.fromhex('12 00 00 00 FD FF FF FF') + bytes(8),
            'Document golden byte vector mismatch')
    pairs = np.array(list(itertools.combinations(range(4), 2)), np.uint8)
    require(list(pairs[:, 0] | (pairs[:, 1] << 2)) == [4, 8, 12, 9, 13, 14], 'Position code table mismatch')
    rng = np.random.default_rng(20261022)
    count = 0
    for k in (4, 8, 12):
        for n in (1, 2, 7, 8, 9, 16, 17, 31, 32, 33):
            total = (k // 4) * n
            pos = pairs[np.arange(total) % 6].reshape(k // 4, n, 2)
            val = rng.integers(-128, 128, size=pos.shape, dtype=np.int8)
            val.flat[0] = 0
            original = np.zeros((k, n), np.int8)
            for g in range(k // 4):
                for col in range(n):
                    for s in (0, 1):
                        original[4 * g + int(pos[g, col, s]), col] = val[g, col, s]
            data, meta = pack_sparse(val, pos)
            decoded_v, decoded_p, decoded_w = unpack_sparse(data, meta, k, n)
            require(np.array_equal(decoded_w, original) and np.array_equal(decoded_v, val)
                    and np.array_equal(decoded_p, pos), 'Synthetic roundtrip failed')
            xq = rng.integers(-128, 128, (3, k), dtype=np.int8)
            expected = golden_dense_int32(xq, original)
            for mode in (0, 1):
                actual = emulate_axi(pack_int8(xq), pack_int8(original) if mode == 0 else data,
                                     object() if mode == 0 else meta, 3, k, n, mode)
                require(np.array_equal(actual, expected), 'Synthetic Dense/Sparse golden mismatch')
            count += 1
    return {'status': 'PASS', 'document_byte_vector': 'PASS', 'six_position_codes': 'PASS',
            'random_seed': 20261022, 'boundary_shapes': count, 'dense_sparse_mode_checks': count * 2,
            'scope': 'CPU software format/arithmetic only; no HLS or hardware'}


def _new_output(candidate, output):
    output, candidate = output.resolve(), candidate.resolve()
    parent = (ROOT / 'candidates').resolve()
    require(output != parent and output.is_relative_to(parent), 'Output must be a NEW directory below candidates/')
    require(not output.exists(), 'Output already exists; never overwrite')
    require(not output.is_relative_to(candidate) and not candidate.is_relative_to(output),
            'Source/output directories must be non-nested and distinct')
    return output


def _save_weights(output, relative, w, values, positions):
    folder = output / relative
    folder.mkdir(parents=True, exist_ok=False)
    sparse_data, sparse_meta = pack_sparse(values, positions)
    blobs = {'W_dense_data.bin': pack_int8(w), 'W_dense_meta.bin': bytes(16),
             'W_sparse_data.bin': sparse_data, 'W_sparse_meta.bin': sparse_meta}
    for name, blob in blobs.items():
        _write_bytes(folder / name, blob)
    return {'dense_data': relative + '/W_dense_data.bin', 'dense_meta': relative + '/W_dense_meta.bin',
            'sparse_data': relative + '/W_sparse_data.bin', 'sparse_meta': relative + '/W_sparse_meta.bin'}


def _save_case(output, relative, x, w, weight_files, case_id, layer_index, scope, expected=None):
    golden = golden_dense_int32(x, w)  # DIRECT original arrays; never derived from packed weights.
    if expected is not None:
        require(np.array_equal(golden, expected), 'Independent golden differs from frozen source case')
    folder = output / relative
    folder.mkdir(parents=True, exist_ok=False)
    _write_bytes(folder / 'X.bin', pack_int8(x))
    _write_bytes(folder / 'Y_golden.bin', pack_int32(golden))
    with (folder / 'Y_golden.npy').open('xb') as stream:
        np.save(stream, golden, allow_pickle=False)
    m, k = x.shape
    n = w.shape[1]
    common = {'X': relative + '/X.bin', 'Y': relative + '/Y_golden.bin'}
    modes = []
    for mode, prefix in ((0, 'dense'), (1, 'sparse')):
        modes.append({'mode': mode, 'buffers': dict(common, W_data=weight_files[prefix + '_data'],
                                                   W_meta=weight_files[prefix + '_meta']),
                      'layout': buffer_layout(m, k, n, mode)})
    return {'case_id': case_id, 'layer_index': layer_index, 'scope': scope,
            'M': m, 'K': k, 'N': n, 'modes': modes,
            'golden_npy': relative + '/Y_golden.npy', 'golden_dtype': 'INT32',
            'golden_bytes': 'little-endian, row-major, final AXI beat zero-padded',
            'dense_reference_uses_same_pruned_weights': True,
            'golden_algorithm': 'Original Xq.astype(INT64) @ original Wq.astype(INT64), range-check, cast INT32'}


def _read_blob(root, name):
    return _inside(root, name).read_bytes()


def _verify_layer_metadata(layer, source):
    row = source['inventory']
    expected = {'layer_index': row['layer_index'], 'layer_id': row['layer_id'],
                'family': row['family'], 'shape_MKN': row['shape_MKN'],
                'source_weight_npz': source['weight_source'], 'source_case_npz': source['case_source'],
                'source_weight_sha256': source['weight_sha256'], 'source_case_sha256': source['case_sha256']}
    for name, value in expected.items():
        require(layer.get(name) == value, 'Layer descriptor/source identity differs: ' + name)


def _verify_payload(root, layer_records, case_records, sources):
    expected_ids = [f'layer_{i:02d}_{kind}' for i in SELECTED for kind in ('public', 'edge')]
    require([r['layer_index'] for r in layer_records] == list(SELECTED), 'Packed layer inventory mismatch')
    require([r['case_id'] for r in case_records] == ['tiny'] + expected_ids, 'Packed case inventory mismatch')
    source_map = {r['inventory']['layer_index']: r for r in sources}
    sparse_bytes, dense_bytes = 0, 0
    for layer in layer_records:
        i, files = layer['layer_index'], layer['weights']
        source = source_map[i]
        _verify_layer_metadata(layer, source)
        w, values, pos = (source['tensors'][key] for key in ('Wq', 'values', 'positions'))
        k, n = w.shape
        v, p, restored = unpack_sparse(_read_blob(root, files['sparse_data']),
                                      _read_blob(root, files['sparse_meta']), k, n)
        require(np.array_equal(v, values) and np.array_equal(p, pos) and np.array_equal(restored, w),
                'Packed sparse values/positions/Wq changed from source')
        require(np.array_equal(unpack_int8(_read_blob(root, files['dense_data']), k, n), w),
                'Dense reference must use original pruned Wq')
        require(_read_blob(root, files['dense_meta']) == bytes(16), 'Dense dummy metadata differs')
        sparse_bytes += len(_read_blob(root, files['sparse_data'])) + len(_read_blob(root, files['sparse_meta']))
        dense_bytes += len(_read_blob(root, files['dense_data']))
    require(sparse_bytes == 829440 and dense_bytes == 1327104, 'Unexpected c062 packed weight totals')
    checked, mode_checked, cases_report = 0, 0, []
    for record in case_records:
        if record['case_id'] == 'tiny':
            x, w, _, _ = _tiny()
            require(record['layer_index'] is None, 'Tiny case source differs')
        else:
            source = source_map[record['layer_index']]
            prefix = '' if record['case_id'].endswith('_public') else 'edge_'
            x, w = source['cases'][prefix + 'Xq'], source['tensors']['Wq']
        m, k = x.shape
        n = w.shape[1]
        require((record['M'], record['K'], record['N']) == (m, k, n), 'Case dimensions differ')
        require(record['dense_reference_uses_same_pruned_weights'] is True, 'Wrong Dense comparison scope')
        expected = golden_dense_int32(x, w)
        if record['case_id'] != 'tiny':
            require(np.array_equal(expected, source['cases'][prefix + 'Y_int32']), 'Frozen case reference differs')
            checked += expected.size
        npy = np.load(_inside(root, record['golden_npy']), allow_pickle=False)
        require(npy.dtype == np.int32 and npy.shape == expected.shape and np.array_equal(npy, expected),
                'Golden NPY differs from original-source INT64 oracle')
        require([r['mode'] for r in record['modes']] == [0, 1], 'Both mode records required')
        for item in record['modes']:
            mode, refs = item['mode'], item['buffers']
            require(set(refs) == {'X', 'W_data', 'W_meta', 'Y'}, 'Incomplete buffer references')
            require(item['layout'] == buffer_layout(m, k, n, mode), 'Buffer layout differs from v1')
            x_blob, w_blob, meta_blob, y_blob = (_read_blob(root, refs[key]) for key in ('X', 'W_data', 'W_meta', 'Y'))
            require(np.array_equal(unpack_int8(x_blob, m, k), x), 'Packed X differs from original source')
            require(np.array_equal(unpack_int32(y_blob, m, n), expected), 'Golden BIN differs from independent oracle')
            require(y_blob == pack_int32(expected), 'Golden BIN not canonical little-endian/padding')
            if mode == 0:
                require(meta_blob == bytes(16), 'Dense metadata file must be canonical zero dummy')
            actual = emulate_axi(x_blob, w_blob, object() if mode == 0 else meta_blob, m, k, n, mode)
            require(np.array_equal(actual, expected), 'AXI byte emulation differs from independent golden')
            require(pack_int32(actual) == y_blob, 'AXI emulation output bits differ')
            mode_checked += actual.size
        cases_report.append({'case_id': record['case_id'], 'elements': expected.size,
                             'dense_mismatches': 0, 'sparse_mismatches': 0})
    require(checked == 935424, 'Not the complete frozen twelve-layer arithmetic corpus')
    return {'status': STATUS, 'layers': 12, 'source_cases': 24, 'document_cases': 1,
            'source_golden_int32_elements': checked, 'mode_output_elements_checked_including_tiny': mode_checked,
            'sparse_weight_bytes_with_metadata': sparse_bytes, 'same_pruned_dense_weight_bytes': dense_bytes,
            'selected_weight_payload_reduction_pct': 37.5, 'cases': cases_report,
            'source_case_scope': 'Original public Dense Xq snapshots and edge Xq, NOT new-model propagated activations',
            'golden_independence': 'Full original Wq INT64 matmul, independent of slots/packing/decoding',
            'hardware_accessed': False, 'HLS_RTL_board_verification': 'NOT_RUN',
            'classification_accuracy_or_performance_measurement': 'NOT_RUN'}


def export_package(candidate_path, output_dir):
    candidate = Path(candidate_path).resolve()
    output = _new_output(candidate, Path(output_dir))  # Refuse existing output BEFORE reading models.
    created = False
    try:
        doc = ROOT / FORMAT_DOC
        require(sha_file(doc) == FORMAT_DOC_SHA256, 'Format document changed; review and version the implementation')
        source_manifest, sources = _source(candidate)
        synthetic = synthetic_checks()
        source_receipt = {'directory': str(candidate), 'candidate_manifest_sha256': sha_file(candidate / 'candidate.json'),
                          'files': source_manifest['files'], 'selection': 'c062', 'layer_indices': list(SELECTED)}
        implementation = {str(Path(__file__).resolve()): sha_file(__file__),
                          str(doc.resolve()): sha_file(doc)}
        tests = ROOT / 'test_pack_sparse24_axi.py'
        if tests.exists():
            implementation[str(tests.resolve())] = sha_file(tests)
        output.mkdir(parents=True, exist_ok=False)
        created = True
        layers, cases = [], []
        tx, tw, tv, tp = _tiny()
        tiny_files = _save_weights(output, 'examples/tiny/weights', tw, tv, tp)
        cases.append(_save_case(output, 'examples/tiny/case', tx, tw, tiny_files,
                                'tiny', None, 'Document section 6 exact byte example'))
        for source in sources:
            row, tensors = source['inventory'], source['tensors']
            i = row['layer_index']
            relative = f'layers/layer_{i:02d}'
            files = _save_weights(output, relative + '/weights', tensors['Wq'], tensors['values'], tensors['positions'])
            layers.append({'layer_index': i, 'layer_id': row['layer_id'], 'family': row['family'],
                           'shape_MKN': row['shape_MKN'], 'weights': files,
                           'source_weight_npz': source['weight_source'], 'source_case_npz': source['case_source'],
                           'source_weight_sha256': source_manifest['files'][source['weight_source']]['sha256'],
                           'source_case_sha256': source_manifest['files'][source['case_source']]['sha256']})
            for kind, prefix in (('public', ''), ('edge', 'edge_')):
                cases.append(_save_case(output, relative + '/' + kind, source['cases'][prefix + 'Xq'],
                                        tensors['Wq'], files, f'layer_{i:02d}_{kind}', i,
                                        'Original public Dense Xq snapshot' if kind == 'public' else 'Six frozen integer boundary rows',
                                        source['cases'][prefix + 'Y_int32']))
            print(f'Prepared layer {i:02d}: values=55296 B, metadata=13824 B; public + edge golden', flush=True)
        verification = _verify_payload(output, layers, cases, sources)  # Reread saved bytes, not in-memory copies.
        verification['synthetic_checks'] = synthetic
        _write_json(output / 'verification.json', verification)
        text = '''# c062 SPLIT128 v1 AXI 数据与 Golden 测试包

这是 Python 数据格式/整数运算测试包，不是 Sparse IP、板端部署包或性能结果。
原始 c062 模型和 NPZ 未修改，没有重新剪枝或量化。

- `layers/layer_XX/weights/W_sparse_data.bin`：两个 INT8 值的连续数组。
- `W_sparse_meta.bin`：每组4 bit位置码；不是完整mask。
- `W_dense_data.bin`：同一份剪枝 Wq 的 Dense 排列，用于数值对照，不是原未剪枝模型权重。
- `W_dense_meta.bin`：Dense 模式的16字节全零占位段，Dense计算路径不读取它。
- 每层 `public/`、`edge/`：`X.bin`、`Y_golden.bin`、`Y_golden.npy`。
- `.bin` 无文件头，整段末尾补零到16字节；Y是little-endian INT32，行间无padding。
- `.npy` 带NumPy文件头，仅供软件读取；不能直接当作AXI输入或输出缓冲区。
- `examples/tiny/`：文档可手算例子，Y为 `[18,-3]`。
- `manifest.json`：完成标记、全部文件身份、每个测试的M/K/N、两种mode的buffer路径和长度。
- `verification.json`：从磁盘重新读取后的核对结果。

Golden直接由原NPZ的Xq与完整剪枝Wq做INT64全K矩阵乘法，检查范围后转INT32；
Sparse字节仿真按128 bit拍解码、选择X后做两槽乘加。二者逐元素比较。
公开Xq是原Dense模型的中间输入快照，不是剪枝模型新传播的激活，也不是图像分类质量集。

本包含12层、24组源输入，源Golden共935424个INT32元素；同时验证Dense/Sparse两种模式。
选中层的Sparse权重含metadata共829440 B，Dense对照共1327104 B；不代表整模型缩小或推理加速。

文件本身没有物理地址；在XRT BO中放置时另行满足64字节起始对齐、容量、cache sync和完成检查。
格式允许的尺寸不等于旧IP白名单；新W_meta/mode控制ABI尚未集成。
只在完整 `manifest.json` 存在、没有 `failure.json` 且verify通过时使用本包。
强验证需要原c062候选，路径移动后用 `--source-candidate` 指定，不静默跳过来源核对。
'''
        with (output / 'README.md').open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        _check_files(candidate, source_manifest['files'])
        require(sha_file(candidate / 'candidate.json') == source_receipt['candidate_manifest_sha256'], 'Source receipt changed')
        for path, digest in implementation.items():
            require(sha_file(path) == digest, 'Implementation/document changed during export')
        files = {p.relative_to(output).as_posix(): {'bytes': p.stat().st_size, 'sha256': sha_file(p)}
                 for p in sorted(output.rglob('*')) if p.is_file()}
        _check_files(output, files)
        manifest = {'schema': 'h576_sparse24_axi_golden_package_v1', 'status': STATUS, 'format': FORMAT_ID,
                    'created_utc': datetime.now(timezone.utc).isoformat(), 'source_candidate': source_receipt,
                    'format_document_sha256': FORMAT_DOC_SHA256, 'implementation_sha256': implementation,
                    'environment': {'python': platform.python_version(), 'numpy': np.__version__},
                    'layers': layers, 'cases': cases, 'files': files,
                    'synthetic_checks': synthetic, 'addresses_bound': False,
                    'hardware_whitelist_validated': False, 'HLS_RTL_board_verification': 'NOT_RUN'}
        _write_json(output / 'manifest.json', manifest)  # LAST write, after every validation.
        return manifest
    except Exception:
        if created:
            _write_json(output / 'failure.json', {'status': 'FAILED_NOT_A_COMPLETED_PACKAGE', 'failure': traceback.format_exc()})
        raise


def verify_package(package_dir, source_candidate=None):
    root = Path(package_dir).resolve(strict=True)
    require(not (root / 'failure.json').exists(), 'Package contains a failure receipt')
    manifest = _json_read(root / 'manifest.json')
    require(manifest['schema'] == 'h576_sparse24_axi_golden_package_v1' and manifest['status'] == STATUS
            and manifest['format'] == FORMAT_ID and manifest['format_document_sha256'] == FORMAT_DOC_SHA256,
            'Unsupported/unfinished package')
    _check_files(root, manifest['files'])
    expected_files = set(manifest['files']) | {'manifest.json'}
    require({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()} == expected_files,
            'Unexpected or missing package files')
    candidate = Path(source_candidate) if source_candidate is not None else Path(manifest['source_candidate']['directory'])
    source_manifest, sources = _source(candidate)
    require(sha_file(candidate / 'candidate.json') == manifest['source_candidate']['candidate_manifest_sha256']
            and source_manifest['files'] == manifest['source_candidate']['files'], 'Different source candidate')
    require(manifest['source_candidate']['selection'] == 'c062'
            and manifest['source_candidate']['layer_indices'] == list(SELECTED), 'Different source selection')
    report = _verify_payload(root, manifest['layers'], manifest['cases'], sources)
    report['synthetic_checks'] = synthetic_checks()
    require(report == _json_read(root / 'verification.json'), 'Stored verification receipt differs from fresh verification')
    require(manifest['synthetic_checks'] == report['synthetic_checks'], 'Stored synthetic receipt differs')
    # No writes: do not update receipts/timestamps or create a new verification file.
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='WRITE a NEW AXI/golden package; source read-only')
    export.add_argument('--candidate', type=Path, default=ROOT / 'candidates/h576_sparse24_mlp12_c062_v1')
    export.add_argument('--output-dir', type=Path, default=ROOT / 'candidates/h576_sparse24_mlp12_c062_axi128_v1')
    verify = commands.add_parser('verify', help='READ-ONLY source, payload, golden and arithmetic verification')
    verify.add_argument('--package', required=True, type=Path)
    verify.add_argument('--source-candidate', type=Path)
    commands.add_parser('selftest', help='IN-MEMORY deterministic small format/arithmetic checks; no files')
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            manifest = export_package(args.candidate, args.output_dir)
            result = {'status': manifest['status'], 'directory': str(args.output_dir.resolve()),
                      'layers': len(manifest['layers']), 'cases': len(manifest['cases']),
                      'format': FORMAT_ID, 'source_changed': False, 'HLS_RTL_board_verification': 'NOT_RUN'}
        elif args.command == 'verify':
            report = verify_package(args.package, args.source_candidate)
            result = {k: v for k, v in report.items() if k != 'cases'}
        else:
            result = synthetic_checks()
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        return 1
    # Presentation is outside the export transaction: a closed stdout cannot
    # turn an already committed, valid package into contradictory failure.json.
    try:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    except (BrokenPipeError, OSError):
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
