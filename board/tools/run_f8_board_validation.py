"""Execute the reviewed temporary F8 load/test/rollback plan as root.

This is deliberately opt-in. A surviving XRT owner prevents overlay removal.
No QSPI, boot files, persistent account changes or reboot are performed.
"""
import argparse
import fcntl
import grp
from hashlib import sha256
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import time
from f8_read_ps_widths import read_widths

OVERLAYS = Path("/sys/kernel/config/device-tree/overlays")
APP = Path("/lib/firmware/xilinx/kv260-f8")
STARTER = Path("/usr/lib/firmware/xilinx/k26-starter-kits")
STARTER_HASHES = {
    "k26_starter_kits.bit.bin": "f9dfe95d14881a2768b2c0e6a36fc363635e8ae7882ecad9725d270490395d3e",
    "k26_starter_kits.dtbo": "1e998ffd5d33a05da3746df3603e65603e1628006bc878209a836cc9bd3c4ae1",
    "shell.json": "2967ccbc1f75c8d4accd1b6fecc11b16a32a5d7992e56c6eebceb99a3410f777",
}


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def snapshot():
    clocks = Path("/sys/kernel/debug/clk/clk_summary").read_text().splitlines()
    pl0 = next(line.split() for line in clocks if line.split() and line.split()[0] == "pl0_ref")
    return {"kernel": os.uname().release,
            "ps_fabric_widths": read_widths(),
            "overlays": sorted(p.name for p in OVERLAYS.iterdir()),
            "module_loaded": Path("/sys/module/zocl").exists(),
            "render_nodes": sorted(str(p) for p in Path("/dev/dri").glob("renderD*")),
            "fpga_state": Path("/sys/class/fpga_manager/fpga0/state").read_text().strip(),
            "pl0_enable_count": int(pl0[1]), "pl0_rate_hz": int(pl0[4]),
            "dfx_active": subprocess.run(["systemctl","is-active","dfx-mgr"],capture_output=True,text=True).stdout.strip()}


def users():
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            comm = (proc/"comm").read_text().strip()
            for fd in (proc/"fd").iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                if target.startswith(("/dev/dri/render", "/dev/zocl", "/dev/fpga")) or target == "/dev/mem":
                    found.append({"pid":int(proc.name),"comm":comm,"fd":target})
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--authorized-temporary-load", action="store_true")
    args = parser.parse_args()
    require(args.authorized_temporary_load and os.geteuid()==0, "Explicit load authorization and root required")
    bundle = args.bundle.resolve()
    project = Path("/home/ubuntu/fpga_project").resolve()
    require(bundle.is_relative_to(project), "Bundle is outside project")
    manifest = json.loads((bundle/"bundle_manifest.json").read_bytes())
    require(manifest["schema"] == "f8_runtime_bundle_v1", "Wrong bundle schema")
    require(manifest["bootgen_word_swap_verified"], "Unverified Linux FPGA image")
    for relative, expected in manifest["files"].items():
        file = (bundle/relative).resolve()
        require(file.is_relative_to(bundle), "Bundle traversal")
        require(sha256(file.read_bytes()).hexdigest()==expected, "Changed bundle file: "+relative)
    for name, expected in STARTER_HASHES.items():
        require(sha256((STARTER/name).read_bytes()).hexdigest()==expected, "Starter rollback hash differs")
    require(not any(p.suffix.lower()==".xclbin" for p in STARTER.iterdir()), "Starter changed: manager could update VART configuration")
    require(not APP.exists(), "Refuse to overwrite an existing FPGA app")
    lock = (project/"deploy/.f8_board.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    run_id = str(time.monotonic_ns())
    native_path = bundle/("native_owner_"+run_id+".log")
    smoke_path = bundle/("board_smoke_"+run_id+".jsonl")
    log = (bundle/("board_load_"+run_id+".jsonl")).open("x")
    def event(name, data):
        line=json.dumps({"event":name,"monotonic_ns":time.monotonic_ns(),"data":data})
        log.write(line+"\n"); log.flush()
        try: print(line,flush=True)
        except (OSError,BrokenPipeError): pass
    def command(argv, timeout=30):
        result=subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        event("command", {"argv":argv,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        require(result.returncode==0, "Command failed: "+argv[0])
        return result.stdout
    before=snapshot()
    event("before",before)
    require(before["kernel"]=="6.8.0-1015-xilinx" and before["dfx_active"]=="active", "Target changed")
    require(before["overlays"]==["k26-starter-kits_image_1"] and not before["module_loaded"] and not before["render_nodes"], "Board is not in the reviewed starter state")
    require(before["fpga_state"]=="operating", "FPGA is not operating")
    widths=before["ps_fabric_widths"]
    require(widths["hpm0_bits"]==128 and widths["hp0_read_bits"]==128 and widths["hp0_write_bits"]==128,
            "Live PS HPM0/HP0 widths do not match the 128-bit F8 platform")
    require(99999000<=before["pl0_rate_hz"]<=100000000, "Initial PL0 rate differs from reviewed 100 MHz")
    busy=users(); event("exclusive_preflight",busy); require(not busy, "FPGA runtime already in use")
    # DFX manager 2024.2 writes VART only for an XRT_FLAT directory containing
    # an xclbin. F8 uses PL_FLAT; the verified starter contains no xclbin.
    # Record and compare this file only; do not write any /etc configuration.
    vart=Path("/etc/vart.conf")
    old_vart=vart.read_bytes() if vart.exists() else None
    account=pwd.getpwnam("ubuntu")
    privilege={"user":account.pw_uid,"group":account.pw_gid,"extra_groups":[grp.getgrnam("render").gr_gid]}
    require((project/".venv/bin/python").exists(), "Existing board Python environment missing")
    event("bundle", {"path":str(bundle),"manifest_sha256":sha256((bundle/"bundle_manifest.json").read_bytes()).hexdigest(),"source_build":manifest["source_build"],
                     "run_id":run_id,"native_log":str(native_path),"smoke_log":str(smoke_path)})
    native=None; log_native=None; app_created=False; board_touched=False; client_started=False; passed=False; failure=None; rollback=[]
    # Disconnecting the SSH client must not terminate the owner or skip cleanup.
    for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM): signal.signal(sig,signal.SIG_IGN)
    try:
        APP.mkdir(); app_created=True
        for name in ("kv260-f8.bit.bin","kv260-f8.dtbo","shell.json"):
            shutil.copyfile(bundle/name,APP/name)
            (APP/name).chmod(0o644)
        command(["dfx-mgr-client","-listPackage"])
        board_touched=True
        command(["dfx-mgr-client","-remove","0"])
        require(not list(OVERLAYS.iterdir()), "Starter overlay was not removed")
        command(["modprobe","zocl"])
        command(["dfx-mgr-client","-load","kv260-f8"],60)
        command(["udevadm","settle","--timeout=5"])
        during=snapshot(); event("loaded",during)
        require(during["overlays"]==["kv260-f8_image_1"] and during["fpga_state"]=="operating", "F8 platform load failed")
        require(len(during["render_nodes"])==1 and during["pl0_enable_count"]>0 and 99999000<=during["pl0_rate_hz"]<=100000000, "Runtime or PL clock not ready")
        env=dict(os.environ, XRT_INI_PATH=str(bundle/"xrt.ini"))
        sock=bundle/"f8.sock"
        log_native=native_path.open("x")
        native=subprocess.Popen([str(bundle/"f8_xrt_service"),str(bundle/"kv260_f8_metadata.xclbin"),str(sock),
                                 *(hex(tag) for tag in manifest["hardware_build_tags"])],
                                cwd=bundle,env=env,stdout=log_native,stderr=subprocess.STDOUT,
                                start_new_session=True,**privilege)
        deadline=time.monotonic()+10
        while not sock.exists():
            require(native.poll() is None,"Native XRT owner startup failed")
            require(time.monotonic()<deadline,"Native XRT owner startup deadline")
            time.sleep(0.05)
        event("owner_started",{"pid":native.pid,"uid":account.pw_uid,"socket":str(sock)})
        client_started=True
        test=subprocess.run([str(project/".venv/bin/python"),"-m","board.f8_smoke","--manifest",str(bundle/"bundle_manifest.json"),
                             "--socket",str(sock),"--output",str(smoke_path)],
                            cwd=bundle,env=env,capture_output=True,text=True,timeout=45,**privilege)
        event("smoke",{"returncode":test.returncode,"stdout":test.stdout,"stderr":test.stderr})
        require(test.returncode==0,"PL development checks failed; inspect retained runtime")
        passed=True
    except Exception as exc:
        failure=type(exc).__name__+": "+str(exc); event("failure",failure)
    finally:
        # TERM is sent directly to our native PID, never to runuser or a process
        # group that might escalate to SIGKILL. The native owner exits only idle.
        if native and native.poll() is None:
            native.send_signal(signal.SIGTERM)
            try: native.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rollback.append("Native owner retained because quiescence is unproven; overlay and BOs retained")
        if log_native: log_native.close()
        if native and client_started and native.poll() is not None:
            native_text=native_path.read_text()
            event("owner_exit",{"returncode":native.returncode,"safe_stop":"F8_XRT_SERVICE_SAFE_STOP" in native_text})
            if native.returncode!=0 or "F8_XRT_SERVICE_SAFE_STOP" not in native_text:
                rollback.append("Native owner exited without a successful quiescence record; platform retained")
        busy=users(); event("cleanup_users",busy)
        if busy: rollback.append("Runtime device still open; no overlay removal")
        if not rollback:
            try:
                if board_touched:
                    now=snapshot()
                    if now["overlays"]==["kv260-f8_image_1"]:
                        command(["dfx-mgr-client","-remove","0"])
                    require(not list(OVERLAYS.iterdir()) or snapshot()["overlays"]==before["overlays"], "Unknown overlay retained")
                    if Path("/sys/module/zocl").exists(): command(["modprobe","-r","zocl"])
                    if not list(OVERLAYS.iterdir()): command(["dfx-mgr-client","-load","k26-starter-kits"],60)
                    after=snapshot(); event("after",after)
                    require(after==before,"Board state differs after rollback")
                    command(["xrt-smi","examine"])
                if app_created:
                    require(APP.resolve()==Path("/usr/lib/firmware/xilinx/kv260-f8"),"Unexpected app path")
                    for name in ("kv260-f8.bit.bin","kv260-f8.dtbo","shell.json"):
                        file=APP/name
                        require(sha256(file.read_bytes()).hexdigest()==manifest["files"][name],"App file changed; retained")
                        file.unlink()
                    APP.rmdir()
                require((vart.read_bytes() if vart.exists() else None)==old_vart,"Unexpected VART configuration change; retained for review")
            except Exception as exc: rollback.append(type(exc).__name__+": "+str(exc))
        event("result",{"pl_development_checks":"PASS" if passed else "FAIL", "rollback":"PASS" if not rollback else "FAIL",
                        "error":failure,"rollback_errors":rollback,"formal_acceptance":"NOT_RUN"})
        log.close(); lock.close()
    return 0 if passed and not rollback else 1


if __name__=="__main__":
    raise SystemExit(main())
