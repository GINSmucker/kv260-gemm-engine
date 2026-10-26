"""Software file audit only; never import a plugin or access devices."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat

ROOT = Path(__file__).absolute().parent


def read(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('linked/reparse tool path')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= 4*1024*1024:
        raise ValueError('bounded ordinary tool file required')
    raw = path.read_bytes()
    after = path.stat()
    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if key(before) != key(after) or len(raw) != before.st_size:
        raise ValueError('tool changed during read')
    return raw


def verify(root=ROOT):
    root = Path(root).absolute()
    value = json.loads(read(root/'tools_manifest.json'))
    required = {'run_model.py', 'model_worker.py', 'model_inputs.py', 'model_runtime.py', 'perf_proof.py',
                'pins.json', 'expected_performance_release.json', 'source_manifest.json',
                'build_arm.py', 'verify_tools.py', 'instructions.json', 'resident.py', 'compare.py'}
    if (value.get('schema') != 'opt50_fair_model_tools_v1' or value.get('hardware_executed') is not False or
            value.get('measurement_profile') != 'same-weights-fair-single-shot-v1' or type(value.get('files')) is not dict or
            not required <= set(value['files']) or len(value['files']) > 256):
        raise ValueError('not the H576-aligned one-shot performance software bundle')
    for name, pin in value['files'].items():
        p = PurePosixPath(name)
        if (str(p) != name or p.is_absolute() or any(v in ('', '.', '..') for v in name.split('/')) or
                any(ch in name for ch in '\\:\0\r\n')):
            raise ValueError('unsafe release filename')
        raw = read(root/name)
        if pin != {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}:
            raise ValueError('software changed: '+name)
    return {'status': 'PERFORMANCE_TOOL_FILES_PASS_NO_HARDWARE_ACCESS', 'files_checked': len(value['files']),
            'hardware_access': False, 'owner_executed': False, 'START_issued': False,
            'next': 'Build ARM software only; a new plan must revalidate the fresh new-boot guardian before any authorized START'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
