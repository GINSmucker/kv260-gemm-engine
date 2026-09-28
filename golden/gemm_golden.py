"""P1 v1.0: exact INT8 x INT8 -> INT32, with a separate FP32 reference."""

import argparse
import json
from pathlib import Path

import numpy as np

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
ROOT = Path(__file__).resolve().parents[1]


def _inputs(A, B):
    arrays = []
    for name, value in (("A", A), ("B", B)):
        array = np.asarray(value)
        if array.ndim != 2 or 0 in array.shape:
            raise ValueError(f"{name} must be a nonempty 2-D matrix")
        if array.dtype.kind not in "iu":
            raise ValueError(f"{name} must contain integers")
        if np.any(array < -128) or np.any(array > 127):
            raise ValueError(f"{name} contains values outside signed INT8")
        arrays.append(array.astype(np.int8))
    A, B = arrays
    if A.shape[1] != B.shape[0]:
        raise ValueError(f"incompatible shapes: {A.shape} x {B.shape}")
    return A, B


def gemm_int8_golden(A, B):
    A, B = _inputs(A, B)
    A32 = A.astype(np.int32)
    B32 = B.astype(np.int32)
    # This bound protects EVERY partial sum, including cancellation cases.
    bound = A.shape[1] * int(np.abs(A32).max()) * int(np.abs(B32).max())
    if bound > INT32_MAX:
        raise OverflowError(f"INT32 safety bound exceeded: {bound}")
    return (A32 @ B32).astype(np.int32)


def gemm_int8_manual(A, B):
    A, B = _inputs(A, B)
    C = np.zeros((A.shape[0], B.shape[1]), dtype=np.int32)
    for i in range(A.shape[0]):
        for j in range(B.shape[1]):
            acc = 0
            for k in range(A.shape[1]):
                acc += int(A[i, k]) * int(B[k, j])
                if not INT32_MIN <= acc <= INT32_MAX:
                    raise OverflowError(f"INT32 overflow at ({i}, {j}), k={k}: {acc}")
            C[i, j] = acc
    return C


def gemm_fp32_reference(A, B):
    """Independent FP32 comparison only; never part of the INT8 kernel."""
    A, B = _inputs(A, B)
    return A.astype(np.float32) @ B.astype(np.float32)


def compare_results(expected, actual, case_name="matrix"):
    expected, actual = np.asarray(expected), np.asarray(actual)
    if expected.shape != actual.shape:
        print(f"FAIL {case_name}: expected shape={expected.shape}, actual shape={actual.shape}")
        return False
    mismatches = np.argwhere(expected != actual)
    for coordinate in mismatches:
        index = tuple(coordinate)
        print(f"FAIL {case_name} C{index}: expected={expected[index]}, actual={actual[index]}")
    return len(mismatches) == 0


def load_case(case_dir):
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    M, K, N = (meta[key] for key in ("M", "K", "N"))
    if any(type(dim) is not int or dim <= 0 for dim in (M, K, N)):
        raise ValueError(f"invalid dimensions: {case_dir}")
    if (meta["dtype_A"], meta["dtype_B"], meta["dtype_C"]) != ("int8", "int8", "int32"):
        raise ValueError(f"invalid numeric protocol: {case_dir}")
    matrices = []
    for filename, shape, low, high in (
        ("A.txt", (M, K), -128, 127),
        ("B.txt", (K, N), -128, 127),
        ("C_gold.txt", (M, N), INT32_MIN, INT32_MAX),
    ):
        matrix = np.loadtxt(case_dir / filename, dtype=np.int64, ndmin=2)
        if matrix.shape != shape or np.any(matrix < low) or np.any(matrix > high):
            raise ValueError(f"invalid shape/range in {case_dir / filename}")
        matrices.append(matrix)
    return meta, *matrices


def self_check(vectors):
    case_dirs = sorted(path.parent for path in vectors.glob("*/meta.json"))
    if not case_dirs:
        raise ValueError(f"no test vectors found in {vectors}; run generate_vectors.py")
    records = []
    all_pass = True
    for case_dir in case_dirs:
        meta, A, B, saved = load_case(case_dir)
        golden = gemm_int8_golden(A, B)
        manual = gemm_int8_manual(A, B)
        passed = compare_results(golden, manual, case_dir.name + "/manual")
        passed = compare_results(golden, saved, case_dir.name + "/saved") and passed
        # All P1 partial sums are <= 131072, exactly representable in FP32.
        fp32 = gemm_fp32_reference(A, B)
        passed = compare_results(golden, fp32, case_dir.name + "/fp32") and passed
        assert golden.dtype == np.int32 and manual.dtype == np.int32
        all_pass = all_pass and passed
        print(f"{'PASS' if passed else 'FAIL'} {case_dir.name}: M={meta['M']} K={meta['K']} N={meta['N']}")
        records.append({"case_name": case_dir.name, "status": "PASS" if passed else "FAIL"})
    result_dir = ROOT / "results" / "golden"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "summary.json").write_text(
        json.dumps({"status": "PASS" if all_pass else "FAIL", "cases": records}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Golden self-check: {'PASS' if all_pass else 'FAIL'} ({len(records)} cases)")
    return 0 if all_pass else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path, default=ROOT / "vectors")
    args = parser.parse_args()
    try:
        return self_check(args.vectors)
    except (OSError, ValueError, KeyError, OverflowError) as error:
        print(f"FAIL Golden: {error}")
        result_dir = ROOT / "results" / "golden"
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "summary.json").write_text(
            json.dumps({"status": "FAIL", "reason": str(error)}, indent=2) + "\n", encoding="utf-8"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
