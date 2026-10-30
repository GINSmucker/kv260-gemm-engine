"""Stage four fixed fix1 ELFs for fix2, preserving the original compile proof.

Only the two CPU self-tests execute; verify-only reads the complete new receipt.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
from build_arm import LIFECYCLE, ROOT, reuse_build, verify_build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'build_arm')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        value = verify_build(args.out) if args.verify_only else reuse_build(args.out)
        print(json.dumps({'status': value['status'],
            'receipt': str(args.out/'build_receipt.json'), 'lifecycle_profile': LIFECYCLE,
            'build_method': value['build_method'], 'hardware_executed': False,
            'native_owner_executed': False, 'verify_only': args.verify_only}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('RESIDENT_ARM_BUILD_NOT_VERIFIED: '+str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
