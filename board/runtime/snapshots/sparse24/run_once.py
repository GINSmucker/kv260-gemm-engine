"""Submit one bounded CPU4/PL4 accuracy chunk; never renew or submit a following chunk."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from verify_tools import ROOT, decode, need, verify

AUTH = "--authorized-resident-batch"
PASS = "PASS_B24_BATCH_ALL_IMAGES_BITWISE_CLOSED"
FAIL = "FAIL_B24_BATCH_NUMERICAL_NATIVE_CLOSED"
REFUSED = "REFUSED_BEFORE_OWNER_NO_MMIO"
RETAINED = "RETAINED_MODEL_STATE_UNCERTAIN"
PROPERTIES = ("Type=exec", "ExitType=cgroup", "KillMode=process", "SendSIGKILL=no",
              "TimeoutStopSec=infinity", "RuntimeMaxSec=infinity", "Restart=no",
              "RemainAfterExit=yes", "OOMPolicy=continue")


def commands(report):
    count, start = report.get("sample_count"), report.get("sample_start")
    need(report.get("status") == "B24_RESIDENT_PLAN_CREATED_NO_HARDWARE_ACCESS" and
         report.get("hardware_executed") is False and report.get("expires_seconds") == 900 and
         type(count) is int and 1 <= count <= 50 and type(start) is int and 0 <= start < 1800 and start+count <= 1800 and
         report.get("total_START_budget") == 36*count and report.get("lifecycle_profile") == "resident-bounded-batch-v4" and
         report.get("ORT_intra_threads") == 4 and report.get("ORT_inter_threads") == 1,
         "not a fresh bounded B24 acceptance chunk check result")
    path = report.get("plan", "")
    match = re.fullmatch(r"/var/lib/opt50-b24-model-v1/plan_([a-f0-9]{12})/plan\.json", path)
    need(match is not None, "unexpected returned plan namespace/token")
    token = match[1]
    script = path[:-len("plan.json")]+"tools/run_model.py"
    run = ["sudo", "systemd-run", "--unit=opt50-b24-model-"+token,
           *["--property="+value for value in PROPERTIES], "/usr/bin/python3", "-B", script,
           "run", "--plan", path, AUTH]
    status = ["sudo", "/usr/bin/python3", "-B", script, "status", "--plan", path, "--summary"]
    need(shlex.split(report.get("next_requires_explicit_batch_authorization", "")) == run and
         shlex.split(report.get("status_command", "")) == status,
         "returned commands do not match the fixed per-owner Native parent/cgroup shape")
    return run[1:], status[1:]


def terminal(state):
    stage = state.get("stage", "")
    if stage == PASS:
        count = state.get("sample_count")
        need(type(count) is int and 1 <= count <= 50 and state.get("committed_samples") == count and
             state.get("completed_PL_calls") == 36*count and state.get("closed") is True and
             state.get("owner_pid") is None and state.get("child_pid") is None and
             state.get("accuracy", {}).get("numerical_status") == "NUMERICAL_PASS",
             "PASS summary lacks complete closed chunk proof")
        return 0
    if stage in (FAIL, REFUSED, RETAINED) or stage.startswith(("RETAINED", "PARTIAL_CLOSED_", "REFUSED_")):
        return 1
    return None


def json_child(argv):
    # Only check/status queries use this helper. No owner timeout/kill exists.
    run = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, check=False)
    need(len(run.stdout.encode()) <= 512*1024 and len(run.stderr.encode()) <= 65536,
         "oversized check/status output; preserve current plan and inspect service")
    if run.returncode:
        print(run.stdout, end="", flush=True)
        print(run.stderr, end="", file=sys.stderr, flush=True)
        raise ValueError("check/status refused; no automatic resubmission")
    need(not run.stderr, "unexpected check/status diagnostics; preserve current plan")
    value = decode(run.stdout.encode())
    need(type(value) is dict, "check/status must return a JSON object")
    return value


def service_snapshot(plan):
    """Read only the exact submitted inner unit; never start/stop/retry it."""
    match=re.fullmatch(r'/var/lib/opt50-b24-model-v1/plan_([a-f0-9]{12})/plan\.json',str(plan))
    need(match is not None,'canonical submitted plan required for service observation')
    unit='opt50-b24-model-'+match[1]+'.service'
    names=('LoadState','ActiveState','SubState','MainPID','ExecMainStatus','Result')
    result=subprocess.run(['/usr/bin/systemctl','show',unit,'--property='+','.join(names)],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=10,check=False)
    need(len(result.stdout.encode()) <= 32768 and len(result.stderr.encode()) <= 8192,
         'oversized inner service observation; preserve plan')
    rows=[line.split('=',1) for line in result.stdout.splitlines() if '=' in line]
    need(len(rows) == len({k for k,_ in rows}) and set(k for k,_ in rows) == set(names),
         'incomplete/duplicate inner service properties; preserve plan')
    fields=dict(rows)
    need(all(type(v) is str and not any(c in v for c in '\0\r\n') for v in fields.values()) and
         re.fullmatch(r'0|[1-9][0-9]*',fields['MainPID']), 'malformed inner service properties')
    need(result.returncode == 0 or fields['LoadState'] == 'not-found',
         'inner service observation failed; no resubmission')
    return {'unit':unit,'query_returncode':result.returncode,**fields}


def service_ended(snapshot):
    return (snapshot.get('MainPID') == '0' and
        (snapshot.get('ActiveState') in ('failed','inactive') or snapshot.get('SubState') in ('failed','exited','dead') or
         snapshot.get('LoadState') == 'not-found'))


def run_once(guardian_plan, layer_plan, uart, authorized, metadata=None, *, previous_model_plan,
             dataset, start=0, count=5, pilot_plan=None):
    need(authorized and uart, "explicit --authorized-batch and confirmed independent UART logging required")
    need(os.name == "posix" and os.geteuid() == 0 and os.uname().machine == "aarch64",
         "run on ARM Linux as root")
    frontend_started_ns = time.monotonic_ns()
    verify()
    argv = ["/usr/bin/python3", "-B", str(ROOT/"run_model.py"), "check", "--guardian-plan", str(guardian_plan),
            "--layer-plan", str(layer_plan), "--previous-model-plan", str(previous_model_plan),
            "--dataset", str(dataset), "--start", str(start), "--count", str(count), "--uart-log-confirmed"]
    if pilot_plan is not None:
        argv += ["--pilot-plan", str(pilot_plan)]
    if metadata is not None:
        argv += ["--metadata", str(metadata)]
    report = json_child(argv)
    need((report.get("sample_start"), report.get("sample_count")) == (start, count), "returned chunk selection differs")
    run, status = commands(report)
    print(json.dumps({"stage": "NEW_MODEL_PLAN_READY_NOT_YET_SUBMITTED", "plan": report["plan"],
                      "frontend_check_and_copy_ms": (time.monotonic_ns()-frontend_started_ns)/1e6,
                      "sample_start": start, "sample_count": count, "total_START_budget": 36*count,
                      "automatic_renewal": False, "ORT_intra_threads": 4, "ORT_inter_threads": 1,
                      "status_command": report["status_command"]}), flush=True)
    # Submit once only. An error, disconnect, warning or uncertain return never
    # causes resubmission or cleanup. The service owns its own resource lifetime.
    submitted = subprocess.run(run, stdin=subprocess.DEVNULL, check=False)
    need(submitted.returncode == 0,
         "service submission failed/uncertain; inspect the printed plan, do not repeat run_once")
    previous = None
    while True:
        state = json_child(status)
        observed = service_snapshot(report["plan"])
        stage = tuple(state.get(k) for k in ("stage", "committed_samples", "verified_pending_samples", "current_id", "image_stage"))
        if stage != previous:
            print(json.dumps({k: state.get(k) for k in ("stage", "committed_samples", "verified_pending_samples", "sample_count", "current_id", "image_stage")}), flush=True)
            previous = stage
        result = terminal(state)
        if result is None and service_ended(observed):
            # Re-read the saved receipt once to resolve the read/status versus
            # service-exit race. This is a read-only query, never a rerun.
            state = json_child(status)
            result = terminal(state)
            if result is None:
                print(json.dumps({'stage':'INNER_SERVICE_EXITED_WITHOUT_TERMINAL_STATUS',
                    'plan':report['plan'],'service':observed,'saved_status':state,
                    'meaning':'Inner service has exited without a validated terminal receipt; inspect its journal and preserve this plan',
                    'hardware_executed':'NOT_DEDUCED_FROM_MISSING_SAVED_STATUS',
                    'automatic_retry':False,'resubmitted':False},indent=2),flush=True)
                return 2
        if result is not None:
            state['inner_service_snapshot'] = observed
            state["frontend_total_ms"] = (time.monotonic_ns()-frontend_started_ns)/1e6
            state["frontend_timing_scope"] = "check/copy/submit/coordinator/final poll; not model latency"
            print(json.dumps(state, indent=2), flush=True)
            return result
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guardian-plan", type=Path, required=True)
    parser.add_argument("--layer-plan", type=Path, required=True)
    parser.add_argument("--previous-model-plan", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--pilot-plan", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--uart-log-confirmed", action="store_true")
    parser.add_argument("--authorized-batch", action="store_true")
    args = parser.parse_args()
    try:
        return run_once(args.guardian_plan, args.layer_plan, args.uart_log_confirmed,
                        args.authorized_batch, args.metadata,
                        previous_model_plan=args.previous_model_plan, dataset=args.dataset,
                        start=args.start, count=args.count, pilot_plan=args.pilot_plan)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("B24_BATCH_STOPPED_NO_REPLAY_OR_RENEWAL: "+str(error), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
