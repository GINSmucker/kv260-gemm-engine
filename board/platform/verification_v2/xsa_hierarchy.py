"""Read-only XSA identity checks, including declared SmartConnect scoped handoffs.

This module never extracts an archive, writes a receipt, or accesses hardware.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import re
import stat
import xml.etree.ElementTree as ET
import zipfile


TOP_HWH = "kv260_opt50.hwh"
TOP_BIT = "kv260_opt50.bit"
TOP_HIERARCHY = "kv260_opt50_i"
SMARTCONNECT = "xilinx.com:ip:smartconnect:1.0"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_name(name):
    """Reject aliases and Windows/POSIX traversal names before reading members."""
    require(isinstance(name, str) and bool(name), "empty XSA member name")
    require(not any(ord(c) < 32 for c in name) and "\\" not in name and ":" not in name,
            "unsafe XSA member name: " + repr(name))
    value = name[:-1] if name.endswith("/") else name
    path = PurePosixPath(value)
    require(bool(value) and not path.is_absolute() and
            all(part not in ("", ".", "..") for part in value.split("/")) and
            all(part == part.rstrip(" .") for part in value.split("/")),
            "unsafe XSA member path: " + repr(name))
    # Windows reserves these basenames even when an extension is present.
    require(all(not re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])",
                                 part.split(".", 1)[0], re.IGNORECASE)
                for part in path.parts), "reserved XSA member path: " + repr(name))
    return value


def xml(raw, label):
    require(len(raw) <= 32 * 1024 * 1024, "oversized XML member: " + label)
    require(re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.IGNORECASE) is None,
            "XML declarations not allowed: " + label)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError("invalid XML member: " + label) from error


def summary(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def check_xsa(archive: zipfile.ZipFile, artifacts: dict) -> dict:
    """Pair a real XSA with external artifacts and validate its HWH hierarchy.

    ``artifacts`` uses verify_system's ``artifacts/<name>: {bytes, sha256}``
    format. The caller remains responsible for obtaining those hashes from
    actual external files and for all non-XSA system checks.
    """
    members = {}
    aliases = set()
    for info in archive.infolist():
        # ZipInfo normalizes Windows backslashes and truncates at NUL. Validate
        # the unnormalized stored name as well, rather than trusting that alias.
        safe_name(info.orig_filename)
        require(info.orig_filename == info.filename, "normalized/aliased XSA member name")
        name = safe_name(info.filename)
        key = name.casefold()
        require(key not in aliases, "duplicate/case-alias XSA member: " + name)
        require(not stat.S_ISLNK(info.external_attr >> 16), "symlink XSA member: " + name)
        require(not info.flag_bits & 1, "encrypted XSA member: " + name)
        aliases.add(key)
        if not info.is_dir():
            members[name] = info
    require("sysdef.xml" in members and
            sum(PurePosixPath(n).name.casefold() == "sysdef.xml" for n in members) == 1,
            "exactly one root sysdef.xml required")

    def read(name):
        require(name in members, "declared XSA member missing: " + name)
        return archive.read(members[name])

    sysdef_raw = read("sysdef.xml")
    sysdef = xml(sysdef_raw, "sysdef.xml")
    require(sysdef.tag == "Project", "unexpected sysdef root")
    declarations = sysdef.findall(".//File")
    handoffs = [e for e in declarations if e.get("Type") == "HW_HANDOFF"]
    defaults = [e for e in handoffs if e.get("BD_TYPE") == "DEFAULT_BD"]
    require(len(defaults) == 1, "exactly one DEFAULT_BD HW_HANDOFF required")
    top = defaults[0]
    require(top.get("Name") == TOP_HWH and top.get("DESIGN_HIERARCHY") == TOP_HIERARCHY,
            "top-level HWH name/hierarchy mismatch")
    bits = [e for e in declarations if e.get("Type") == "BIT"]
    require(len(bits) == 1 and bits[0].get("Name") == TOP_BIT,
            "exactly one declared top-level bit required")
    require({n for n in members if n.lower().endswith(".bit")} == {TOP_BIT},
            "unknown/duplicate bitstream in XSA")

    pairs = {}
    for name in (TOP_BIT, TOP_HWH):
        raw = read(name)
        found = summary(raw)
        expected = artifacts.get("artifacts/" + name)
        require(isinstance(expected, dict) and found ==
                {"bytes": expected.get("bytes"), "sha256": expected.get("sha256")},
                "XSA/external artifact mismatch: " + name)
        pairs[name] = found

    top_root = xml(read(TOP_HWH), TOP_HWH)
    require(top_root.tag == "EDKSYSTEM", "unexpected top-level HWH root")
    modules = top_root.findall(".//MODULE")
    scoped_expected = {}
    for instance in ("ctrl", "data"):
        matches = [m for m in modules if m.get("INSTANCE") == instance]
        require(len(matches) == 1, "unique SmartConnect module required: " + instance)
        module = matches[0]
        require(module.get("VLNV") == SMARTCONNECT and module.get("BDTYPE") == "SBD",
                "unexpected scoped module type: " + instance)
        bd = module.get("BD", "")
        require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", bd) is not None,
                "invalid SmartConnect BD identity: " + instance)
        name = bd + ".hwh"
        require(name not in scoped_expected and name != TOP_HWH,
                "duplicate scoped BD identity")
        scoped_expected[name] = {
            "owner": instance, "hierarchy": TOP_HIERARCHY + "/" + instance + "/inst"
        }

    scoped = [e for e in handoffs if e.get("BD_TYPE") == "SCOPED_BD"]
    require(len(handoffs) == 1 + len(scoped) and len(scoped) == len(scoped_expected),
            "unknown/missing/duplicate HWH declaration")
    declared = {}
    for entry in scoped:
        name = entry.get("Name", "")
        require(name in scoped_expected and name not in declared,
                "unknown/duplicate scoped HWH declaration: " + name)
        require(entry.get("DESIGN_HIERARCHY") == scoped_expected[name]["hierarchy"],
                "scoped HWH hierarchy mismatch: " + name)
        declared[name] = entry
    expected_names = {TOP_HWH, *scoped_expected}
    require({n for n in members if n.lower().endswith(".hwh")} == expected_names,
            "undeclared/missing HWH archive member")

    children = {}
    for name, identity in scoped_expected.items():
        raw = read(name)
        child = xml(raw, name)
        child_modules = child.findall(".//MODULE")
        require(child.tag == "EDKSYSTEM" and bool(child_modules),
                "empty/unexpected scoped HWH: " + name)
        for module in child_modules:
            instance = module.get("INSTANCE", "").casefold()
            vlnv = module.get("VLNV", "").casefold()
            modtype = module.get("MODTYPE", "").casefold()
            require(instance not in ("gemm", "ps") and not vlnv.startswith("xilinx.com:hls:") and
                    not any(t in vlnv or t in modtype for t in
                            ("model_gemm", "zynq_ultra_ps", "processing_system7", "versal_cips")),
                    "additional compute/PS system in scoped HWH: " + name)
        children[name] = {**summary(raw), **identity}
    return {
        "schema": "opt50_xsa_declared_hierarchy_v2",
        "status": "PASS",
        "sysdef": summary(sysdef_raw),
        "top_hwh": {**pairs[TOP_HWH], "name": TOP_HWH, "hierarchy": TOP_HIERARCHY},
        "bitstream": {**pairs[TOP_BIT], "name": TOP_BIT},
        "scoped_hwh": children,
        "scope": "Archive/file identity only; NOT board execution",
    }
