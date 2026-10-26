"""Fixed OPT50 mixed Dense/Sparse model API; no loader, STOP or recovery.

Verification is read-only and precedes CDLL loading, owner connection and ORT
session creation. Only this release's graph is accepted. Runtime failures never
replay a RUN; close destroys the ORT session before the plugin/client handle.
The manifest is a released-artifact contract, not a cryptographic signature.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading

SCHEMA = 'opt50_full_model_mixed_bundle_v1'
MANIFEST_SHA = 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb'
WIRE_VERSION = 50
GRAPH = 'graphs/model_w8a8_pl.onnx'
SPARSE = frozenset((26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47))
REQUIRED = {'weights.bin', 'pl_w_data.bin', 'pl_w_meta.bin', 'reference.onnx', 'model_catalog.hpp',
            GRAPH, 'bundle/bundle_manifest.json', 'bundle/layers.json'} | {
                'bundle/weights/%02d_Wq.bin' % index for index in range(49)}
REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def decode_json(raw):
    def pairs(items):
        result = {}
        for name, value in items:
            require(name not in result, 'duplicate JSON key: ' + name)
            result[name] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON value')))


def ordinary(path):
    path = Path(path)
    require('..' not in path.parts, 'parent traversal refused')
    path = path.absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & REPARSE,
                'symlink/reparse path refused')
    return path


def read_file(path, limit=64 * 1024 * 1024):
    path = ordinary(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
            'single-link bounded ordinary file required')
    # Python 3.12 Windows lstat/fstat disagree about st_ctime semantics. Keep
    # inode/size/mtime plus the consistent birth time there; Linux uses ctime.
    key = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                        info.st_birthtime_ns if os.name == 'nt' else info.st_ctime_ns)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        require(key(os.fstat(stream.fileno())) == key(before), 'file replaced before read')
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and key(os.fstat(stream.fileno())) == key(before),
                'file changed during read')
    require(key(path.lstat()) == key(before), 'file replaced after read')
    return raw


def relative(name):
    require(type(name) is str, 'nonstring model path')
    path = PurePosixPath(name)
    require(str(path) == name and not path.is_absolute() and
            all(item not in ('', '.', '..') for item in name.split('/')) and
            not any(char in name for char in '\\:\0\r\n'), 'unsafe model relative path')
    return path


def pin(value):
    require(type(value) is dict and set(value) == {'bytes', 'sha256'} and
            type(value['bytes']) is int and 0 <= value['bytes'] <= 64 * 1024 * 1024 and
            type(value['sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['sha256']),
            'invalid model file identity')
    return value


def inventory(root):
    result, pending, directories = set(), [root], 0
    while pending:
        folder = pending.pop(); directories += 1
        require(directories <= 256, 'model directory inventory too large')
        for path in folder.iterdir():
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & REPARSE,
                    'linked model member refused')
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            else:
                require(stat.S_ISREG(info.st_mode), 'nonregular model member refused')
                result.add(path.relative_to(root).as_posix())
                require(len(result) <= 128, 'model file inventory too large')
    return result


def shape(index):
    if index == 48:
        return 1, 192, 1000
    return ((197, 192, 576), (197, 192, 192), (197, 192, 576), (197, 576, 192))[index % 4]


def verify_layers(manifest, raw):
    records = manifest.get('layers')
    bundle = decode_json(raw['bundle/bundle_manifest.json'])
    layers = decode_json(raw['bundle/layers.json'])
    require(type(records) is list and type(layers) is list and len(records) == len(layers) == 49,
            'exactly 49 ordered layer records required')
    expected_files = {'layers.json'} | {'weights/%02d_Wq.bin' % index for index in range(49)}
    require(type(bundle) is dict and type(bundle.get('files')) is dict and
            set(bundle['files']) == expected_files and
            bundle.get('source_semantic_sha256') == manifest['reference_sha256'], 'bundle manifest identity differs')
    for name, expected in bundle['files'].items():
        require(pin(expected) == manifest['files']['bundle/' + name], 'root/bundle artifact pins differ')
    joined, canonical_offset = [], 0
    data, metadata = raw['pl_w_data.bin'], raw['pl_w_meta.bin']
    require(len(metadata) >= 64 and metadata[:64] == bytes(64), 'fixed Dense metadata zero prefix required')
    for index, (record, layer) in enumerate(zip(records, layers)):
        m, k, n = shape(index)
        route = 'PL' if index < 48 and index % 4 != 1 else 'PS'
        mode = int(index in SPARSE)
        require(type(record) is dict and type(layer) is dict and type(record.get('layer_index')) is int and
                type(layer.get('layer_index')) is int and record['layer_index'] == layer['layer_index'] == index and
                [record.get(key) for key in ('m', 'k', 'n')] == [m, k, n] and
                [layer.get(key) for key in ('M', 'K', 'N')] == [m, k, n] and
                all(type(record[key]) is int for key in ('m', 'k', 'n', 'mode', 'canonical_offset',
                    'w_data_offset', 'w_data_bytes', 'w_meta_offset', 'w_meta_bytes')) and
                record['canonical_offset'] == canonical_offset and record.get('route') == route and
                record['mode'] == mode and layer.get('opt50_route') == route and layer.get('opt50_mode') == mode,
                'fixed ordered layer shape/route/mode differs')
        name = 'weights/%02d_Wq.bin' % index
        weight = raw['bundle/' + name]
        require(len(weight) == k*n and type(layer.get('Wq')) is dict and
                layer['Wq'].get('file') == name and
                {key: layer['Wq'].get(key) for key in ('bytes', 'sha256')} == identity(weight),
                'canonical per-layer Wq mismatch')
        numeric = layer.get('numeric_identity')
        require(type(numeric) is dict and set(numeric) == {'Wq', 'sw', 'bias'} and
                numeric['Wq'] == record.get('Wq_sha256') == identity(weight)['sha256'] and
                numeric['sw'] == record.get('sw_sha256') and numeric['bias'] == record.get('bias_sha256') and
                all(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) for value in numeric.values()),
                'layer Wq/scale/bias identities differ')
        joined.append(weight); canonical_offset += len(weight)
        offset, count, moffset, mcount = (record[key] for key in (
            'w_data_offset', 'w_data_bytes', 'w_meta_offset', 'w_meta_bytes'))
        if route == 'PS':
            require((offset, count, moffset, mcount) == (0, 0, 0, 0), 'PS layer must not claim PL storage')
            continue
        require(offset >= 0 and offset % 64 == 0 and moffset >= 0 and moffset % 64 == 0 and
                count == k*n//(2 if mode else 1) and mcount == (k*n//8 if mode else 16) and
                offset+count <= len(data) and moffset+mcount <= len(metadata), 'PL packed layer bounds differ')
        values, codes = data[offset:offset+count], metadata[moffset:moffset+mcount]
        if not mode:
            require(moffset == 0 and codes == bytes(16) and values == weight, 'PL Dense canonical bytes differ')
        else:
            require(moffset >= 64, 'Sparse metadata overlaps Dense zero prefix')
            expanded = bytearray(k*n)
            for t in range(k//4*n):
                code = codes[t//2] >> (4*(t % 2)) & 15
                p0, p1 = code & 3, code >> 2
                require(p0 < p1, 'invalid 2:4 position code')
                group, column = divmod(t, n)
                expanded[(4*group+p0)*n+column] = values[2*t]
                expanded[(4*group+p1)*n+column] = values[2*t+1]
            require(bytes(expanded) == weight, 'Sparse packing and canonical graph Wq differ')
    joined = b''.join(joined)
    require(len(joined) == 4615680 and joined == raw['weights.bin'] and
            identity(joined)['sha256'] == manifest['logical_weights_sha256'], '49-layer logical model identity differs')


def verify_bundle(bundle_root):
    root = ordinary(bundle_root)
    require(root.is_dir(), 'model bundle root directory required')
    manifest_raw = read_file(root/'manifest.json', 2*1024*1024)
    require(identity(manifest_raw)['sha256'] == MANIFEST_SHA, 'fixed released manifest identity differs')
    manifest = decode_json(manifest_raw)
    require(type(manifest) is dict and manifest.get('schema') == SCHEMA and
            type(manifest.get('files')) is dict and set(manifest['files']) == REQUIRED and
            manifest.get('routes') == {'PL': 36, 'PS': 13, 'PL_dense': 24, 'PL_sparse24': 12} and
            all(manifest.get(name) is False for name in ('hardware_executed', 'accuracy_verified', 'performance_verified')),
            'fixed OPT50 model manifest schema/inventory differs')
    require(inventory(root) == REQUIRED | {'manifest.json'}, 'unlisted/missing model artifact')
    raw = {}
    for name, expected in manifest['files'].items():
        path = relative(name)
        raw[name] = read_file(root.joinpath(*path.parts))
        require(identity(raw[name]) == pin(expected), 'model artifact mismatch: '+name)
    for name, filename in (('logical_weights_sha256', 'weights.bin'), ('reference_sha256', 'reference.onnx'),
                           ('custom_graph_sha256', GRAPH), ('pl_data_sha256', 'pl_w_data.bin'),
                           ('pl_meta_sha256', 'pl_w_meta.bin')):
        require(type(manifest.get(name)) is str and manifest[name] == manifest['files'][filename]['sha256'],
                'root aggregate identity differs: '+name)
    verify_layers(manifest, raw)
    return {'root': root, 'manifest': manifest, 'graph_bytes': raw[GRAPH]}


def dependencies():
    import numpy as np
    import onnxruntime as ort
    require(np.__version__ == '1.26.4' and ort.__version__ == '1.22.1', 'fixed NumPy/ORT versions required')
    require('CPUExecutionProvider' in ort.get_available_providers(), 'CPUExecutionProvider unavailable')
    return np, ort


def configure_library(lib):
    for name in ('opt50_model_identity', 'opt50_reference_identity'):
        fn = getattr(lib, name); fn.argtypes = []; fn.restype = C.c_char_p
    lib.opt50_wire_version.argtypes = []; lib.opt50_wire_version.restype = C.c_uint32
    lib.kv5_b_open.argtypes = [C.c_char_p, C.c_void_p, C.POINTER(C.c_void_p)]; lib.kv5_b_open.restype = C.c_int
    for name in ('end', 'abort', 'error', 'close'):
        getattr(lib, 'kv5_b_'+name).argtypes = [C.c_void_p]
    for name in ('begin', 'end', 'abort'):
        getattr(lib, 'kv5_b_'+name).restype = C.c_int
    lib.kv5_b_begin.argtypes = [C.c_void_p, C.c_uint64]
    lib.kv5_b_error.restype = C.c_char_p; lib.kv5_b_close.restype = None


class Runner:
    def __init__(self, bundle_root, plugin, socket, options=None, *, bind_affinity=True):
        self._run_lock = threading.Lock()
        self.handle, self.session, self.lib = C.c_void_p(), None, None
        self._failed, self._active, self._closed, self._last_run_id = False, False, False, 0
        checked = verify_bundle(bundle_root)  # No DLL or owner contact before this completes.
        self.identity = checked['manifest']
        socket_raw = os.fsencode(socket)
        require(bool(socket_raw) and b'\0' not in socket_raw and len(socket_raw) < 108, 'invalid Unix owner socket path')
        self.np, ort = dependencies()
        plugin = ordinary(plugin); before = identity(read_file(plugin))
        self.lib = C.CDLL(str(plugin))
        try:
            configure_library(self.lib)
        except AttributeError as error:
            raise ValueError('OPT50 identity/kv5 exports missing; the old v5 plugin is incompatible') from error
        require(identity(read_file(plugin)) == before, 'plugin changed while loading')
        wire = self.lib.opt50_wire_version()
        require(self.lib.opt50_model_identity() == self.identity['logical_weights_sha256'].encode('ascii') and
                self.lib.opt50_reference_identity() == self.identity['reference_sha256'].encode('ascii') and
                type(wire) is int and wire == WIRE_VERSION,
                'plugin model/reference/wire identity mismatch; old v5 plugin is not compatible')
        digest = (C.c_ubyte*32).from_buffer_copy(bytes.fromhex(self.identity['logical_weights_sha256']))
        try:
            if self.lib.kv5_b_open(socket_raw, digest, C.byref(self.handle)):
                raise RuntimeError('OPT50 owner/model handshake failed; no retry')
            if not self.handle.value:
                raise RuntimeError('plugin returned a null owner handle')
            if options is None:
                options = ort.SessionOptions()
                options.intra_op_num_threads, options.inter_op_num_threads = 4, 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
                if bind_affinity:
                    require(hasattr(os, 'sched_setaffinity'), 'Linux affinity unavailable; explicitly disable for host tests')
                    os.sched_setaffinity(0, {0})
                    options.add_session_config_entry('session.intra_op_thread_affinities', '2;3;4')
                for key in ('session.intra_op.allow_spinning', 'session.inter_op.allow_spinning'):
                    options.add_session_config_entry(key, '0')
            options.register_custom_ops_library(str(plugin))
            # Verified inline graph bytes, never an arbitrary override or a
            # second path read after verification. Only CPU EP hosts the custom ops.
            self.session = ort.InferenceSession(checked['graph_bytes'], options, providers=['CPUExecutionProvider'])
        except BaseException:
            self._close_unlocked()
            raise

    def _check(self, code):
        if code:
            error = self.lib.kv5_b_error(self.handle)
            raise RuntimeError(error.decode('utf-8', errors='replace') if error else 'OPT50 plugin operation failed')

    def run(self, image, run_id):
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError('Runner busy; concurrent/reentrant calls are rejected, not queued')
        try:
            return self._run_once(image, run_id)
        finally:
            self._run_lock.release()

    def _run_once(self, image, run_id):
        if self._closed or self._failed or self._active or self.session is None:
            raise RuntimeError('closed, failed or already-active Runner; no implicit retry')
        require(type(run_id) is int and run_id == 1 and self._last_run_id == 0,
                'single authorized model invocation only: run_id=1 once; benchmarks require a separately budgeted version')
        np = self.np
        require(isinstance(image, np.ndarray) and image.dtype == np.dtype('float32') and
                image.shape == (1, 3, 224, 224) and bool(np.isfinite(image).all()),
                'input must already be finite float32 with exact shape [1,3,224,224]; no implicit cast')
        image = np.ascontiguousarray(image)  # Copy layout only; dtype is unchanged.
        self._last_run_id = run_id  # Consume before BEGIN: an ambiguous call must not be replayed.
        try:
            self._check(self.lib.kv5_b_begin(self.handle, run_id)); self._active = True
            logits = self.session.run(None, {'image': image})[0]
            require(isinstance(logits, np.ndarray) and logits.dtype == np.dtype('float32') and
                    logits.shape == (1, 1000) and bool(np.isfinite(logits).all()), 'invalid fixed-model logits')
            self._check(self.lib.kv5_b_end(self.handle)); self._active = False
            return logits
        except BaseException:
            self._failed = True
            if self._active:
                self._active = False
                try:
                    self.lib.kv5_b_abort(self.handle)  # Best effort only; never retransmit RUN.
                except BaseException:
                    pass
            raise

    def close(self):
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError('Runner busy; close cannot interrupt an active invocation')
        try:
            self._close_unlocked()
        finally:
            self._run_lock.release()

    def _close_unlocked(self):
        self.session = None  # ORT kernels must be destroyed before plugin/client.
        self._closed = True
        if self.handle.value and self.lib is not None:
            handle = self.handle
            self.handle = C.c_void_p()
            self.lib.kv5_b_close(handle)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
