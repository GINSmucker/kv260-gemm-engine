"""Run synthetic Python tests only; no Vivado, owner, XRT or board invocation.

Tests write and clean their own isolated temporary fixtures. NumPy is needed.
External model/history fixtures remain absent and are reported as SKIP.
"""
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'runtime_snapshot'))
sys.path.insert(0, str(ROOT / 'runtime_tests'))


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'runtime_tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
