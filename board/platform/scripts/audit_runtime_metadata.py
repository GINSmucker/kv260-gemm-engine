"""Pinned-package verification and job creation. Never imports XRT or touches PL."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET

FORMAT = "H576_SPARSE24_SPLIT128_V1"
ABI = "OPT50_SPLIT128_AP_CTRL_HS_V1"
PACKAGE_SHA = "6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf"
HEADER_SHA = "fc243d24a6e87de438fa0db99e9ade55a3c1467b9ed9346a58eb34c95cf91c73"
OLD_METADATA_SHA = "f320692b5d8a8717e98dd67a62772f1b944b88b2ea25bc37338c5cd99d4b1ab9"
LAYERS = (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47)
REGISTERS = {"X": 0x18, "W_data": 0x24, "W_meta": 0x30, "Y": 0x3c,
             "M": 0x48, "K": 0x50, "N": 0x58, "mode": 0x60}


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, name: str) -> Path:
    require(isinstance(name, str) and bool(name), "empty/non-string package path")
    parts = PurePosixPath(name)
    require(not parts.is_absolute() and ".." not in parts.parts and
            "\\" not in name and ":" not in name and
            not any(c in name for c in "\r\n\0"), "unsafe package path")
    path = root.joinpath(*parts.parts)
    require(path.resolve().is_relative_to(root.resolve()), "package path escapes root")
    require(path.is_file() and not path.is_symlink(), "not a regular package file: " + name)
    return path


def sizes(m: int, k: int, n: int, mode: int) -> dict[str, int]:
    require(all(type(x) is int for x in (m, k, n, mode)), "non-integer shape/mode")
    require(1 <= m <= 197 and 1 <= k <= 576 and 1 <= n <= 576 and
            k % 16 == 0 and n % 16 == 0 and k * n <= 110592 and mode in (0, 1),
            "shape/mode outside OPT50 capability; document tiny K4/N2 is NOT a hardware case")
    return {"X": m * k, "W_data": k * n // 2 if mode else k * n,
            "W_meta": k * n // 8 if mode else 16, "Y": 4 * m * n}


def sparse_equivalence(dense: bytes, values: bytes, meta: bytes, k: int, n: int) -> None:
    require(k % 4 == 0 and len(dense) == k * n and
            len(values) == k * n // 2 and len(meta) == k * n // 8,
            "weight lengths do not match SPLIT128")
    rebuilt = bytearray(k * n)
    for t in range(k // 4 * n):
        code = (meta[t // 2] >> (4 * (t % 2))) & 15
        p0, p1 = code & 3, code >> 2
        require(p0 < p1, f"invalid position code at record {t}")
        g, column = divmod(t, n)
        rebuilt[(4 * g + p0) * n + column] = values[2 * t]
        rebuilt[(4 * g + p1) * n + column] = values[2 * t + 1]
    require(bytes(rebuilt) == dense, "compressed weights do not reproduce the pruned Dense weights")


def verify_package(root: Path) -> dict:
    root = root.resolve(strict=True)
    raw = (root / "manifest.json").read_bytes()
    require(digest(raw) == PACKAGE_SHA, "package manifest differs from the pinned c062 export")
    manifest = json.loads(raw)
    require(manifest["schema"] == "h576_sparse24_axi_golden_package_v1" and
            manifest["format"] == FORMAT, "package schema/format")
    require(len(manifest["files"]) == 129, "unexpected manifest inventory")
    for name, record in manifest["files"].items():
        path = safe_path(root, name)
        require(path.stat().st_size == record["bytes"], "length mismatch: " + name)
        require(digest(path.read_bytes()) == record["sha256"], "SHA256 mismatch: " + name)
    require(tuple(layer["layer_index"] for layer in manifest["layers"]) == LAYERS,
            "unexpected layer inventory/order")
    for layer in manifest["layers"]:
        _, k, n = layer["shape_MKN"]
        w = {name: safe_path(root, path).read_bytes() for name, path in layer["weights"].items()}
        require(w["dense_meta"] == bytes(16), "Dense metadata placeholder")
        sparse_equivalence(w["dense_data"], w["sparse_data"], w["sparse_meta"], k, n)
    cases = [c for c in manifest["cases"] if c["layer_index"] is not None]
    expected = {f"layer_{layer}_{kind}" for layer in LAYERS for kind in ("public", "edge")}
    require(len(cases) == 24 and {c["case_id"] for c in cases} == expected,
            "unexpected real-layer case inventory")
    for case in cases:
        m, k, n = case["M"], case["K"], case["N"]
        require(m == (197 if case["case_id"].endswith("_public") else 6), "unexpected M")
        require((k, n) == ((192, 576) if case["layer_index"] % 4 == 2 else (576, 192)),
                "unexpected layer dimensions")
        require([v["mode"] for v in case["modes"]] == [0, 1], "mode inventory")
        for variant in case["modes"]:
            for name, count in sizes(m, k, n, variant["mode"]).items():
                path = safe_path(root, variant["buffers"][name])
                require(path.stat().st_size == count, "incorrect case buffer size")
                layout = variant["layout"][name]
                require(layout["allocated_bytes"] == count and layout["alignment_bytes"] == 64,
                        "incorrect allocation/alignment declaration")
    return manifest


def job_fields(root: Path, manifest: dict, case: dict, variant: dict) -> dict[str, str]:
    require(case["layer_index"] is not None, "tiny format example is not a DUT case")
    root = root.resolve(strict=True)
    m, k, n, mode = case["M"], case["K"], case["N"], variant["mode"]
    expected = sizes(m, k, n, mode)
    fields = {"schema": "opt50_layer_job_v1", "abi": ABI, "format": FORMAT,
              "register_header_sha256": HEADER_SHA,
              "package_manifest": str(root / "manifest.json"),
              "package_manifest_sha256": PACKAGE_SHA, "case_id": case["case_id"],
              "M": str(m), "K": str(k), "N": str(n), "mode": str(mode)}
    for name in ("X", "W_data", "W_meta", "Y"):
        path = variant["buffers"][name]
        record = manifest["files"][path]
        require(record["bytes"] == expected[name], "job buffer size")
        key = "Y_golden" if name == "Y" else name
        fields[key] = str(safe_path(root, path))
        fields[key + "_bytes"] = str(record["bytes"])
        fields[key + "_sha256"] = record["sha256"]
    require(len(fields) == 23, "job schema field count")
    require(not any(any(c in value for c in "\n\r\0") for value in fields.values()),
            "unsupported character in job path/value")
    return fields


def serialize(fields: dict[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in fields.items())


def inspect_metadata_xml(raw: bytes) -> dict:
    start, end = raw.find(b"<?xml"), raw.find(b"</project>")
    require(start >= 0 and end > start, "missing embedded XML project")
    project = ET.fromstring(raw[start:end + len(b"</project>")])
    kernels = [x for x in project.iter("kernel") if x.get("name") == "model_gemm_top"]
    require(len(kernels) == 1, "expected one model_gemm_top in metadata")
    kernel = kernels[0]
    require(kernel.get("hwControlProtocol") == "ap_ctrl_none", "user-managed XRT metadata required")
    args = list(kernel.findall("arg"))
    require(len(args) == 8, "OPT50 requires eight arguments; legacy six-argument metadata rejected")
    for index, (name, offset) in enumerate(REGISTERS.items()):
        arg = args[index]
        require(arg.get("name") == name and int(arg.get("id", "-1"), 0) == index and
                int(arg.get("offset", "-1"), 0) == offset and
                int(arg.get("size", "0"), 0) == (8 if index < 4 else 4),
                "metadata argument differs from exported OPT50 register map: " + name)
        port = ("M_AXI_GMEM0", "M_AXI_GMEM1", "M_AXI_GMEM1", "M_AXI_GMEM2")[index] if index < 4 else "S_AXI_CONTROL"
        require(arg.get("port") == port and arg.get("addressQualifier") == ("1" if index < 4 else "0"),
                "metadata argument AXI mapping: " + name)
    ports = {x.get("name"): x for x in kernel.findall("port")}
    for name in ("M_AXI_GMEM0", "M_AXI_GMEM1", "M_AXI_GMEM2"):
        require(name in ports and ports[name].get("dataWidth") == "128", "metadata AXI width")
    return {"status": "XML_ABI_MATCH_NOT_HARDWARE_PROOF", "arguments": REGISTERS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("--package", type=Path, required=True)
        if name == "prepare":
            p.add_argument("--case", default="layer_26_edge", help="case ID or all")
            p.add_argument("--mode", choices=("dense", "sparse", "both"), default="both")
            p.add_argument("--out", type=Path, required=True, help="new directory, never overwritten")
    p = sub.add_parser("audit-metadata")
    p.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "audit-metadata":
        raw = args.metadata.read_bytes()
        require(len(raw) <= 2 * 1024 * 1024, "metadata too large")
        require(digest(raw) != OLD_METADATA_SHA, "this is the frozen H576 metadata, NOT OPT50")
        print(json.dumps(inspect_metadata_xml(raw), indent=2))
        return
    manifest = verify_package(args.package)
    result = {"status": "PINNED_PACKAGE_FILES_AND_SPARSE_LAYOUT_PASS", "files_checked": 129,
              "layers": 12, "physical_PL_starts": 0, "manifest_sha256": PACKAGE_SHA,
              "scope": "File identity and compression equivalence; NOT a new arithmetic or board test"}
    if args.command == "prepare":
        selected = [c for c in manifest["cases"] if c["layer_index"] is not None and
                    (args.case == "all" or c["case_id"] == args.case)]
        require(bool(selected), "unknown case or unsupported tiny case")
        modes = {0, 1} if args.mode == "both" else {0 if args.mode == "dense" else 1}
        jobs = [(f"{case['case_id']}_{'sparse' if v['mode'] else 'dense'}.kv",
                 job_fields(args.package, manifest, case, v))
                for case in selected for v in case["modes"] if v["mode"] in modes]
        args.out.mkdir(parents=True, exist_ok=False)
        for name, fields in jobs:
            with (args.out / name).open("x", encoding="utf-8", newline="\n") as output:
                output.write(serialize(fields))
        result.update(status="JOBS_PREPARED_NO_HARDWARE_CHANGE", jobs=len(jobs),
                      INT32_outputs_to_compare=sum(int(f["M"]) * int(f["N"]) for _, f in jobs),
                      output=str(args.out.resolve()))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, ET.ParseError) as error:
        raise SystemExit("REFUSED: " + str(error))
