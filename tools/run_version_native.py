"""Build/run three archived HLS testbenches as native C++; NEVER HLS/RTL/board.

Writes only a NEW output directory and compiler temporaries. Requires an existing
Windows Vitis 2026.1 installation. The 'rtl' argument names a synthetic test set;
it does not invoke an RTL simulator. Real weight payloads are not needed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vitis-root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    compiler = args.vitis_root / 'win64/tools/clang-16/bin/clang++.exe'
    mingw = args.vitis_root / 'tps/mingw/10.0.0/win64.o/nt'
    if not compiler.is_file() or args.out.exists() or args.out.is_symlink():
        raise ValueError('Existing toolchain and NEW output path required')
    args.out.mkdir(parents=True)
    temporary = args.out/'compiler_tmp'
    temporary.mkdir()
    env = os.environ.copy()
    env['TEMP'] = str(temporary.resolve())
    env['TMP'] = str(temporary.resolve())
    env['PATH'] = os.pathsep.join([str(args.vitis_root/'lib/win64.o'), str(mingw/'bin'),
                                 str(args.vitis_root/'win64/lib/csim'), env.get('PATH', '')])
    rows = []
    versions = [('v1_dense_initial', 'src/gemm.cpp', []),
                ('v2_h576_dense', 'src/model_gemm_top.cpp', ['rtl']),
                ('v3_sparse_opt50', 'src/model_gemm_top.cpp', ['rtl'])]
    for name, top, profile in versions:
        source = root/'hls/versions'/name
        folder = args.out/name
        folder.mkdir()
        executable = folder/'native_testbench.exe'
        command = [str(compiler), '--target=x86_64-w64-windows-gnu', '--sysroot='+str(mingw),
                   '-std=c++11', '-O2', '-Wno-unknown-pragmas', '-ferror-limit=8',
                   '-I', str(args.vitis_root/'include'), str(source/top),
                   str(source/'tb/testbench.cpp'), '-pthread', '-Wl,--stack,16777216',
                   '-o', str(executable.resolve())]
        build = subprocess.run(command, cwd=folder, env=env, capture_output=True, timeout=180)
        (folder/'build.stdout').write_bytes(build.stdout)
        (folder/'build.stderr').write_bytes(build.stderr)
        if build.returncode:
            raise RuntimeError('Native build failed; see '+str(folder/'build.stderr'))
        run = subprocess.run([str(executable.resolve()), *profile], cwd=folder, env=env,
                             capture_output=True, timeout=240)
        (folder/'run.stdout').write_bytes(run.stdout)
        (folder/'run.stderr').write_bytes(run.stderr)
        row = {'version': name, 'build_returncode': build.returncode, 'run_returncode': run.returncode,
               'profile': profile or ['default'],
               'top_source_sha256': hashlib.sha256((source/top).read_bytes()).hexdigest(),
               'testbench_sha256': hashlib.sha256((source/'tb/testbench.cpp').read_bytes()).hexdigest(),
               'stdout_sha256': hashlib.sha256(run.stdout).hexdigest()}
        rows.append(row)
        print(json.dumps(row), flush=True)
        if run.returncode:
            raise RuntimeError('Native cases failed; see '+str(folder/'run.stdout'))
    result = {'status': 'PASS', 'scope': 'Native C++ functional regression only; not Vitis CSim, RTL, timing or board proof',
              'compiler': 'Vitis 2026.1 bundled clang-16 native Windows',
              'versions': rows, 'hardware_access': False, 'FPGA_latency_measured': False}
    (args.out/'native_result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
