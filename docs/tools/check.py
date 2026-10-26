"""Check the active v2 branch; does not claim the old upstream package is unchanged."""
from pathlib import Path
import runpy
import sys

if len(sys.argv) != 1:
    raise SystemExit('This version entry checks active layout only. Use tools/run_active_native.py for native C++; original check source is retained in docs/provenance/check_upstream.py.')
root = Path(__file__).resolve().parents[2]
runpy.run_path(str(root/'tools/check_version_layout.py'), run_name='__main__')
