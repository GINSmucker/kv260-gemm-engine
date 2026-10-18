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

ROOT = Path(__file__).resolve().parents[2]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def command(args, cwd, log):
    r = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, timeout=240)
    log.write_bytes(r.stdout + r.stderr)
    if r.returncode:
        raise RuntimeError(f"Command failed ({r.returncode}): {args[0]}; see {log}")
    return r


def architecture_contract(args, run):
    """Reproduce the pinned public numerical contract without operating hardware."""
    if not args.research_source or not args.hls_include or not (args.hls_include / 'ap_int.h').is_file():
        raise ValueError('--architecture-contract requires --research-source and existing --hls-include')
    cxx, git = shutil.which(args.cxx), shutil.which('git')
    if not cxx or not git:
        raise RuntimeError('Existing C++ compiler and Git are required')
    evidence = json.loads((ROOT / 'results/architecture_contract_20261011.json').read_bytes())
    identity = evidence['source_identity']
    commit = identity['head']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid pinned A commit')
    dest = run / 'architecture_contract'
    dest.mkdir()
    test = evidence['test_source_utf8'].encode('utf-8')
    if hashlib.sha256(test).hexdigest() != evidence['verification']['test_source_sha256']:
        raise ValueError('Contract test identity mismatch')
    (dest / 'contract_test.cpp').write_bytes(test)
    for name in ('src/model_gemm_top.cpp', 'src/model_gemm_core.hpp', 'include/model_gemm_config.hpp'):
        relative = 'hls/' + name
        blob = subprocess.run([git, 'show', commit + ':' + relative], cwd=ROOT,
                              capture_output=True, timeout=30)
        if blob.returncode:
            raise RuntimeError('Pinned A commit unavailable; run git fetch origin hls/opt40-axi128-burst')
        spec = identity['A_source_files'][relative]
        if len(blob.stdout) != spec['bytes'] or hashlib.sha256(blob.stdout).hexdigest() != spec['sha256']:
            raise ValueError('A source identity mismatch: ' + relative)
        target = dest / 'A_OPT40' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.stdout)
        source = args.research_source.resolve() / 'hardware/model_gemm/candidates/opt45_y768_v1' / name
        spec = identity['local_OPT45_source_files'][name]
        if source.stat().st_size != spec['bytes'] or sha(source) != spec['sha256']:
            raise ValueError('OPT45 source identity mismatch: ' + name)
        target = dest / 'LOCAL_OPT45' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    results = {}
    for name, top, flags in (
        ('A_OPT40', 'A_OPT40/hls/src/model_gemm_top.cpp', ['-DTEST_A_OPT40']),
        ('LOCAL_OPT45', 'LOCAL_OPT45/src/model_gemm_top.cpp', []),
    ):
        executable = dest / (name + ('.exe' if sys.platform == 'win32' else ''))
        command([cxx, '-std=c++11', '-O3', '-Wno-unknown-pragmas',
                 '-I' + str(args.hls_include.resolve()), *flags, dest / top,
                 dest / 'contract_test.cpp', '-o', executable], dest, dest / (name + '_compile.log'))
        output = command([executable], dest, dest / (name + '_contract.log'))
        log = output.stdout.decode('utf-8')
        expected = evidence['verification']['tests'][name]
        if f"SUMMARY status=PASS valid={expected['valid_cases']} invalid={expected['invalid_cases']} outputs={expected['integer_outputs']}" not in log:
            raise ValueError('Native contract failed: ' + name)
        results[name] = {key: expected[key] for key in ('valid_cases', 'invalid_cases', 'integer_outputs')}
        results[name]['status'] = 'PASS'
    return {'scope': 'ORDINARY_CPP_ONLY_NOT_HLS_RTL_BOARD_OR_PERFORMANCE', 'tests': results}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--node', action='store_true')
    p.add_argument('--cpp', action='store_true')
    p.add_argument('--architecture-contract', action='store_true', help='Pinned A/OPT45 ordinary C++ numerical contract')
    p.add_argument('--cxx', default='g++')
    p.add_argument('--model-bundle', type=Path)
    p.add_argument('--research-source', type=Path, help='Extracted opt45-source root; --cpp adds ordinary C++ TB')
    p.add_argument('--hls-include', type=Path, help='Existing Vitis HLS include directory containing ap_int.h')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = {'scope': 'HANDOFF_LOCAL_SOFTWARE_ONLY', 'PL_starts': 0,
              'HLS_synthesis': 'NOT_RUN', 'RTL_cosim': 'NOT_RUN', 'ARM_rebuild': 'NOT_RUN'}
    manifest = ROOT / 'docs/PACKAGE_MANIFEST.json'
    if not manifest.exists():
        raise RuntimeError('PACKAGE_MANIFEST.json missing; this is not the published snapshot')
    entries = json.loads(manifest.read_text(encoding='utf-8'))['files']
    for name, item in entries.items():
        file = (ROOT / name).resolve()
        if not file.is_relative_to(ROOT) or file.stat().st_size != item['bytes'] or sha(file) != item['sha256']:
            raise ValueError('Package identity mismatch: ' + name)
    result['package_files_verified'] = len(entries)
    for file in ROOT.rglob('*.py'):
        if not any(x in file.relative_to(ROOT).parts for x in ('build', 'assets', 'system', '.venv', '__pycache__')):
            ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file))
    sys.path.insert(0, str(ROOT))
    for name in ('model.b.common', 'model.b.quantization', 'model.b.golden_int64', 'model.b.runtime', 'model.b.runtime_v2a', 'model.b.v2a.cli'):
        importlib.import_module(name)
    result['python_core_imports'] = 'PASS'
    if a.research_source:
        research = a.research_source.resolve()
        identity = json.loads((research / 'asset_manifest.json').read_bytes())
        spec = json.loads((ROOT / 'docs/locks/ASSETS.lock.json').read_bytes())['assets']['opt45-source']
        if identity['source_identity']['A_delivery_manifest_sha256'] != spec['source_manifest_sha256']:
            raise ValueError('Research source identity mismatch')
        for name, item in identity['files'].items():
            file = (research / name).resolve()
            if not file.is_relative_to(research) or file.stat().st_size != item['bytes'] or sha(file) != item['sha256']:
                raise ValueError('Research member identity mismatch: ' + name)
        result['OPT45_source_members_verified'] = len(identity['files'])

    broken = []
    for file in [ROOT / 'README.md', ROOT / 'hls/README.md', ROOT / 'model/README.md', ROOT / 'board/README.md', *list((ROOT / 'docs').glob('*.md')), *list((ROOT / 'docs/reference').glob('*.md'))]:
        for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)', file.read_text(encoding='utf-8')):
            if '://' not in link and not link.startswith('#') and not (file.parent / link.split('#')[0]).exists():
                broken.append({'document': file.name, 'link': link})
    if broken:
        raise ValueError('Broken handoff links: ' + json.dumps(broken))
    result['handoff_links'] = 'PASS'
    run = ROOT / 'build' / ('check_' + str(time.time_ns()))
    run.mkdir(parents=True)
    if a.architecture_contract:
        result['architecture_contract'] = architecture_contract(a, run)
    if a.node:
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node >=24 required; no automatic installation')
        files = sorted((ROOT / 'board/ui/tests').glob('*.test.mjs'))
        command([node, '--test', '--test-isolation=none', *files], ROOT / 'board/ui', run / 'node_tests.log')
        command([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'board/ui/tests', '-p', 'test*.py'], ROOT, run / 'python_ui.log')
        result['UI_existing_tests'] = 'PASS'
    if a.cpp:
        cxx = shutil.which(a.cxx)
        if not cxx:
            raise RuntimeError('C++ compiler missing; supply --cxx')
        result['native_Cpp_public'] = {}
        candidates = [('opt35a_clear_v1_full', ROOT / 'hls/candidates/opt35a_clear_v1_full'),
                      ('opt38_fullk_v1_full', ROOT / 'hls')]
        if a.research_source:
            if not a.hls_include or not (a.hls_include / 'ap_int.h').is_file():
                raise FileNotFoundError('OPT45 ordinary C++ requires --hls-include with existing Vitis ap_int.h')
            candidates.append(('opt45_y768_v1', a.research_source.resolve() / 'hardware/model_gemm/candidates/opt45_y768_v1'))
        for name, src in candidates:
            dest = run / name
            dest.mkdir()
            executable = dest / ('testbench.exe' if sys.platform == 'win32' else 'testbench')
            compile_args = [cxx, '-std=c++11', '-O3', '-Wno-unknown-pragmas']
            if name == 'opt45_y768_v1':
                compile_args += ['-I' + str(a.hls_include.resolve())]
            command(compile_args + [src / 'src/model_gemm_top.cpp', src / 'tb/testbench.cpp', '-o', executable], dest, dest / 'compile.log')
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
        lock = json.loads((ROOT / 'docs/locks/BASELINE_LOCK.json').read_text())
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
