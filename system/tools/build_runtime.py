"""Build source artifacts on AArch64 Linux; no device open, driver load or PL start."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--variant', choices=('v2a', 'v2b'), default='v2a')
    p.add_argument('--compiler', default='g++-13')
    p.add_argument('--xrt-include', type=Path, default=Path('/usr/include/xrt'))
    p.add_argument('--uuid-include', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    if a.execute and (platform.system() != 'Linux' or platform.machine().lower() not in ('aarch64', 'arm64')):
        raise RuntimeError('Actual builds require AArch64 Linux; no WSL/host fallback')
    output = ROOT / 'build' / ('runtime_' + a.variant + '_' + str(time.time_ns()))
    source = output / 'source'
    source.mkdir(parents=True)
    base = ROOT / 'board/model_backend'
    common_files = ['guard_bytes.h', 'sha256_crypto.h', 'y_snapshot.h']
    if a.variant == 'v2a':
        common_files += [f.name for f in base.glob('stream_v2a_*.h')]
    for name in common_files:
        shutil.copyfile(base / name, source / name)
    shutil.copytree(base / 'vendor', source / 'vendor')
    shutil.copytree(base / a.variant, source / a.variant)
    v = source / a.variant
    headers = ROOT / 'vendor/onnxruntime'
    plugin = ROOT / 'model/b/custom_op/quant_linear_v2a.cpp' if a.variant == 'v2a' else ROOT / 'research/v2b/model/b/custom_op/quant_linear_v2b.cpp'
    c = [a.compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread']
    owner = output / ('model_xrt_owner_' + a.variant)
    bridge = output / ('libkv260_gemm_backend_' + a.variant + '.so')
    commands = [c + ['-I' + str(a.xrt_include), '-I' + str(a.uuid_include), str(v / 'owner.cpp'), '-lxrt_coreutil', '-l:libuuid.so.1', '-l:libcrypto.so.3', '-o', str(owner)],
                c + ['-shared', '-fPIC', '-fvisibility=hidden', '-fvisibility-inlines-hidden', str(v / 'backend.cpp'), '-ldl', '-l:libcrypto.so.3', '-o', str(bridge)],
                [a.compiler, '-std=c++17', '-O3', '-shared', '-fPIC', '-fno-fast-math', '-ffp-contract=off', '-I' + str(headers), '-I' + str(ROOT / 'vendor/plugin'), '-I' + str(v), str(plugin), '-ldl', '-l:libcrypto.so.3', '-o', str(output / ('libquant_linear_' + a.variant + '_arm.so'))]]
    report = {'scope': 'COMPILE_ONLY_NOT_NEW_READY', 'variant': a.variant, 'PL_starts': 0, 'commands': commands}
    if a.execute:
        if not a.xrt_include.is_dir() or not a.uuid_include.is_dir():
            raise FileNotFoundError('Provide existing XRT and uuid SDK headers')
        for index, command in enumerate(commands):
            # After the owner build bind its real digest into the bridge; never alter the published pins.
            if index == 1:
                pins = (v / 'pins.h').read_text()
                import re
                pins, count = re.subn(r'(OWNER_SHA\s*=\s*")[0-9a-f]{64}(";)', lambda m: m[1] + sha(owner) + m[2], pins)
                if count != 1:
                    raise ValueError('Unreviewed owner pin format')
                (v / 'pins.h').write_text(pins, encoding='utf-8')
            r = subprocess.run(command, capture_output=True)
            (output / (str(index) + '.log')).write_bytes(r.stdout + r.stderr)
            if r.returncode:
                raise RuntimeError('Compiler failed; preserved output: ' + str(output))
        report.update(status='PASS_COMPILE_ONLY', binaries={f.name: sha(f) for f in (owner, bridge, output / ('libquant_linear_' + a.variant + '_arm.so'))})
    else:
        report['status'] = 'PREPARED_NOT_COMPILED'
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
