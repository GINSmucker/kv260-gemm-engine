"""Export a NEW fixed OPT50 mixed-model software bundle, without board access.

The existing C graph is reused; exactly twelve INT8 initializers and their
W_sha256 identity attributes change. No topology, quantizer, scale or bias is
altered. The output is software preparation, NOT model/board acceptance.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

import numpy as np
import onnx
from onnx import numpy_helper

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
SPARSE = WORKSPACE / 'h576_sparse24'
CANDIDATE = SPARSE / 'candidates/h576_sparse24_mlp12_c062_v1'
AXI = SPARSE / 'model_gemm_opt50/tb/axi_cases'
ARCHIVE = SPARSE / 'inputs/h576-runtime-public-ui.tar.gz'
PREFIX = 'v5_h576_ui_20261017_01/'
SELECTED = (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47)
ARCHIVE_SHA = 'c29a006fe472fd2bfb545d7bbf77f34a5090ff9315043cdd7e5830e518d61f7c'
BASE_SHA = 'f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'
REFERENCE_SHA = '50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49'
CANDIDATE_SHA = '86ef8803d667ef8fd2e959822abaa4e84d7655f28c381b452b0b19e8f8ae8528'
AXI_SHA = '6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf'
OLD_GRAPH_SHA = '0a695c9813a503e84a7efb728476b77aa4ee0c53935810ea43b9d2a52764f23a'
EXPECTED_WEIGHTS_SHA = '88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def safe_relative(name):
    require(type(name) is str and name and not any(c in name for c in '\\:\0\r\n'), 'unsafe relative path')
    path = PurePosixPath(name)
    require(not path.is_absolute() and str(path) == name and
            all(part not in ('', '.', '..') for part in name.split('/')), 'unsafe relative path')
    return path


def checked_file(path, expected_sha=None):
    path = Path(path)
    require(not any(part.is_symlink() for part in (path, *path.parents)), 'linked source refused')
    require(path.is_file() and path.stat().st_size <= 80*1024*1024, 'source missing or too large')
    raw = path.read_bytes()
    require(expected_sha is None or sha(raw) == expected_sha, 'fixed input SHA differs: ' + str(path))
    return raw


def load_sources():
    raw = checked_file(ARCHIVE, ARCHIVE_SHA)
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        members = archive.getmembers()
        require(len({item.name for item in members}) == len(members), 'duplicate archive member')
        names = ['bundle/layers.json', 'bundle/bundle_manifest.json', 'graphs/model_w8a8_pl.onnx']
        names += ['bundle/weights/%02d_Wq.bin' % i for i in range(49)]
        payload = {}
        for name in names:
            entry = archive.getmember(PREFIX + name)
            require(entry.isfile() and not entry.issym() and not entry.islnk() and entry.size <= 32*1024*1024,
                    'unsafe archive input member')
            payload[name] = archive.extractfile(entry).read()
    candidate_raw = checked_file(CANDIDATE / 'candidate.json', CANDIDATE_SHA)
    candidate = json.loads(candidate_raw)
    npzs = {i: checked_file(CANDIDATE / ('weights/layer_%02d.npz' % i),
                           candidate['files']['weights/layer_%02d.npz' % i]['sha256']) for i in SELECTED}
    axi_manifest = checked_file(AXI / 'manifest.json', AXI_SHA)
    axi_files = json.loads(axi_manifest)['files']
    axi_payload = {}
    for i in SELECTED:
        for name in ('W_dense_data.bin', 'W_dense_meta.bin', 'W_sparse_data.bin', 'W_sparse_meta.bin'):
            relative = 'layers/layer_%02d/weights/%s' % (i, name)
            axi_payload[relative] = checked_file(AXI / relative, axi_files[relative]['sha256'])
            require(identity(axi_payload[relative]) == axi_files[relative], 'AXI size identity differs')
    return {'archive_sha256': sha(raw), 'archive': payload, 'candidate_raw': candidate_raw,
            'base_raw': checked_file(SPARSE / 'inputs/baseline/h576_w8.onnx', BASE_SHA),
            'reference_raw': checked_file(CANDIDATE / 'pruned_w8a8_reference.onnx', REFERENCE_SHA),
            'npzs': npzs, 'axi_manifest_raw': axi_manifest, 'axi': axi_payload}


def tensors(model):
    require(len({item.name for item in model.graph.initializer}) == len(model.graph.initializer),
            'duplicate ONNX initializer')
    require(all(item.data_location == onnx.TensorProto.DEFAULT for item in model.graph.initializer),
            'external ONNX initializer refused')
    return {item.name: numpy_helper.to_array(item) for item in model.graph.initializer}


def shape(i):
    if i == 48:
        return 1, 192, 1000
    return (197, 576, 192) if i % 4 == 3 else (197, 192, 192 if i % 4 == 1 else 576)


def sparse_pack(z, w):
    k, n = w.shape
    expected = {'positions': np.uint8, 'mask': np.bool_, 'W_fp32': np.float32,
                'Wq': np.int8, 'values': np.int8, 'sw': np.float32, 'bias': np.float32}
    require(set(z) == set(expected) and all(z[key].dtype == dtype for key, dtype in expected.items()),
            'sparse NPZ tensor inventory/dtypes differ')
    p, v = z['positions'], z['values']
    require(p.shape == v.shape == (k//4, n, 2) and z['mask'].shape == w.shape and
            z['W_fp32'].shape == w.shape and z['Wq'].shape == w.shape and
            np.array_equal(z['Wq'], w) and (p[..., 0] < p[..., 1]).all() and (p <= 3).all(),
            'sparse positions/weights/shapes differ')
    restored = np.zeros(w.shape, np.int8)
    expected_mask = np.zeros(w.shape, np.bool_)
    columns = np.arange(n)
    for group in range(k//4):
        for slot in range(2):
            rows = 4*group + p[group, :, slot]
            restored[rows, columns] = v[group, :, slot]
            expected_mask[rows, columns] = True
    require(np.array_equal(restored, w) and np.array_equal(expected_mask, z['mask']),
            'sparse slot reconstruction differs from same pruned Dense weights')
    code = (p[..., 0] | (p[..., 1] << 2)).reshape(-1)
    metadata = (code[::2] | (code[1::2] << 4)).astype(np.uint8).tobytes()
    values_raw = np.ascontiguousarray(v).tobytes()
    # Decode the emitted physical bytes independently, not the source positions.
    packed = np.frombuffer(metadata, np.uint8)
    decoded = np.empty(code.size, np.uint8)
    decoded[::2] = packed & 15
    decoded[1::2] = packed >> 4
    positions = np.stack((decoded & 3, decoded >> 2), axis=-1).reshape(k//4, n, 2)
    values = np.frombuffer(values_raw, np.int8).reshape(k//4, n, 2)
    require(np.array_equal(positions, p) and np.array_equal(values, v), 'physical sparse byte decode changed source slots')
    # Independent full-K INT64 versus slot-selected arithmetic on signed edge rows.
    x = np.empty((6, k), np.int8)
    x[0] = -128; x[1] = 127; x[2] = 0
    x[3] = np.arange(k, dtype=np.int64).astype(np.int8)
    x[4] = np.where(np.arange(k) % 2, -128, 127).astype(np.int8)
    x[5] = np.random.default_rng(20261024 + k + n).integers(-128, 128, k, dtype=np.int16).astype(np.int8)
    full = x.astype(np.int64) @ w.astype(np.int64)
    slots = np.zeros((6, n), np.int64)
    for group in range(k//4):
        for slot in range(2):
            slots += x[:, 4*group + positions[group, :, slot]].astype(np.int64) * values[group, :, slot].astype(np.int64)
    require(np.array_equal(full, slots) and full.min() >= -(2**31) and full.max() < 2**31,
            'independent sparse/full-K INT64 arithmetic differs or overflows INT32')
    return values_raw, metadata, int(full.size)


def mixed_graph(source_raw, weights):
    require(sha(source_raw) == OLD_GRAPH_SHA, 'fixed C fused graph differs')
    original = onnx.load_model_from_string(source_raw)
    out = copy.deepcopy(original)
    source = tensors(original)
    indices = {item.name: index for index, item in enumerate(out.graph.initializer)}
    attrs = {}
    for node_index, node in enumerate(out.graph.node):
        layer_attrs = [item for item in node.attribute if item.name == 'layer_index']
        if not layer_attrs:
            continue
        require(len(layer_attrs) == 1 and node.domain == 'com.kv260', 'unexpected layer node identity')
        i = int(layer_attrs[0].i)
        require(0 <= i < 49 and i not in attrs, 'duplicate or invalid custom layer node')
        hashes = [index for index, item in enumerate(node.attribute) if item.name == 'W_sha256']
        require(len(hashes) == 1 and node.attribute[hashes[0]].type == onnx.AttributeProto.STRING and
                node.attribute[hashes[0]].s.decode() == sha(source['BQL_%d_Wq' % i].tobytes()),
                'original custom node W_sha256 differs from its weight bytes')
        attrs[i] = (node_index, hashes[0])
    require(set(attrs) == set(range(49)), 'all 49 custom layer identities required')
    for i in SELECTED:
        name = 'BQL_%d_Wq' % i
        out.graph.initializer[indices[name]].CopyFrom(numpy_helper.from_array(weights[i], name))
        node, attr = attrs[i]
        out.graph.node[node].attribute[attr].s = sha(weights[i].tobytes()).encode()
    # Reverse only the approved twelve initializer and twelve identity changes.
    reverse = copy.deepcopy(out)
    for i in SELECTED:
        name = 'BQL_%d_Wq' % i
        reverse.graph.initializer[indices[name]].CopyFrom(original.graph.initializer[indices[name]])
        node, attr = attrs[i]
        reverse.graph.node[node].attribute[attr].CopyFrom(original.graph.node[node].attribute[attr])
    require(reverse.SerializeToString() == original.SerializeToString(), 'unapproved fused graph topology/attribute/initializer change')
    onnx.checker.check_model(out)
    return out.SerializeToString(), source


def make_header(records, weights_sha, data_sha, meta_sha):
    lines = ['#pragma once', '#include <array>', '#include <cstddef>', '#include <cstdint>',
             'namespace opt50_model {',
             'struct Layer { uint32_t m,k,n,mode; bool pl; size_t canonical_offset,data_offset,data_bytes,meta_offset,meta_bytes; const char *w_sha,*scale_sha,*bias_sha; };',
             'inline constexpr size_t weights_bytes=4615680, data_bytes=3317760, meta_bytes=165952;',
             'inline constexpr size_t max_x_bytes=113472, max_y_bytes=453888;',
             'inline constexpr const char *weights_sha="%s", *data_sha="%s", *meta_sha="%s", *reference_sha="%s";' %
             (weights_sha, data_sha, meta_sha, REFERENCE_SHA),
             'inline constexpr std::array<Layer,49> layers={{']
    for row in records:
        numbers = [row[key] for key in ('m', 'k', 'n', 'mode')]
        fields = ','.join(map(str, numbers)) + ',' + ('true' if row['route'] == 'PL' else 'false')
        fields += ',' + ','.join(str(row[key]) for key in ('canonical_offset', 'w_data_offset', 'w_data_bytes', 'w_meta_offset', 'w_meta_bytes'))
        fields += ',"%s","%s","%s"' % (row['Wq_sha256'], row['sw_sha256'], row['bias_sha256'])
        lines.append('    {' + fields + '},')
    return ('\n'.join(lines + ['}};', '} // namespace opt50_model', ''])).encode()


def assemble(sources):
    require(sources['archive_sha256'] == ARCHIVE_SHA and sha(sources['candidate_raw']) == CANDIDATE_SHA and
            sha(sources['base_raw']) == BASE_SHA and sha(sources['reference_raw']) == REFERENCE_SHA and
            sha(sources['axi_manifest_raw']) == AXI_SHA, 'fixed source identity differs')
    candidate = json.loads(sources['candidate_raw'])
    require(candidate['layer_indices'] == list(SELECTED) and set(sources['npzs']) == set(SELECTED), 'only c062 MLP12 is authorized')
    old = sources['archive']
    bundle_manifest = json.loads(old['bundle/bundle_manifest.json'])
    original_layers = json.loads(old['bundle/layers.json'])
    require(len(original_layers) == 49 and bundle_manifest['source_semantic_sha256'] == BASE_SHA, 'original full-model bundle differs')
    for name, expected in bundle_manifest['files'].items():
        safe_relative(name)
        require(identity(old['bundle/' + name]) == expected, 'original bundle file identity differs: ' + name)
    base = onnx.load_model_from_string(sources['base_raw'])
    reference = onnx.load_model_from_string(sources['reference_raw'])
    a, b = tensors(base), tensors(reference)
    expected_names = {'BQL_%d_Wf' % i for i in SELECTED}
    original_initializers = {item.name: item for item in base.graph.initializer}
    require(set(a) == set(b), 'reference initializer inventory changed')
    changed = {name for name in a if not np.array_equal(a[name], b[name])}
    require(changed == expected_names, 'reference changed nonselected weights, scale or bias')
    reverse = copy.deepcopy(reference)
    for item in reverse.graph.initializer:
        if item.name in changed:
            item.CopyFrom(original_initializers[item.name])
    require(reverse.SerializeToString() == base.SerializeToString(), 'reference graph changed outside twelve weights')
    onnx.checker.check_model(reference)
    weights, sparse, integer_outputs = [], {}, 0
    for i in range(49):
        m, k, n = shape(i)
        layer = original_layers[i]
        require(layer['layer_index'] == i and [layer[key] for key in ('M', 'K', 'N')] == [m, k, n], 'original layer topology differs')
        wf, sf, bf = (b['BQL_%d_%s' % (i, suffix)] for suffix in ('Wf', 'sf', 'bf'))
        require(wf.dtype == np.float32 and wf.shape == (k, n) and np.isfinite(wf).all() and
                np.array_equal(wf, np.rint(wf)) and wf.min() >= -127 and wf.max() <= 127,
                'Wf must be an exact signed INT8 numeric reference, not arbitrary FP32')
        require(sf.dtype == bf.dtype == np.float32 and sf.shape == bf.shape == (n,) and
                np.isfinite(sf).all() and (sf > 0).all() and np.isfinite(bf).all(), 'scale/bias type/shape/values differ')
        w = np.ascontiguousarray(wf.astype(np.int8)); weights.append(w)
        old_bytes = old['bundle/weights/%02d_Wq.bin' % i]
        require(np.array_equal(np.frombuffer(old_bytes, np.int8).reshape(k, n), a['BQL_%d_Wf' % i]) and
                sha(sf.astype('<f4').tobytes()) == layer['numeric_identity']['sw'] and
                sha(bf.astype('<f4').tobytes()) == layer['numeric_identity']['bias'], 'original W/scale/bias numeric identity differs')
        if i in SELECTED:
            raw = sources['npzs'][i]
            require(identity(raw) == candidate['files']['weights/layer_%02d.npz' % i], 'sparse NPZ source identity differs')
            with np.load(io.BytesIO(raw), allow_pickle=False) as container:
                z = {key: container[key] for key in container.files}
            require(np.array_equal(z['sw'].reshape(-1), sf) and np.array_equal(z['bias'], bf), 'sparse NPZ scale/bias differs')
            values, meta, count = sparse_pack(z, w)
            integer_outputs += count
            sparse[i] = values, meta
            prefix = 'layers/layer_%02d/weights/' % i
            for name, payload in [('W_dense_data.bin', w.tobytes()), ('W_dense_meta.bin', bytes(16)),
                                  ('W_sparse_data.bin', values), ('W_sparse_meta.bin', meta)]:
                key = prefix + name
                require(payload == sources['axi'][key] and identity(payload) == json.loads(sources['axi_manifest_raw'])['files'][key],
                        'mixed sparse weight bytes differ from fixed tested AXI package')
    graph_raw, graph_source = mixed_graph(old['graphs/model_w8a8_pl.onnx'], weights)
    for i in range(49):
        require(np.array_equal(graph_source['BQL_%d_sw' % i], b['BQL_%d_sf' % i]) and
                np.array_equal(graph_source['BQL_%d_bias' % i], b['BQL_%d_bf' % i]) and
                np.array_equal(graph_source['BQL_%d_Wq' % i], a['BQL_%d_Wf' % i]), 'C graph numeric source differs')
    canonical, data, meta = bytearray(), bytearray(), bytearray(64)
    records, bundle_layers, artifacts = [], copy.deepcopy(original_layers), {}
    for i, w in enumerate(weights):
        m, k, n = shape(i); pl = i < 48 and i % 4 != 1; mode = int(i in SELECTED)
        w_raw = w.tobytes(); w_sha = sha(w_raw)
        row = {'layer_index': i, 'layer_id': original_layers[i]['layer_id'], 'family': original_layers[i]['family'],
               'm': m, 'k': k, 'n': n, 'route': 'PL' if pl else 'PS', 'mode': mode,
               'canonical_offset': len(canonical), 'w_data_offset': 0, 'w_data_bytes': 0,
               'w_meta_offset': 0, 'w_meta_bytes': 0, 'Wq_sha256': w_sha,
               'sw_sha256': sha(b['BQL_%d_sf' % i].astype('<f4').tobytes()),
               'bias_sha256': sha(b['BQL_%d_bf' % i].astype('<f4').tobytes())}
        canonical.extend(w_raw)
        if pl:
            physical = sparse[i][0] if mode else w_raw
            require(len(data) % 64 == 0, 'PL data layer alignment')
            row.update(w_data_offset=len(data), w_data_bytes=len(physical), w_meta_offset=0, w_meta_bytes=16)
            data.extend(physical)
            if mode:
                require(len(meta) % 64 == 0, 'PL metadata layer alignment')
                row.update(w_meta_offset=len(meta), w_meta_bytes=len(sparse[i][1]))
                meta.extend(sparse[i][1])
        records.append(row)
        relative = 'weights/%02d_Wq.bin' % i
        artifacts['bundle/' + relative] = w_raw
        layer = bundle_layers[i]
        layer['source_dense_numeric_identity'] = copy.deepcopy(layer['numeric_identity'])
        layer['numeric_identity'] = {'Wq': w_sha, 'sw': row['sw_sha256'], 'bias': row['bias_sha256']}
        layer['Wq'] = {'file': relative, **identity(w_raw)}
        layer['mode'] = 'sparse24_w8a8' if mode else 'dense_w8a8'
        layer['PL'] = 'OPT50_SOFTWARE_PREPARED_NOT_BOARD_ACCEPTED' if pl else 'PS_UNCHANGED'
        layer['opt50_route'] = row['route']; layer['opt50_mode'] = mode
    require((len(canonical), len(data), len(meta)) == (4615680, 3317760, 165952) and sha(canonical) == EXPECTED_WEIGHTS_SHA,
            'mixed model byte counts/49-weight identity differ')
    artifacts['bundle/layers.json'] = json_bytes(bundle_layers)
    bundle_files = {name.removeprefix('bundle/'): identity(raw) for name, raw in artifacts.items()}
    artifacts['bundle/bundle_manifest.json'] = json_bytes({'schema': 'opt50_mixed_runtime_bundle_v1',
        'status': 'SOFTWARE_PREPARED_NOT_BOARD_TESTED', 'source_semantic_sha256': REFERENCE_SHA,
        'logical_weights_sha256': sha(canonical), 'files': bundle_files})
    artifacts.update({'weights.bin': bytes(canonical), 'pl_w_data.bin': bytes(data), 'pl_w_meta.bin': bytes(meta),
                      'reference.onnx': sources['reference_raw'], 'graphs/model_w8a8_pl.onnx': graph_raw})
    artifacts['model_catalog.hpp'] = make_header(records, sha(canonical), sha(data), sha(meta))
    source_pins = {'archive': {'sha256': ARCHIVE_SHA}, 'candidate.json': identity(sources['candidate_raw']),
                   'baseline_w8.onnx': identity(sources['base_raw']), 'reference.onnx': identity(sources['reference_raw']),
                   'axi_manifest.json': identity(sources['axi_manifest_raw']),
                   **{'weights/layer_%02d.npz' % i: identity(raw) for i, raw in sources['npzs'].items()}}
    manifest = {'schema': 'opt50_full_model_mixed_bundle_v1', 'status': 'SOFTWARE_PREPARED_NOT_BOARD_TESTED',
                'logical_weights_sha256': sha(canonical), 'reference_sha256': REFERENCE_SHA,
                'pl_data_sha256': sha(data), 'pl_meta_sha256': sha(meta),
                'custom_graph_sha256': sha(graph_raw), 'format': 'H576_SPARSE24_SPLIT128_V1',
                'routes': {'PL': 36, 'PS': 13, 'PL_dense': 24, 'PL_sparse24': 12},
                'sparse_layer_indices': list(SELECTED), 'layers': records, 'source_pins': source_pins,
                'verification': {'all49_W_scale_bias_identity': 'PASS', 'reference_only12_weights_change': 'PASS',
                    'fused_graph_changed_initializers': ['BQL_%d_Wq' % i for i in SELECTED],
                    'fused_graph_changed_W_sha256_layer_indices': list(SELECTED),
                    'other_fused_graph_bytes_unchanged': True, 'axi12_weight_byte_identity': 'PASS',
                    'independent_INT64_vs_slot_outputs_compared': integer_outputs,
                    'weights_in_file_have_no_BO_guards': True},
                'hardware_executed': False, 'accuracy_verified': False, 'performance_verified': False,
                'files': {name: identity(raw) for name, raw in sorted(artifacts.items())}}
    return artifacts, manifest


def verify_package(package, expected=None):
    package = Path(package)
    raw = checked_file(package / 'manifest.json')
    manifest = json.loads(raw)
    require(manifest.get('schema') == 'opt50_full_model_mixed_bundle_v1' and
            manifest.get('logical_weights_sha256') == EXPECTED_WEIGHTS_SHA and
            manifest.get('reference_sha256') == REFERENCE_SHA and manifest.get('hardware_executed') is False,
            'mixed package identity differs')
    require(type(manifest.get('files')) is dict and len(manifest['files']) == 57 and
            not any(path.is_symlink() for path in package.rglob('*')), 'mixed package files/links differ')
    require({p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()} ==
            set(manifest['files']) | {'manifest.json'}, 'mixed output file inventory differs')
    for name, pin in manifest['files'].items():
        safe_relative(name)
        require(identity(checked_file(package / name)) == pin, 'mixed output file changed: ' + name)
    # Full source-derived reproduction rejects a self-consistently altered manifest.
    if expected is None:
        _, expected = assemble(load_sources())
    require(manifest == expected, 'mixed manifest differs from fixed source-derived expected package')
    return manifest


def export_package(output):
    output = Path(output).absolute()
    require(not output.exists() and output.parent.is_dir() and
            not any(p.is_symlink() for p in (output, *output.parents)), 'NEW unlinked output with existing parent required')
    artifacts, manifest = assemble(load_sources())
    output.mkdir()
    for name, raw in artifacts.items():
        safe_relative(name)
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            require(stream.write(raw) == len(raw), 'short model artifact write')
    # Read every newly written file before publishing the final completion marker.
    for name, pin in manifest['files'].items():
        require(identity(checked_file(output / name)) == pin, 'new artifact reread mismatch')
    # Ensure fixed sources did not move/change while the new output was written.
    _, repeated = assemble(load_sources())
    require(repeated == manifest, 'source-derived model changed during export')
    with (output / 'manifest.json').open('xb') as stream:
        stream.write(json_bytes(manifest))
    verify_package(output, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('export')
    command.add_argument('--out', type=Path, default=HERE / 'generated_model_v1')
    command = commands.add_parser('verify')
    command.add_argument('--package', type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = export_package(args.out) if args.command == 'export' else verify_package(args.package)
    print(json.dumps({'status': manifest['status'], 'schema': manifest['schema'], 'routes': manifest['routes'],
                      'logical_weights_sha256': manifest['logical_weights_sha256'],
                      'reference_sha256': manifest['reference_sha256'], 'files': len(manifest['files']),
                      'hardware_executed': False, 'accuracy_verified': False, 'performance_verified': False}, indent=2))


if __name__ == '__main__':
    main()
