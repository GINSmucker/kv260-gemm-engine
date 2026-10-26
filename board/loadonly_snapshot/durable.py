"""Fail-closed local durability primitives; no device or process operations.

Successful writes mean fsync(file), then fsync(parent), completed. Windows has
no supported directory-fsync implementation here and is rejected before writes.
Tests can mock ``fsync_dir`` to exercise file logic on a Windows development PC;
those tests are not a claim of native Windows power-loss durability.

Callers must separately establish trusted ownership and permissions of the
containing directories. No API changes ownership, follows symlinks/reparse
points, overwrites an exclusive output, or removes a failed write's evidence.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


_PORTABLE_RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {
    prefix + str(number) for prefix in ('COM', 'LPT') for number in range(1, 10)
}
_EVENT = re.compile(r'event_([0-9]{6})\.json\Z')
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,79}\Z')
_REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


def _name(name):
    if (not name or name in ('.', '..') or name[-1:] in ('.', ' ')
            or any(c in name for c in '\\/:\x00\r\n')
            or name.split('.')[0].upper() in _PORTABLE_RESERVED):
        raise ValueError('unsafe or non-portable filename: ' + repr(name))
    return name


def _path(path):
    original = Path(path)
    if original.drive and not original.root:
        raise ValueError('drive-relative path is forbidden')
    if '..' in original.parts:
        raise ValueError('parent traversal is forbidden')
    result = original.absolute()
    _name(result.name)
    return result


def _not_link(info):
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _REPARSE:
        raise ValueError('symlink or reparse point is forbidden')


def _directory(info):
    _not_link(info)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('expected regular directory')


def _regular(info, *, single_link=False):
    _not_link(info)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('expected regular file')
    if single_link and info.st_nlink != 1:
        raise ValueError('hard-linked mutable output is forbidden')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _chain(path):
    """Validate every existing component without resolving links."""
    parts = path.parts
    current = Path(parts[0])
    _directory(current.lstat())
    for part in parts[1:]:
        _name(part)
        current /= part
        _directory(current.lstat())


def _open_directory(path):
    """Open a POSIX directory one O_NOFOLLOW component at a time."""
    if os.name != 'posix':
        raise NotImplementedError('POSIX directory fsync is required')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            _name(part)
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        _directory(os.fstat(descriptor))
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def fsync_dir(path):
    """Synchronize an existing real directory; unsupported/error => raise."""
    directory = Path(path).absolute()
    if '..' in Path(path).parts:
        raise ValueError('parent traversal is forbidden')
    descriptor = _open_directory(directory)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _parent(path):
    path = _path(path)
    _chain(path.parent)
    descriptor = _open_directory(path.parent) if os.name == 'posix' else None
    try:
        # Also rejects an unsupported platform before any mutation. A later
        # directory fsync is still required after publishing each new entry.
        fsync_dir(path.parent)
        yield path, descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _stat(path, descriptor):
    if descriptor is None:
        return path.lstat()
    return os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)


def _open(path, flags, mode, descriptor):
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    if descriptor is None:
        return os.open(path, flags, mode)
    return os.open(path.name, flags, mode, dir_fd=descriptor)


def _bytes(value):
    # Encode completely before creating any output (NaN and arbitrary objects
    # must not result in a partial plan or action-intent record).
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _write(descriptor, payload):
    with os.fdopen(descriptor, 'wb') as handle:
        if handle.write(payload) != len(payload):
            raise OSError('short write')
        handle.flush()
        os.fsync(handle.fileno())
        return os.fstat(handle.fileno())


def _publish_check(path, descriptor, expected_identity=None, *, renamed=False):
    _chain(path.parent)
    if descriptor is not None:
        actual_parent = path.parent.stat()
        pinned_parent = os.fstat(descriptor)
        if (actual_parent.st_dev, actual_parent.st_ino) != (pinned_parent.st_dev, pinned_parent.st_ino):
            raise ValueError('parent directory identity changed')
    info = _stat(path, descriptor)
    _regular(info, single_link=True)
    if expected_identity is not None:
        # Rename may update ctime; inode, byte count and mtime must not change.
        actual = _identity(info)
        if (actual[:4] != expected_identity[:4] if renamed else actual != expected_identity):
            raise ValueError('published file identity changed')


def new_json(path, value):
    """Create one immutable JSON file exclusively, then sync its directory."""
    payload = _bytes(value)
    with _parent(path) as (path, descriptor):
        output = _open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, descriptor)
        written = _write(output, payload)
        _publish_check(path, descriptor, _identity(written))
        fsync_dir(path.parent)
    return path


def replace_json(path, value):
    """Atomically publish JSON; existing target must be a single-link file.

    An error can leave an exclusive ``.tmp_`` file or an already-replaced target;
    it is deliberately propagated. Callers must not treat an exception as a
    successful durable status update or erase partial evidence automatically.
    """
    payload = _bytes(value)
    with _parent(path) as (path, descriptor):
        try:
            existing = _stat(path, descriptor)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _regular(existing, single_link=True)
        temporary = path.with_name('.' + path.name + '.tmp_' + uuid.uuid4().hex)
        output = _open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, descriptor)
        written = _write(output, payload)
        _publish_check(temporary, descriptor, _identity(written))
        try:
            latest = _stat(path, descriptor)
        except FileNotFoundError:
            latest = None
        if ((existing is None) != (latest is None)
                or (existing is not None and _identity(existing) != _identity(latest))):
            raise ValueError('replacement target changed')
        if descriptor is None:
            os.replace(temporary, path)
        else:
            os.replace(temporary.name, path.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        _publish_check(path, descriptor, _identity(written), renamed=True)
        fsync_dir(path.parent)
    return path


def _expected(expected):
    if (not isinstance(expected, dict) or set(expected) != {'bytes', 'sha256'}
            or type(expected['bytes']) is not int or expected['bytes'] < 0
            or not isinstance(expected['sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', expected['sha256'])):
        raise ValueError('expected exact bytes and lowercase SHA256')
    return expected


def _mode(mode):
    if type(mode) is not int or mode < 0 or mode & ~0o777:
        raise ValueError('only ordinary POSIX permission bits are permitted')
    return mode


def copy_new(src, dst, expected, mode=0o600):
    """Copy stable, exact-hash regular input to a new exclusive durable file."""
    expected = _expected(expected)
    mode = _mode(mode)
    with _parent(src) as (source, source_parent):
        source_info = _stat(source, source_parent)
        _regular(source_info)
        source_fd = _open(source, os.O_RDONLY, 0, source_parent)
        with os.fdopen(source_fd, 'rb') as input_handle:
            before = os.fstat(input_handle.fileno())
            _regular(before)
            if _identity(before) != _identity(source_info) or before.st_size != expected['bytes']:
                raise ValueError('source identity or byte count mismatch')
            with _parent(dst) as (destination, destination_parent):
                output = _open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                               mode, destination_parent)
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(output, 'wb') as output_handle:
                    while True:
                        block = input_handle.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > expected['bytes']:
                            raise ValueError('source grew during copy')
                        digest.update(block)
                        if output_handle.write(block) != len(block):
                            raise OSError('short copy write')
                    after = os.fstat(input_handle.fileno())
                    if _identity(before) != _identity(after):
                        raise ValueError('source changed during copy')
                    if {'bytes': size, 'sha256': digest.hexdigest()} != expected:
                        raise ValueError('source content mismatch')
                    output_handle.flush()
                    os.fsync(output_handle.fileno())
                    written = os.fstat(output_handle.fileno())
                _publish_check(destination, destination_parent, _identity(written))
                # Re-open through the pinned parent to check actual destination
                # bytes, not merely the source digest of a successful write.
                verify_fd = _open(destination, os.O_RDONLY, 0, destination_parent)
                with os.fdopen(verify_fd, 'rb') as verify:
                    dest_before = os.fstat(verify.fileno())
                    _regular(dest_before, single_link=True)
                    dest_digest = hashlib.sha256()
                    dest_size = 0
                    for block in iter(lambda: verify.read(1024 * 1024), b''):
                        dest_size += len(block)
                        dest_digest.update(block)
                    if (_identity(dest_before) != _identity(os.fstat(verify.fileno()))
                            or _identity(dest_before) != _identity(written)
                            or {'bytes': dest_size, 'sha256': dest_digest.hexdigest()} != expected):
                        raise ValueError('destination verification mismatch')
                fsync_dir(destination.parent)
    return dict(expected)


def mkdir_new(path, mode=0o700):
    """Create exactly one new real directory under an existing safe parent."""
    mode = _mode(mode)
    with _parent(path) as (path, descriptor):
        if descriptor is None:
            os.mkdir(path, mode)
        else:
            os.mkdir(path.name, mode, dir_fd=descriptor)
        _directory(_stat(path, descriptor))
        fsync_dir(path)
        fsync_dir(path.parent)
    return path


def _read_record(path):
    """Read a bounded immutable event without a leaf-link check/open race."""
    with _parent(path) as (path, parent_descriptor):
        info = _stat(path, parent_descriptor)
        _regular(info, single_link=True)
        descriptor = _open(path, os.O_RDONLY, 0, parent_descriptor)
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            _regular(before, single_link=True)
            if _identity(before) != _identity(info):
                raise ValueError('journal record identity changed')
            if before.st_size > 16 * 1024 * 1024:
                raise ValueError('journal record too large')
            raw = handle.read(16 * 1024 * 1024 + 1)
            if len(raw) != before.st_size or _identity(os.fstat(handle.fileno())) != _identity(before):
                raise ValueError('journal record changed during read')
        _publish_check(path, parent_descriptor, _identity(before))
    value = json.loads(raw)
    if not isinstance(value, dict) or _bytes(value) != raw:
        raise ValueError('journal record is not canonical immutable JSON')
    return value


class Journal:
    """Numbered immutable records; an append returns only after durable intent.

    Use ``append(action, 'intent', ...)`` *before* any effect, then append a
    separate result/error. This class never performs the effect. Failure to
    append must abort the action; retry/recovery must inspect any partial record
    instead of overwriting it. Sequence gaps, malformed records, and aliases
    refuse recovery rather than resetting counters.
    """
    def __init__(self, folder):
        self.folder = Path(folder).absolute()
        if '..' in Path(folder).parts:
            raise ValueError('parent traversal is forbidden')
        _chain(self.folder)
        fsync_dir(self.folder)
        records = []
        for path in self.folder.iterdir():
            if not path.name.startswith('event_'):
                continue
            match = _EVENT.fullmatch(path.name)
            if not match:
                raise ValueError('ambiguous journal event filename')
            _regular(path.lstat(), single_link=True)
            records.append((int(match.group(1)), path))
        records.sort()
        for expected_sequence, (sequence, path) in enumerate(records, 1):
            if sequence != expected_sequence:
                raise ValueError('journal sequence gap')
            value = _read_record(path)
            if (value.get('schema') != 'opt50_durable_event_v1'
                    or type(value.get('sequence')) is not int
                    or value['sequence'] != sequence
                    or not isinstance(value.get('action'), str)
                    or not _TOKEN.fullmatch(value['action'])
                    or not isinstance(value.get('phase'), str)
                    or not _TOKEN.fullmatch(value['phase'])):
                raise ValueError('invalid journal event')
        self.next_sequence = len(records) + 1

    def append(self, action, phase, **details):
        if (not isinstance(action, str) or not _TOKEN.fullmatch(action)
                or not isinstance(phase, str) or not _TOKEN.fullmatch(phase)):
            raise ValueError('action and phase must be bounded safe tokens')
        if self.next_sequence > 999999:
            raise ValueError('journal sequence exhausted')
        if {'schema', 'sequence', 'action', 'phase'} & set(details):
            raise ValueError('reserved event fields')
        value = dict(details, schema='opt50_durable_event_v1',
                     sequence=self.next_sequence, action=action, phase=phase)
        new_json(self.folder / ('event_%06d.json' % self.next_sequence), value)
        self.next_sequence += 1
        return value
