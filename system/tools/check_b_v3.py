"""Same-machine CF1/v3 B CPU equivalence and diagnostic timing; no PL backend."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worker(a):
    import numpy as np
    import onnxruntime as ort
    sys.path.insert(0, str(ROOT))
    from model.b.runtime import verify_bundle
    from research.v3.validate_timing import validate
    bundle = a.bundle.resolve()
    identity = verify_bundle(bundle)
    if identity['manifest_sha256'] != json.loads((ROOT / 'BASELINE_LOCK.json').read_text())['B_manifest_sha256']:
        raise ValueError('Wrong frozen bundle')
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update(KV260_B_BACKEND='cpu_reference', KV260_B_RUN_ID='g1cpu', KV260_B_IMAGE_ID='img00',
                      KV260_B_TRACE_FILE=str(output / 'trace.jsonl'), KV260_B_DUMP_DIR='',
                      KV260_B_TIMING_FILE=str(output / 'timing.jsonl') if a.worker == 'v3' else '')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    options.register_custom_ops_library(str(a.plugin.resolve()))
    begin = time.perf_counter_ns()
    session = ort.InferenceSession(str(bundle / 'graphs/model_w8a8_pl.onnx'), options, providers=['CPUExecutionProvider'])
    load_ns = time.perf_counter_ns() - begin
    tensor = np.load(bundle / 'regression/00_input.npy', allow_pickle=False)
    begin = time.perf_counter_ns()
    logits = session.run(None, {'image': tensor})[0]
    session_ns = time.perf_counter_ns() - begin
    traces = [json.loads(line) for line in (output / 'trace.jsonl').read_text().splitlines()]
    layers = json.loads((bundle / 'layers.json').read_text())
    if [r['layer_id'] for r in traces] != [r['layer_id'] for r in layers] or any(r['starts_delta'] != 0 for r in traces):
        raise ValueError('Wrong complete CPU graph/starts identity')
    np.save(output / 'logits.npy', logits, allow_pickle=False)
    result = {'status': 'PASS_CPU_FUNCTION_ONLY', 'PL_starts': 0, 'kind': a.worker,
              'plugin_sha256': sha(a.plugin), 'bundle_identity': identity, 'graph_sha256': sha(bundle / 'graphs/model_w8a8_pl.onnx'),
              'session_load_ns': load_ns, 'session_run_including_terminal_export_ns': session_ns,
              'timing_use': 'DIAGNOSTIC_NOT_FAIR_PERFORMANCE', 'layers': len(traces), 'logits_sha256': sha(output / 'logits.npy')}
    if a.worker == 'v3':
        rows = [json.loads(line) for line in (output / 'timing.jsonl').read_text().splitlines()]
        result['timing_schema'] = validate(rows, traces)
        # Error-path checks use actual complete rows; no synthetic PASS trace.
        for key in ('identity', 'negative', 'boolean', 'closure', 'missing', 'PL_label'):
            changed = copy.deepcopy(rows)
            if key == 'identity': changed[0]['request_id'] = 'other_task'
            elif key == 'negative': changed[0]['phases']['B_receipt']['wall_ns'] = -1
            elif key == 'boolean': changed[0]['phases']['B_receipt']['wall_ns'] = True
            elif key == 'closure': changed[0]['thread_cpu_ns'] += 1
            elif key == 'missing': changed.pop()
            else: changed[0]['backend'] = 'pl_v2b'
            try:
                validate(changed, traces)
            except ValueError:
                continue
            raise AssertionError('Bad diagnostic schema accepted: ' + key)
        result['timing_rejections'] = 6
        # The new PL binding must remain unavailable, with no retry/fallback.
        os.environ['KV260_B_BACKEND'] = 'pl_v2b'
        try:
            session.run(None, {'image': tensor})
        except Exception as error:
            if 'V3 G1 has no qualified ARM' not in str(error):
                raise
        else:
            raise AssertionError('Unqualified PL candidate was enabled')
        result['PL_gate_rejection'] = 'PASS_ZERO_START'
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--cxx')
    p.add_argument('--worker', choices=('cf1', 'v3'))
    p.add_argument('--plugin', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.worker:
        worker(a)
        return
    if sys.platform != 'win32':
        raise RuntimeError('This checked compile recipe is Windows-only; use actual ARM toolchain for ARM qualification')
    cxx = shutil.which(a.cxx or '')
    if not cxx:
        raise FileNotFoundError('Select an installed C++17 compiler')
    run = ROOT / 'build' / ('b_v3_' + str(time.time_ns()))
    run.mkdir(parents=True)
    result = {'status': 'NOT_RUN', 'scope': 'SAME_MACHINE_B_CPU_DIAGNOSTIC', 'PL_starts': 0, 'logs': str(run)}
    result['compile_commands'] = {}
    sources = [ROOT / p for p in ('research/v2b/model/b/custom_op/quant_linear_v2b.cpp',
                'research/v3/custom_op/quant_linear_v3.cpp', 'research/v3/validate_timing.py',
                'board/model_backend/v3/timing.h', 'board/model_backend/v3/tests/test_sha.h',
                'board/model_backend/v2b/gemm_backend_v2b.h', 'vendor/plugin/sha256_crypto.h')]
    sources += sorted((ROOT / 'vendor/onnxruntime').glob('*.h'))
    result['sources'] = {str(f.relative_to(ROOT)): sha(f) for f in sources}
    try:
        for kind, source in [('cf1', ROOT / 'research/v2b/model/b/custom_op/quant_linear_v2b.cpp'),
                             ('v3', ROOT / 'research/v3/custom_op/quant_linear_v3.cpp')]:
            plugin = run / (kind + '.dll')
            command = [cxx, '-std=c++17', '-O3', '-static', '-shared', '-fno-fast-math', '-ffp-contract=off',
                       '-I' + str(ROOT / 'vendor/onnxruntime'), '-I' + str(ROOT / 'vendor/plugin'),
                       '-I' + str(ROOT / 'board/model_backend/v2b'), '-DKV260_V3_SHA_PROVIDER_ONLY',
                       '-include', str(ROOT / 'board/model_backend/v3/tests/test_sha.h'),
                       str(source), '-lbcrypt', '-o', str(plugin)]
            result['compile_commands'][kind] = command
            commands = [('compile', command), ('run', [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', kind,
                        '--bundle', str(a.bundle.resolve()), '--plugin', str(plugin), '--output', str(run / kind)])]
            for step, command in commands:
                r = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=240)
                (run / (kind + '_' + step + '.log')).write_bytes(r.stdout + r.stderr)
                if r.returncode:
                    raise RuntimeError(f'{kind} {step} failed ({r.returncode}); see {run}')
        import numpy as np
        before = np.load(run / 'cf1/logits.npy', allow_pickle=False)
        after = np.load(run / 'v3/logits.npy', allow_pickle=False)
        traces = [[json.loads(line) for line in (run / k / 'trace.jsonl').read_text().splitlines()] for k in ('cf1', 'v3')]
        fields = ('layer_id', 'layer_index', 'M', 'K', 'N', 'X_sha256', 'W_sha256', 'Y_sha256', 'request_id', 'starts_delta', 'fallback_count')
        if not np.array_equal(before, after) or any(any(b[k] != n[k] for k in fields) for b, n in zip(*traces)):
            raise ValueError('Same-machine B numerical/payload identity mismatch')
        archived = np.load(a.bundle / 'regression/00_quant_logits.npy', allow_pickle=False)
        result.update(status='PASS_SAME_MACHINE_CANDIDATE_EQUIVALENCE', logits_max_abs=0.0,
                      complete_layer_payload_matches=49, archived_host_quant_max_abs=float(np.max(np.abs(after - archived))),
                      archived_host_gate='SEPARATE_NO_TOLERANCE_CHANGE',
                      candidates={k: json.loads((run / k / 'result.json').read_text()) for k in ('cf1', 'v3')})
    except Exception as error:
        result.update(status='FAIL', error=str(error))
        raise
    finally:
        (run / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
