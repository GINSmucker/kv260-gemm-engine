"""Regression checks for source/evidence omissions found during branch review."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check_release


class SupplementalSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT, prefix='supplemental_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = json.loads((ROOT / 'supplemental_sources.json').read_bytes())
        names = list(self.manifest['files']) + [
            'supplemental_sources.json',
            'results/platform_history/platform_project_manifest.json',
            'board/platform/verification_v2/verification_manifest.json',
            'board/platform/scripts/logic_sim.tcl',
            'board/platform/scripts/create_system.tcl',
        ]
        for name in names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def save_manifest(self):
        (self.root / 'supplemental_sources.json').write_text(
            json.dumps(self.manifest), encoding='utf-8')

    def test_original_sources_and_consumer_dependencies(self):
        result = check_release.verify_supplemental(self.root)
        self.assertEqual(result['platform_testbenches_checked'], 2)
        self.assertEqual(result['historical_reports_checked'], 6)
        self.assertGreater(result['website_modules_checked'], 5)

    def test_dropped_testbench_is_rejected_even_if_removed_from_manifest(self):
        name = 'board/platform/tests/tb_opt50_board_logic.sv'
        (self.root / name).unlink()
        del self.manifest['files'][name]
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'tb_opt50_board_logic'):
            check_release.verify_supplemental(self.root)

    def test_dropped_display_data_is_rejected_even_if_removed_from_manifest(self):
        name = 'results/model_planning/v5_h576_ui_20261017_01/local_chrome/benchmark.json'
        (self.root / name).unlink()
        del self.manifest['files'][name]
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'benchmark.json'):
            check_release.verify_supplemental(self.root)

    def test_changed_report_cannot_be_reauthorized_by_new_manifest_hash(self):
        name = 'results/platform_history/implementation_reports/timing_summary.rpt'
        raw = (self.root / name).read_bytes() + b'\nchanged historical report\n'
        (self.root / name).write_bytes(raw)
        self.manifest['files'][name].update(check_release.pin(raw))
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'historical implementation report differs'):
            check_release.verify_supplemental(self.root)

    def test_new_local_import_requires_a_declared_file(self):
        name = 'board/ui/server.mjs'
        raw = (self.root / name).read_bytes() + b'\nimport "./missing-local-module.mjs";\n'
        (self.root / name).write_bytes(raw)
        self.manifest['files'][name].update(check_release.pin(raw))
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'missing-local-module'):
            check_release.verify_supplemental(self.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
