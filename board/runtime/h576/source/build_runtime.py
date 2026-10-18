"""Rebuild the measured ARM research runtime with installed SDKs."""
from pathlib import Path
import subprocess
commands=[['/usr/bin/g++-13', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread', '-I/usr/include/xrt', '-I/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include', '-fPIC', '-shared', '-fvisibility=hidden', '-fno-fast-math', '-ffp-contract=off', '-Iheaders', 'b_plugin.cpp', 'client.cpp', '-lxrt_coreutil', '-l:libuuid.so.1', '-l:libcrypto.so.3', '-o', '../libquant_v5.so'], ['/usr/bin/g++-13', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread', '-I/usr/include/xrt', '-I/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include', '-I.', 'owner.cpp', 'kernel.cpp', '-lxrt_coreutil', '-l:libuuid.so.1', '-l:libcrypto.so.3', '-o', '../native_owner']]
for command in commands:subprocess.run(command,cwd=Path(__file__).resolve().parent,check=True)
