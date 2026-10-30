"""New B24 aligned full-model export, strictly offline; preserve old versions.

All 49 Wq/sw/bias and all 54 non-Linear parameters come from B's pinned final
release. The new B graph receives bounded H576 QKV/layout/GELU/premax fusions.
All 49 quantized tensors and 54 non-Linear parameters remain new B values.
No package code, pickle, torch, device, loader, network or training is invoked.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto

HERE = Path(__file__).resolve().parent
ARCHIVE_ROOT = 'B_FINAL_H576_24L_100K_20261026_v1'
ZIP_SHA = '7ada4de5cf99d09d3d6a5b9d92108bcbce85dc636e0c1669d8b14d5171139fc4'
B_MANIFEST_SHA = 'c0f35179ea2edd7fe848cc5643232da86b795b92a18fa27624f5770cad02f769'
REFERENCE_SHA = 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
SCHEMA = 'opt50_b24_full_model_aligned_bundle_v1'
FORMAT = 'H576_SPARSE24_SPLIT128_V1'
ALIGNED_TOPOLOGY_SHA = '2cc754b2c284416207b8be029300be6d775cc940834030492d2a9a6e95b05647'
SELECTED = tuple(i for i in range(48) if i % 4 in (2, 3))
SAMPLES = ('dev0000', 'dev4499')
CORE = {'weights.bin', 'pl_w_data.bin', 'pl_w_meta.bin', 'reference.onnx', 'model_catalog.hpp',
        'graphs/model_w8a8_pl.onnx', 'bundle/layers.json', 'bundle/bundle_manifest.json'} | {
            'bundle/weights/%02d_Wq.bin' % i for i in range(49)}
EXTRAS = {'source/B_MANIFEST.json', 'source/B_RELEASE.json', 'source/B_LAYERS.json',
          'inputs/references.json'} | {'inputs/'+s+'_'+role+'.npy' for s in SAMPLES for role in ('input', 'logits')}
EXTRAS.add('inputs/zero_input.npy')
REQUIRED = CORE | EXTRAS
SUFFIXES = ('abs', 'max', 'iszero', 'scaled', 'floor', 'sx', 'xd', 'rne', 'xq', 'yi', 'i32', 'f', 't1', 't2', 'output')
OPS = ('Abs', 'ReduceMax', 'Equal', 'Div', 'Max', 'Where', 'Div', 'Round', 'Clip', 'MatMul', 'Cast', 'Cast', 'Mul', 'Mul', 'Add')


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(raw):
    return {'bytes': len(raw), 'sha256': sha(raw)}


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)+'\n').encode('utf-8')


def json_read(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, 'duplicate JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def safe_relative(name):
    need(type(name) is str and name and not any(c in name for c in '\\:\0\r\n'), 'unsafe relative name')
    path = PurePosixPath(name)
    need(not path.is_absolute() and str(path) == name and all(x not in ('', '.', '..') for x in name.split('/')),
         'unsafe relative path')
    need(all(x == x.rstrip(' .') for x in path.parts), 'unsafe trailing Windows path character')
    return path


def checked_file(path, limit=512*1024*1024):
    path = Path(path).absolute()
    need(not any(p.is_symlink() for p in (path, *path.parents)), 'linked source/output refused')
    info = path.stat()
    need(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit, 'missing/nonregular/oversized source')
    return path.read_bytes()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def shape(index):
    return (1, 192, 1000) if index == 48 else ((197, 192, 576), (197, 192, 192),
                                              (197, 192, 576), (197, 576, 192))[index % 4]


def wanted_members():
    names = {'RELEASE.json', 'parameters/layers.json', 'parameters/all_fp32.npz',
             'parameters/parameter_index.json', 'models/final/model_w8a8.onnx', 'samples/samples.json'}
    names |= {'models/final/weights/layer_%02d.npz' % i for i in range(49)}
    names |= {'weights_axi/layer_%02d/%s' % (i, n) for i in range(49)
              for n in ('W_dense_data.bin', 'W_dense_meta.bin')}
    names |= {'weights_axi/layer_%02d/%s' % (i, n) for i in SELECTED
              for n in ('W_sparse_data.bin', 'W_sparse_meta.bin')}
    names |= {'samples/'+s+'_'+role+'.npy' for s in SAMPLES for role in ('input', 'logits')}
    return names


def load_sources(source_zip):
    path = Path(source_zip).absolute()
    need(not any(p.is_symlink() for p in (path, *path.parents)) and path.is_file(), 'ordinary source ZIP required')
    need(file_sha(path) == ZIP_SHA, 'fixed source ZIP identity differs')
    selected, prefix = {}, ARCHIVE_ROOT+'/'
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        need(0 < len(infos) <= 2048, 'ZIP member count')
        seen, folded, total = {}, set(), 0
        for item in infos:
            name = item.filename[:-1] if item.is_dir() else item.filename
            safe_relative(name)
            need(name == ARCHIVE_ROOT or name.startswith(prefix), 'ZIP root differs')
            need(name not in seen and name.casefold() not in folded, 'duplicate/case-colliding ZIP member')
            kind = stat.S_IFMT(item.external_attr >> 16)
            need(kind in ((0, stat.S_IFDIR) if item.is_dir() else (0, stat.S_IFREG)), 'linked/special ZIP member')
            need(not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                 'encrypted/unsupported ZIP member')
            need(0 <= item.file_size <= 128*1024*1024, 'oversized ZIP member')
            total += item.file_size
            need(total <= 512*1024*1024, 'oversized ZIP expansion')
            seen[name], _ = item, folded.add(name.casefold())
        manifest_raw = archive.read(seen[prefix+'MANIFEST.json'])
        need(sha(manifest_raw) == B_MANIFEST_SHA, 'fixed B MANIFEST identity differs')
        manifest = json_read(manifest_raw)
        need(manifest.get('schema_version') == 'B_RELEASE_SHA256_V1' and manifest.get('algorithm') == 'SHA-256' and
             manifest.get('file_count') == len(manifest['files']) == 765, 'B manifest schema/count')
        need(archive.read(seen[prefix+'MANIFEST.sha256']).decode('ascii').split() == [B_MANIFEST_SHA, 'MANIFEST.json'],
             'B MANIFEST sidecar differs')
        actual = {n[len(prefix):] for n, item in seen.items() if not item.is_dir()}
        need(actual == set(manifest['files']) | {'MANIFEST.json', 'MANIFEST.sha256', 'work/DELIVERY_RECEIPT.json'},
             'B ZIP inventory differs')
        wanted = wanted_members()
        need(wanted <= set(manifest['files']), 'required B source missing')
        for name in sorted(actual):
            safe_relative(name)
            digest, count, chunks = hashlib.sha256(), 0, []
            with archive.open(seen[prefix+name]) as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(chunk); count += len(chunk)
                    if name in wanted:
                        chunks.append(chunk)
            if name in manifest['files']:
                need({'bytes': count, 'sha256': digest.hexdigest()} == manifest['files'][name],
                     'source member SHA/length differs: '+name)
            if name in wanted:
                selected[name] = b''.join(chunks)
    need(file_sha(path) == ZIP_SHA, 'source ZIP changed during read')
    return {'manifest_raw': manifest_raw, 'files': selected,
            'archive_audit': {'zip_sha256': ZIP_SHA, 'CRC_files_checked': len(actual),
                              'manifest_files_SHA_checked': len(manifest['files']), 'pt_handling': 'OPAQUE_HASH_ONLY_NOT_EXTRACTED',
                              'package_code_executed': False}}


def tensors(model):
    need(len({t.name for t in model.graph.initializer}) == len(model.graph.initializer), 'duplicate ONNX initializer')
    need(all(t.data_location == TensorProto.DEFAULT for t in model.graph.initializer), 'external ONNX data refused')
    return {t.name: numpy_helper.to_array(t) for t in model.graph.initializer}


def npz_arrays(raw):
    with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
        need(len(archive.files) == len(set(archive.files)) and len(archive.files) <= 160, 'NPZ inventory')
        result = {name: archive[name] for name in archive.files}
    need(all(a.dtype.kind != 'O' for a in result.values()), 'object array refused')
    return result


def sparse_pack(arrays, weight):
    k, n = weight.shape
    positions, values, mask = arrays['positions'], arrays['values'], arrays['mask']
    need(positions.dtype == np.uint8 and values.dtype == np.int8 and mask.dtype == np.bool_ and
         positions.shape == values.shape == (k//4, n, 2) and mask.shape == weight.shape and
         np.all(positions[..., 0] < positions[..., 1]) and np.all(positions < 4), 'invalid sparse positions/mask')
    restored, expected_mask = np.zeros_like(weight), np.zeros(weight.shape, np.bool_)
    cols = np.arange(n)
    for g in range(k//4):
        for slot in (0, 1):
            rows = 4*g+positions[g, :, slot]
            restored[rows, cols] = values[g, :, slot]
            expected_mask[rows, cols] = True
    need(np.array_equal(restored, weight) and np.array_equal(expected_mask, mask), 'sparse reconstruction/mask differs')
    need(np.all(arrays['W_fp32'][~mask] == 0), 'unmasked FP32 value survived pruning')
    codes = (positions[..., 0] | (positions[..., 1] << 2)).ravel(order='C')
    metadata = (codes[::2] | (codes[1::2] << 4)).astype(np.uint8).tobytes()
    raw_values = values.tobytes(order='C')
    # Independent signed edge oracle, decoded again from physical bytes.
    packed = np.frombuffer(metadata, np.uint8)
    decoded = np.empty(codes.size, np.uint8)
    decoded[::2], decoded[1::2] = packed & 15, packed >> 4
    pos = np.stack((decoded & 3, decoded >> 2), axis=-1).reshape(k//4, n, 2)
    val = np.frombuffer(raw_values, np.int8).reshape(k//4, n, 2)
    x = np.empty((6, k), np.int8)
    x[0], x[1], x[2] = -128, 127, 0
    x[3] = np.arange(k, dtype=np.int64).astype(np.int8)
    x[4] = np.where(np.arange(k) % 2, -128, 127).astype(np.int8)
    x[5] = np.random.default_rng(20261027+k+n).integers(-128, 128, k, dtype=np.int16).astype(np.int8)
    expected = x.astype(np.int64) @ weight.astype(np.int64)
    actual = np.zeros((6, n), np.int64)
    for g in range(k//4):
        for slot in (0, 1):
            actual += x[:, 4*g+pos[g, :, slot]].astype(np.int64)*val[g, :, slot].astype(np.int64)
    need(np.array_equal(actual, expected) and np.min(expected) >= -(1 << 31) and np.max(expected) < (1 << 31),
         'independent full-K/decoded-slot INT64 arithmetic differs')
    return raw_values, metadata, expected.size


def quantizer_pattern(index, activation, output):
    p = 'BQL_%d_' % index
    return [([activation], [p+'abs']), ([p+'abs', p+'axes'], [p+'max']),
            ([p+'max', p+'zero'], [p+'iszero']), ([p+'max', p+'127'], [p+'scaled']),
            ([p+'scaled', p+'tiny'], [p+'floor']), ([p+'iszero', p+'one', p+'floor'], [p+'sx']),
            ([activation, p+'sx'], [p+'xd']), ([p+'xd'], [p+'rne']),
            ([p+'rne', p+'lo', p+'127'], [p+'xq']), ([p+'xq', p+'Wf'], [p+'yi']),
            ([p+'yi'], [p+'i32']), ([p+'i32'], [p+'f']), ([p+'f', p+'sx'], [p+'t1']),
            ([p+'t1', p+'sf'], [p+'t2']), ([p+'t2', p+'bf'], [output])]


def convert_graph(reference, layers, weights, scales, biases):
    need(sha(reference) == REFERENCE_SHA, 'fixed new reference identity differs')
    original = onnx.load_model_from_string(reference)
    need(all(not n.domain for n in original.graph.node), 'unexpected original custom domain')
    out, arrays = copy.deepcopy(original), tensors(original)
    nodes = list(original.graph.node)
    lookup = {n.name: j for j, n in enumerate(nodes)}
    need(len(lookup) == len(nodes), 'duplicate ONNX node name')
    consumers = {}
    for j, node in enumerate(nodes):
        for name in node.input:
            consumers.setdefault(name, set()).add(j)
    starts, saved = {}, {}
    for i, layer in enumerate(layers):
        prefix, start = 'BQL_%d_' % i, lookup['BQL_%d_abs' % i]
        need(np.array_equal(arrays[prefix+'Wf'], weights[i].astype(np.float32)) and
             arrays[prefix+'sf'].tobytes() == scales[i].tobytes() and
             arrays[prefix+'bf'].tobytes() == biases[i].tobytes(), 'custom replacement lost new B W/scale/bias')
        group = nodes[start:start+15]
        pattern = quantizer_pattern(i, layer['activation_fp32'], layer['float_output'])
        need([n.name for n in group] == [prefix+s for s in SUFFIXES] and [n.op_type for n in group] == list(OPS),
             'new B quantizer node sequence differs')
        for n, (inputs, outputs) in zip(group, pattern):
            need(list(n.input) == inputs and list(n.output) == outputs, 'new B quantizer wiring differs')
        need({a.name: helper.get_attribute_value(a) for a in group[1].attribute} == {'keepdims': 1} and
             {a.name: helper.get_attribute_value(a) for a in group[10].attribute} == {'to': TensorProto.INT32} and
             {a.name: helper.get_attribute_value(a) for a in group[11].attribute} == {'to': TensorProto.FLOAT} and
             all(not n.attribute for j, n in enumerate(group) if j not in (1, 10, 11)), 'quantizer attributes differ')
        for n in group[:-1]:
            for value in n.output:
                need(consumers.get(value, set()) <= set(range(start, start+15)) and
                     value not in {v.name for v in original.graph.output}, 'quantizer intermediate escaped its group')
        expected_constants = {'axes': np.array([-1], np.int64), 'zero': np.array(0, np.float32),
                              '127': np.array(127, np.float32), 'tiny': np.array(np.finfo(np.float32).tiny, np.float32),
                              'one': np.array(1, np.float32), 'lo': np.array(-127, np.float32)}
        for suffix, expected in expected_constants.items():
            need(arrays[prefix+suffix].dtype == expected.dtype and np.array_equal(arrays[prefix+suffix], expected),
                 'quantizer constant differs')
        m, k, n = shape(i)
        custom = helper.make_node('QuantLinear', [layer['activation_fp32'], prefix+'Wq', prefix+'sw', prefix+'bias'],
                                  [layer['float_output']], name='BQL_%d' % i, domain='com.kv260', M=m, K=k, N=n,
                                  W_sha256=sha(weights[i].tobytes()), family=layer['family'], layer_id=layer['layer_id'],
                                  layer_index=i, quant_protocol_id='B_W8A8_ROW_DYNAMIC_V1',
                                  output_shape=[1, n] if i == 48 else [1, m, n])
        starts[start], saved[i] = custom, group
    rebuilt, j = [], 0
    while j < len(nodes):
        if j in starts:
            rebuilt.append(starts[j]); j += 15
        else:
            rebuilt.append(copy.deepcopy(nodes[j])); j += 1
    del out.graph.node[:]; out.graph.node.extend(rebuilt)
    nonlinear = [t for t in original.graph.initializer if not t.name.startswith('BQL_')]
    need(len(nonlinear) == 54, 'exactly 54 original new non-Linear tensors required')
    del out.graph.initializer[:]; out.graph.initializer.extend(copy.deepcopy(nonlinear))
    for i in range(49):
        for suffix, a in (('Wq', weights[i]), ('sw', scales[i]), ('bias', biases[i])):
            out.graph.initializer.append(numpy_helper.from_array(np.ascontiguousarray(a), 'BQL_%d_%s' % (i, suffix)))
    out.opset_import.append(helper.make_opsetid('com.kv260', 1))
    valid = {v.name for v in out.graph.input} | {t.name for t in out.graph.initializer} | {
        v for n in out.graph.node for v in n.output}
    infos = [v for v in out.graph.value_info if v.name in valid]
    del out.graph.value_info[:]; out.graph.value_info.extend(infos)
    onnx.checker.check_model(out)
    need([n.SerializeToString() for n in out.graph.node if n.domain != 'com.kv260'] ==
         [n.SerializeToString() for n in nodes if not n.name.startswith('BQL_')], 'non-Linear node bytes changed')
    need([t.SerializeToString() for t in out.graph.initializer[:54]] == [t.SerializeToString() for t in nonlinear],
         'new non-Linear initializer bytes changed')
    # Reversible source transform, not reuse of any old model graph.
    reverse = copy.deepcopy(out)
    restored_nodes = []
    for node in out.graph.node:
        if node.domain == 'com.kv260':
            i = next(a.i for a in node.attribute if a.name == 'layer_index')
            restored_nodes.extend(copy.deepcopy(saved[i]))
        else:
            restored_nodes.append(copy.deepcopy(node))
    del reverse.graph.node[:]; reverse.graph.node.extend(restored_nodes)
    del reverse.graph.initializer[:]; reverse.graph.initializer.extend(copy.deepcopy(original.graph.initializer))
    del reverse.graph.value_info[:]; reverse.graph.value_info.extend(copy.deepcopy(original.graph.value_info))
    del reverse.opset_import[:]; reverse.opset_import.extend(copy.deepcopy(original.opset_import))
    need(reverse.SerializeToString() == original.SerializeToString(), 'reverse custom transform does not reconstruct exact B model')
    return out.SerializeToString(), {'custom_operator': 'com.kv260::QuantLinear', 'custom_layers': 49,
        'nonlinear_nodes_preserved': len(nodes)-49*15, 'nonlinear_parameters_preserved': 54,
        'reverse_transform_exact_reference': True, 'optional_QKV_layout_GELU_fusions': 'NOT_ENABLED'}


def generic_integer_expansion(custom_raw):
    """Independent standard-ONNX implementation of generic C QuantLinear semantics."""
    out = onnx.load_model_from_string(custom_raw)
    expanded = []
    for node in out.graph.node:
        if node.domain != 'com.kv260':
            expanded.append(copy.deepcopy(node)); continue
        need(node.op_type == 'QuantLinear' and len(node.input) == 4 and len(node.output) == 1, 'unsupported custom op')
        i = next(a.i for a in node.attribute if a.name == 'layer_index')
        p, x, w, sw, bias = 'B24_CPU_%d_' % i, *node.input
        for suffix, value in (('axes', np.array([-1], np.int64)), ('zero', np.array(0, np.float32)),
                              ('one', np.array(1, np.float32)), ('tiny', np.array(np.finfo(np.float32).tiny, np.float32)),
                              ('limit', np.array(127, np.float32)), ('low', np.array(-127, np.float32))):
            out.graph.initializer.append(numpy_helper.from_array(value, p+suffix))
        def add(op, inputs, name, **attrs):
            expanded.append(helper.make_node(op, inputs, [p+name], name=p+name, **attrs))
        add('Abs', [x], 'abs'); add('ReduceMax', [p+'abs', p+'axes'], 'max', keepdims=1)
        add('Equal', [p+'max', p+'zero'], 'iszero'); add('Div', [p+'max', p+'limit'], 'scaled')
        add('Max', [p+'scaled', p+'tiny'], 'floor'); add('Where', [p+'iszero', p+'one', p+'floor'], 'sx')
        add('Div', [x, p+'sx'], 'div'); add('Round', [p+'div'], 'rne')
        add('Clip', [p+'rne', p+'low', p+'limit'], 'clip')
        add('Cast', [p+'clip'], 'xq', to=TensorProto.INT8)
        add('MatMulInteger', [p+'xq', w], 'yi32'); add('Cast', [p+'yi32'], 'yf', to=TensorProto.FLOAT)
        add('Mul', [p+'yf', p+'sx'], 't1'); add('Mul', [p+'t1', sw], 't2')
        expanded.append(helper.make_node('Add', [p+'t2', bias], list(node.output), name=p+'output'))
    del out.graph.node[:]; out.graph.node.extend(expanded)
    imports = [v for v in out.opset_import if v.domain != 'com.kv260']
    del out.opset_import[:]; out.opset_import.extend(imports)
    onnx.checker.check_model(out)
    return out.SerializeToString()


def attributes(node):
    return {a.name: helper.get_attribute_value(a) for a in node.attribute}


def array_npy(array):
    stream = io.BytesIO()
    np.save(stream, array, allow_pickle=False)
    return stream.getvalue()


def topology_signature(model):
    """Parameter-name connections plus constant values; excludes only W hashes."""
    arrays = tensors(model)
    learned = {t.name for t in model.graph.initializer
               if not t.name.startswith('BQL_') and not t.name.startswith('/')}
    def value(name):
        if name == '/Expand_output_0':
            return {'learned_alias': 'cls_token'}
        if name in arrays and name not in learned and not name.startswith('BQL_'):
            array = arrays[name]
            return {'constant_dtype': str(array.dtype), 'shape': list(array.shape), 'hex': array.tobytes().hex()}
        return name
    def attribute(item):
        raw = helper.get_attribute_value(item)
        return raw.decode('utf8') if isinstance(raw, bytes) else raw
    return [{'name': n.name, 'domain': n.domain, 'op': n.op_type,
             'inputs': [value(name) for name in n.input], 'outputs': list(n.output),
             'attributes': {a.name: attribute(a) for a in n.attribute if a.name != 'W_sha256'}}
            for n in model.graph.node]


def constant_fold(model):
    """Only evaluate all-constant standard nodes; never use old model values."""
    out = copy.deepcopy(model)
    arrays = tensors(out)
    kept, derived = [], {}
    for node in out.graph.node:
        a = attributes(node)
        values = [arrays.get(name) for name in node.input]
        result = None
        if not node.domain and node.op_type == 'Constant':
            need(set(a) == {'value'}, 'non-Tensor constant refused')
            result = numpy_helper.to_array(a['value'])
        elif not node.domain and values and all(v is not None for v in values):
            if node.op_type == 'Unsqueeze':
                result = np.expand_dims(values[0], tuple(int(x) for x in values[1]))
            elif node.op_type == 'Concat':
                result = np.concatenate(values, axis=a['axis'])
            elif node.op_type == 'Reshape':
                need(a.get('allowzero', 0) == 0 and (values[1] != 0).all(), 'unsupported constant reshape')
                result = values[0].reshape(tuple(int(x) for x in values[1]))
            elif node.op_type == 'Shape':
                need(not a, 'constant Shape attributes')
                result = np.array(values[0].shape, np.int64)
            elif node.op_type == 'ConstantOfShape':
                value = numpy_helper.to_array(a['value']) if 'value' in a else np.array([0], np.float32)
                result = np.full(tuple(int(x) for x in values[0]), value.item(), dtype=value.dtype)
            elif node.op_type == 'Mul':
                result = values[0] * values[1]
            elif node.op_type == 'Equal':
                result = values[0] == values[1]
            elif node.op_type == 'Where':
                result = np.where(*values)
            elif node.op_type == 'Expand':
                target = np.broadcast_shapes(values[0].shape, tuple(int(x) for x in values[1]))
                result = np.broadcast_to(values[0], target).copy()
        if result is None:
            kept.append(copy.deepcopy(node))
        else:
            need(len(node.output) == 1 and node.output[0] not in arrays, 'constant output alias')
            arrays[node.output[0]] = np.ascontiguousarray(result) if result.ndim else result.copy()
            derived[node.output[0]] = numpy_helper.from_array(arrays[node.output[0]], node.output[0])
    del out.graph.node[:]; out.graph.node.extend(kept)
    out.graph.initializer.extend(derived.values())
    return out, arrays, derived


def fuse_graph(generic_raw):
    """Fuse strict patterns from the NEW graph, not from an old graph template."""
    original = onnx.load_model_from_string(generic_raw)
    out, arrays, derived = constant_fold(original)
    nodes = list(out.graph.node)
    by_name = {n.name: n for n in nodes}
    need(len(by_name) == len(nodes), 'duplicate fusion node')
    replacement, removed, certificates = {}, set(), []

    def match(name, op, inputs=None, attrs=None):
        node = by_name[name]
        need(not node.domain and node.op_type == op and
             (inputs is None or list(node.input) == inputs) and
             (attrs is None or attributes(node) == attrs), 'fusion pattern differs: '+name)
        return node

    def constant(name, expected):
        need(name in arrays and arrays[name].dtype == expected.dtype and
             arrays[name].shape == expected.shape and arrays[name].tobytes() == expected.tobytes(),
             'fusion constant differs: '+name)

    for block in range(12):
        p = '/blocks/blocks.%d/' % block
        q = by_name['BQL_%d' % (4*block)]
        need(q.domain == 'com.kv260' and q.op_type == 'QuantLinear' and len(q.output) == 1, 'QKV origin')
        reshape = match(p+'attn/Reshape', 'Reshape', attrs={'allowzero': 0})
        need(reshape.input[0] == q.output[0], 'QKV reshape input')
        constant(reshape.input[1], np.array([1, 197, 3, 3, 64], np.int64))
        trans = match(p+'attn/Transpose', 'Transpose', [reshape.output[0]], {'perm': [2, 0, 3, 1, 4]})
        split = match(p+'attn/Split', 'Split', attrs={'axis': 0})
        need(split.input[0] == trans.output[0] and len(split.output) == 3, 'QKV split input/count')
        constant(split.input[1], np.array([1, 1, 1], np.int64))
        squeezes = []
        for part, suffix in enumerate(('', '_1', '_2')):
            sq = match(p+'attn/Squeeze'+suffix, 'Squeeze', attrs={})
            need(sq.input[0] == split.output[part], 'QKV squeeze part')
            constant(sq.input[1], np.array([0], np.int64)); squeezes.append(sq)
        fused = copy.deepcopy(q); fused.op_type = 'QuantQKV'
        del fused.output[:]; fused.output.extend(s.output[0] for s in squeezes)
        replacement[q.name] = fused
        group = [reshape, trans, split, *squeezes]
        removed.update(n.name for n in group)
        certificates.append({'kind': 'QuantQKV', 'block': block,
                             'removed_SHA256': sha(b''.join(n.SerializeToString() for n in group))})

        merge_trans = match(p+'attn/Transpose_2', 'Transpose', attrs={'perm': [0, 2, 1, 3]})
        merge = match(p+'attn/Reshape_1', 'Reshape', attrs={'allowzero': 0})
        need(merge.input[0] == merge_trans.output[0], 'attention merge wiring')
        constant(merge.input[1], np.array([1, 197, 192], np.int64))
        replacement[merge_trans.name] = helper.make_node('AttentionMerge4', [merge_trans.input[0]],
            list(merge.output), name=p+'attn/AttentionMerge4', domain='com.kv260')
        removed.add(merge.name)

        fc1 = by_name['BQL_%d' % (4*block+2)]
        fc2 = by_name['BQL_%d' % (4*block+3)]
        div = match(p+'mlp/act/Div', 'Div', attrs={})
        need(div.input[0] == fc1.output[0], 'GELU fc1 boundary')
        constant(div.input[1], np.array(np.sqrt(2), np.float32))
        erf = match(p+'mlp/act/Erf', 'Erf', [div.output[0]], {})
        add = match(p+'mlp/act/Add', 'Add', attrs={})
        need(add.input[0] == erf.output[0], 'GELU Erf boundary')
        constant(add.input[1], np.array(1, np.float32))
        mul = match(p+'mlp/act/Mul', 'Mul', [fc1.output[0], add.output[0]], {})
        half = match(p+'mlp/act/Mul_1', 'Mul', attrs={})
        need(half.input[0] == mul.output[0] and fc2.input[0] == half.output[0], 'GELU multiplication order')
        constant(half.input[1], np.array(0.5, np.float32))
        maxima = half.output[0]+'_absmax'
        replacement[div.name] = helper.make_node('Gelu4', [div.input[0]], [half.output[0], maxima],
            name=p+'mlp/act/Gelu4', domain='com.kv260')
        removed.update(n.name for n in (erf, add, mul, half))
        pre = copy.deepcopy(fc2); pre.op_type = 'QuantLinearPremax'; pre.input.append(maxima)
        replacement[fc2.name] = pre
        certificates.append({'kind': 'Gelu4', 'block': block,
                             'removed_SHA256': sha(b''.join(n.SerializeToString() for n in (div, erf, add, mul, half)))})
    replaced = [replacement.get(n.name, copy.deepcopy(n)) for n in nodes if n.name not in removed]
    # Closed-subgraph enforcement: DCE must remove no data-computing node beyond
    # the explicitly validated replacements. Only shape-constant tails may die.
    producers = {v: n for n in replaced for v in n.output}
    live, pending = set(), [v.name for v in out.graph.output]
    while pending:
        value = pending.pop()
        if value in producers and producers[value].name not in live:
            node = producers[value]; live.add(node.name); pending.extend(node.input)
    dead = [n for n in replaced if n.name not in live]
    need(all(n.op_type in ('Constant', 'Shape', 'Unsqueeze', 'Concat', 'Reshape', 'Slice') and
             not n.domain for n in dead), 'fusion discarded unexpected computing/Linear node')
    final = [n for n in replaced if n.name in live]
    del out.graph.node[:]; out.graph.node.extend(final)
    used = {v for n in final for v in n.input}
    originals = list(original.graph.initializer)
    # Keep all 54 new learned non-Linear parameters (including cls_token), even
    # when the class-token Expand has become an identical constant alias.
    del out.graph.initializer[:]
    out.graph.initializer.extend(copy.deepcopy(originals))
    out.graph.initializer.extend(copy.deepcopy(t) for name, t in derived.items() if name in used)
    need(arrays['/Expand_output_0'].tobytes() == arrays['cls_token'].tobytes() and
         arrays['/Expand_output_0'].shape == arrays['cls_token'].shape, 'new cls_token alias changed')
    need([t.SerializeToString() for t in out.graph.initializer[:len(originals)]] ==
         [t.SerializeToString() for t in originals], 'fusion changed any of 201 new parameter tensors')
    valid = used | {v for n in final for v in n.output} | {v.name for v in out.graph.input}
    infos = [v for v in out.graph.value_info if v.name in valid]
    del out.graph.value_info[:]; out.graph.value_info.extend(infos)
    from collections import Counter
    counts = Counter(n.op_type for n in final if n.domain == 'com.kv260')
    need(counts == {'QuantQKV': 12, 'AttentionMerge4': 12, 'Gelu4': 12,
                    'QuantLinearPremax': 12, 'QuantLinear': 25}, 'fused op inventory')
    need(len(final) == 191, 'H576 aligned topology must have 191 nodes')
    need(sha(json_bytes(topology_signature(out))) == ALIGNED_TOPOLOGY_SHA,
         'aligned graph differs from independently pinned H576 topology')
    onnx.checker.check_model(out)
    return out.SerializeToString(), {'optional_QKV_layout_GELU_fusions': 'H576_ALIGNED_ENABLED',
        'fusion_source': 'NEW_B24_GENERIC_GRAPH_ONLY', 'nodes': len(final), 'custom_op_counts': dict(counts),
        'all201_new_parameter_TensorProtos_preserved': True, 'new_cls_token_folded_alias_exact': True,
        'GELU_FP32_order': 'Div_sqrt2_Erf_Add1_Mul_x_Mul_half_NO_REASSOCIATION',
        'validated_subgraphs': certificates, 'constant_nodes_folded': len(derived),
        'semantic_topology_sha256': sha(json_bytes(topology_signature(out))),
        'semantic_topology_comparison': '191_NODES_MATCH_OLD_H576_WITH_NEW_PARAMETER_BINDINGS',
        'old_topology_comparison_source_sha256': '8a462d73b5e8bb6544f65c6187dc621b560ae1329520f5556430ff42c97f0dfa',
        'old_model_parameters_or_graph_used': False}


def integer_expansion(custom_raw):
    """Independent standard ONNX semantics for all existing H576 fused C ops."""
    out = onnx.load_model_from_string(custom_raw)
    expanded = []
    def tensor(name, array):
        out.graph.initializer.append(numpy_helper.from_array(array, name))
        return name
    def node(op, inputs, outputs, name, **attrs):
        expanded.append(helper.make_node(op, inputs, outputs, name=name, **attrs))
    for op in list(out.graph.node):
        if op.domain != 'com.kv260':
            expanded.append(copy.deepcopy(op)); continue
        p = 'B24_ALIGNED_CPU_'+op.name.replace('/', '_')+'_'
        if op.op_type == 'AttentionMerge4':
            node('Transpose', list(op.input), [p+'trans'], p+'trans', perm=[0, 2, 1, 3])
            s = tensor(p+'shape', np.array([1, 197, 192], np.int64))
            node('Reshape', [p+'trans', s], list(op.output), p+'output', allowzero=0)
        elif op.op_type == 'Gelu4':
            x = op.input[0]
            sq = tensor(p+'sqrt2', np.array(np.sqrt(2), np.float32))
            one = tensor(p+'one', np.array(1, np.float32))
            half = tensor(p+'half', np.array(.5, np.float32))
            axes = tensor(p+'axes', np.array([0, 2], np.int64))
            node('Div', [x, sq], [p+'div'], p+'div')
            node('Erf', [p+'div'], [p+'erf'], p+'erf')
            node('Add', [p+'erf', one], [p+'add'], p+'add')
            node('Mul', [x, p+'add'], [p+'mul'], p+'mul')
            node('Mul', [p+'mul', half], [op.output[0]], p+'output')
            node('Abs', [op.output[0]], [p+'abs'], p+'abs')
            node('ReduceMax', [p+'abs', axes], [op.output[1]], p+'max', keepdims=0)
        else:
            need(op.op_type in ('QuantLinear', 'QuantQKV', 'QuantLinearPremax'), 'unknown fused custom op')
            need(len(op.input) == (5 if op.op_type == 'QuantLinearPremax' else 4), 'fused op inputs')
            x, w, sw, bias = op.input[:4]
            for suffix, value in (('axes', np.array([-1], np.int64)), ('zero', np.array(0, np.float32)),
                                  ('one', np.array(1, np.float32)), ('tiny', np.array(np.finfo(np.float32).tiny, np.float32)),
                                  ('limit', np.array(127, np.float32)), ('low', np.array(-127, np.float32))):
                tensor(p+suffix, value)
            if op.op_type == 'QuantLinearPremax':
                rs = tensor(p+'maxshape', np.array([1, 197, 1], np.int64))
                node('Reshape', [op.input[4], rs], [p+'max'], p+'max', allowzero=0)
            else:
                node('Abs', [x], [p+'abs'], p+'abs')
                node('ReduceMax', [p+'abs', p+'axes'], [p+'max'], p+'max', keepdims=1)
            for typ, ins, suffix in [('Equal', [p+'max',p+'zero'], 'iszero'),
                ('Div',[p+'max',p+'limit'],'scaled'), ('Max',[p+'scaled',p+'tiny'],'floor'),
                ('Where',[p+'iszero',p+'one',p+'floor'],'sx'), ('Div',[x,p+'sx'],'div'),
                ('Round',[p+'div'],'rne'), ('Clip',[p+'rne',p+'low',p+'limit'],'clip')]:
                node(typ, ins, [p+suffix], p+suffix)
            node('Cast',[p+'clip'],[p+'xq'],p+'xq',to=TensorProto.INT8)
            node('MatMulInteger',[p+'xq',w],[p+'yi32'],p+'yi32')
            node('Cast',[p+'yi32'],[p+'yf'],p+'yf',to=TensorProto.FLOAT)
            node('Mul',[p+'yf',p+'sx'],[p+'t1'],p+'t1')
            node('Mul',[p+'t1',sw],[p+'t2'],p+'t2')
            raw = p+'output' if op.op_type == 'QuantQKV' else op.output[0]
            node('Add',[p+'t2',bias],[raw],p+'output')
            if op.op_type == 'QuantQKV':
                shape_name=tensor(p+'shape',np.array([1,197,3,3,64],np.int64))
                split=tensor(p+'split',np.array([1,1,1],np.int64))
                axes=tensor(p+'qaxes',np.array([0],np.int64))
                node('Reshape',[raw,shape_name],[p+'reshape'],p+'reshape',allowzero=0)
                node('Transpose',[p+'reshape'],[p+'trans'],p+'trans',perm=[2,0,3,1,4])
                node('Split',[p+'trans',split],[p+'part'+str(i) for i in range(3)],p+'split',axis=0)
                for i in range(3):
                    node('Squeeze',[p+'part'+str(i),axes],[op.output[i]],p+'squeeze'+str(i))
    del out.graph.node[:]; out.graph.node.extend(expanded)
    imports=[v for v in out.opset_import if v.domain != 'com.kv260']
    del out.opset_import[:]; out.opset_import.extend(imports)
    onnx.checker.check_model(out)
    return out.SerializeToString()


def cpu_equivalence(reference, custom, sources):
    import onnxruntime as ort
    need(np.__version__ == '1.26.4' and ort.__version__ == '1.22.1', 'fixed numerical environment')
    def session(raw, threads):
        options=ort.SessionOptions(); options.intra_op_num_threads=threads; options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        for name in ('intra_op', 'inter_op'):
            options.add_session_config_entry('session.'+name+'.allow_spinning','0')
        options.log_severity_level=3
        return ort.InferenceSession(raw,options,providers=['CPUExecutionProvider'])
    expansion=integer_expansion(custom)
    base, fused, one = session(reference,4), session(expansion,4), session(reference,1)
    results=[]
    for sid in (*SAMPLES,'zero'):
        x = np.zeros((1,3,224,224),np.float32) if sid=='zero' else np.load(io.BytesIO(sources['samples/'+sid+'_input.npy']),allow_pickle=False)
        need(x.dtype==np.float32 and x.shape==(1,3,224,224) and np.isfinite(x).all(),'aligned input dtype/shape')
        actual=base.run(None,{base.get_inputs()[0].name:x})[0]
        alternate=fused.run(None,{fused.get_inputs()[0].name:x})[0]
        ref1=one.run(None,{one.get_inputs()[0].name:x})[0]
        mismatch=int(np.count_nonzero(actual.view(np.uint32)!=alternate.view(np.uint32)))
        need(mismatch==0,'aligned fused standard expansion differs at '+sid)
        if sid!='zero':
            supplied=np.load(io.BytesIO(sources['samples/'+sid+'_logits.npy']),allow_pickle=False)
            need(ref1.tobytes()==supplied.tobytes(),'B supplied thread1 provenance differs')
        results.append({'sample_id':sid,'input_raw_sha256':sha(x.tobytes()),'logits':1000,
            'reference4_raw_logits_sha256':sha(actual.tobytes()),'fused4_bitwise_mismatches':mismatch,
            'reference4_vs_reference1_bitwise_mismatches':int(np.count_nonzero(actual.view(np.uint32)!=ref1.view(np.uint32))),
            'reference4_vs_reference1_max_abs':float(np.max(np.abs(actual-ref1))),
            'top5':np.argsort(-actual[0],kind='stable')[:5].tolist()})
    return {'status':'PASS_CPU_ALIGNED4_TWO_REAL_AND_ZERO','samples':results,
        'standard_fused_integer_expansion_sha256':sha(expansion),'numpy_version':np.__version__,'ORT_version':ort.__version__,
        'ORT':{'intra':4,'inter':1,'sequential':True,'optimization':'OFF','spinning':0},
        'board_affinity_required':{'main_CPU':0,'ORT_worker_affinities':'2;3;4','owner_CPUs':[0,1,2,3]},
        'local_affinity':'NOT_APPLIED_WINDOWS_TOPOLOGY_DIFFERS','B_saved_logits':'THREAD1_PROVENANCE_ONLY',
        'scope':'CPU standard ONNX expansion, not native ARM NEON/chunkedErf/plugin/PL performance acceptance'}


def make_header(records, weights_sha, data_sha, meta_sha):
    lines = ['#pragma once', '#include <array>', '#include <cstddef>', '#include <cstdint>', 'namespace opt50_model {',
        'struct Layer { uint32_t m,k,n,mode; bool pl; size_t canonical_offset,data_offset,data_bytes,meta_offset,meta_bytes; const char *w_sha,*scale_sha,*bias_sha; };',
        'inline constexpr size_t weights_bytes=4615680, data_bytes=2654208, meta_bytes=331840;',
        'inline constexpr size_t max_x_bytes=113472, max_y_bytes=453888;',
        'inline constexpr const char *weights_sha="%s", *data_sha="%s", *meta_sha="%s", *reference_sha="%s";' %
        (weights_sha, data_sha, meta_sha, REFERENCE_SHA),
        'inline constexpr const char *source_model_sha="%s";' % REFERENCE_SHA,
        'inline constexpr std::array<Layer,49> layers={{']
    for r in records:
        line = ','.join(str(r[k]) for k in ('m', 'k', 'n', 'mode'))+','+('true' if r['route'] == 'PL' else 'false')
        line += ','+','.join(str(r[k]) for k in ('canonical_offset', 'w_data_offset', 'w_data_bytes', 'w_meta_offset', 'w_meta_bytes'))
        line += ',"%s","%s","%s"' % (r['Wq_sha256'], r['sw_sha256'], r['bias_sha256'])
        lines.append('    {'+line+'},')
    return ('\n'.join(lines+['}};', '} // namespace opt50_model', ''])).encode('ascii')


def generic_cpu_equivalence(reference, custom, sources):
    import onnxruntime as ort
    need(np.__version__ == '1.26.4' and ort.__version__ == '1.22.1', 'fixed NumPy1.26.4/ORT1.22.1 required')
    def session(raw):
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        for name in ('intra_op', 'inter_op'):
            options.add_session_config_entry('session.'+name+'.allow_spinning', '0')
        options.log_severity_level = 3
        return ort.InferenceSession(raw, options, providers=['CPUExecutionProvider'])
    expansion = integer_expansion(custom)
    base, integer = session(reference), session(expansion)
    samples, results = json_read(sources['samples/samples.json']), []
    need([s['id'] for s in samples] == list(SAMPLES), 'fixed two development samples required')
    for sample in samples:
        sid = sample['id']
        x = np.load(io.BytesIO(sources['samples/'+sid+'_input.npy']), allow_pickle=False)
        expected = np.load(io.BytesIO(sources['samples/'+sid+'_logits.npy']), allow_pickle=False)
        need(x.dtype == np.float32 and x.shape == (1, 3, 224, 224) and np.isfinite(x).all() and
             expected.dtype == np.float32 and expected.shape == (1, 1000) and np.isfinite(expected).all(), 'demo arrays')
        need(sha(x.tobytes()) == sample['preprocessed_sha256'], 'B demo preprocessed data hash differs')
        actual = base.run(None, {base.get_inputs()[0].name: x})[0]
        alternate = integer.run(None, {integer.get_inputs()[0].name: x})[0]
        need(actual.tobytes() == expected.tobytes(), 'local B reference differs from supplied B logits')
        need(alternate.tobytes() == actual.tobytes(), 'all49 generic integer expansion differs from full B reference')
        results.append({'sample_id': sid, 'input_raw_sha256': sha(x.tobytes()),
                        'input_file_sha256': sha(sources['samples/'+sid+'_input.npy']),
                        'logits_file_sha256': sha(sources['samples/'+sid+'_logits.npy']),
                        'logits_raw_sha256': sha(actual.tobytes()), 'logits': 1000, 'bitwise_mismatches': 0,
                        'B_reference_match': True, 'all49_integer_expansion_match': True,
                        'top5': np.argsort(-actual[0], kind='stable')[:5].tolist()})
    return {'status': 'PASS_CPU_TWO_REAL_SAMPLES_ALL49_INTEGER_EXPANSION', 'samples': results,
            'standard_integer_expansion_sha256': sha(expansion), 'numpy_version': np.__version__,
            'ORT_version': ort.__version__, 'ORT': {'intra': 1, 'inter': 1, 'sequential': True, 'optimization': 'OFF', 'spinning': 0},
            'scope': 'CPU standard-ONNX expansion of C arithmetic; NOT native ARM plugin, board execution or dataset accuracy'}


def assemble(sources):
    manifest_raw, files = sources['manifest_raw'], sources['files']
    need(sha(manifest_raw) == B_MANIFEST_SHA, 'B manifest identity differs')
    source_manifest = json_read(manifest_raw)
    need(set(files) == wanted_members(), 'selected source inventory differs')
    for name, raw in files.items():
        need(identity(raw) == source_manifest['files'][name], 'fixed source member identity differs: '+name)
    release, layers = json_read(files['RELEASE.json']), json_read(files['parameters/layers.json'])
    need(release.get('source_model_sha256', {}).get('w8a8') == REFERENCE_SHA and
         release.get('sparse_layer_indices') == list(SELECTED) and release.get('format_id') == FORMAT and
         len(layers) == 49, 'new B model/route identity differs')
    reference_raw = files['models/final/model_w8a8.onnx']
    need(sha(reference_raw) == REFERENCE_SHA, 'B reference identity differs')
    reference = onnx.load_model_from_string(reference_raw)
    onnx.checker.check_model(reference)
    initializers = tensors(reference)
    state, index = npz_arrays(files['parameters/all_fp32.npz']), json_read(files['parameters/parameter_index.json'])
    need(set(state) == set(index['parameters']) and len(state) == 152 and index['nonlinear_fp32_tensor_count'] == 54,
         'all152 new model parameter identity required')
    nonlinear = [name for name, r in index['parameters'].items() if not r['linear_tensor']]
    need(len(nonlinear) == 54, '54 new non-Linear tensors required')
    for name, a in state.items():
        pin = index['parameters'][name]
        need(a.dtype == np.float32 and np.isfinite(a).all() and list(a.shape) == pin['shape'] and sha(a.tobytes()) == pin['array_sha256'],
             'new parameter tensor differs: '+name)
        if name in nonlinear:
            need(name in initializers and initializers[name].dtype == np.float32 and
                 initializers[name].tobytes() == a.tobytes(), 'reference lost new non-Linear parameter: '+name)
    weights, scales, biases, sparse, edge_outputs = [], [], [], {}, 0
    for i, layer in enumerate(layers):
        m, k, n = shape(i)
        route, mode = ('PL' if i < 48 and i % 4 != 1 else 'PS'), int(i in SELECTED)
        need(layer['index'] == i and [layer[v] for v in ('M', 'K', 'N')] == [m, k, n] and
             layer['route'] == route and layer['mode'] == mode, 'B fixed shape/route/mode differs')
        z = npz_arrays(files['models/final/weights/layer_%02d.npz' % i])
        expected = {'W_fp32', 'Wq', 'sw', 'bias'} | ({'mask', 'positions', 'values'} if mode else set())
        need(set(z) == expected, 'B per-layer NPZ inventory differs')
        w, sw, bias = z['Wq'], z['sw'], z['bias']
        need(w.dtype == np.int8 and w.shape == (k, n) and sw.dtype == bias.dtype == np.float32 and
             sw.shape == (1, n) and bias.shape == (n,) and np.isfinite(sw).all() and np.all(sw > 0) and
             np.isfinite(bias).all() and z['W_fp32'].dtype == np.float32 and z['W_fp32'].shape == (k, n),
             'B Wq/scale/bias dtype/shape differs')
        p = 'BQL_%d_' % i
        need(initializers[p+'Wf'].dtype == np.float32 and np.array_equal(initializers[p+'Wf'], w.astype(np.float32)) and
             initializers[p+'sf'].tobytes() == sw.reshape(-1).tobytes() and
             initializers[p+'bf'].tobytes() == bias.tobytes(), 'new graph/NPZ Wq scale bias differs')
        need(np.array_equal(state[layer['layer_id']+'.weight'].T, z['W_fp32']) and
             np.array_equal(state[layer['layer_id']+'.bias'], bias), 'per-layer data lost new trained FP32 identity')
        need(np.array_equal(np.clip(np.rint(z['W_fp32']/sw), -127, 127).astype(np.int8), w),
             'W8 quantization disagrees with new FP32/scales')
        base = 'weights_axi/layer_%02d/' % i
        need(files[base+'W_dense_data.bin'] == w.tobytes() and files[base+'W_dense_meta.bin'] == bytes(16),
             'B Dense AXI bytes differ')
        if mode:
            values, meta, count = sparse_pack(z, w)
            need(values == files[base+'W_sparse_data.bin'] and meta == files[base+'W_sparse_meta.bin'], 'B Sparse AXI bytes differ')
            sparse[i], edge_outputs = (values, meta), edge_outputs+int(count)
        weights.append(np.ascontiguousarray(w)); scales.append(np.ascontiguousarray(sw.reshape(-1))); biases.append(bias.copy())
    generic, conversion = convert_graph(reference_raw, layers, weights, scales, biases)
    custom, fusion = fuse_graph(generic)
    conversion.update(fusion)
    numerical = cpu_equivalence(reference_raw, custom, files)
    canonical, data, meta = bytearray(), bytearray(), bytearray(64)
    records, bundle_layers, artifacts = [], [], {}
    for i, layer in enumerate(layers):
        m, k, n = shape(i); pl, mode = layer['route'] == 'PL', int(i in SELECTED)
        w = weights[i].tobytes()
        row = {'layer_index': i, 'layer_id': layer['layer_id'], 'family': layer['family'],
               'm': m, 'k': k, 'n': n, 'route': layer['route'], 'mode': mode,
               'canonical_offset': len(canonical), 'w_data_offset': 0, 'w_data_bytes': 0, 'w_meta_offset': 0,
               'w_meta_bytes': 0, 'Wq_sha256': sha(w), 'sw_sha256': sha(scales[i].astype('<f4').tobytes()),
               'bias_sha256': sha(biases[i].astype('<f4').tobytes())}
        canonical.extend(w)
        if pl:
            payload = sparse[i][0] if mode else w
            need(len(data) % 64 == 0, 'dual-BO data alignment')
            row.update(w_data_offset=len(data), w_data_bytes=len(payload), w_meta_offset=0, w_meta_bytes=16)
            data.extend(payload)
            if mode:
                need(len(meta) % 64 == 0, 'dual-BO metadata alignment')
                row.update(w_meta_offset=len(meta), w_meta_bytes=len(sparse[i][1])); meta.extend(sparse[i][1])
        records.append(row)
        filename = 'weights/%02d_Wq.bin' % i
        artifacts['bundle/'+filename] = w
        bundle_layers.append({'layer_index': i, 'layer_id': layer['layer_id'], 'family': layer['family'],
            'M': m, 'K': k, 'N': n, 'mode': 'sparse24_w8a8' if mode else 'dense_w8a8',
            'opt50_route': layer['route'], 'opt50_mode': mode,
            'numeric_identity': {'Wq': row['Wq_sha256'], 'sw': row['sw_sha256'], 'bias': row['bias_sha256']},
            'Wq': {'file': filename, **identity(w)}})
    need((len(canonical), len(data), len(meta)) == (4615680, 2654208, 331840), 'new dual-BO capacities differ')
    artifacts['bundle/layers.json'] = json_bytes(bundle_layers)
    artifacts['bundle/bundle_manifest.json'] = json_bytes({'schema': 'opt50_b24_runtime_bundle_v1',
        'status': 'SOFTWARE_PREPARED_NOT_BOARD_TESTED', 'source_semantic_sha256': REFERENCE_SHA,
        'logical_weights_sha256': sha(canonical), 'files': {
            name.removeprefix('bundle/'): identity(raw) for name, raw in artifacts.items()}})
    artifacts.update({'weights.bin': bytes(canonical), 'pl_w_data.bin': bytes(data), 'pl_w_meta.bin': bytes(meta),
                      'reference.onnx': reference_raw, 'graphs/model_w8a8_pl.onnx': custom,
                      'model_catalog.hpp': make_header(records, sha(canonical), sha(data), sha(meta)),
                      'source/B_MANIFEST.json': manifest_raw, 'source/B_RELEASE.json': files['RELEASE.json'],
                      'source/B_LAYERS.json': files['parameters/layers.json']})
    for sample in SAMPLES:
        for role in ('input', 'logits'):
            artifacts['inputs/'+sample+'_'+role+'.npy'] = files['samples/'+sample+'_'+role+'.npy']
    artifacts['inputs/zero_input.npy'] = array_npy(np.zeros((1, 3, 224, 224), np.float32))
    artifacts['inputs/references.json'] = json_bytes(numerical)
    need(set(artifacts) == REQUIRED and len(CORE) == 57 and len(artifacts) == 66, 'new model file closure differs')
    manifest = {'schema': SCHEMA, 'status': 'SOFTWARE_PREPARED_NOT_BOARD_TESTED', 'format': FORMAT,
        'source_model_sha256': REFERENCE_SHA, 'reference_sha256': REFERENCE_SHA, 'logical_weights_sha256': sha(canonical),
        'pl_data_sha256': sha(data), 'pl_meta_sha256': sha(meta), 'custom_graph_sha256': sha(custom),
        'routes': {'PL': 36, 'PS': 13, 'PL_dense': 12, 'PL_sparse24': 24}, 'sparse_layer_indices': list(SELECTED),
        'layers': records, 'source_pins': {'ZIP_sha256': ZIP_SHA, 'B_MANIFEST_sha256': B_MANIFEST_SHA,
            **{name: identity(raw) for name, raw in sorted(files.items())}},
        'verification': {'source_archive': sources['archive_audit'], 'all49_new_Wq_scale_bias': 'PASS',
            'all54_new_nonlinear_parameters': 'PASS', 'graph_conversion': conversion,
            'independent_INT64_vs_slot_outputs_compared': edge_outputs, 'full_model_CPU': numerical,
            'weights_in_file_have_no_BO_guards': True, 'old12_model_graph_or_parameters_reused': False},
        'hardware_executed': False, 'accuracy_verified': False, 'performance_verified': False,
        'files': {name: identity(raw) for name, raw in sorted(artifacts.items())}}
    return artifacts, manifest


def verify_package(package, expected=None, source_zip=None):
    root = Path(package).absolute()
    need(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), 'ordinary model directory required')
    manifest = json_read(checked_file(root/'manifest.json', 2*1024*1024))
    need(manifest.get('schema') == SCHEMA and manifest.get('source_model_sha256') == REFERENCE_SHA and
         manifest.get('reference_sha256') == REFERENCE_SHA and set(manifest.get('files', {})) == REQUIRED,
         'new B24 model manifest schema/identity differs')
    found = set()
    for path in root.rglob('*'):
        need(not path.is_symlink(), 'linked model member')
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
        else:
            need(path.is_dir(), 'special model member')
    need(found == REQUIRED | {'manifest.json'}, 'unlisted/missing model artifact')
    for name, pin in manifest['files'].items():
        safe_relative(name)
        need(identity(checked_file(root/name, 64*1024*1024)) == pin, 'model file changed: '+name)
    if expected is None:
        need(source_zip is not None, 'source ZIP required for independent full-source manifest reproduction')
        _, expected = assemble(load_sources(source_zip))
    need(manifest == expected, 'self-consistent model manifest differs from source-derived expected model')
    return manifest


def export_package(source_zip, output):
    output = Path(output).absolute()
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir() and
         not any(p.is_symlink() for p in output.parents), 'NEW unlinked output with existing parent required')
    artifacts, manifest = assemble(load_sources(source_zip))
    output.mkdir()
    for name, raw in artifacts.items():
        path = output/safe_relative(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            need(stream.write(raw) == len(raw), 'short model artifact write')
            stream.flush(); os.fsync(stream.fileno())
    need(file_sha(source_zip) == ZIP_SHA, 'source archive changed during output')
    for name, pin in manifest['files'].items():
        need(identity(checked_file(output/name, 64*1024*1024)) == pin, 'output readback differs')
    with (output/'manifest.json').open('xb') as stream:
        raw = json_bytes(manifest)
        need(stream.write(raw) == len(raw), 'short manifest write')
        stream.flush(); os.fsync(stream.fileno())
    verify_package(output, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('export', 'verify'):
        sub = commands.add_parser(name)
        sub.add_argument('--source-zip', type=Path, required=True)
        sub.add_argument('--out' if name == 'export' else '--package', type=Path,
                         default=HERE/'model' if name == 'export' else None, required=name == 'verify')
    args = parser.parse_args(argv)
    manifest = export_package(args.source_zip, args.out) if args.command == 'export' else verify_package(args.package, source_zip=args.source_zip)
    return {'status': manifest['status'], 'schema': SCHEMA, 'routes': manifest['routes'],
            'logical_weights_sha256': manifest['logical_weights_sha256'], 'reference_sha256': REFERENCE_SHA,
            'pl_data_sha256': manifest['pl_data_sha256'], 'pl_meta_sha256': manifest['pl_meta_sha256'],
            'custom_graph_sha256': manifest['custom_graph_sha256'], 'files': len(manifest['files']),
            'manifest_sha256': sha(json_bytes(manifest)), 'hardware_executed': False}


if __name__ == '__main__':
    try:
        print(json.dumps(main(), indent=2))
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        raise SystemExit('REFUSED: '+str(error))
