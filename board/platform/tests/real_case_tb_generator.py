"""Freeze all 48 pinned real-layer jobs as integrated-RTL testbench inputs.

Only Python/file work is performed. This does not execute a simulator, modify
the source package, patch a vendor model, or claim RTL/board acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


SYSTEM = Path(__file__).resolve().parents[1]
PACKAGE_SHA = "6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf"
LAYERS = (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47)
JOB_COUNT = 48
INT32_COUNT = 1870848
MAX_WORDS = 28368  # 197*576*4 / 16, largest Y buffer, not an arbitrary limit.
API_CHUNK_WORDS = 128
API_CHUNK_BYTES = API_CHUNK_WORDS*16
VIP_PARAMS_SHA = "0567041c4ab3f87456bde9500e03ea06e50c38e1148258b5535bddde081d8758"
VIP_APIS_SHA = "7e63d100da957c96cbc4a2503612873e4bf766bb6b414d1722ebf42516612453"
INCLUDE_NAME = "opt50_real_cases.svh"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(raw):
    return dict(bytes=len(raw), sha256=digest(raw))


def package_checker():
    """Reuse the identical package auditor in source and standalone projects."""
    local = SYSTEM / "scripts/audit_runtime_metadata.py"
    source = local if local.is_file() else SYSTEM.parent / "scripts/prepare_cases.py"
    require(source.is_file() and not source.is_symlink(), "missing regular pinned-package auditor")
    spec = importlib.util.spec_from_file_location("_opt50_real_cases_package_auditor", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.PACKAGE_SHA == PACKAGE_SHA and tuple(module.LAYERS) == LAYERS,
            "package auditor identity differs")
    return module


def encode_hex(raw):
    """One 128-bit word per line; source byte zero is word bits [7:0]."""
    require(isinstance(raw, bytes) and raw and len(raw) % 16 == 0,
            "SV payload must contain a positive whole number of 128-bit words")
    require(len(raw) // 16 <= MAX_WORDS, "SV payload exceeds reviewed largest buffer")
    return "".join(raw[at:at + 16][::-1].hex() + "\n" for at in range(0, len(raw), 16))


def sv_string(text):
    require(isinstance(text, str) and not any(ord(c) < 32 for c in text), "unsafe SV string")
    return json.dumps(text, ensure_ascii=False)


SV_TASKS = r'''
// Generated source only. The parent TB supplies demand/ticks/csr_read,
// csr_write/write_read/read_equals/check_idle, clock, phase, pl_starts,
// GEMM/STATUS constants and the ORIGINAL or explicitly identified PSVIP.
// No $finish, no overall PASS and no replacement of an AXI transaction here.
localparam integer OPT50_REAL_MAX_WORDS = 28368;
// Installed _23_local_params.sv:114-117: max_burst_bits=32768, but
// all_strb_valid is only 2048 bits. _23_apis.sv:288-304 forwards this
// mask to DDR write_mem. Therefore 4096-byte API writes are NOT valid.
// Keep the vendor vector width; bound every API data chunk to 2048 bytes.
localparam integer OPT50_REAL_API_CHUNK_WORDS = 128;
localparam integer OPT50_REAL_API_CHUNK_BYTES = 16*OPT50_REAL_API_CHUNK_WORDS;
localparam integer OPT50_REAL_CASE_LIMIT_CYCLES = 1000000;
localparam logic [39:0] OPT50_REAL_X = 40'h00100000;
localparam logic [39:0] OPT50_REAL_W = 40'h00200000;
localparam logic [39:0] OPT50_REAL_META = 40'h00300000;
localparam logic [39:0] OPT50_REAL_Y = 40'h00400000;
logic [127:0] opt50_real_words [0:OPT50_REAL_MAX_WORDS-1];
longint unsigned opt50_real_cycles = 0;
longint unsigned opt50_real_compared = 0;
integer opt50_real_jobs = 0;
always @(posedge clock) opt50_real_cycles <= opt50_real_cycles + 1;

task automatic opt50_real_read_hex(input string path, input integer byte_count);
    integer fd, i, scanned;
    logic [127:0] extra;
    begin
        demand(byte_count > 0 && byte_count % 16 == 0 &&
               byte_count/16 <= OPT50_REAL_MAX_WORDS, "real-case hex dimensions");
        fd = $fopen(path, "r");
        demand(fd != 0, $sformatf("cannot read frozen payload %s", path));
        for (i = 0; i < byte_count/16; i++) begin
            opt50_real_words[i] = 'x;
            scanned = $fscanf(fd, "%h", opt50_real_words[i]);
            demand(scanned == 1 && !$isunknown(opt50_real_words[i]),
                   $sformatf("short/unknown frozen payload %s word=%0d", path, i));
        end
        scanned = $fscanf(fd, "%h", extra);
        demand(scanned < 1 && $feof(fd), "extra/malformed frozen payload data");
        $fclose(fd);
    end
endtask

task automatic opt50_real_write_loaded(input logic [39:0] address, input integer byte_count);
    logic [32767:0] chunk;
    integer offset, count, i;
    begin
        for (offset = 0; offset < byte_count/16; offset += OPT50_REAL_API_CHUNK_WORDS) begin
            count = byte_count/16-offset;
            if (count > OPT50_REAL_API_CHUNK_WORDS) count = OPT50_REAL_API_CHUNK_WORDS;
            chunk = '0;
            for (i = 0; i < count; i++) chunk[128*i +: 128] = opt50_real_words[offset+i];
            `PSVIP.write_mem(chunk, address+16*offset, 16*count);
        end
    end
endtask

task automatic opt50_real_compare_loaded(input logic [39:0] address,
                                        input integer byte_count, input bit golden);
    logic [32767:0] chunk;
    integer offset, count, i, lane;
    begin
        for (offset = 0; offset < byte_count/16; offset += OPT50_REAL_API_CHUNK_WORDS) begin
            count = byte_count/16-offset;
            if (count > OPT50_REAL_API_CHUNK_WORDS) count = OPT50_REAL_API_CHUNK_WORDS;
            chunk = 'x;
            `PSVIP.read_mem(address+16*offset, 16*count, chunk);
            for (i = 0; i < count; i++) begin
                if (golden) begin
                    for (lane = 0; lane < 4; lane++) begin
                        demand(chunk[128*i+32*lane +: 32] === opt50_real_words[offset+i][32*lane +: 32],
                               $sformatf("real INT32 mismatch element=%0d actual=%0d expected=%0d",
                                         (offset+i)*4+lane, $signed(chunk[128*i+32*lane +: 32]),
                                         $signed(opt50_real_words[offset+i][32*lane +: 32])));
                        opt50_real_compared++;
                    end
                end else begin
                    demand(chunk[128*i +: 128] === opt50_real_words[offset+i],
                           $sformatf("real input changed at address=%h word=%0d", address, offset+i));
                end
            end
        end
    end
endtask

task automatic opt50_real_guards(input logic [39:0] address,
                                input integer byte_count, input bit initialize);
    logic [32767:0] chunk;
    begin
        if (initialize) begin
            chunk = '0; chunk[511:0] = {64{8'ha5}};
            `PSVIP.write_mem(chunk, address-64, 64);
            `PSVIP.write_mem(chunk, address+byte_count, 64);
        end else begin
            chunk = 'x; `PSVIP.read_mem(address-64, 64, chunk);
            demand(chunk[511:0] === {64{8'ha5}}, $sformatf("real prefix guard changed address=%h", address));
            chunk = 'x; `PSVIP.read_mem(address+byte_count, 64, chunk);
            demand(chunk[511:0] === {64{8'ha5}}, $sformatf("real suffix guard changed address=%h", address));
        end
    end
endtask

task automatic opt50_real_input(input string path, input logic [39:0] address,
                               input integer byte_count, input bit initialize);
    begin
        opt50_real_read_hex(path, byte_count);
        if (initialize) begin
            opt50_real_guards(address, byte_count, 1);
            opt50_real_write_loaded(address, byte_count);
        end
        opt50_real_compare_loaded(address, byte_count, 0);
        opt50_real_guards(address, byte_count, 0);
    end
endtask

task automatic opt50_real_initialize_y(input integer byte_count);
    logic [32767:0] chunk, observed;
    integer offset, count;
    begin
        opt50_real_guards(OPT50_REAL_Y, byte_count, 1);
        chunk = {4096{8'hcd}};
        for (offset = 0; offset < byte_count; offset += OPT50_REAL_API_CHUNK_BYTES) begin
            count = byte_count-offset;
            if (count > OPT50_REAL_API_CHUNK_BYTES) count = OPT50_REAL_API_CHUNK_BYTES;
            `PSVIP.write_mem(chunk, OPT50_REAL_Y+offset, count);
            observed = 'x;
            `PSVIP.read_mem(OPT50_REAL_Y+offset, count, observed);
            for (integer word = 0; word < count/16; word++)
                demand(observed[128*word +: 128] === {16{8'hcd}}, "real Y prefill/readback mismatch");
        end
        opt50_real_guards(OPT50_REAL_Y, byte_count, 0);
    end
endtask

task automatic opt50_run_frozen_case(input string case_id, input integer m,
                                    input integer k, input integer n, input integer mode,
                                    input string x_path, input string w_path,
                                    input string meta_path, input string golden_path);
    integer x_bytes, w_bytes, meta_bytes, y_bytes;
    logic [31:0] control;
    bit seen_done, completed;
    longint unsigned deadline;
    begin
        phase = $sformatf("frozen %s mode=%0d", case_id, mode);
        demand((m == 6 || m == 197) && ((k == 192 && n == 576) ||
               (k == 576 && n == 192)) && (mode == 0 || mode == 1), "real-layer job capability");
        x_bytes = m*k; w_bytes = mode ? k*n/2 : k*n;
        meta_bytes = mode ? k*n/8 : 16; y_bytes = 4*m*n;
        check_idle();
        opt50_real_input(x_path, OPT50_REAL_X, x_bytes, 1);
        opt50_real_input(w_path, OPT50_REAL_W, w_bytes, 1);
        opt50_real_input(meta_path, OPT50_REAL_META, meta_bytes, 1);
        opt50_real_initialize_y(y_bytes);
        write_read(GEMM+'h18, OPT50_REAL_X[31:0]); write_read(GEMM+'h1c, 0);
        write_read(GEMM+'h24, OPT50_REAL_W[31:0]); write_read(GEMM+'h28, 0);
        write_read(GEMM+'h30, OPT50_REAL_META[31:0]); write_read(GEMM+'h34, 0);
        write_read(GEMM+'h3c, OPT50_REAL_Y[31:0]); write_read(GEMM+'h40, 0);
        write_read(GEMM+'h48, m); write_read(GEMM+'h50, k);
        write_read(GEMM+'h58, n); write_read(GEMM+'h60, mode);
        check_idle();
        csr_write(STATUS+'h40, 32'h434c5245);
        csr_read(GEMM, control);
        demand((control & 32'h85) === 32'h04, "real-case START requires idle and no auto-restart");
        seen_done = 0; completed = 0;
        deadline = opt50_real_cycles + OPT50_REAL_CASE_LIMIT_CYCLES;
        csr_write(GEMM, 1); pl_starts++;
        while (opt50_real_cycles < deadline) begin
            csr_read(GEMM, control);
            seen_done = seen_done || control[1];
            if (seen_done && ((control & 32'h85) == 32'h04)) begin
                completed = 1;
                break;
            end
            ticks(256);
        end
        demand(completed, "real-case completion deadline; no retry or synthetic flush");
        read_equals(GEMM+'h10, 0);
        check_idle(); // Includes all monitored outstanding counts, errors and drained status.
        read_equals(STATUS+'h34, y_bytes/16);
        opt50_real_read_hex(golden_path, y_bytes);
        opt50_real_compare_loaded(OPT50_REAL_Y, y_bytes, 1);
        opt50_real_guards(OPT50_REAL_Y, y_bytes, 0);
        opt50_real_input(x_path, OPT50_REAL_X, x_bytes, 0);
        opt50_real_input(w_path, OPT50_REAL_W, w_bytes, 0);
        opt50_real_input(meta_path, OPT50_REAL_META, meta_bytes, 0);
        check_idle();
        opt50_real_jobs++;
        $display("OPT50_REAL_CASE_OUTPUT_MATCH case=%s mode=%0d M=%0d K=%0d N=%0d elements=%0d jobs=%0d",
                 case_id, mode, m, k, n, m*n, opt50_real_jobs);
    end
endtask
'''


def render_include(jobs, out):
    calls = []
    for job in jobs:
        paths = [sv_string((out / job["buffers"][name]["hex"]).as_posix())
                 for name in ("X", "W_data", "W_meta", "Y")]
        calls.append("        opt50_run_frozen_case(" + sv_string(job["case_id"]) +
                     f", {job['M']}, {job['K']}, {job['N']}, {job['mode']},\n            " +
                     ", ".join(paths) + ");")
    footer = '''
task automatic run_real_cases;
    integer starts_before;
    begin
        demand(opt50_real_jobs == 0 && opt50_real_compared == 0, "full48 may run only once");
        demand(`PSVIP.max_burst_bits == 32768 && `PSVIP.max_burst_bytes == 4096 &&
               $bits(`PSVIP.all_strb_valid) == 2048, "actual compiled PS VIP API dimensions/strobe differ");
        demand(OPT50_REAL_API_CHUNK_BYTES <= $bits(`PSVIP.all_strb_valid) &&
               OPT50_REAL_API_CHUNK_BYTES <= `PSVIP.max_burst_bytes,
               "real API chunk exceeds actual per-byte valid write strobe capacity");
        starts_before = pl_starts;
__CALLS__
        demand(opt50_real_jobs == 48 && pl_starts-starts_before == 48 &&
               opt50_real_compared == 1870848, "full48 exact jobs/START/output counts required");
        $display("OPT50_REAL_CASES_COMPLETED profile=full48 jobs=48 PL_STARTS=48 INT32=1870848 PACKAGE_SHA256=__PACKAGE_SHA__ BOARD_NOT_RUN");
    end
endtask
'''
    return ("// Frozen H576 real-layer package " + PACKAGE_SHA + "\n" + SV_TASKS +
            footer.replace("__CALLS__", "\n".join(calls)).replace("__PACKAGE_SHA__", PACKAGE_SHA))


def generate(package: Path, out: Path) -> dict:
    package = package.resolve(strict=True)
    out = out.resolve()
    require(not out.exists(), "new sim_cases output directory required; never overwrite")
    require(out.parent.is_dir(), "sim_cases parent must already exist")
    require(not out.is_relative_to(package) and not package.is_relative_to(out),
            "sim_cases output overlaps the frozen source package")
    auditor = package_checker()
    manifest = auditor.verify_package(package)
    cases = [c for c in manifest["cases"] if c["layer_index"] is not None]
    require(tuple(c["case_id"] for c in cases) ==
            tuple(f"layer_{layer}_{kind}" for layer in LAYERS for kind in ("public", "edge")),
            "full48 requires the exact ordered real-case inventory")
    require(sum(c["M"]*c["N"] for c in cases for _ in c["modes"]) == INT32_COUNT,
            "full48 output count mismatch")
    names = sorted({v["buffers"][key] for c in cases for v in c["modes"]
                    for key in ("X", "W_data", "W_meta", "Y")})
    encoded, sources = {}, {}
    for index, name in enumerate(names):
        raw = auditor.safe_path(package, name).read_bytes()
        require(identity(raw) == manifest["files"][name], "frozen source changed during generation: " + name)
        relative = f"data/p{index:04d}.hex"
        encoded[relative] = encode_hex(raw).encode("ascii")
        sources[name] = {**identity(raw), "hex": relative, "words": len(raw)//16}
    jobs = []
    for case in cases:
        require([v["mode"] for v in case["modes"]] == [0, 1], "both modes required in order")
        for variant in case["modes"]:
            expected = auditor.sizes(case["M"], case["K"], case["N"], variant["mode"])
            buffers = {key: dict(source=variant["buffers"][key], **sources[variant["buffers"][key]])
                       for key in ("X", "W_data", "W_meta", "Y")}
            require(all(buffers[key]["bytes"] == size for key, size in expected.items()), "job byte count mismatch")
            jobs.append(dict(case_id=case["case_id"], layer_index=case["layer_index"],
                             M=case["M"], K=case["K"], N=case["N"], mode=variant["mode"], buffers=buffers))
    require(len(jobs) == JOB_COUNT, "exactly 48 real jobs required")
    include = render_include(jobs, out).encode("utf-8")
    encoded[INCLUDE_NAME] = include
    # Check the whole package a second time BEFORE publishing any output, not
    # just a partial collection of bytes under a stale manifest hash.
    auditor.verify_package(package)
    result = dict(schema="opt50_integrated_real_cases_v1", status="GENERATED_NOT_SIMULATED",
                  profile="full48", package_manifest_sha256=PACKAGE_SHA, jobs=jobs,
                  job_count=JOB_COUNT, INT32_outputs=INT32_COUNT,
                  source_buffers=sources, files={name: identity(raw) for name, raw in encoded.items()},
                  api_chunk_words=API_CHUNK_WORDS, api_chunk_bytes=API_CHUNK_BYTES,
                  vip_api_contract=dict(params_sha256=VIP_PARAMS_SHA, apis_sha256=VIP_APIS_SHA,
                                        max_burst_vector_bits=32768, valid_write_strobe_bits=2048,
                                        reason="Top write_mem forwards 2048-bit all_strb_valid; all data API chunks <=2048 bytes"),
                  physical_PL_starts=0, simulator="NOT_RUN", board="NOT_RUN",
                  scope="Frozen inputs for 48 integrated RTL jobs; NOT simulation or board acceptance")
    out.mkdir()
    (out / "data").mkdir()
    for name, raw in encoded.items():
        with (out / name).open("xb") as handle:
            handle.write(raw)
    with (out / "real_cases_manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, indent=2, ensure_ascii=False)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.package, args.out)
    print(json.dumps({key: result[key] for key in ("status", "profile", "job_count", "INT32_outputs",
                                                   "physical_PL_starts", "simulator", "board")}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit("REFUSED: " + str(error))
