"""Compile and run public ordinary-C++ G1 tests, retaining every attempt."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cxx', required=True)
    p.add_argument('--cxx-flag', action='append', default=[])
    a = p.parse_args()
    compiler = shutil.which(a.cxx)
    if not compiler:
        raise FileNotFoundError('Select an installed C++17 compiler')
    run = ROOT / 'build' / ('runtime_v3_' + str(time.time_ns()))
    run.mkdir(parents=True)
    executable = run / ('test_core.exe' if sys.platform == 'win32' else 'test_core')
    warning_flags = ['-Wno-error=tautological-constant-out-of-range-compare'] if 'clang' in Path(compiler).name.lower() else []
    command = [compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
               # Frozen nlohmann JSON has a Clang 23 constant-range warning.
               *warning_flags, *a.cxx_flag,
               str(ROOT / 'board/model_backend/v3/tests/test_core.cpp'),
               '-lbcrypt' if sys.platform == 'win32' else '-l:libcrypto.so.3', '-o', str(executable)]
    result = {'scope': 'PUBLIC_G1_CORE_ONLY', 'PL_starts': 0, 'ARM_rebuild': 'NOT_RUN',
              'production_socket_file_adapters': 'NOT_INTEGRATED', 'commands': [command],
              'status': 'NOT_RUN', 'logs': str(run)}
    try:
        for name, args in [('compile', command), ('run', [str(executable), str(ROOT / 'BASELINE_LOCK.json')])]:
            r = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=240)
            (run / (name + '.log')).write_bytes(r.stdout + r.stderr)
            if r.returncode:
                raise RuntimeError(f'{name} failed ({r.returncode}); preserved {run}')
        result['tests'] = json.loads((run / 'run.log').read_text(encoding='utf-8'))
        result['status'] = 'PASS_LOCAL_CORE'
        sources = [*sorted((ROOT / 'board/model_backend/v3').rglob('*')), *sorted((ROOT / 'board/model_backend/v2b/protocol').glob('*.h')),
                   ROOT / 'board/model_backend/vendor/json.hpp', ROOT / 'board/model_backend/sha256_crypto.h', ROOT / 'BASELINE_LOCK.json']
        result['sources'] = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sources if f.is_file()}
        result['binary_sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
    except Exception as error:
        result.update(status='FAIL', error=str(error))
        raise
    finally:
        (run / 'verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
