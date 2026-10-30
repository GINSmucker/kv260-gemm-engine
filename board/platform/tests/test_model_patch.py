"""Pure software source-transform tests; not an RTL or board test."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import model_patch


class ModelPatchTests(unittest.TestCase):
    def test_reviewed_installed_source_only_request_order_and_banner(self):
        source = model_patch.VENDOR_HDL / "zynq_ultra_ps_e_vip_v1_0_vl_rfs.sv"
        if not source.exists():
            self.skipTest("reviewed installed source unavailable")
        original, fixed = model_patch.patch_source(source.read_bytes())
        self.assertEqual(fixed.count("LOCAL_SIM_FIX: publish after payload"), 36)
        self.assertEqual(fixed.count(model_patch.BANNER), 1)
        self.assertEqual(original.count("#0 prt_data"), fixed.count("#0 prt_data"))
        self.assertEqual(original.count("#0 prt_strb"), fixed.count("#0 prt_strb"))
        self.assertIn("prt_bytes = prt_bytes3;\n           prt_req = 1;", fixed)

    def test_unreviewed_source_refused(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            model_patch.patch_source(b"module unknown; endmodule")

    def test_changed_branch_count_refused_even_with_mocked_identity(self):
        raw = b"module " + model_patch.MODULE.encode() + b"();\nreg [2:0] state;\nendmodule"
        with mock.patch.object(model_patch, "SOURCE_SHA", model_patch.sha(raw)):
            with self.assertRaisesRegex(ValueError, "36"):
                model_patch.patch_source(raw)


if __name__ == "__main__":
    unittest.main()
