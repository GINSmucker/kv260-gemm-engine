"""Typed load-log permissions: synthetic metadata plus an optional native test.

Only new isolated temporary fixtures are created/removed. No guardian files,
processes, locks, devices, firmware or existing permissions are modified.
Windows fixtures emulate POSIX ownership/modes explicitly; they are not a
claim of native Linux permission or O_NOFOLLOW verification.
"""
import contextlib
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

import resident


class GuardianLogPermissionTests(unittest.TestCase):
    def setUp(self):
        fixtures = Path(__file__).parent/'.test_tmp'
        fixtures.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='typed_guardian_log_', dir=fixtures)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.records = self.base/'records'
        self.plan_root = self.records/'plan_74abb1e54663'
        self.execution = self.plan_root/'execution'
        self.execution.mkdir(parents=True)
        self.path = self.execution/'command_01.stdout'
        self.raw = b'remove from slot 0 returns: 0 (Ok)\n'
        self.path.write_bytes(self.raw)
        self.expected = resident.identity(self.raw)
        self.permissions = 0o644
        self.owner = 0
        self.links = 1
        self.parent_permissions = 0o700
        self.leaf_kind = stat.S_IFREG
        self.attributes = 0
        self.parent_owner = 0
        self.stat_transform = lambda value: value
        self.fd_transform = lambda value, index: value
        self.lstat_calls = self.fstat_calls = 0
        real_lstat, real_fstat = Path.lstat, os.fstat
        directories = {self.records, self.plan_root, self.execution}

        def values(info, *, mode, owner, links, attributes=0):
            return types.SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                st_size=info.st_size, st_mtime_ns=0, st_ctime_ns=0,
                st_uid=owner, st_gid=0, st_nlink=links, st_mode=mode,
                st_file_attributes=attributes)

        def observed_lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path in directories:
                return values(info, mode=stat.S_IFDIR|self.parent_permissions,
                              owner=self.parent_owner, links=info.st_nlink)
            if path == self.path:
                self.lstat_calls += 1
                result = values(info, mode=self.leaf_kind|self.permissions,
                                owner=self.owner, links=self.links, attributes=self.attributes)
                return self.stat_transform(result)
            return info

        def observed_fstat(descriptor):
            info = real_fstat(descriptor)
            self.fstat_calls += 1
            result = values(info, mode=stat.S_IFREG|self.permissions, owner=self.owner, links=self.links)
            return self.fd_transform(result, self.fstat_calls)

        context = contextlib.ExitStack()
        self.addCleanup(context.close)
        context.enter_context(mock.patch.object(resident, 'OLD_PLAN', self.plan_root/'plan.json'))
        context.enter_context(mock.patch.object(Path, 'lstat', new=observed_lstat))
        context.enter_context(mock.patch.object(os, 'fstat', new=observed_fstat))
        # Windows has no O_NOFOLLOW; only the metadata-emulation tests use 0.
        # The optional native POSIX test below exercises the actual flag.
        if not hasattr(os, 'O_NOFOLLOW'):
            context.enter_context(mock.patch.object(os, 'O_NOFOLLOW', 0, create=True))

    def read(self):
        return resident.read_guardian_command_log(self.path, self.expected)

    def test_root_0600_and_0644_inside_0700_are_accepted_with_actual_bytes(self):
        for mode in (0o600, 0o644):
            self.permissions = mode
            self.assertEqual(self.read(), self.raw)

    def test_all_eight_literal_load_log_names_are_supported(self):
        for index in range(1, 5):
            for suffix in ('stdout', 'stderr'):
                target = self.execution/('command_%02d.%s' % (index, suffix))
                if target != self.path:
                    target.write_bytes(self.raw)
                # Use the same synthetic leaf observer for each permitted file.
                self.path = target
                self.assertEqual(self.read(), self.raw)

    def test_root_owned_does_not_relax_generic_private_reader(self):
        self.permissions = 0o644
        with self.assertRaisesRegex(ValueError, 'root private single-link'):
            resident.read_file(self.path, private=True)
        self.permissions = 0o600
        self.assertEqual(resident.read_file(self.path, private=True), self.raw)

    def test_nonroot_writable_executable_or_special_modes_are_rejected(self):
        for mode in (0o640, 0o660, 0o664, 0o666, 0o755, 0o700, 0o4644, 0o2644):
            self.permissions = mode
            with self.assertRaisesRegex(ValueError, '0600/0644', msg=oct(mode)):
                self.read()
        self.permissions = 0o644
        self.owner = 1000
        with self.assertRaisesRegex(ValueError, 'root-owned'):
            self.read()

    def test_hardlinks_symlinks_reparse_and_nonregular_files_are_rejected(self):
        self.links = 2
        with self.assertRaisesRegex(ValueError, 'single-link'):
            self.read()
        self.links = 1
        for kind in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFCHR):
            self.leaf_kind = kind
            with self.assertRaises(ValueError):
                self.read()
        self.leaf_kind = stat.S_IFREG
        self.attributes = 0x400
        with self.assertRaisesRegex(ValueError, 'linked'):
            self.read()

    def test_each_protected_parent_must_be_root_private_0700(self):
        for mode in (0o755, 0o750, 0o770, 0o500):
            self.parent_permissions = mode
            with self.assertRaises(ValueError, msg=oct(mode)):
                self.read()
        self.parent_permissions = 0o700
        self.parent_owner = 1000
        with self.assertRaisesRegex(ValueError, 'root private directory'):
            self.read()

    def test_unknown_alias_relative_and_other_directory_paths_fail_before_open(self):
        candidates = (self.execution/'command_00.stdout', self.execution/'command_05.stderr',
                      self.execution/'command_01.json', self.execution/'command_01.stdout.bak',
                      self.plan_root/'command_01.stdout', Path('command_01.stdout'),
                      self.execution/'sub/../command_01.stdout')
        with mock.patch.object(os, 'open') as opened:
            for path in candidates:
                with self.assertRaisesRegex(ValueError, 'fixed guardian', msg=str(path)):
                    resident.read_guardian_command_log(path, self.expected)
        opened.assert_not_called()

    def test_expected_size_hash_and_pin_shape_are_checked_inside_reader(self):
        with self.assertRaisesRegex(ValueError, 'SHA/size'):
            resident.read_guardian_command_log(self.path, resident.identity(b'x'*len(self.raw)))
        for expected in (None, {}, dict(self.expected, extra=1),
                         {'bytes': True, 'sha256': self.expected['sha256']},
                         {'bytes': 32*1024*1024+1, 'sha256': 'a'*64}):
            with self.assertRaises(ValueError):
                resident.read_guardian_command_log(self.path, expected)
        with self.assertRaisesRegex(ValueError, 'bounded'):
            resident.read_guardian_command_log(self.path, resident.identity(self.raw+b'extra'))

    def test_empty_stderr_with_its_hash_is_accepted(self):
        target = self.execution/'command_01.stderr'
        target.write_bytes(b'')
        self.path = target
        self.assertEqual(resident.read_guardian_command_log(target, resident.identity(b'')), b'')

    def test_fd_inode_replacement_before_read_is_rejected(self):
        def changed(value, _):
            value.st_ino += 1
            return value
        self.fd_transform = changed
        with self.assertRaisesRegex(ValueError, 'before read'):
            self.read()

    def test_fd_metadata_change_during_read_is_rejected_even_for_allowed_modes(self):
        for field, changed in (('st_mode', stat.S_IFREG|0o600), ('st_gid', 77),
                               ('st_ino', 1000000), ('st_mtime_ns', 99), ('st_ctime_ns', 88)):
            self.fstat_calls = 0
            def transform(value, index):
                if index > 1:
                    setattr(value, field, changed)
                return value
            self.fd_transform = transform
            with self.assertRaisesRegex(ValueError, 'during read', msg=field):
                self.read()

    def test_fd_owner_or_link_change_during_read_is_rejected(self):
        for field, changed in (('st_uid', 1000), ('st_nlink', 2)):
            self.fstat_calls = 0
            def transform(value, index):
                if index > 1:
                    setattr(value, field, changed)
                return value
            self.fd_transform = transform
            with self.assertRaisesRegex(ValueError, 'root-owned', msg=field):
                self.read()

    def test_leaf_or_parent_replacement_after_read_is_rejected(self):
        # Once both fd observations have completed, emulate a new leaf inode.
        def after(value):
            if self.fstat_calls >= 2:
                value.st_ino += 1
            return value
        self.stat_transform = after
        with self.assertRaisesRegex(ValueError, 'after read'):
            self.read()
        self.stat_transform = lambda value: value
        self.fstat_calls = 0
        parent_ids = resident._guardian_log_parents
        count = 0
        def changed_parent(execution):
            nonlocal count
            count += 1
            result = parent_ids(execution)
            if count > 1:
                result['synthetic replacement'] = (0, 99, 0o40700, 0, 0)
            return result
        with mock.patch.object(resident, '_guardian_log_parents', side_effect=changed_parent), \
                self.assertRaisesRegex(ValueError, 'after read'):
            self.read()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'native POSIX root fixture only; no real guardian paths are touched')
class NativePosixGuardianLogTests(unittest.TestCase):
    def test_native_posix_0600_0644_bytes_and_link_rejection(self):
        fixtures = Path(__file__).parent/'.test_tmp'
        fixtures.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='native_typed_log_', dir=fixtures) as name:
            root = Path(name)
            records = root/'records'; records.mkdir(mode=0o700)
            plan_root = records/'plan_74abb1e54663'; plan_root.mkdir(mode=0o700)
            execution = plan_root/'execution'; execution.mkdir(mode=0o700)
            target = execution/'command_01.stdout'
            raw = b'native file-only synthetic log\n'
            target.write_bytes(raw)
            expected = resident.identity(raw)
            with mock.patch.object(resident, 'OLD_PLAN', plan_root/'plan.json'):
                for mode in (0o600, 0o644):
                    target.chmod(mode)  # Only this NEW isolated test fixture.
                    self.assertEqual(resident.read_guardian_command_log(target, expected), raw)
                link = execution/'not_a_guardian_log'
                os.link(target, link)
                with self.assertRaisesRegex(ValueError, 'single-link'):
                    resident.read_guardian_command_log(target, expected)
                link.unlink()
                alias = execution/'command_02.stdout'; alias.symlink_to(target)
                with self.assertRaisesRegex(ValueError, 'linked'):
                    resident.read_guardian_command_log(alias, expected)


if __name__ == '__main__':
    unittest.main()
