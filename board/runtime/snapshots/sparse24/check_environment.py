"""Read-only software prerequisites. Does not open XRT, a model owner or PL CSR."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys

sys.dont_write_bytecode = True
import dataset


def inspect(board=False):
    actual = {}
    wanted = {'numpy': '1.26.4', 'onnxruntime': '1.22.1', 'pillow': '12.3.0'}
    for name in wanted:
        try:
            actual[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual[name] = None
    matches = all(actual[k] == v for k, v in wanted.items())
    report = {'status': 'SOFTWARE_READY_NO_MODEL_OR_HARDWARE_ACCESS' if matches else 'SOFTWARE_DEPENDENCIES_MISSING_OR_DIFFERENT',
              'expected_versions': wanted, 'actual_versions': actual, 'interpreter': sys.executable,
              'platform': platform.system(), 'machine': platform.machine(),
              'source_model_sha256': dataset.SOURCE_MODEL, 'selected_images': len(dataset.labels()),
              'images_available': 'NOT_CHECKED_USE_DATASET_VERIFY', 'hardware_accessed': False,
              'packages_installed_or_modified': False}
    if board:
        kernel = platform.release()
        good = os.name == 'posix' and platform.machine() == 'aarch64' and kernel == '6.8.0-1015-xilinx'
        affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else []
        good = good and {0, 1, 2, 3} <= set(affinity)
        report.update(board_platform_matches=good, kernel=kernel, available_affinity=affinity,
                      boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip() if os.name == 'posix' else None)
        if not good:
            report['status'] = 'BOARD_PLATFORM_OR_AFFINITY_DIFFERS_NO_HARDWARE_ACCESS'
        matches = matches and good
    return report, 0 if matches else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', action='store_true')
    args = parser.parse_args()
    result, code = inspect(args.board)
    print(json.dumps(result, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
