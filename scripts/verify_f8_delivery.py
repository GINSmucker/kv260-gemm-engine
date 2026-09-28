"""Read-only, standard-library checks of the exact 2026-09-28 F8 delivery.

This verifies archived bytes and source provenance. It does not access hardware
or turn a recorded development PASS into a new board/acceptance result.
"""
from hashlib import sha256
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "results/c_integration/f8_bundle_20260928_012117_473"
MANIFEST_SHA = "bafb429c5262cea3d6c53b8eac9f86e9e37d18540839cbb050a638bf4e06734d"


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def within(root, relative):
    path = (root / relative.replace("\\", "/")).resolve()
    require(path.is_relative_to(root.resolve()), f"Path escapes delivery: {relative}")
    return path


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def check_map(root, entries):
    for relative, expected in entries.items():
        require(digest(within(root, relative)) == expected, f"SHA-256 mismatch: {root / relative}")
    return len(entries)


def main():
    require(digest(BUNDLE / "bundle_manifest.json") == MANIFEST_SHA, "Frozen bundle manifest changed")
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_bytes())
    checks = {"bundle_file_hashes": check_map(BUNDLE, manifest["files"])}
    sources = {name: value for name, value in manifest["files"].items()
               if name.startswith(("board/", "hardware/"))}
    checks["runtime_and_platform_sources"] = check_map(ROOT, sources)

    build = json.loads((BUNDLE / "hardware_build.json").read_bytes())
    checks["hardware_build_inputs"] = check_map(ROOT, build["source_sha256"])
    release = ROOT / "results/external_ip_review_20260927/release"
    archive = release / "gemm_top.zip"
    require(digest(archive) == build["original_ip_sha256"], "Original IP archive changed")
    extracted = 0
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            if member.is_dir():
                continue
            require(within(release / "ip", member.filename).read_bytes() == package.read(member),
                    f"Original IP extraction changed: {member.filename}")
            extracted += 1
    checks["original_ip_extracted_files"] = extracted

    component = (release / "ip/component.xml").read_bytes()
    metadata = build["metadata_change"]
    require(sha256(component).hexdigest() == metadata["before_sha256"], "Original component metadata changed")
    for index in range(3):
        old = (f'spirit:id="BUSIFPARAM_VALUE.M_AXI_GMEM{index}.HAS_BURST">0</spirit:value>').encode()
        require(component.count(old) == 1, f"Missing unique HAS_BURST for port {index}")
        component = component.replace(old, old.replace(b">0<", b">1<"))
    require(sha256(component).hexdigest() == metadata["after_sha256"], "Isolated metadata patch differs")
    identity = build["original_ip_sha256"] + metadata["after_sha256"] + "".join(build["source_sha256"].values())
    require(sha256(identity.encode()).hexdigest() == manifest["build_fingerprint"] == build["build_fingerprint"],
            "Hardware fingerprint differs")

    boards = ROOT / "board/hw/board_files"
    entries = json.loads((boards / "sha256.json").read_bytes())
    checks["official_board_files"] = check_map(boards, {item["Path"]: item["SHA256"] for item in entries})
    summary = json.loads((BUNDLE / "board_validation_summary.json").read_bytes())
    require(summary["bundle_manifest_sha256"] == MANIFEST_SHA, "Board summary names another bundle")
    require(summary["board_run_id"] == "262414267686403", "Unexpected archived board run")
    checks["raw_board_logs"] = check_map(BUNDLE, summary["raw_files_sha256"])
    require(summary["result"]["pl_development_checks"] == "PASS" and summary["result"]["rollback"] == "PASS",
            "Archived development result is not PASS")
    require(summary["smoke_result"]["compared_tasks"] == 9 and summary["smoke_result"]["compared_outputs"] == 576,
            "Archived comparison counts differ")
    print(json.dumps({"schema": "f8_repository_delivery_check_v1", "status": "PASS", "checks": checks,
                      "board_execution_this_command": "NOT_RUN", "formal_acceptance": "NOT_RUN",
                      "archived_board_run_id": summary["board_run_id"]}, indent=2))


if __name__ == "__main__":
    main()
