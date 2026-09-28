"""Read the small standard-case container and verify every delivered byte."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import numpy as np

from .backend import InputError, ResultMismatchError, TaskSpec, _validate_spec


@dataclass(frozen=True)
class CaseData:
    manifest: dict
    manifest_sha256: str
    Xq: np.ndarray
    Wq: np.ndarray
    Y_ref_int32: np.ndarray

    @property
    def spec(self) -> TaskSpec:
        return TaskSpec(
            self.manifest["M"], self.manifest["K"], self.manifest["N"],
            self.manifest["mode"], self.manifest["layout_id"],
        )


def load_case(directory: Path) -> CaseData:
    directory = Path(directory)
    manifest_bytes = (directory / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
        raise InputError("unsupported case schema")
    for field in ("case_id", "mode", "layout_id", "logical_layout", "byte_order",
                  "encoder_version", "M", "K", "N", "source", "quantization"):
        if field not in manifest:
            raise InputError(f"missing manifest field: {field}")
    if (manifest["logical_layout"] != "C-contiguous row-major X[M,K], W[K,N], Y[M,N]" or
            manifest["byte_order"] != "little" or manifest["encoder_version"] != "numpy_npz_v1"):
        raise InputError("unsupported case layout, byte order, or encoder")
    spec = TaskSpec(manifest["M"], manifest["K"], manifest["N"], manifest["mode"], manifest["layout_id"])
    _validate_spec(spec)
    payload = manifest.get("payload")
    if not isinstance(payload, dict) or payload.get("file") != "tensors.npz":
        raise InputError("payload must be tensors.npz in the case directory")
    payload_bytes = (directory / "tensors.npz").read_bytes()
    if len(payload_bytes) != payload.get("bytes") or sha256(payload_bytes).hexdigest() != payload.get("sha256"):
        raise InputError("tensors.npz byte length or SHA-256 mismatch")
    expected = {
        "Xq": ((spec.M, spec.K), "|i1"),
        "Wq": ((spec.K, spec.N), "|i1"),
        "Y_ref_int32": ((spec.M, spec.N), "<i4"),
    }
    with np.load(BytesIO(payload_bytes), allow_pickle=False) as saved:
        if set(saved.files) != set(expected):
            raise InputError("unexpected tensor names")
        arrays = {name: saved[name] for name in expected}
    tensor_manifest = manifest.get("tensors")
    if not isinstance(tensor_manifest, dict) or set(tensor_manifest) != set(expected):
        raise InputError("manifest tensor descriptors are incomplete")
    for name, (shape, dtype) in expected.items():
        array = arrays[name]
        descriptor = tensor_manifest[name]
        if array.shape != shape or array.dtype.str != dtype or not array.flags.c_contiguous:
            raise InputError(f"invalid tensor shape, dtype, byte order, or layout: {name}")
        if descriptor != {"shape": list(shape), "dtype": dtype, "bytes": array.nbytes}:
            raise InputError(f"tensor descriptor mismatch: {name}")
    return CaseData(manifest, sha256(manifest_bytes).hexdigest(),
                    arrays["Xq"], arrays["Wq"], arrays["Y_ref_int32"])


def verify_output(case: CaseData, actual: np.ndarray) -> None:
    expected = case.Y_ref_int32
    if not isinstance(actual, np.ndarray) or actual.shape != expected.shape or actual.dtype != np.int32:
        raise InputError("output shape or dtype does not match INT32 reference")
    mismatches = np.argwhere(actual != expected)
    if len(mismatches):
        m, n = map(int, mismatches[0])
        raise ResultMismatchError(
            f"result mismatch at ({m},{n}): actual={actual[m,n]}, expected={expected[m,n]}"
        )


def quantize_toy_inputs(case: CaseData) -> tuple[np.ndarray, np.ndarray]:
    """Round the constructed float example back to signed INT8 (nearest even)."""
    q = case.manifest["quantization"]
    scale_x = float(q["scale_x"])
    scale_w = np.asarray(q["scale_w"], dtype=np.float64)
    if (not np.isfinite(scale_x) or scale_x <= 0 or
            scale_w.shape != (case.spec.N,) or not np.all(np.isfinite(scale_w)) or
            not np.all(scale_w > 0)):
        raise InputError("invalid toy quantization scales")
    X_float = case.Xq.astype(np.float64) * scale_x
    W_float = case.Wq.astype(np.float64) * scale_w[None, :]
    X_requant = np.clip(np.rint(X_float / scale_x), -128, 127).astype(np.int8)
    W_requant = np.clip(np.rint(W_float / scale_w[None, :]), -128, 127).astype(np.int8)
    if not np.array_equal(X_requant, case.Xq) or not np.array_equal(W_requant, case.Wq):
        raise InputError("constructed toy floats did not requantize to the case")
    return X_requant, W_requant


def postprocess_toy(case: CaseData, actual: np.ndarray) -> np.ndarray:
    """Model-side computation consumes this run's result, never the saved reference."""
    if actual.shape != (case.spec.M, case.spec.N) or actual.dtype != np.int32:
        raise InputError("toy postprocess requires the current INT32 output")
    q = case.manifest["quantization"]
    scale_x = float(q["scale_x"])
    scale_w = np.asarray(q["scale_w"], dtype=np.float64)
    bias = np.asarray(q["bias"], dtype=np.float64)
    if (not np.isfinite(scale_x) or scale_x <= 0 or
            scale_w.shape != (case.spec.N,) or bias.shape != (case.spec.N,) or
            not np.all(np.isfinite(scale_w)) or not np.all(scale_w > 0) or
            not np.all(np.isfinite(bias))):
        raise InputError("invalid toy quantization parameters")
    return scale_x * actual.astype(np.float64) * scale_w + bias
