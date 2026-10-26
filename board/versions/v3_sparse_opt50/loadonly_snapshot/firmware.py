"""Strict read-only firmware reuse, or exclusive creation of the SAME files.

No loader, subprocess, device, module, CSR, deletion or overwrite operation is
available here. Existing firmware is reusable only when its directory and three
files are root-owned, non-writable by others, unlinked and exactly hash-matched.
The plan pins inode identities as well as contents, so replacement is refused.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _key(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _trusted(info, *, directory):
    require(not stat.S_ISLNK(info.st_mode) and
            not getattr(info, 'st_file_attributes', 0) & 0x400,
            'firmware symlink/reparse refused')
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
            info.st_uid == 0 and not info.st_mode & 0o022,
            'firmware must be root-owned and not group/world writable')
    if not directory:
        require(info.st_nlink == 1, 'firmware hard link refused')


def _chain(folder):
    folder = Path(folder).absolute()
    require('..' not in folder.parts, 'firmware parent traversal refused')
    for item in reversed((folder, *folder.parents)):
        _trusted(item.lstat(), directory=True)
    return folder


def _binding(info):
    return {'device': info.st_dev, 'inode': info.st_ino,
            'uid': info.st_uid, 'mode': stat.S_IMODE(info.st_mode)}


def expected_from_audit(package, inventory, app):
    package = Path(package).absolute()
    require(type(app) is str and re.fullmatch(r'[A-Za-z0-9_-]+', app), 'unsafe firmware app name')
    files = inventory['files']
    result = {}
    for source_key, destination in (('firmware', app + '.bit.bin'), ('dtbo', app + '.dtbo')):
        relative = Path(inventory['source_paths'][source_key]).relative_to(package).as_posix()
        result[destination] = files[relative]
    result['shell.json'] = files['firmware/shell.json']
    validate_expected(result, app)
    return result


def validate_expected(expected, app):
    require(type(expected) is dict and set(expected) == {app + '.bit.bin', app + '.dtbo', 'shell.json'},
            'exact three-file firmware inventory required')
    for name, pin in expected.items():
        require('/' not in name and '\\' not in name and name not in ('.', '..') and
                type(pin) is dict and set(pin) == {'bytes', 'sha256'} and
                type(pin['bytes']) is int and 0 < pin['bytes'] <= 32*1024*1024 and
                type(pin['sha256']) is str and re.fullmatch('[a-f0-9]{64}', pin['sha256']),
                'invalid nonempty audited firmware identity')


def _read(path, expected):
    before = path.lstat()
    _trusted(before, directory=False)
    require(before.st_size == expected['bytes'], 'firmware byte count differs: ' + path.name)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        _trusted(opened, directory=False)
        require(_key(opened) == _key(before), 'firmware replaced before read')
        digest, total = hashlib.sha256(), 0
        for block in iter(lambda: stream.read(1024*1024), b''):
            total += len(block)
            require(total <= expected['bytes'], 'firmware grew while reading')
            digest.update(block)
        require(_key(os.fstat(stream.fileno())) == _key(before) and
                _key(path.lstat()) == _key(before), 'firmware changed while reading')
    require({'bytes': total, 'sha256': digest.hexdigest()} == expected,
            'firmware SHA differs: ' + path.name)
    return dict(_binding(before), bytes=before.st_size, mtime_ns=before.st_mtime_ns,
                ctime_ns=before.st_ctime_ns, sha256=digest.hexdigest())


def inspect(folder, expected, app):
    """Read-only readiness record, including absent/existing disposition."""
    validate_expected(expected, app)
    folder = Path(folder).absolute()
    parent = _chain(folder.parent)
    parent_binding = _binding(parent.lstat())
    try:
        before = folder.lstat()
    except FileNotFoundError:
        return {'schema': 'opt50_firmware_staging_v2', 'directory': str(folder),
                'disposition': 'CREATE_NEW_EXCLUSIVELY', 'parent': parent_binding,
                'expected': expected, 'observed': None}
    _trusted(before, directory=True)
    require({path.name for path in folder.iterdir()} == set(expected),
            'existing firmware has missing/extra files; preserve for review')
    observed = {name: _read(folder/name, pin) for name, pin in sorted(expected.items())}
    require(_binding(folder.lstat()) == _binding(before) and
            {path.name for path in folder.iterdir()} == set(expected) and
            _binding(parent.lstat()) == parent_binding, 'firmware directory replaced during inspection')
    return {'schema': 'opt50_firmware_staging_v2', 'directory': str(folder),
            'disposition': 'REUSE_VERIFIED_READ_ONLY', 'parent': parent_binding,
            'expected': expected, 'observed': {'directory': _binding(before), 'files': observed}}


def assert_same(folder, frozen, expected, app):
    require(inspect(folder, expected, app) == frozen,
            'firmware changed since the frozen plan/staging inspection; no overwrite or retry')
    return frozen


def stage(folder, frozen, expected, app, sources, durable):
    """Reuse exact verified files without writes, else copy only to NEW paths."""
    require(type(frozen) is dict and frozen.get('schema') == 'opt50_firmware_staging_v2' and
            frozen.get('directory') == str(Path(folder).absolute()) and frozen.get('expected') == expected,
            'firmware staging plan differs')
    assert_same(folder, frozen, expected, app)
    if frozen['disposition'] == 'REUSE_VERIFIED_READ_ONLY':
        return frozen
    require(frozen['disposition'] == 'CREATE_NEW_EXCLUSIVELY' and set(sources) == set(expected),
            'only new exclusive firmware creation is permitted')
    durable.mkdir_new(folder, mode=0o755)
    for name, pin in sorted(expected.items()):
        durable.copy_new(sources[name], Path(folder)/name, pin, mode=0o644)
    result = inspect(folder, expected, app)
    require(result['disposition'] == 'REUSE_VERIFIED_READ_ONLY', 'new firmware verification failed')
    return result
