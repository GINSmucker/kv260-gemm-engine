"""Synthetic binding rejection tests; never open a device or invoke a loader."""
from copy import deepcopy
from pathlib import Path
import ast
import unittest
from unittest.mock import patch

import board
from test_controller import baseline, loaded, obs


class BindingTests(unittest.TestCase):
    def test_actual_bound_driver_passes_despite_legacy_checker_false(self):
        value = loaded()
        value['snapshot_legacy_zocl_driver_present'] = False
        result = board.loaded(value, value['boot_id']['value'])
        self.assertEqual(result['driver_binding']['driver'], '/sys/bus/platform/drivers/zocl-drm')
        self.assertEqual(result['CSR'], 'BLOCKED')
        self.assertEqual(result['START'], 'BLOCKED')
        self.assertEqual(result['reset_and_PS_widths'], 'NOT_CHECKED')

    def test_registered_driver_without_binding_is_not_success(self):
        value = loaded()
        value['drm_driver_bindings'] = obs([])
        with self.assertRaisesRegex(ValueError, 'binding'):
            board.loaded(value, value['boot_id']['value'])

    def test_each_binding_component_is_required(self):
        good = loaded()['drm_driver_bindings']['value'][0]
        for key, replacement in (
            ('name', 'renderD129'), ('driver', '/sys/bus/platform/drivers/zocl'),
            ('module', '/sys/module/another'), ('of_node', '/sys/firmware/devicetree/base/axi/other'),
            ('compatible', ['xlnx,zocl', 'other']), ('status', 'disabled'),
            ('major', 1), ('minor', 129), ('sysfs_dev', '226:129'), ('major', True),
            ('character_device', False), ('sysfs_device', '/tmp/not-sysfs'),
            ('reverse_devices', []), ('reverse_devices', [good['sysfs_device']]*2),
            ('sysfs_char_matches', False),
        ):
            with self.subTest(key=key, replacement=replacement):
                value = deepcopy(good); value[key] = replacement
                with self.assertRaises(ValueError):
                    board.validate_binding(value)

    def test_incomplete_binding_observation_is_not_success(self):
        value = loaded()
        value['drm_driver_bindings'] = {'ok': False, 'error': 'FileNotFoundError'}
        with self.assertRaisesRegex(ValueError, 'incomplete observation'):
            board.loaded(value, value['boot_id']['value'])

    def test_extra_or_missing_render_refused(self):
        for names in ([], ['renderD128', 'renderD129'], ['renderD129']):
            value = loaded(); value['render_nodes'] = obs(names)
            with self.subTest(names=names), self.assertRaises(ValueError):
                board.loaded(value, value['boot_id']['value'])

    def test_unused_starter_rejects_any_real_zocl_registry_or_binding(self):
        for key, value in (
            ('zocl_registered_drivers', obs(['zocl-drm'])),
            ('drm_driver_bindings', loaded()['drm_driver_bindings']),
            ('zocl_module_present', True),
        ):
            candidate = baseline(); candidate[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                board.baseline(candidate)

    def test_observer_helpers_are_metadata_only(self):
        source = Path(board.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        functions = {node.name: ast.get_source_segment(source, node)
                     for node in tree.body if isinstance(node, ast.FunctionDef)}
        for name in ('registered_drivers', 'binding_observations', 'validate_binding'):
            body = functions[name]
            for forbidden in ('subprocess', 'mmap', 'write_text', 'write_bytes', '.open(', 'insmod'):
                self.assertNotIn(forbidden, body)
        with patch.object(board.Path, 'lstat', side_effect=FileNotFoundError('synthetic vanished node')):
            with self.assertRaises(FileNotFoundError):
                board.binding_observations(['renderD128'])


if __name__ == '__main__':
    unittest.main()
