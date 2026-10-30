"""Restoration rejects path escape and wrong payload identity before creating output."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import check_release
import restore_bundle
class RestorationBoundaryTests(unittest.TestCase):
    def test_member_path_rejections(self):
        for name in ('../escape','/absolute','C:/escape','C:escape','model/../escape','model\\escape','a//b','./a'):
            with self.subTest(name=name),self.assertRaises(ValueError):check_release.safe(ROOT,name)
    def test_wrong_asset_refused_without_partial_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='restore_test_') as temp:
            folder=Path(temp);fake=folder/'wrong.tar.gz';fake.write_bytes(b'wrong asset');out=folder/'out'
            with self.assertRaisesRegex(ValueError,'exact declared external asset'):restore_bundle.restore('dense36',out,fake)
            self.assertFalse(out.exists())
if __name__=='__main__':unittest.main(verbosity=2)
