"""Prepare integer and full-reference goldens; no native owner or PL access.

The 49 layer inputs are C's original Dense intermediate snapshots. They are
NOT propagated activations of the pruned graph. The complete reference calls
are separate synthetic image-shaped inputs and are not classification tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile

import numpy as np
import onnxruntime as ort
import prepare_model as p

ROOT = Path(__file__).resolve().parent


def require(ok, message):
    if not ok:
        raise ValueError(message)


def decode_pl(record, data, metadata):
    k, n = record['k'], record['n']
    require(type(k) is int and type(n) is int and k > 0 and n > 0 and record['route'] == 'PL', 'PL shape required')
    offset, count = record['w_data_offset'], record['w_data_bytes']
    mo, mc = record['w_meta_offset'], record['w_meta_bytes']
    require(all(type(v) is int and v >= 0 for v in (offset, count, mo, mc)) and
            offset+count <= len(data) and mo+mc <= len(metadata), 'packed buffer range')
    raw = data[offset:offset+count]
    if record['mode'] == 0:
        require(count == k*n and mc == 16 and metadata[mo:mo+16] == bytes(16), 'Dense payload/placeholder')
        return np.frombuffer(raw, np.int8).reshape(k, n)
    require(record['mode'] == 1 and k % 4 == 0 and count == k*n//2 and mc == k*n//8, 'Sparse payload shape')
    records = k//4*n
    codes = np.frombuffer(metadata[mo:mo+mc], np.uint8)
    nibble = np.empty(records, np.uint8)
    nibble[0::2] = codes & 15
    nibble[1::2] = codes >> 4
    lo, hi = nibble & 3, nibble >> 2
    require(np.all(lo < hi), 'invalid sparse metadata position code')
    values = np.frombuffer(raw, np.int8).reshape(records, 2)
    result = np.zeros((k, n), np.int8)
    t = np.arange(records)
    result[4*(t//n)+lo, t % n] = values[:, 0]
    result[4*(t//n)+hi, t % n] = values[:, 1]
    return result


def integer_golden(x, w):
    require(x.dtype == np.int8 and w.dtype == np.int8 and x.ndim == w.ndim == 2 and x.shape[1] == w.shape[0],
            'INT8 matrix dimensions')
    result = x.astype(np.int64) @ w.astype(np.int64)
    require(np.all(result >= -(2**31)) and np.all(result < 2**31), 'INT32 overflow')
    return result.astype('<i4')


def run(package, output):
    require(np.__version__ == '1.26.4' and ort.__version__ == '1.22.1', 'fixed NumPy/ORT versions required')
    package, output = Path(package).absolute(), Path(output).absolute()
    manifest = p.verify_package(package)
    require(not output.exists() and not output.is_symlink() and output.parent.is_dir(), 'NEW evidence directory required')
    archive_raw = p.checked_file(p.ARCHIVE, p.ARCHIVE_SHA)
    with tarfile.open(p.ARCHIVE, 'r:gz') as archive:
        snapshots = {i: archive.extractfile('v5_h576_ui_20261017_01/assets/layer_inputs/%d_Xq.bin' % i).read()
                     for i in range(49)}
    require(hashlib.sha256(archive_raw).hexdigest() == p.ARCHIVE_SHA, 'source archive identity')
    canonical = (package / 'weights.bin').read_bytes()
    data, meta = (package / 'pl_w_data.bin').read_bytes(), (package / 'pl_w_meta.bin').read_bytes()
    output.mkdir()
    layers = []
    for row in manifest['layers']:
        i, m, k, n = row['layer_index'], row['m'], row['k'], row['n']
        raw = snapshots[i]
        require(len(raw) == m*k, 'C input snapshot length')
        x = np.frombuffer(raw, np.int8).reshape(m, k)
        start = row['canonical_offset']
        w = np.frombuffer(canonical[start:start+k*n], np.int8).reshape(k, n)
        require(p.sha(w.tobytes()) == row['Wq_sha256'], 'canonical layer weight identity')
        golden = integer_golden(x, w)
        if row['route'] == 'PL':
            decoded = decode_pl(row, data, meta)
            np.testing.assert_array_equal(decoded, w)
            np.testing.assert_array_equal(integer_golden(x, decoded), golden)
        if i in p.SELECTED:
            known = p.AXI / ('layers/layer_%02d/public/Y_golden.bin' % i)
            require(p.checked_file(known) == golden.tobytes(), 'selected public golden differs from board-tested package')
        folder = output / ('layer_%02d' % i); folder.mkdir()
        (folder / 'X.bin').write_bytes(raw)
        (folder / 'Y_golden.bin').write_bytes(golden.tobytes())
        layers.append({'layer': i, 'route': row['route'], 'mode': row['mode'], 'shape_MKN': [m, k, n],
                       'INT32_outputs': int(golden.size), 'X_sha256': p.sha(raw), 'Y_sha256': p.sha(golden.tobytes())})
        print('CPU_LAYER_GOLDEN_READY %d/49 layer=%d route=%s mode=%d' % (i+1, i, row['route'], row['mode']), flush=True)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4; options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    for key in ('session.intra_op.allow_spinning', 'session.inter_op.allow_spinning'):
        options.add_session_config_entry(key, '0')
    session = ort.InferenceSession(str(package / 'reference.onnx'), options, providers=['CPUExecutionProvider'])
    synthetic = []
    for name, image in (
        ('zero', np.zeros((1, 3, 224, 224), np.float32)),
        ('pattern', ((np.arange(3*224*224, dtype=np.int32) % 257 - 128).astype(np.float32)/np.float32(128)).reshape(1, 3, 224, 224))):
        logits = session.run(None, {'image': image})[0]
        require(logits.shape == (1, 1000) and logits.dtype == np.float32 and np.isfinite(logits).all(), 'reference logits contract')
        np.save(output / (name+'_input.npy'), image, allow_pickle=False)
        np.save(output / (name+'_logits.npy'), logits, allow_pickle=False)
        synthetic.append({'name': name, 'input_sha256': p.sha((output / (name+'_input.npy')).read_bytes()),
                          'logits_sha256': p.sha(logits.astype('<f4').tobytes()), 'logits': 1000})
    result = {'schema': 'opt50_model_offline_evidence_v1', 'status': 'OFFLINE_GOLDENS_READY_NOT_ARM_OR_PL_PROOF',
              'model_manifest_sha256': p.sha((package / 'manifest.json').read_bytes()),
              'reference_sha256': manifest['reference_sha256'], 'layers': layers,
              'layer_INT32_outputs': sum(item['INT32_outputs'] for item in layers),
              'layer_input_scope': 'Original C Dense intermediate snapshots, NOT propagated pruned-model activations',
              'complete_CPU_reference_synthetic_inputs': synthetic,
              'native_owner_executed': False, 'hardware_executed': False,
              'accuracy_verified': False, 'performance_verified': False}
    (output / 'result.json').write_bytes(p.json_bytes(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=ROOT / 'generated_model_v1')
    parser.add_argument('--out', type=Path, default=ROOT / 'offline_evidence_v1')
    args = parser.parse_args()
    result = run(args.model, args.out)
    print(json.dumps({key: result[key] for key in ('status', 'layer_INT32_outputs', 'hardware_executed',
                                                  'accuracy_verified', 'performance_verified')}, indent=2))
