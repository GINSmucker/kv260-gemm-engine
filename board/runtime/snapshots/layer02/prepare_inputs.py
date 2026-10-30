"""Prepare/verify isolated B24 layer02 inputs; never execute B code or access PL.

Preparation streams every ZIP member only for SHA/CRC verification, including
opaque .pt bytes. Only the eleven explicitly listed input/provenance files are
copied. No pickle, torch, ONNX runtime, network, device or firmware API is used.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

FORMAT = "H576_SPARSE24_SPLIT128_V1"
ABI = "OPT50_SPLIT128_AP_CTRL_HS_V1"
SCHEMA = "opt50_b24_layer02_inputs_v1"
CASE_ID = "B24_dev0000_layer02"
ARCHIVE_ROOT = "B_FINAL_H576_24L_100K_20261026_v1"
ZIP_SHA = "7ada4de5cf99d09d3d6a5b9d92108bcbce85dc636e0c1669d8b14d5171139fc4"
B_MANIFEST_SHA = "c0f35179ea2edd7fe848cc5643232da86b795b92a18fa27624f5770cad02f769"
MODEL_SHA = "ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43"
M, K, N = 197, 192, 576
SPARSE_LAYERS = [i for i in range(48) if i % 4 in (2, 3)]
PAYLOAD_MEMBERS = {
    "X": "cases/dev0000/layer_02/X.bin",
    "W_dense_data": "weights_axi/layer_02/W_dense_data.bin",
    "W_dense_meta": "weights_axi/layer_02/W_dense_meta.bin",
    "W_sparse_data": "weights_axi/layer_02/W_sparse_data.bin",
    "W_sparse_meta": "weights_axi/layer_02/W_sparse_meta.bin",
    "Y_golden": "cases/dev0000/layer_02/Y_golden.bin",
}
PAYLOAD_FILES = {key: key + ".bin" for key in PAYLOAD_MEMBERS}
SOURCE_MEMBERS = {
    "source/B_MANIFEST.json": "MANIFEST.json",
    "source/B_MANIFEST.sha256": "MANIFEST.sha256",
    "source/B_RELEASE.json": "RELEASE.json",
    "source/B_LAYERS.json": "parameters/layers.json",
    "source/B_CASE.json": "cases/dev0000/layer_02/manifest.json",
}
SELECTED = {**SOURCE_MEMBERS, **{PAYLOAD_FILES[k]: v for k, v in PAYLOAD_MEMBERS.items()}}
EXPECTED_SIZES = dict(X=M*K, W_dense_data=K*N, W_dense_meta=16,
                      W_sparse_data=K*N//2, W_sparse_meta=K*N//8, Y_golden=4*M*N)
SHA_RE = re.compile(r"[0-9a-f]{64}")


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def json_object(raw, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, "duplicate JSON key: " + label)
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    need(isinstance(result, dict), "JSON object required: " + label)
    return result


def safe_relative(name):
    need(isinstance(name, str) and bool(name) and
         not any(c in name for c in "\\:\x00\r\n"), "unsafe member name")
    path = PurePosixPath(name)
    need(not path.is_absolute() and all(p not in ("", ".", "..") for p in name.split("/")),
         "unsafe member path")
    devices = {"CON", "PRN", "AUX", "NUL", *("COM"+str(i) for i in range(1, 10)),
               *("LPT"+str(i) for i in range(1, 10))}
    need(all(p.rstrip(" .") == p and p.split(".")[0].upper() not in devices for p in path.parts),
         "unsafe Windows member component")
    return path


def regular(path, limit):
    path = Path(path).absolute()
    need(not any(p.is_symlink() for p in (path, *path.parents)), "linked input refused")
    st = path.stat()
    need(stat.S_ISREG(st.st_mode) and 0 < st.st_size <= limit, "missing/nonregular/oversized input")
    return path


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_entries(archive):
    """Validate central-directory paths/types before reading any member."""
    infos = archive.infolist()
    need(0 < len(infos) <= 2048, "archive member count")
    members, folded, total = {}, set(), 0
    prefix = ARCHIVE_ROOT + "/"
    for item in infos:
        name = item.filename[:-1] if item.is_dir() else item.filename
        safe_relative(name)
        need(name == ARCHIVE_ROOT or name.startswith(prefix), "archive root mismatch")
        need(name not in members and name.casefold() not in folded, "duplicate/case-colliding member")
        folded.add(name.casefold())
        need(not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
             "encrypted/unsupported ZIP member")
        kind = stat.S_IFMT(item.external_attr >> 16)
        need(kind in ((0, stat.S_IFDIR) if item.is_dir() else (0, stat.S_IFREG)),
             "linked/special ZIP member")
        need(0 <= item.file_size <= 128*1024*1024, "oversized ZIP member")
        total += item.file_size
        need(total <= 512*1024*1024, "oversized ZIP expansion")
        members[name] = item
    return members


def validate_original_manifest(raw, sidecar):
    need(sha(raw) == B_MANIFEST_SHA, "B original manifest identity changed")
    need(sidecar.decode("ascii").split() == [B_MANIFEST_SHA, "MANIFEST.json"], "B manifest sidecar differs")
    value = json_object(raw, "B_MANIFEST")
    need(value.get("schema_version") == "B_RELEASE_SHA256_V1" and value.get("algorithm") == "SHA-256",
         "B manifest schema")
    files = value.get("files")
    need(isinstance(files, dict) and value.get("file_count") == len(files) == 765,
         "B manifest file count")
    total = 0
    for name, record in files.items():
        safe_relative(name)
        need(isinstance(record, dict) and set(record) == {"bytes", "sha256"} and
             type(record["bytes"]) is int and record["bytes"] >= 0 and
             isinstance(record["sha256"], str) and SHA_RE.fullmatch(record["sha256"]), "B manifest record")
        total += record["bytes"]
    need(value.get("total_bytes") == total == 263718896, "B manifest total bytes")
    return value


def audit_zip(path):
    """All-member CRC + SHA inventory, but only selected files retained in RAM."""
    path = regular(path, 512*1024*1024)
    size = path.stat().st_size
    need(file_sha(path) == ZIP_SHA, "source ZIP SHA256 differs from authorized B24 release")
    selected = {}
    with zipfile.ZipFile(path) as archive:
        entries = inspect_entries(archive)
        prefix = ARCHIVE_ROOT + "/"
        raw_manifest = archive.read(entries[prefix+"MANIFEST.json"])
        sidecar = archive.read(entries[prefix+"MANIFEST.sha256"])
        original = validate_original_manifest(raw_manifest, sidecar)
        actual_files = {name[len(prefix):] for name, info in entries.items() if not info.is_dir()}
        exempt = {"MANIFEST.json", "MANIFEST.sha256", "work/DELIVERY_RECEIPT.json"}
        need(actual_files == set(original["files"]) | exempt, "archive inventory differs from B manifest")
        reverse = {member: local for local, member in SELECTED.items()}
        checked = 0
        for name in sorted(actual_files):
            info = entries[prefix+name]
            digest, count, chunks = hashlib.sha256(), 0, []
            # Reading to EOF makes zipfile verify CRC, including opaque .pt bytes.
            with archive.open(info) as source:
                for chunk in iter(lambda: source.read(1024*1024), b""):
                    count += len(chunk)
                    digest.update(chunk)
                    if name in reverse:
                        chunks.append(chunk)
            need(count == info.file_size, "ZIP member short read")
            if name in original["files"]:
                need({"bytes": count, "sha256": digest.hexdigest()} == original["files"][name],
                     "B manifest payload differs: " + name)
                checked += 1
            if name in reverse:
                selected[reverse[name]] = b"".join(chunks)
    need(set(selected) == set(SELECTED), "selected input inventory incomplete")
    need(path.stat().st_size == size and file_sha(path) == ZIP_SHA, "source ZIP changed while auditing")
    return selected, {"status": "ALL_MEMBER_CRC_AND_OUTER_MANIFEST_SHA_PASS",
                      "archive_files_crc_checked": len(actual_files), "manifest_files_sha_checked": checked,
                      "opaque_pt_bytes_hashed_only": True, "executed_package_code": False,
                      "archive_bytes": size}


def check_sources(raw):
    original = validate_original_manifest(raw["source/B_MANIFEST.json"], raw["source/B_MANIFEST.sha256"])
    for local, member in SELECTED.items():
        if member in original["files"]:
            need(identity(raw[local]) == original["files"][member], "copied source/payload changed: " + local)
    release = json_object(raw["source/B_RELEASE.json"], "B_RELEASE")
    need(release.get("schema_version") == "B_H576_FINAL_RELEASE_V1" and release.get("format_id") == FORMAT and
         release.get("sparse_layers_count") == 24 and release.get("sparse_layer_indices") == SPARSE_LAYERS and
         release.get("source_model_sha256", {}).get("w8a8") == MODEL_SHA, "B24 release identity/routes")
    model = original["files"].get("models/final/model_w8a8.onnx")
    need(model is not None and model["sha256"] == MODEL_SHA, "B W8A8 model identity")
    layers = json.loads(raw["source/B_LAYERS.json"])
    need(isinstance(layers, list) and len(layers) == 49, "49 B model layers required")
    for i, layer in enumerate(layers):
        expected = (1, 192, 1000) if i == 48 else ((197, 576, 192) if i % 4 == 3 else
                    (197, 192, 192) if i % 4 == 1 else (197, 192, 576))
        need(layer.get("index") == i and tuple(layer.get(v) for v in ("M", "K", "N")) == expected and
             layer.get("mode") == int(i in SPARSE_LAYERS) and
             layer.get("route") == ("PL" if i < 48 and i % 4 != 1 else "PS"), "B route/shape contract")
    case = json_object(raw["source/B_CASE.json"], "B_CASE")
    need(case.get("schema_version") == "B_GEMM_REAL_LAYER_CASE_V1" and case.get("case_id") == "dev0000_L02" and
         case.get("layer_index") == 2 and case.get("route") == "PL" and case.get("mode") == 1 and
         case.get("format_id") == FORMAT and tuple(case.get(v) for v in ("M", "K", "N")) == (M, K, N) and
         case.get("model_w8a8_sha256") == MODEL_SHA and case.get("byte_order") == "little-endian",
         "B layer02 case identity")
    need([c.get("mode") for c in case.get("calls", [])] == [0, 1], "Dense/Sparse case modes")
    for mode, call in enumerate(case["calls"]):
        keys = {"X": "X", "Y": "Y_golden", "W_data": "W_sparse_data" if mode else "W_dense_data",
                "W_meta": "W_sparse_meta" if mode else "W_dense_meta"}
        need(call.get("buffers") == {key: PAYLOAD_MEMBERS[value] for key, value in keys.items()},
             "B case source buffer paths")
        for key, value in keys.items():
            length = EXPECTED_SIZES[value]
            expected = {"logical_bytes": 0 if key == "W_meta" and mode == 0 else length,
                        "allocated_bytes": length, "beat_count": length//16, "alignment_bytes": 64}
            need(call.get("layout", {}).get(key) == expected, "B case byte/beat/alignment contract")
    return original


def decode_sparse(data, meta, k, n):
    """Independent 128-bit-word decoder; no B codec import or nonzero scanning."""
    need(type(k) is int and type(n) is int and k > 0 and n > 0 and k % 4 == 0, "invalid sparse shape")
    records = k*n//4
    need(len(data) == ((records*2+15)//16)*16 and len(meta) == (((records+1)//2+15)//16)*16,
         "sparse physical length")
    need(not any(data[records*2:]) and not any(meta[(records+1)//2:]), "nonzero sparse tail padding")
    need(records % 2 == 0 or meta[records//2] >> 4 == 0, "unused high nibble must be zero")
    data_words = [int.from_bytes(data[i:i+16], "little") for i in range(0, len(data), 16)]
    meta_words = [int.from_bytes(meta[i:i+16], "little") for i in range(0, len(meta), 16)]
    rebuilt, codes, zeros = bytearray(k*n), collections.Counter(), 0
    for t in range(records):
        code = (meta_words[t//32] >> (4*(t % 32))) & 15
        p0, p1 = code & 3, code >> 2
        need(p0 < p1, "invalid sparse position code at record " + str(t))
        pair = (data_words[t//8] >> (16*(t % 8))) & 65535
        a, b = pair & 255, pair >> 8
        g, col = divmod(t, n)
        rebuilt[(4*g+p0)*n+col] = a
        rebuilt[(4*g+p1)*n+col] = b
        codes[code] += 1
        zeros += (a == 0) + (b == 0)
    return bytes(rebuilt), {"records": records, "position_codes": {str(c): codes[c] for c in sorted(codes)},
                            "retained_zero_values": zeros, "reconstructed_dense_sha256": sha(rebuilt)}


def check_payloads(raw):
    for key, length in EXPECTED_SIZES.items():
        need(len(raw[PAYLOAD_FILES[key]]) == length, "payload length differs: " + key)
    need(raw[PAYLOAD_FILES["W_dense_meta"]] == bytes(16), "Dense metadata dummy must be zero")
    rebuilt, layout = decode_sparse(raw[PAYLOAD_FILES["W_sparse_data"]], raw[PAYLOAD_FILES["W_sparse_meta"]], K, N)
    need(rebuilt == raw[PAYLOAD_FILES["W_dense_data"]], "Sparse reconstruction differs from SAME pruned Dense W")
    need(set(layout["position_codes"]) == {"4", "8", "9", "12", "13", "14"}, "layer02 six-code coverage differs")
    return {"status": "INDEPENDENT_SPLIT128_DECODE_PASS", **layout}


def golden_check(raw):
    # NumPy is needed only on the preparation host, or for optional verify --arithmetic.
    import numpy as np
    x = np.frombuffer(raw[PAYLOAD_FILES["X"]], dtype=np.int8).reshape(M, K)
    w = np.frombuffer(raw[PAYLOAD_FILES["W_dense_data"]], dtype=np.int8).reshape(K, N)
    output = x.astype(np.int64) @ w.astype(np.int64)
    need(np.all(output >= -(1 << 31)) and np.all(output <= (1 << 31)-1), "INT32 overflow: no wrap/saturation allowed")
    golden = raw[PAYLOAD_FILES["Y_golden"]]
    need(output.astype("<i4").tobytes(order="C") == golden, "independent INT64 full-K dot differs from B Golden")
    return {"status": "INDEPENDENT_INT64_FULL_K_GOLDEN_MATCH", "elements": M*N, "mismatches": 0,
            "minimum": int(output.min()), "maximum": int(output.max()), "golden_sha256": sha(golden),
            "numpy_version": np.__version__, "physical_PL_starts": 0}


def make_manifest(raw, archive_audit, arithmetic):
    check_sources(raw)
    layout = check_payloads(raw)
    payloads = {key: {"file": PAYLOAD_FILES[key], **identity(raw[PAYLOAD_FILES[key]]),
                      "source_member": PAYLOAD_MEMBERS[key]} for key in PAYLOAD_FILES}
    provenance = {local: {**identity(raw[local]), "source_member": member}
                  for local, member in SOURCE_MEMBERS.items()}
    return {"schema": SCHEMA, "status": "INPUTS_READY_NOT_BOARD_TEST", "case_id": CASE_ID,
            "abi": ABI, "format": FORMAT, "layer_index": 2, "layer_id": "blocks.0.mlp.fc1",
            "M": M, "K": K, "N": N, "modes": [0, 1], "source_model_w8a8_sha256": MODEL_SHA,
            "source": {"zip_name": ARCHIVE_ROOT+".zip", "zip_sha256": ZIP_SHA,
                       "original_manifest_sha256": B_MANIFEST_SHA, "archive_audit": archive_audit,
                       "provenance_files": provenance}, "payloads": payloads,
            "format_validation": layout, "preparation_arithmetic": arithmetic,
            "comparison": "Both modes use the SAME new B24 pruned Wq; Golden is reference-only, never device output",
            "scope": "One new-model layer02 integer input package; no old12 overwrite; no classification acceptance",
            "hardware_accessed": False, "physical_PL_starts": 0}


def write_new(path, raw):
    with path.open("xb") as output:
        need(output.write(raw) == len(raw), "short output write")
        output.flush()
        os.fsync(output.fileno())


def prepare(source_zip, out):
    out = Path(out).absolute()
    need(not out.exists() and not out.is_symlink(), "output exists; use a NEW data directory")
    need(not any(p.is_symlink() for p in out.parents), "linked output parent refused")
    raw, archive_audit = audit_zip(source_zip)
    arithmetic = golden_check(raw)
    manifest = make_manifest(raw, archive_audit, arithmetic)
    # Audit everything before creating the new directory. Never extractall().
    out.mkdir(parents=True, exist_ok=False)
    (out/"source").mkdir()
    for local, content in raw.items():
        write_new(out/local, content)
    write_new(out/"manifest.json", json_bytes(manifest))
    return {"status": "B24_LAYER02_INPUTS_PREPARED_NOT_BOARD_TEST", "data": str(out),
            "manifest": identity((out/"manifest.json").read_bytes()), "payloads": len(PAYLOAD_FILES),
            "integer_outputs_recomputed": M*N, "physical_PL_starts": 0,
            "writes": "New isolated data directory only; old12, B ZIP and board unchanged"}


def verify_data(data, arithmetic=False):
    root = Path(data).absolute()
    need(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "unsafe data directory")
    found = set()
    for path in root.rglob("*"):
        need(not path.is_symlink(), "linked data member")
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
        else:
            need(path.is_dir() and path.relative_to(root).as_posix() == "source", "unexpected/special data directory")
    need(found == set(SELECTED) | {"manifest.json"}, "unexpected/missing data member")
    manifest = json_object(regular(root/"manifest.json", 128*1024).read_bytes(), "input manifest")
    raw = {name: regular(root/name, 2*1024*1024).read_bytes() for name in SELECTED}
    preparation = manifest.get("preparation_arithmetic", {})
    need(preparation.get("status") == "INDEPENDENT_INT64_FULL_K_GOLDEN_MATCH" and
         preparation.get("elements") == M*N and preparation.get("mismatches") == 0 and
         preparation.get("golden_sha256") == sha(raw[PAYLOAD_FILES["Y_golden"]]) and
         preparation.get("physical_PL_starts") == 0, "preparation Golden receipt differs")
    audit = manifest.get("source", {}).get("archive_audit", {})
    need(audit.get("status") == "ALL_MEMBER_CRC_AND_OUTER_MANIFEST_SHA_PASS" and
         audit.get("archive_files_crc_checked") == 768 and audit.get("manifest_files_sha_checked") == 765 and
         audit.get("opaque_pt_bytes_hashed_only") is True and audit.get("executed_package_code") is False and
         type(audit.get("archive_bytes")) is int and audit["archive_bytes"] > 0, "original archive audit receipt differs")
    expected = make_manifest(raw, audit, preparation)
    need(manifest == expected, "local manifest contract/identity differs")
    report = golden_check(raw) if arithmetic else {"status": "NOT_RERUN_STDLIB_VERIFY", "physical_PL_starts": 0}
    return {"status": "B24_LAYER02_FILES_AND_FORMAT_PASS", "case_id": CASE_ID,
            "files_checked": len(SELECTED), "payloads": len(PAYLOAD_FILES), "manifest": identity((root/"manifest.json").read_bytes()),
            "arithmetic": report, "physical_PL_starts": 0,
            "scope": "Local pinned files and independent sparse layout; original ZIP CRC is preparation provenance, not rerun here"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["verify"]:
        parser = argparse.ArgumentParser(description="Read-only file/format verify; stdlib by default, no board access")
        parser.add_argument("--data", type=Path, required=True)
        parser.add_argument("--arithmetic", action="store_true", help="Optionally recompute INT64 Golden; requires NumPy")
        args = parser.parse_args(argv[1:])
        return verify_data(args.data, args.arithmetic)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="NEW directory; existing outputs are never overwritten")
    args = parser.parse_args(argv)
    return prepare(args.source_zip, args.out)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, ImportError) as error:
        raise SystemExit("REFUSED: " + str(error))
