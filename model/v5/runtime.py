"""Minimal product API: load a fixed graph, run, close. No CPU fallback."""
import ctypes as C
import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort


class Runner:
    def __init__(self, bundle, plugin, socket):
        self.handle = C.c_void_p()
        self.session = None
        self.lib = C.CDLL(str(Path(plugin).resolve()))
        self.lib.kv5_b_open.argtypes = [C.c_char_p, C.c_void_p, C.POINTER(C.c_void_p)]
        for name in ('end', 'abort', 'close', 'error'):
            getattr(self.lib, 'kv5_b_' + name).argtypes = [C.c_void_p]
        self.lib.kv5_b_error.restype = C.c_char_p
        self.lib.kv5_b_close.restype = None
        self.lib.kv5_b_begin.argtypes = [C.c_void_p, C.c_uint64]
        bundle = Path(bundle)
        manifest = json.loads((bundle / 'bundle_manifest.json').read_bytes())

        def checked(name):
            path = bundle / name
            spec = manifest['files'][name]
            if path.stat().st_size != spec['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
                raise ValueError('model artifact mismatch: ' + name)
            return path

        layers = json.loads(checked('layers.json').read_bytes())
        weights = b''.join(checked(layer['Wq']['file']).read_bytes() for layer in layers)
        model = (C.c_ubyte * 32).from_buffer_copy(hashlib.sha256(weights).digest())
        graph = checked('graphs/model_w8a8_pl.onnx')
        if self.lib.kv5_b_open(str(socket).encode(), model, C.byref(self.handle)):
            raise RuntimeError('v5 owner/model handshake failed')
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
            for key in ('session.intra_op.allow_spinning', 'session.inter_op.allow_spinning'):
                options.add_session_config_entry(key, '0')
            options.register_custom_ops_library(str(Path(plugin).resolve()))
            self.session = ort.InferenceSession(str(graph), options, providers=['CPUExecutionProvider'])
        except BaseException:
            self.close()
            raise

    def _check(self, code):
        if code:
            raise RuntimeError(self.lib.kv5_b_error(self.handle).decode())

    def run(self, image, run_id):
        image = np.ascontiguousarray(image, dtype=np.float32)
        self._check(self.lib.kv5_b_begin(self.handle, run_id))
        try:
            logits = self.session.run(None, {'image': image})[0]
            self._check(self.lib.kv5_b_end(self.handle))
            return logits
        except BaseException:
            # Best-effort release of a completed graph. No RUN retransmission.
            self.lib.kv5_b_abort(self.handle)
            raise

    def close(self):
        self.session = None
        if self.handle.value:
            self.lib.kv5_b_close(self.handle)
            self.handle = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
