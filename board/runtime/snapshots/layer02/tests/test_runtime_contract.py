"""Run actual device-free CLI refusals; never uses --run or touches an FPGA.

Set OPT50_B24_OFFLINE_EXE to the separately compiled device-free executable.
Fixtures and rejection outputs stay in a new workspace .test_build directory.
Sparse metadata rehashed after corruption is deliberately rejected by the fixed
payload pin before reaching the decoder; the native self-test exercises decoder
rejection independently. No full-model accuracy or board evidence is claimed.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
NAMES = ("X", "W_dense_data", "W_dense_meta",
         "W_sparse_data", "W_sparse_meta", "Y_golden")


class RuntimeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("OPT50_B24_OFFLINE_EXE")
        if not configured:
            raise unittest.SkipTest("Set OPT50_B24_OFFLINE_EXE for executable negative tests")
        cls.executable = Path(configured).resolve(strict=True)
        cls.workspace = ROOT.parents[1]
        build = cls.workspace / ".test_build/b24_layer02_runtime_20261027"
        build.mkdir(parents=True, exist_ok=True)
        cls.evidence = Path(tempfile.mkdtemp(prefix="negative_", dir=build))
        cls.inventory = json.loads((DATA / "manifest.json").read_bytes())
        cls.base = {
            "schema": "opt50_b24_layer02_job_v1",
            "case_id": "B24_dev0000_layer02",
            "M": "197", "K": "192", "N": "576",
            "package_manifest": str((DATA / "source/B_MANIFEST.json").resolve()),
            "package_manifest_sha256": "c0f35179ea2edd7fe848cc5643232da86b795b92a18fa27624f5770cad02f769",
            "source_model_sha256": "ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43",
            "run_id": "0123456789ab",
            "boot_id": "00000000-0000-0000-0000-000000000000",
            "expected_parent_pid": str(os.getpid()),
            "companion_lock": "/var/lib/opt50-first-access-fd96539d-v1/companion.lock",
            "metadata": "OFFLINE_NOT_OPENED.xclbin",
            "metadata_sha256": "715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551",
        }
        for name in NAMES:
            item = cls.inventory["payloads"][name]
            cls.base[name] = str((DATA / item["file"]).resolve())
            cls.base[name + "_sha256"] = item["sha256"]
        if len(cls.base) != 26:
            raise AssertionError("Fixture must be a complete 26-field offline job")

    def setUp(self):
        self.folder = self.evidence / self._testMethodName
        self.folder.mkdir()

    def write_job(self, fields=None, extra=""):
        fields = self.base if fields is None else fields
        path = self.folder / "job.kv"
        path.write_text("".join(key + "=" + value + "\n"
                                for key, value in fields.items()) + extra,
                        encoding="utf-8")
        return path

    def rejected(self, arguments, expected):
        # Relative ASCII job name avoids Windows narrow-char argv conversion.
        # UTF-8 absolute payload paths are parsed from the job, not shell argv.
        before = sorted(p.name for p in self.folder.iterdir())
        result = subprocess.run([str(self.executable), *arguments],
                                cwd=self.folder, capture_output=True, text=True,
                                timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(expected, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before,
                         "Rejected offline job must not create receipts or results")
        (self.folder / "refusal.stdout").write_text(result.stdout, encoding="utf-8")
        (self.folder / "refusal.stderr").write_text(result.stderr, encoding="utf-8")

    def mutate_field(self, key, value, expected):
        fields = dict(self.base)
        fields[key] = value
        self.write_job(fields)
        self.rejected(["--offline", "--job", "job.kv"], expected)

    def test_unknown_cli_option(self):
        self.rejected(["--not-a-mode"], "unknown option")

    def test_missing_cli_value(self):
        self.rejected(["--offline", "--job"], "missing option value")

    def test_duplicate_cli_option(self):
        self.rejected(["--offline", "--offline"], "duplicate command option")

    def test_conflicting_offline_self_test_modes(self):
        self.rejected(["--offline", "--self-test"], "choose exactly")

    def test_old_twelve_layer_case_rejected(self):
        self.mutate_field("case_id", "layer_26_public",
                          "only B24_dev0000_layer02")

    def test_wrong_model_identity(self):
        self.mutate_field("source_model_sha256", "0" * 64,
                          "fixed B24 final W8A8 model identity")

    def test_old_job_schema_rejected(self):
        self.mutate_field("schema", "opt50_real_layer_job_v1",
                          "schema/26 fields required")

    def test_missing_field(self):
        fields = dict(self.base)
        del fields["source_model_sha256"]
        self.write_job(fields)
        self.rejected(["--offline", "--job", "job.kv"],
                      "schema/26 fields required")

    def test_extra_field(self):
        fields = dict(self.base, unexpected="not-accepted")
        self.write_job(fields)
        self.rejected(["--offline", "--job", "job.kv"],
                      "schema/26 fields required")

    def test_duplicate_kv_field(self):
        self.write_job(extra="M=197\n")
        self.rejected(["--offline", "--job", "job.kv"], "duplicate key: M")

    def test_malformed_boot_uuid(self):
        self.mutate_field("boot_id", "yesterday-or-not-a-uuid",
                          "plan boot must be a canonical UUID")

    def test_unsupported_shape(self):
        self.mutate_field("M", "196", "only B24_dev0000_layer02")

    def test_wrong_package_manifest_pin(self):
        self.mutate_field("package_manifest_sha256", "0" * 64,
                          "fixed production case package manifest identity")

    def test_changed_package_manifest_bytes(self):
        fields = dict(self.base)
        altered = self.folder / "manifest_changed.json"
        altered.write_bytes(Path(fields["package_manifest"]).read_bytes() + b" ")
        fields["package_manifest"] = str(altered)
        self.write_job(fields)
        self.rejected(["--offline", "--job", "job.kv"],
                      "fixed production case package manifest identity")

    def changed_payload(self, name, rehash=False, invalid_code=False):
        fields = dict(self.base)
        raw = bytearray(Path(fields[name]).read_bytes())
        raw[0] = 0 if invalid_code else raw[0] ^ 1
        altered = self.folder / (name + "_changed.bin")
        altered.write_bytes(raw)
        fields[name] = str(altered)
        if rehash:
            fields[name + "_sha256"] = hashlib.sha256(raw).hexdigest()
        self.write_job(fields)
        expected = ("fixed B24_dev0000_layer02 payload SHA: " if rehash
                    else "real-layer payload length/SHA: ") + name
        self.rejected(["--offline", "--job", "job.kv"], expected)

    def test_changed_input_bytes(self):
        self.changed_payload("X")

    def test_changed_dense_weight_bytes(self):
        self.changed_payload("W_dense_data")

    def test_changed_golden_bytes(self):
        self.changed_payload("Y_golden")

    def test_changed_golden_and_recomputed_local_sha_still_rejected(self):
        self.changed_payload("Y_golden", rehash=True)

    def test_illegal_sparse_code_rehash_rejected_by_frozen_pin(self):
        self.changed_payload("W_sparse_meta", rehash=True, invalid_code=True)


if __name__ == "__main__":
    unittest.main()
