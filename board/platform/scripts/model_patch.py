"""Create a simulation-only, explicitly identified local PS VIP workaround.

Never edits the AMD installation or HLS/hardware RTL. The original vendor run
remains FAIL. This is not an AMD-issued fix or production hardware acceptance.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re

SOURCE_SHA = "ddba4288b512d1f1d2dd4e7d2c70d4aaf774d6a27915cd697e506151cfe1e25e"
PARAMS_SHA = "0567041c4ab3f87456bde9500e03ea06e50c38e1148258b5535bddde081d8758"
MODULE = "zynq_ultra_ps_e_vip_v1_0_23_arb_wr_6"
PARAMS = "zynq_ultra_ps_e_vip_v1_0_23_local_params.sv"
LIBRARY = "opt50_ps_vip_fixed"
BANNER = "OPT50_PSVIP_ARB_FIXED_V1 changes=36"
VENDOR_HDL = Path("D:/Vivado/2026.1/Vivado/data/ip/xilinx/zynq_ultra_ps_e_vip_v1_0/hdl")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def patch_source(raw):
    require(sha(raw) == SOURCE_SHA, "unreviewed vendor source identity")
    text = raw.decode("utf-8")
    found = list(re.finditer(r"(?m)^module " + MODULE + r"\s*\(", text))
    require(len(found) == 1, "unique original write arbiter required")
    start = found[0].start()
    end = text.index("endmodule", start) + len("endmodule")
    original = text[start:end]
    # Every wait/serve selection branch publishes req before two #0 updates.
    # Preserve those updates and all state/ack/reset logic. Move ONLY req=1
    # after the selected address/byte fields have been assigned.
    pattern = re.compile(r"(?m)^(?P<indent>[ \t]*)prt_req\s*=\s*1\s*;(?P<body>.*?)"
                         r"(?P<last>^[ \t]*prt_bytes\s*=\s*prt_bytes(?P<port>[0-5])\s*;)", re.DOTALL)
    ports = []

    def move(match):
        body = match["body"]
        port = match["port"]
        require(len(re.findall(r"#0\s+prt_(?:data|strb)\s*=", body)) == 2,
                "unexpected payload scheduling pattern")
        require(not re.search(r"\b(?:if|else|case|endcase)\b|prt_req\s*=", body),
                "replacement crossed a branch")
        # QoS is assigned before req in some serve branches; it is untouched.
        for field in ("data", "strb", "addr"):
            require(re.search(r"prt_" + field + r"\s*=\s*prt_" + field + port + r"\s*;", body),
                    "selected payload/QoS differs from byte port")
        ports.append(int(port))
        return body + match["last"] + "\n" + match["indent"] + "prt_req = 1; // LOCAL_SIM_FIX: publish after payload"

    fixed, count = pattern.subn(move, original)
    require(count == 36 and all(ports.count(p) == 6 for p in range(6)), "expected exactly 36 reviewed selections")
    state = "reg [2:0] state;"
    require(fixed.count(state) == 1, "unexpected arbiter state declaration")
    fixed = fixed.replace(state, state + '\ninitial $display("' + BANNER + ' instance=%m");', 1)
    prefix = ("`timescale 1ns/1ps\n// SIMULATION ONLY: local workaround, NOT an AMD-issued fix.\n"
              "// Same module name in a separate explicitly ordered simulation library.\n")
    return original, prefix + fixed + "\n"


def prepare_patch(out, vendor_hdl=VENDOR_HDL):
    out = out.resolve()
    vendor_hdl = vendor_hdl.resolve(strict=True)
    require(not out.exists() and out.parent.is_dir(), "fresh patch output with existing parent required")
    require(not out.is_relative_to(vendor_hdl) and not vendor_hdl.is_relative_to(out), "output overlaps vendor source")
    source = vendor_hdl / "zynq_ultra_ps_e_vip_v1_0_vl_rfs.sv"
    raw = source.read_bytes()
    params = (vendor_hdl / PARAMS).read_bytes()
    require(sha(params) == PARAMS_SHA, "unreviewed vendor parameters")
    original, fixed = patch_source(raw)
    out.mkdir()
    files = {
        "vendor_arb_wr_6_fixed.sv": fixed.encode("utf-8"),
        PARAMS: params,
        "arbiter_change.diff": "".join(difflib.unified_diff(original.splitlines(True), fixed.splitlines(True),
                                                          fromfile="original_module", tofile="local_sim_module")).encode("utf-8"),
    }
    for name, contents in files.items():
        with (out / name).open("xb") as handle:
            handle.write(contents)
    manifest = dict(schema="opt50_local_psvip_workaround_v1", library=LIBRARY,
                    module=MODULE, runtime_banner=BANNER, request_moves=36,
                    original_vendor_source=str(source), original_source_sha256=sha(raw),
                    original_module_sha256=sha(original.encode("utf-8")),
                    generator_sha256=sha(Path(__file__).read_bytes()),
                    files={n: dict(bytes=len(b), sha256=sha(b)) for n, b in files.items()},
                    purpose="Simulation scheduling workaround only; not an AMD fix or hardware change",
                    use_in_synthesis=False, use_in_implementation=False,
                    vendor_installation_modified=False, board="NOT_RUN")
    with (out / "patch_manifest.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2) + "\n")
    require(source.read_bytes() == raw and (vendor_hdl / PARAMS).read_bytes() == params,
            "vendor input changed during preparation")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--vendor-hdl", type=Path, default=VENDOR_HDL)
    args = parser.parse_args()
    print(json.dumps(prepare_patch(args.out, args.vendor_hdl), indent=2))
