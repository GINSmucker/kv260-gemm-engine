"""Create a separate, pinned Python-only v2 checker; never invokes Vivado or XRT."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

BASE_SHA = "56cd26ee1c175ecc98e0f2e5e6b7d216912db97221be18e1400ee47478ed3bb4"
HERE = Path(__file__).resolve().parent


def require(ok, message):
    if not ok:
        raise ValueError(message)


def summary(path):
    raw = path.read_bytes()
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def replace_once(source, before, after):
    require(source.count(before) == 1, "base checker differs at replacement boundary")
    return source.replace(before, after, 1)


def generate(base_raw):
    """Keep the frozen checker's other checks exactly, with explicit replacements."""
    require(hashlib.sha256(base_raw).hexdigest() == BASE_SHA, "unreviewed base checker")
    source = base_raw.decode("utf-8")
    source = replace_once(source,
        "from prepare_system import digest, relative_file, require, HEADER_SHA",
        "import sys\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        "from prepare_system import digest, relative_file, require, HEADER_SHA\n"
        "from verification_support import completion_provenance")
    source = replace_once(source, "def verify(root, run):", "def verify(root, run, *, write_receipt=True):")
    source = replace_once(source,
        '    require(not receipt_path.exists(), "verification receipt already exists; preserve it")',
        '    require(not write_receipt or not receipt_path.exists(), "verification receipt already exists; preserve it")\n'
        '    provenance = completion_provenance(root, run, Path(__file__).resolve().parent)')
    # The reviewed canonical checker already handles declared XSA hierarchy.
    # Do not replace or reimplement that check in the finish/provenance layer.
    require(source.count('        xsa_hierarchy = check_xsa(archive, artifacts)') == 1,
            "canonical XSA hierarchy check missing/duplicated")
    source = replace_once(source,
        '    with receipt_path.open("x", encoding="utf-8") as file:\n'
        '        file.write(json.dumps(receipt, indent=2)+"\\n")',
        '    receipt.update(verifier_version="2", verification_provenance=provenance)\n'
        '    if write_receipt:\n'
        '        with receipt_path.open("x", encoding="utf-8") as file:\n'
        '            file.write(json.dumps(receipt, indent=2)+"\\n")')
    source = replace_once(source,
        '    args = parser.parse_args()\n    print(json.dumps(verify(ROOT, args.run), indent=2))',
        '    parser.add_argument("--read-only", action="store_true", help="do not write system_verified.json")\n'
        '    args = parser.parse_args()\n'
        '    print(json.dumps(verify(ROOT, args.run, write_receipt=not args.read_only), indent=2))')
    return source


def prepare(root, run, finish, out):
    root, run, finish = (p.resolve(strict=True) for p in (root, run, finish))
    out = out.resolve()
    require(run.is_relative_to(root / "build") and finish.parent == root and out.parent == root,
            "run/finish/new verifier folder outside intended system root")
    require(not out.exists(), "new verifier folder already exists; preserve it")
    base = root / "scripts/verify_system.py"
    generated = generate(base.read_bytes())
    plan_path = finish / "finish_plan.json"
    plan = json.loads(plan_path.read_bytes())
    require(plan["schema"] == "opt50_route_finish_v1" and Path(plan["run"]).resolve() == run and
            Path(plan["system_root"]).resolve() == root, "wrong finish plan")
    require(plan["recipe_sha256"] == summary(root / "project_manifest.json")["sha256"] and
            plan["run_manifest_sha256"] == summary(run / "run_manifest.json")["sha256"],
            "finish/source identity mismatch")
    require((finish / "execution/finish_pass.txt").is_file() and
            not (finish / "execution/failure.txt").exists(), "finish has not completed successfully")
    # Preflight everything before creating the new, separate checker folder.
    local = {name: (HERE / name).read_bytes() for name in
             ("xsa_hierarchy.py", "verification_support.py", "prepare_verifier.py")}
    finish_files = ["finish_plan.json", "execution/finish_pass.txt", "execution/vivado_finish.log",
                    "execution/vivado_stderr.log"]
    finish_files += ["execution/artifacts/" + n for n in
                     ("kv260_opt50.bit", "kv260_opt50.bit.bin", "kv260_opt50.hwh", "kv260_opt50.xsa")]
    finish_files += ["execution/reports/" + n for n in
                     ("timing_summary.rpt", "clocks.rpt", "check_timing.rpt", "drc.rpt",
                      "route_status.rpt", "utilization.rpt")]
    finish_evidence = {n: summary(finish / n) for n in finish_files}
    out.mkdir()
    for name, raw in local.items():
        with (out / name).open("xb") as file:
            file.write(raw)
    with (out / "verify_system_v2.py").open("x", encoding="utf-8", newline="\n") as file:
        file.write(generated)
    manifest = dict(schema="opt50_verifier_deployment_v2", status="PREPARED_NOT_VERIFIED",
                    system_root=str(root), run_id=run.name, finish_folder=finish.name,
                    base_checker=summary(base), recipe_sha256=plan["recipe_sha256"],
                    run_manifest_sha256=plan["run_manifest_sha256"],
                    files={n: summary(out / n) for n in (*local, "verify_system_v2.py")},
                    finish_evidence=finish_evidence,
                    scope="Python/file verification only; NO Vivado or device access", board="NOT_RUN")
    with (out / "verification_manifest.json").open("x", encoding="utf-8") as file:
        file.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "run", "finish", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.root, args.run, args.finish, args.out)
    print(json.dumps(dict(status=result["status"], folder=str(args.out), board="NOT_RUN",
                         writes="new verifier folder only; frozen sources and hardware artifacts unchanged"), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit("REFUSED: " + str(error))
