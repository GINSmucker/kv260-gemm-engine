"""Generic codec copied from the reviewed SPLIT128 v1 upstream; no c062 identity binding."""
import itertools
from numbers import Integral
import numpy as np
FORMAT_ID="H576_SPARSE24_SPLIT128_V1"
UINT64_MAX=(1<<64)-1
INT32_MAX=(1<<31)-1
BEAT_BYTES=16

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
