"""Diagnostic hooks for an already authorized coordinator; never starts or stops owners."""
import ctypes as C
import hashlib
import importlib.util
import os
from pathlib import Path
import time


def enable_profile(options, prefix):
    options.enable_profiling = True
    options.profile_file_prefix = str(prefix)
    return options


def diagnostic_run(runner, image, run_id):
    # Resolve the export before consuming a run. The coordinator retains normal
    # CPU comparison, INFO, window/budget admission and uncertain-state handling.
    if any(os.environ.get(k) for k in ('KV5_DUMP', 'KV5_QKV_DUMP')):
        raise ValueError('tensor dumps cannot be used for timing')
    function = runner.lib.opt50_diagnostic_linear_v1
    function.argtypes = [C.c_void_p, C.POINTER(C.c_uint64), C.c_size_t]
    function.restype = C.c_int
    stamps = (C.c_uint64*(49*7))()
    started = time.monotonic_ns()
    logits = runner.run(image, run_id=run_id)
    finished = time.monotonic_ns()
    # Export/copy/serialization are outside the timed Runner boundary.
    runner._check(function(runner.handle, stamps, len(stamps)))
    capture = {'schema': 'opt50_final_capture_v1', 'status': 'NOT RUN',
               'kind': 'instrumented_diagnostic_not_performance', 'run_id': run_id,
               'runner_start_ns': started, 'runner_end_ns': finished,
               'linear_stamps': [list(stamps[i*7:(i+1)*7]) for i in range(49)],
               'input_tensor_sha256': hashlib.sha256(image.tobytes(order='C')).hexdigest(),
               'logits_sha256': hashlib.sha256(logits.tobytes(order='C')).hexdigest()}
    return logits, capture


def complete_capture(capture, reference, actual, native):
    """Call only after the coordinator's normal, durable INFO has returned."""
    import numpy as np
    if not (reference.shape == actual.shape == (1,1000) and reference.dtype == actual.dtype == np.float32
            and bool(np.isfinite(reference).all()) and bool(np.isfinite(actual).all())):
        raise ValueError('finite 1000-class FP32 outputs required')
    mismatch = int(np.count_nonzero(reference.view(np.uint32) != actual.view(np.uint32)))
    if mismatch:
        raise ValueError('FP32 bitwise mismatch; preserve outputs, no next graph')
    result = dict(capture, status='PASS', bitwise_mismatches=mismatch,
                  CPU_logits_sha256=hashlib.sha256(reference.tobytes(order='C')).hexdigest(),
                  boot_id=native['boot_id'], source_model_sha256=native['source_model_sha256'])
    if hashlib.sha256(actual.tobytes(order='C')).hexdigest() != capture['logits_sha256']:
        raise ValueError('captured logits changed')
    # Import locally to keep hot-path capture independent of the analyzer.
    spec = importlib.util.spec_from_file_location('_opt50_final_analyzer', Path(__file__).with_name('analyze.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.decompose(result,native)
    return result
