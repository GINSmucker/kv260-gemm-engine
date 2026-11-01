"""Compile a staged candidate on the matching ARM host, without launching an owner."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess

from prepare import pin


def verify(root):
    root = Path(root).resolve()
    value = json.loads((root/'candidate_manifest.json').read_bytes())
    if value['schema'] != 'opt50_final_candidate_v1':
        raise ValueError('candidate schema')
    actual = {p.relative_to(root).as_posix(): pin(p.read_bytes()) for p in root.rglob('*')
              if p.is_file() and p.name != 'candidate_manifest.json'}
    if actual != value['files']:
        raise ValueError('candidate inventory/bytes differ')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compiler', default='g++-13')
    args = parser.parse_args()
    candidate, output = args.candidate.resolve(), args.out.absolute()
    manifest = verify(candidate)
    if platform.system() != 'Linux' or platform.machine() != 'aarch64':
        raise ValueError('matching native AArch64 Linux required; host build is not ARM')
    resolved_compiler = shutil.which(args.compiler)
    compiler = str(Path(resolved_compiler).resolve()) if resolved_compiler else None
    if compiler is None or output.exists() or output.is_relative_to(candidate):
        raise ValueError('existing compiler and NEW separate output required')
    version = subprocess.check_output([compiler, '--version'], text=True)
    flags = [compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
             '-DKV5_DIAGNOSTIC', '-DOPT50_PERFORMANCE_LIGHT=1', '-DOPT50_H576_ALIGNED=1',
             '-DOPT50_RESIDENT_BATCH=1', '-fno-fast-math', '-ffp-contract=off',
             '-I/usr/include/xrt', '-I/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include',
             '-I'+str(candidate/'model'), '-I'+str(candidate/'source'), '-I'+str(candidate/'source/headers')]
    libraries = ['-lxrt_coreutil', '-l:libuuid.so.1', '-l:libcrypto.so.3']
    commands = [flags+[str(candidate/('test_'+name+'.cpp')), '-o', str(output/name)]
                for name in ('model_core', 'performance_core')]
    commands += [flags+['-fPIC', '-shared', '-fvisibility=hidden', str(candidate/'source/b_plugin.cpp'),
                       str(candidate/'source/client.cpp'), *libraries, '-o', str(output/'libopt50_model.so')],
                 flags+[str(candidate/'source/owner.cpp'), str(candidate/'source/kernel.cpp'), *libraries,
                        '-o', str(output/'opt50_model_owner')]]
    output.mkdir(parents=True)
    record = {'schema': 'opt50_final_arm_build_v1', 'status': 'FAIL', 'variant': manifest['variant'],
              'builder': pin(Path(__file__).read_bytes()),
              'candidate_manifest': pin((candidate/'candidate_manifest.json').read_bytes()),
              'compiler': {'path': compiler, 'version': version, **pin(Path(compiler).read_bytes())},
              'hardware_executed': False, 'owner_executed': False, 'commands': commands, 'steps': []}
    if record['builder'] != manifest['tools']['build.py']:
        raise ValueError('builder differs from candidate preparation')
    try:
        for ordinal, command in enumerate(commands):
            result = subprocess.run(command, capture_output=True, timeout=240, cwd=output)
            for stream in ('stdout', 'stderr'):
                (output/f'compile_{ordinal}.{stream}').write_bytes(getattr(result, stream))
            record['steps'].append({'command': command, 'returncode': result.returncode})
            if result.returncode:
                raise RuntimeError('compile failed; inspect saved logs')
        for name in ('model_core', 'performance_core'):
            argv = [str(output/name)]
            if name == 'model_core':
                argv += [str(candidate/'model'/n) for n in ('weights.bin', 'pl_w_data.bin', 'pl_w_meta.bin')]
            result = subprocess.run(argv, capture_output=True, timeout=180, cwd=output)
            record['steps'].append({'command': argv, 'returncode': result.returncode,
                                    'stdout': result.stdout.decode(), 'stderr': result.stderr.decode()})
            if result.returncode or result.stderr:
                raise RuntimeError('CPU selftest failed')
        symbols = subprocess.check_output(['nm', '-D', '--defined-only', str(output/'libopt50_model.so')], text=True)
        record['diagnostic_export_present'] = 'opt50_diagnostic_linear_v1' in symbols
        if record['diagnostic_export_present'] != (manifest['variant'] == 'diagnostic'):
            raise ValueError('diagnostic export differs from variant')
        record['binaries'] = {name: pin((output/name).read_bytes()) for name in
                              ('model_core', 'performance_core', 'libopt50_model.so', 'opt50_model_owner')}
        for name in record['binaries']:
            raw = (output/name).read_bytes()
            if not (len(raw) >= 64 and raw[:7] == b'\x7fELF\x02\x01\x01' and struct.unpack_from('<H',raw,18)[0] == 183):
                raise ValueError('AArch64 ELF64 required: '+name)
        if verify(candidate) != manifest:
            raise ValueError('candidate changed during build')
        record['status'] = 'PASS'
    finally:
        (output/'build_receipt.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
