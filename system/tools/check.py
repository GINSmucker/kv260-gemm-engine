"""Offline handoff checks. Optional C++/Node/CPU tests never operate the FPGA."""
import argparse
import ast
import hashlib
import importlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def command(args, cwd, log):
    r = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, timeout=240)
    log.write_bytes(r.stdout + r.stderr)
    if r.returncode:
        raise RuntimeError(f"Command failed ({r.returncode}): {args[0]}; see {log}")
    return r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--node', action='store_true')
    p.add_argument('--cpp', action='store_true')
    p.add_argument('--cxx', default='g++')
    p.add_argument('--model-bundle', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = {'scope': 'HANDOFF_LOCAL_SOFTWARE_ONLY', 'PL_starts': 0,
              'HLS_synthesis': 'NOT_RUN', 'RTL_cosim': 'NOT_RUN', 'ARM_rebuild': 'NOT_RUN'}
    manifest = ROOT / 'PACKAGE_MANIFEST.json'
    if not manifest.exists():
        raise RuntimeError('PACKAGE_MANIFEST.json missing; this is not the published snapshot')
    entries = json.loads(manifest.read_text(encoding='utf-8'))['files']
    for name, item in entries.items():
        file = (ROOT.parent / name).resolve()
        if not file.is_relative_to(ROOT.parent) or file.stat().st_size != item['bytes'] or sha(file) != item['sha256']:
            raise ValueError('Package identity mismatch: ' + name)
    result['package_files_verified'] = len(entries)
    for file in ROOT.rglob('*.py'):
        if not any(x in file.relative_to(ROOT).parts for x in ('build', 'assets', '.venv', '__pycache__')):
            ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file))
    sys.path.insert(0, str(ROOT))
    for name in ('model.b.common', 'model.b.quantization', 'model.b.golden_int64', 'model.b.runtime', 'model.b.runtime_v2a', 'model.b.v2a.cli'):
        importlib.import_module(name)
    result['python_core_imports'] = 'PASS'
    broken = []
    for file in [ROOT / 'README.md', *list((ROOT / 'docs').glob('*.md'))]:
        for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)', file.read_text(encoding='utf-8')):
            if '://' not in link and not link.startswith('#') and not (file.parent / link.split('#')[0]).exists():
                broken.append({'document': file.name, 'link': link})
    if broken:
        raise ValueError('Broken handoff links: ' + json.dumps(broken))
    result['handoff_links'] = 'PASS'
    run = ROOT / 'build' / ('check_' + str(time.time_ns()))
    run.mkdir(parents=True)
    if a.node:
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node >=24 required; no automatic installation')
        files = sorted((ROOT / 'ui/tests').glob('*.test.mjs'))
        command([node, '--test', '--test-isolation=none', *files], ROOT / 'ui', run / 'node_tests.log')
        command([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'ui/tests', '-p', 'test_agent.py'], ROOT, run / 'python_ui.log')
        result['UI_existing_tests'] = 'PASS'
    if a.cpp:
        cxx = shutil.which(a.cxx)
        if not cxx:
            raise RuntimeError('C++ compiler missing; supply --cxx')
        result['native_Cpp_public'] = {}
        for name in ('opt35a_clear_v1_full', 'opt38_fullk_v1_full'):
            src = ROOT / 'hardware/model_gemm/candidates' / name
            dest = run / name
            dest.mkdir()
            executable = dest / ('testbench.exe' if sys.platform == 'win32' else 'testbench')
            command([cxx, '-std=c++11', '-O3', '-Wno-unknown-pragmas', src / 'src/model_gemm_top.cpp', src / 'tb/testbench.cpp', '-o', executable], dest, dest / 'compile.log')
            command([executable, 'rtl'], dest, dest / 'run.log')
            summary = json.loads((dest / 'rtl_tb_summary.json').read_text())
            if summary['status'] != 'PASS' or summary['valid_cases_passed'] != 8 or summary['invalid_cases_rejected'] != 8:
                raise ValueError('Native public TB failed: ' + name)
            result['native_Cpp_public'][name] = summary
        result['native_Cpp_scope'] = 'Ordinary C++ execution with HLS pragmas ignored; not RTL or HLS validation'
    if a.model_bundle:
        import numpy as np
        from model.b.runtime import Runner, verify_bundle
        bundle = a.model_bundle.resolve()
        identity = verify_bundle(bundle)
        lock = json.loads((ROOT / 'BASELINE_LOCK.json').read_text())
        if identity['manifest_sha256'] != lock['B_manifest_sha256']:
            raise ValueError('Wrong frozen model identity')
        tensor = np.load(bundle / 'regression/00_input.npy', allow_pickle=False)
        prefix = 'windows' if sys.platform == 'win32' else 'arm'
        result['CPU_model_regression'] = {}
        for kind, suffix in (('fp32', 'fp32'), ('w8a8_reference', 'quant')):
            runner = Runner(bundle, prefix + '_' + kind, threads=4)
            actual = runner.run(tensor, 'handoff_' + suffix, 'img0000')
            expected = np.load(bundle / ('regression/00_' + suffix + '_logits.npy'), allow_pickle=False)
            difference = float(np.max(np.abs(np.asarray(actual['logits']) - expected)))
            if difference > 1e-4 or actual['PL_starts'] != 0:
                raise ValueError('Same-host CPU regression failed: ' + kind)
            result['CPU_model_regression'][kind] = {'max_abs': difference, 'PL_starts': 0}
        result['CPU_model_scope'] = 'One frozen same-host input; not quality or performance certification'
    result['status'] = 'PASS'
    result['logs'] = str(run)
    output = a.output.resolve() if a.output else run / 'verification.json'
    if output.exists():
        raise FileExistsError('Use a fresh verification output')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
