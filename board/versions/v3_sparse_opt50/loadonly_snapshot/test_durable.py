"""Host-only durability unit tests. No hardware and no board commands.

Windows tests mock directory fsync; this does not qualify Windows durability.
Scratch data is kept under this new workspace's .test_tmp, not system TEMP.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

import durable


SCRATCH = Path(__file__).parent / '.test_tmp'
REAL_FSYNC_DIR = durable.fsync_dir
REAL_IDENTITY = durable._identity


class DurableTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='durable_', dir=SCRATCH))
        self.directory_sync = mock.patch.object(durable, 'fsync_dir')
        self.sync = self.directory_sync.start()
        self.addCleanup(self.directory_sync.stop)
        self.addCleanup(shutil.rmtree, self.folder)
        if os.name == 'nt':
            # Windows defers file last-write timestamps until the writer closes.
            # Actual Windows writes are unsupported; normalize only these test
            # comparisons to exercise publication logic with mocked dir fsync.
            identities = mock.patch.object(durable, '_identity',
                                           side_effect=lambda info: REAL_IDENTITY(info)[:3] + (0, 0))
            identities.start()
            self.addCleanup(identities.stop)

    def expected(self, content):
        return {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}

    def test_new_json_canonical_utf8_and_no_overwrite(self):
        path = self.folder / 'plan.json'
        durable.new_json(path, {'z': 2, 'a': '板卡'})
        self.assertEqual(path.read_bytes(), '{"a":"板卡","z":2}\n'.encode('utf-8'))
        with self.assertRaises(FileExistsError):
            durable.new_json(path, {'changed': True})
        self.assertEqual(json.loads(path.read_bytes()), {'z': 2, 'a': '板卡'})

    def test_serialization_fails_before_creation(self):
        for value in ({'x': float('nan')}, {'x': object()}):
            path = self.folder / 'invalid.json'
            with self.assertRaises((ValueError, TypeError)):
                durable.new_json(path, value)
            self.assertFalse(path.exists())

    def test_write_flush_then_fsync(self):
        trace = []
        class FakeFile(io.BytesIO):
            def write(self, data):
                trace.append('write')
                return super().write(data)
            def flush(self):
                trace.append('flush')
            def fileno(self):
                return 123
        value = FakeFile()
        with mock.patch.object(durable.os, 'fdopen', return_value=value), \
                mock.patch.object(durable.os, 'fsync', side_effect=lambda fd: trace.append('fsync')), \
                mock.patch.object(durable.os, 'fstat', return_value='identity'):
            self.assertEqual(durable._write(123, b'abc'), 'identity')
        self.assertEqual(trace, ['write', 'flush', 'fsync'])

    def test_flush_error_and_short_write_propagate_without_fsync(self):
        for short in (True, False):
            handle = mock.MagicMock()
            handle.__enter__.return_value = handle
            handle.write.return_value = 1 if short else 3
            if not short:
                handle.flush.side_effect = OSError('flush failed')
            with self.subTest(short=short), \
                    mock.patch.object(durable.os, 'fdopen', return_value=handle), \
                    mock.patch.object(durable.os, 'fsync') as fsync:
                with self.assertRaisesRegex(OSError, 'short write' if short else 'flush failed'):
                    durable._write(123, b'abc')
                fsync.assert_not_called()

    def test_new_json_file_sync_precedes_directory_publish_sync(self):
        trace = []
        self.sync.side_effect = lambda path: trace.append('directory')
        real_sync = durable.os.fsync
        with mock.patch.object(durable.os, 'fsync', side_effect=lambda fd: (trace.append('file'), real_sync(fd))[1]):
            durable.new_json(self.folder / 'event.json', {'safe': True})
        self.assertEqual(trace, ['directory', 'file', 'directory'])

    def test_unsupported_directory_sync_fails_before_creation(self):
        self.sync.side_effect = NotImplementedError('directory fsync unavailable')
        path = self.folder / 'plan.json'
        with self.assertRaises(NotImplementedError):
            durable.new_json(path, {})
        self.assertFalse(path.exists())

    def test_file_fsync_error_propagates(self):
        with mock.patch.object(durable.os, 'fsync', side_effect=OSError('EIO')):
            with self.assertRaisesRegex(OSError, 'EIO'):
                durable.new_json(self.folder / 'partial.json', {})
        self.assertTrue((self.folder / 'partial.json').exists())

    def test_directory_publish_error_propagates_without_overwriting_evidence(self):
        self.sync.side_effect = [None, OSError('directory EIO')]
        path = self.folder / 'event.json'
        with self.assertRaisesRegex(OSError, 'directory EIO'):
            durable.new_json(path, {'status': 'not_durable'})
        self.assertTrue(path.exists())
        with self.assertRaises(FileExistsError):
            self.sync.side_effect = None
            durable.new_json(path, {})

    def test_replace_file_fsync_replace_directory_order(self):
        path = self.folder / 'status.json'
        durable.new_json(path, {'step': 1})
        trace = []
        self.sync.side_effect = lambda directory: trace.append('directory')
        actual_fsync, actual_replace = durable.os.fsync, durable.os.replace
        with mock.patch.object(durable.os, 'fsync', side_effect=lambda fd: (trace.append('file'), actual_fsync(fd))[1]), \
                mock.patch.object(durable.os, 'replace', side_effect=lambda *a, **kw: (trace.append('replace'), actual_replace(*a, **kw))[1]):
            durable.replace_json(path, {'step': 2})
        self.assertEqual(trace, ['directory', 'file', 'replace', 'directory'])
        self.assertEqual(json.loads(path.read_bytes()), {'step': 2})
        self.assertEqual(list(self.folder.glob('*.tmp_*')), [])

    def test_replace_new_target_allowed(self):
        path = self.folder / 'new_status.json'
        durable.replace_json(path, {'ready': False})
        self.assertEqual(json.loads(path.read_bytes()), {'ready': False})

    def test_failed_replace_keeps_original_and_temporary(self):
        path = self.folder / 'status.json'
        durable.new_json(path, {'step': 1})
        with mock.patch.object(durable.os, 'replace', side_effect=OSError('replace failed')):
            with self.assertRaisesRegex(OSError, 'replace failed'):
                durable.replace_json(path, {'step': 2})
        self.assertEqual(json.loads(path.read_bytes()), {'step': 1})
        self.assertEqual(len(list(self.folder.glob('.status.json.tmp_*'))), 1)

    def test_replace_directory_refused(self):
        target = self.folder / 'directory.json'
        target.mkdir()
        with self.assertRaises(ValueError):
            durable.replace_json(target, {})
        self.assertEqual(list(self.folder.glob('.directory.json.tmp_*')), [])

    def test_hardlink_mutable_target_refused(self):
        path = self.folder / 'status.json'
        path.write_bytes(b'original')
        alias = self.folder / 'alias.json'
        try:
            os.link(path, alias)
        except (OSError, NotImplementedError):
            self.skipTest('hardlinks unavailable')
        with self.assertRaisesRegex(ValueError, 'hard-linked'):
            durable.replace_json(path, {})
        self.assertEqual(alias.read_bytes(), b'original')

    def test_copy_new_verifies_bytes_hash_and_no_overwrite(self):
        payload = b'weights\x00\xff' * 100
        source, destination = self.folder / 'source.bin', self.folder / 'copy.bin'
        source.write_bytes(payload)
        self.assertEqual(durable.copy_new(source, destination, self.expected(payload)), self.expected(payload))
        self.assertEqual(destination.read_bytes(), payload)
        with self.assertRaises(FileExistsError):
            durable.copy_new(source, destination, self.expected(payload))

    def test_copy_size_mismatch_before_destination_creation(self):
        source, destination = self.folder / 'source.bin', self.folder / 'copy.bin'
        source.write_bytes(b'abc')
        with self.assertRaisesRegex(ValueError, 'byte count'):
            durable.copy_new(source, destination, self.expected(b'abcd'))
        self.assertFalse(destination.exists())

    def test_copy_wrong_hash_never_reports_success(self):
        source, destination = self.folder / 'source.bin', self.folder / 'copy.bin'
        source.write_bytes(b'abc')
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            durable.copy_new(source, destination, self.expected(b'xyz'))

    def test_copy_directory_refused(self):
        with self.assertRaisesRegex(ValueError, 'regular file'):
            durable.copy_new(self.folder, self.folder / 'out', self.expected(b''))

    def test_copy_source_identity_change_refused(self):
        source, destination = self.folder / 'source.bin', self.folder / 'copy.bin'
        source.write_bytes(b'abc')
        actual_identity = durable._identity
        count = 0
        def changed(info):
            nonlocal count
            count += 1
            value = actual_identity(info)
            return value if count <= 3 else value[:-1] + (value[-1] + 1,)
        with mock.patch.object(durable, '_identity', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                durable.copy_new(source, destination, self.expected(b'abc'))

    def test_copy_destination_corruption_detected_by_independent_readback(self):
        source, destination = self.folder / 'source.bin', self.folder / 'copy.bin'
        source.write_bytes(b'abc')
        actual_publish_check = durable._publish_check
        def corrupt(path, *args, **kwargs):
            actual_publish_check(path, *args, **kwargs)
            if path == destination:
                path.write_bytes(b'xyz')
        with mock.patch.object(durable, '_publish_check', side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, 'destination verification mismatch'):
                durable.copy_new(source, destination, self.expected(b'abc'))

    def test_invalid_hash_spec_and_modes_refused(self):
        for value in ({}, {'bytes': True, 'sha256': '0' * 64},
                      {'bytes': 1, 'sha256': 'A' * 64},
                      {'bytes': 0, 'sha256': '0' * 64, 'extra': 1}):
            with self.assertRaises(ValueError):
                durable.copy_new(self.folder / 'missing', self.folder / 'out', value)
        for mode in (-1, 0o4755, True):
            with self.assertRaises(ValueError):
                durable.mkdir_new(self.folder / 'bad_mode', mode)

    def test_mkdir_new_syncs_child_and_parent_without_existing_reuse(self):
        target = self.folder / 'new_directory'
        self.assertEqual(durable.mkdir_new(target), target)
        self.assertEqual(self.sync.call_args_list,
                         [mock.call(self.folder), mock.call(target), mock.call(self.folder)])
        with self.assertRaises(FileExistsError):
            durable.mkdir_new(target)

    def test_guarded_aliases_and_parent_traversal_refused(self):
        for name in ('CON', 'con.json', 'NUL', 'COM1.log', 'name.', 'name ', 'x:y', 'x\\y', 'x\ny'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                durable._name(name)
        with self.assertRaises(ValueError):
            durable.new_json(self.folder / '..' / 'escape.json', {})

    def test_symlink_leaf_and_parent_refused(self):
        source = self.folder / 'source.json'
        source.write_bytes(b'original')
        link = self.folder / 'link.json'
        link_dir = self.folder / 'linked_parent'
        actual_dir = self.folder / 'real_parent'
        actual_dir.mkdir()
        try:
            link.symlink_to(source)
            link_dir.symlink_to(actual_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable to development user')
        for function, args in ((durable.replace_json, (link, {})),
                               (durable.copy_new, (link, self.folder / 'copy', self.expected(b'original'))),
                               (durable.new_json, (link_dir / 'new.json', {}))):
            with self.assertRaises(ValueError):
                function(*args)
        self.assertEqual(source.read_bytes(), b'original')

    def test_mocked_reparse_point_refused(self):
        fake = mock.Mock(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with self.assertRaisesRegex(ValueError, 'reparse'):
            durable._directory(fake)

    def test_mocked_symlink_leaf_and_parent_refused_before_writes(self):
        info = mock.Mock(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0)
        with mock.patch.object(durable, '_stat', return_value=info), \
                mock.patch.object(durable, '_open') as opening:
            with self.assertRaisesRegex(ValueError, 'symlink'):
                durable.replace_json(self.folder / 'linked.json', {})
            opening.assert_not_called()
        actual_lstat = durable.Path.lstat
        def inspect(path):
            return info if path == self.folder else actual_lstat(path)
        with mock.patch.object(durable.Path, 'lstat', inspect), \
                mock.patch.object(durable, '_open') as opening:
            with self.assertRaisesRegex(ValueError, 'symlink'):
                durable.new_json(self.folder / 'child.json', {})
            opening.assert_not_called()

    def test_native_windows_directory_fsync_refuses(self):
        if os.name != 'nt':
            self.skipTest('Windows-specific fail-closed check')
        with self.assertRaises(NotImplementedError):
            REAL_FSYNC_DIR(self.folder)

    def test_native_posix_real_directory_fsync_smoke(self):
        if os.name != 'posix':
            self.skipTest('requires a POSIX target; no Windows durability claim')
        # This target-side test uses real directory/file fsync and real pinned
        # directory descriptors, not the default development fsync-dir mock.
        with mock.patch.object(durable, 'fsync_dir', REAL_FSYNC_DIR):
            directory = durable.mkdir_new(self.folder / 'native_posix')
            durable.new_json(directory / 'source.json', {'stage': 1})
            payload = (directory / 'source.json').read_bytes()
            durable.copy_new(directory / 'source.json', directory / 'copy.json', self.expected(payload))
            durable.replace_json(directory / 'copy.json', {'stage': 2})
            journal = durable.Journal(directory)
            journal.append('native_file_only_test', 'intent')
            journal.append('native_file_only_test', 'result', physical_PL_starts=0)
            self.assertEqual(durable.Journal(directory).next_sequence, 3)
            self.assertEqual(json.loads((directory / 'copy.json').read_bytes()), {'stage': 2})

    def test_journal_durable_intent_before_effect_and_restart_counter(self):
        journal = durable.Journal(self.folder)
        trace = []
        self.sync.side_effect = lambda path: trace.append('directory')
        event = journal.append('temporary_load', 'intent', boot_id='test')
        trace.append('effect_would_follow')
        self.assertEqual(event['sequence'], 1)
        self.assertEqual(trace[-2:], ['directory', 'effect_would_follow'])
        journal.append('temporary_load', 'result', returncode=0)
        recovered = durable.Journal(self.folder)
        self.assertEqual(recovered.next_sequence, 3)
        self.assertEqual(recovered.append('inspect', 'intent')['sequence'], 3)

    def test_failed_intent_stops_caller_and_does_not_advance_counter(self):
        journal = durable.Journal(self.folder)
        effect = mock.Mock()
        self.sync.side_effect = [None, OSError('intent directory EIO')]
        with self.assertRaisesRegex(OSError, 'intent directory EIO'):
            journal.append('load', 'intent')
            effect()
        effect.assert_not_called()
        self.assertEqual(journal.next_sequence, 1)

    def test_journal_sequence_gap_or_partial_refuses_recovery(self):
        (self.folder / 'event_000002.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'gap'):
            durable.Journal(self.folder)
        (self.folder / 'event_000002.json').unlink()
        (self.folder / 'event_000001.json').write_bytes(b'{')
        with self.assertRaises(json.JSONDecodeError):
            durable.Journal(self.folder)

    def test_journal_alias_and_reserved_fields_refused(self):
        journal = durable.Journal(self.folder)
        for action, phase in (('../load', 'intent'), ('load', 'intent\n'), ('', 'result')):
            with self.assertRaises(ValueError):
                journal.append(action, phase)
        with self.assertRaises(ValueError):
            journal.append('load', 'intent', sequence=99)
        (self.folder / 'event_000001.json.extra').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            durable.Journal(self.folder)

    def test_journal_noncanonical_or_edited_field_refuses_recovery(self):
        journal = durable.Journal(self.folder)
        journal.append('load', 'intent')
        path = self.folder / 'event_000001.json'
        original = path.read_bytes()
        path.write_bytes(b' ' + original)
        with self.assertRaisesRegex(ValueError, 'canonical'):
            durable.Journal(self.folder)
        value = json.loads(original)
        value['sequence'] = 2
        path.write_bytes(durable._bytes(value))
        with self.assertRaisesRegex(ValueError, 'invalid journal event'):
            durable.Journal(self.folder)


if __name__ == '__main__':
    unittest.main()
