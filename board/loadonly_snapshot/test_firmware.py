"""Synthetic files only: no board, loader, process or device access."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

import firmware


APP = 'kv260_opt50_fd96539d_4cbcf732'


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).parent/'.test_tmp'
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='synthetic_firmware_', dir=parent)
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.folder = self.parent/APP
        self.raw = {APP+'.bit.bin': b'synthetic-bitstream', APP+'.dtbo': b'synthetic-dtbo',
                    'shell.json': b'{"shell_type":"XRT_FLAT"}\n'}
        self.expected = {name: identity(raw) for name, raw in self.raw.items()}
        # Windows/nonroot development fixtures are NOT production ownership
        # evidence. Production's ownership/mode checks are separately tested.
        trusted = mock.patch.object(firmware, '_trusted')
        trusted.start(); self.addCleanup(trusted.stop)
        key = firmware._key
        portable = mock.patch.object(firmware, '_key', side_effect=lambda info: (*key(info)[:3], 0, 0))
        portable.start(); self.addCleanup(portable.stop)

    def create(self):
        self.folder.mkdir()
        for name, raw in self.raw.items():
            (self.folder/name).write_bytes(raw)

    def test_absence_is_a_readonly_new_exclusive_disposition(self):
        view = firmware.inspect(self.folder, self.expected, APP)
        self.assertEqual(view['disposition'], 'CREATE_NEW_EXCLUSIVELY')
        self.assertFalse(self.folder.exists())

    def test_verified_existing_firmware_reuse_does_not_call_writer(self):
        self.create()
        before = {name: (self.folder/name).read_bytes() for name in self.raw}
        frozen = firmware.inspect(self.folder, self.expected, APP)
        writer = mock.Mock()
        self.assertEqual(firmware.stage(self.folder, frozen, self.expected, APP, {}, writer), frozen)
        writer.mkdir_new.assert_not_called(); writer.copy_new.assert_not_called()
        self.assertEqual(before, {name: (self.folder/name).read_bytes() for name in self.raw})

    def test_partial_or_extra_inventory_is_refused_not_repaired(self):
        self.create()
        (self.folder/'extra').write_bytes(b'keep evidence')
        with self.assertRaisesRegex(ValueError, 'missing/extra'):
            firmware.inspect(self.folder, self.expected, APP)
        self.assertEqual((self.folder/'extra').read_bytes(), b'keep evidence')
        (self.folder/'extra').unlink()
        (self.folder/'shell.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing/extra'):
            firmware.inspect(self.folder, self.expected, APP)

    def test_wrong_hash_and_zero_length_are_refused(self):
        self.create()
        (self.folder/(''+APP+'.bit.bin')).write_bytes(b'x'*len(self.raw[APP+'.bit.bin']))
        with self.assertRaisesRegex(ValueError, 'SHA differs'):
            firmware.inspect(self.folder, self.expected, APP)
        (self.folder/(''+APP+'.bit.bin')).write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'byte count'):
            firmware.inspect(self.folder, self.expected, APP)

    def test_replacement_after_plan_refused_even_with_same_bytes(self):
        self.create()
        frozen = firmware.inspect(self.folder, self.expected, APP)
        victim = self.folder/'shell.json'
        replacement = self.parent/'replacement'
        replacement.write_bytes(victim.read_bytes())
        replacement.replace(victim)
        with self.assertRaisesRegex(ValueError, 'changed since'):
            firmware.assert_same(self.folder, frozen, self.expected, APP)

    def test_new_directory_becoming_existing_is_not_implicitly_reused(self):
        frozen = firmware.inspect(self.folder, self.expected, APP)
        self.create()
        writer = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'changed since'):
            firmware.stage(self.folder, frozen, self.expected, APP, {}, writer)
        writer.mkdir_new.assert_not_called(); writer.copy_new.assert_not_called()

    def test_existing_directory_disappearing_is_not_created_again(self):
        self.create()
        frozen = firmware.inspect(self.folder, self.expected, APP)
        preserved = self.parent/'preserved_old_directory'
        self.folder.rename(preserved)
        writer = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'changed since'):
            firmware.stage(self.folder, frozen, self.expected, APP, {}, writer)
        self.assertTrue(preserved.is_dir())
        self.assertFalse(self.folder.exists())
        writer.mkdir_new.assert_not_called(); writer.copy_new.assert_not_called()

    def test_replaced_directory_with_same_filenames_is_refused(self):
        self.create()
        frozen = firmware.inspect(self.folder, self.expected, APP)
        self.folder.rename(self.parent/'preserved_original')
        self.create()
        with self.assertRaisesRegex(ValueError, 'changed since'):
            firmware.assert_same(self.folder, frozen, self.expected, APP)

    def test_new_staging_is_exclusive_and_verifies_every_file(self):
        frozen = firmware.inspect(self.folder, self.expected, APP)
        sources = {}
        for name, raw in self.raw.items():
            source = self.parent/('source_'+name)
            source.write_bytes(raw); sources[name] = source
        def mkdir(path, mode):
            self.assertEqual(mode, 0o755); path.mkdir()
        def copy(source, target, pin, mode):
            self.assertEqual(mode, 0o644)
            self.assertEqual(identity(source.read_bytes()), pin)
            with target.open('xb') as stream:
                stream.write(source.read_bytes())
        writer = types.SimpleNamespace(mkdir_new=mock.Mock(side_effect=mkdir),
                                       copy_new=mock.Mock(side_effect=copy))
        result = firmware.stage(self.folder, frozen, self.expected, APP, sources, writer)
        self.assertEqual(result['disposition'], 'REUSE_VERIFIED_READ_ONLY')
        self.assertEqual(writer.mkdir_new.call_count, 1)
        self.assertEqual(writer.copy_new.call_count, 3)

    def test_bad_plan_and_inventory_do_not_write(self):
        writer = mock.Mock()
        for bad in ({}, dict(self.expected, extra=identity(b'x')),
                    dict(self.expected, **{'shell.json': identity(b'')})):
            with self.assertRaises(ValueError):
                firmware.stage(self.folder, {}, bad, APP, {}, writer)
        writer.mkdir_new.assert_not_called(); writer.copy_new.assert_not_called()

    def test_expected_pins_come_from_audited_source_paths(self):
        package = self.parent/'package'
        audit = {'files': {'firmware/a.bit.bin': self.expected[APP+'.bit.bin'],
                           'generated/a.dtbo': self.expected[APP+'.dtbo'],
                           'firmware/shell.json': self.expected['shell.json']},
                 'source_paths': {'firmware': str(package/'firmware/a.bit.bin'),
                                  'dtbo': str(package/'generated/a.dtbo')}}
        self.assertEqual(firmware.expected_from_audit(package, audit, APP), self.expected)
        audit['source_paths']['dtbo'] = str(self.parent/'outside.dtbo')
        with self.assertRaises(ValueError):
            firmware.expected_from_audit(package, audit, APP)


class FirmwarePermissionTests(unittest.TestCase):
    def value(self, **fields):
        values = dict(st_mode=stat.S_IFREG|0o644, st_uid=0, st_nlink=1,
                      st_file_attributes=0)
        values.update(fields)
        return types.SimpleNamespace(**values)

    def test_production_trust_requires_root_regular_nonwritable_singlelink(self):
        firmware._trusted(self.value(), directory=False)
        for fields in (dict(st_uid=1000), dict(st_mode=stat.S_IFREG|0o664),
                       dict(st_mode=stat.S_IFREG|0o646), dict(st_nlink=2),
                       dict(st_mode=stat.S_IFLNK|0o777), dict(st_file_attributes=0x400),
                       dict(st_mode=stat.S_IFDIR|0o755)):
            with self.assertRaises(ValueError, msg=str(fields)):
                firmware._trusted(self.value(**fields), directory=False)

    def test_production_trust_requires_real_root_directory(self):
        firmware._trusted(self.value(st_mode=stat.S_IFDIR|0o755), directory=True)
        for fields in (dict(st_uid=1000), dict(st_mode=stat.S_IFDIR|0o775),
                       dict(st_mode=stat.S_IFLNK|0o777), dict(st_file_attributes=0x400)):
            with self.assertRaises(ValueError):
                firmware._trusted(self.value(**fields), directory=True)


if __name__ == '__main__':
    unittest.main()
