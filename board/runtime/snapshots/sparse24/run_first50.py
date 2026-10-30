"""Authorize at most 50 new images: one pilot, then at most one bounded phase.

No plan is retried, extended, or replayed. The independent coordinators retain
their existing 900-second checks, companion locks, and 36-START owner budgets.
This wrapper owns only a separate action lock and root-private static receipts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ACTION_ROOT = Path('/var/lib/opt50-b24-first50-v3')
PROFILE = 'b24-batch-acceptance-cpu4-pl4-v1'
SOURCE_MODEL = 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
PASS = 'PASS_B24_BATCH_ALL_IMAGES_BITWISE_CLOSED'
NEW_PLAN = 'NEW_MODEL_PLAN_READY_NOT_YET_SUBMITTED'
PLAN_RE = re.compile(r'/var/lib/opt50-b24-model-v1/plan_([a-f0-9]{12})/plan\.json')
BOOT_RE = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}')
WINDOW_SECONDS = 900
RESERVE_SECONDS = max(60.0, WINDOW_SECONDS * 0.25)
LOG_LIMIT = 8 * 1024 * 1024


def need(ok, why):
    if not ok:
        raise ValueError(why)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def decode(raw):
    def unique(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate receipt JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite receipt JSON')))


def number(value):
    need(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'invalid monotonic interval')
    return value


class Tee:
    """Persist front-end Python receipts before its flush returns to submitter."""
    encoding = 'utf-8'

    def __init__(self, stream, console, on_flush=None):
        self.stream, self.console, self.bytes = stream, console, 0
        self.on_flush = on_flush

    def write(self, text):
        need(type(text) is str, 'text-only phase stdout required')
        self.bytes += len(text.encode('utf-8'))
        need(self.bytes <= LOG_LIMIT, 'phase receipt exceeded bounded log size')
        self.stream.write(text)
        self.console.write(text)
        return len(text)

    def flush(self):
        self.stream.flush(); os.fsync(self.stream.fileno())
        if self.on_flush is not None:
            self.on_flush()
        self.console.flush()


@contextmanager
def wrapper_lock(a):
    """Independent lock. Never acquire or pass the Native companion lock here."""
    import fcntl
    if not ACTION_ROOT.exists():
        a.durable.mkdir_new(ACTION_ROOT)
    a.c.private_dir(ACTION_ROOT)
    path = ACTION_ROOT/'wrapper.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor); after = path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077 and
             info.st_nlink == 1 and (info.st_dev, info.st_ino) == (after.st_dev, after.st_ino),
             'root private single-link wrapper lock required')
        os.fsync(descriptor); a.durable.fsync_dir(ACTION_ROOT)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


class Runtime:
    """Production operations; tests inject a device-free fake instead."""
    def ensure_native(self):
        need(os.name == 'posix' and sys.platform.startswith('linux') and os.geteuid() == 0 and
             os.uname().machine == 'aarch64', 'root native ARM Linux required')

    def api(self):
        import run_model
        return run_model.api()

    def front(self, **kwargs):
        import run_once
        return run_once.run_once(**kwargs)

    def boot(self):
        value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        need(BOOT_RE.fullmatch(value), 'canonical current boot ID required')
        return value

    def lock(self, a):
        return wrapper_lock(a)

    def now(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)

    def service(self, unit, timeout=2.0):
        # A timeout terminates only this readonly query helper. Never signal the
        # independent coordinator, Native owner, workers, or loaded firmware.
        value = subprocess.run(['systemctl', 'show', unit,
                                '--property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus,Result'],
                               capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
                               timeout=timeout)
        need(not value.stderr and len(value.stdout) <= 16384, 'unexpected readonly systemd diagnostics')
        return dict(line.split('=', 1) for line in value.stdout.splitlines() if '=' in line)

    def event_paths(self, root):
        return sorted((root/'batch_events').glob('event_*.json'))

    def open_log(self, a, path):
        a.c.private_dir(path.parent)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        stream = os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n')
        stream.flush(); os.fsync(stream.fileno()); a.durable.fsync_dir(path.parent)
        return stream


def plan_metadata(raw, start, count):
    found = []
    for line in raw.decode('utf-8').splitlines():
        try:
            value = decode(line)
        except json.JSONDecodeError:
            continue
        if type(value) is dict and value.get('stage') == NEW_PLAN:
            found.append(value)
    need(len(found) == 1, 'exactly one NEW_MODEL_PLAN receipt required; never guess the latest plan')
    value = found[0]
    need(type(value.get('plan')) is str and PLAN_RE.fullmatch(value['plan']), 'unexpected phase plan namespace')
    for field, wanted in (('sample_start', start), ('sample_count', count), ('total_START_budget', count*36),
                          ('ORT_intra_threads', 4), ('ORT_inter_threads', 1)):
        need(type(value.get(field)) is int and value[field] == wanted, 'phase receipt selection/budget/threads differ: '+field)
    need(value.get('automatic_renewal') is False, 'automatic renewal is forbidden')
    return value


def wait_closed_service(runtime, unit):
    deadline = runtime.now() + 15.0
    while runtime.now() < deadline:
        state = runtime.service(unit, timeout=min(2.0, deadline-runtime.now()))
        if all(state.get(key) == value for key, value in {
            'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'exited',
            'MainPID': '0', 'ExecMainStatus': '0', 'Result': 'success'
        }.items()):
            return
        need(state.get('LoadState') == 'loaded' and state.get('ActiveState') != 'failed' and
             state.get('Result') == 'success', 'phase service has failed or is uncertain; no further submission')
        runtime.sleep(min(1.0, max(0.0, deadline-runtime.now())))
    raise ValueError('phase coordinator has not exited normally within 15s; no kill or retry')


def phase_proof(a, runtime, report, *, start, count, boot, source_tools, guardian_plan, not_before, dataset_pin=None):
    path = Path(report['plan']); root = path.parent; token = PLAN_RE.fullmatch(report['plan'])[1]
    for folder in (root.parent, root, root/'tools', root/'model'):
        a.c.private_dir(folder)
    tools_raw = a.c.read_file(root/'tools/tools_manifest.json', private=True, limit=1024*1024)
    need(identity(tools_raw) == source_tools['manifest'], 'phase used a different source tools manifest')
    plan = a.c.read_json(path, private=True)
    need(plan.get('schema') == 'opt50_b24_acceptance_batch_plan_v1' and plan.get('plan_path') == report['plan'] and
         plan.get('token') == token and plan.get('unit') == 'opt50-b24-model-'+token and
         plan.get('measurement_profile') == PROFILE and plan.get('source_model_sha256') == SOURCE_MODEL and
         plan.get('tool_files') == source_tools and plan.get('boot_id') == boot == runtime.boot() and
         Path(plan.get('guardian_plan', '')).as_posix() == Path(guardian_plan).as_posix(),
         'phase plan source/profile/boot/guardian identity differs')
    for key, wanted in (('sample_start', start), ('sample_count', count), ('total_START_budget', count*36),
                        ('expires_seconds', 900), ('expected_PL_starts', 36),
                        ('expected_Dense_calls', 12), ('expected_Sparse_calls', 24)):
        need(type(plan.get(key)) is int and plan[key] == wanted, 'sealed phase bounds differ: '+key)
    created = number(plan.get('created_monotonic'))
    need(not_before <= created <= runtime.now(), 'phase plan was not newly created in this action phase')
    if dataset_pin is not None:
        need(plan.get('dataset_manifest') == dataset_pin, 'second phase dataset differs from pilot')
    need(type(plan.get('dataset_manifest')) is dict, 'sealed dataset identity required')
    state = a.c.read_json(root/'batch_status.json', private=True)
    done = a.c.read_json(root/'batch_completion.json', private=True)
    need(created <= number(state.get('heartbeat_monotonic')) <= runtime.now(),
         'phase final heartbeat is outside this phase monotonic timeline')
    need(state.get('stage') == done.get('stage') == PASS and state.get('completion') == done and
         state.get('boot_id') == boot and done.get('schema') == 'opt50_b24_batch_completion_v1' and
         done.get('plan_token') == token and done.get('closed') is True and
         done.get('dataset_manifest') == plan.get('dataset_manifest') and
         state.get('committed_samples') == count and state.get('completed_PL_calls') == count*36 and
         state.get('owner_pid') is None and state.get('child_pid') is None and
         done.get('owner_pid') is None and done.get('child_pid') is None,
         'phase did not produce matching private closed PASS receipts')
    expected = {'sample_start': start, 'sample_count': count, 'committed_samples': count,
                'completed_PL_calls': count*36, 'Dense_calls': count*12, 'Sparse_calls': count*24,
                'compared_logits': count*1000, 'total_START_budget': count*36}
    need(all(type(done.get(k)) is int and done[k] == v for k, v in expected.items()), 'private completion counters differ')
    accuracy = done.get('accuracy', {})
    need(accuracy.get('numerical_status') == 'NUMERICAL_PASS' and
         type(accuracy.get('max_abs_difference')) in (int, float) and accuracy['max_abs_difference'] == 0 and
         all(accuracy.get(k) is True for k in ('all_devices_closed', 'all_native_END_verified',
                                              'all_postrun_buffer_checks_verified')) and
         all(type(accuracy.get(k)) is int and accuracy[k] == v for k, v in {
             'images': count, 'compared_logits': count*1000, 'completed_PL_calls': count*36,
             'Dense_calls': count*12, 'Sparse_calls': count*24,
             'bitwise_mismatches': 0, 'bitwise_mismatch_images': 0}.items()),
         'phase numerical/native closure proof is incomplete')
    wait_closed_service(runtime, plan['unit'])
    need(runtime.boot() == boot, 'boot changed after coordinator exit')
    return {'plan': report['plan'], 'root': root, 'plan_value': plan, 'state': state, 'completion': done}


def pilot_timing(a, runtime, proof, start, count, remaining):
    plan = proof['plan_value']; created = number(plan['created_monotonic'])
    final = number(proof['state'].get('heartbeat_monotonic'))
    paths = runtime.event_paths(proof['root'])
    need(0 < len(paths) <= 10000, 'bounded pilot batch journal required')
    admits, commits, previous = {}, {}, created
    for sequence, path in enumerate(paths, 1):
        row = a.c.read_json(path, private=True)
        need(path.name == 'event_%06d.json' % sequence and row.get('sequence') == sequence and
             row.get('schema') == 'opt50_durable_event_v1', 'pilot journal gap or schema differs')
        if row.get('action') != 'batch':
            continue
        state = row.get('state', {}); stage = row.get('phase')
        need(state.get('stage') == stage and state.get('boot_id') == plan['boot_id'] and
             state.get('unit') == plan['unit'], 'pilot journal belongs to another phase/boot')
        stamp = number(state.get('heartbeat_monotonic'))
        need(stamp >= previous, 'pilot journal monotonic order differs'); previous = stamp
        target = admits if stage == 'SAMPLE_ADMITTED_BEFORE_ANY_CHILD' else commits if stage == 'SAMPLE_COMMITTED_NATIVE_CLOSED' else None
        if target is not None:
            ordinal = state.get('current_ordinal')
            need(type(ordinal) is int and start <= ordinal < start+count and ordinal not in target,
                 'pilot admission/commit ordinal repeated or outside range')
            target[ordinal] = stamp
    ordinals = list(range(start, start+count))
    need(set(admits) == set(commits) == set(ordinals), 'pilot does not have one admission/commit per image')
    intervals = []
    previous = created
    for ordinal in ordinals:
        need(previous <= admits[ordinal] < commits[ordinal] <= final, 'pilot image chronology differs')
        intervals.append(commits[ordinal]-admits[ordinal]); previous = commits[ordinal]
    before, after = admits[start]-created, final-commits[start+count-1]
    gaps = [admits[ordinal+1]-commits[ordinal] for ordinal in ordinals[:-1]]
    max_gap = max(gaps, default=0.0)
    conservative_image = max(intervals)+max_gap
    prediction = before + remaining*conservative_image + after
    return {'pilot_image_seconds': intervals, 'max_image_seconds': max(intervals),
            'fixed_before_seconds': before, 'fixed_after_seconds': after,
            'max_inter_image_gap_seconds': max_gap, 'conservative_image_seconds': conservative_image,
            'remaining_images': remaining, 'predicted_remaining_phase_seconds': prediction,
            'window_seconds': WINDOW_SECONDS, 'reserved_headroom_seconds': RESERVE_SECONDS,
            'fits_with_headroom': prediction+RESERVE_SECONDS <= WINDOW_SECONDS,
            'scope': 'Observed journal intervals include checks and archive work; a prediction, not a runtime guarantee'}


def run_first50(guardian_plan, layer_plan, previous_model_plan, dataset, *, start=20, count=50,
                uart=False, authorized=False, runtime=None):
    need(authorized is True and uart is True, 'explicit --authorized-up-to-50-images and UART logging required')
    need(type(start) is int and type(count) is int and 0 <= start < 1800 and
         1 <= count <= 50 and start+count <= 1800, 'select 1..50 new images within the sealed 1800-image range')
    runtime = Runtime() if runtime is None else runtime
    runtime.ensure_native(); a = runtime.api(); a.c.root_linux()
    boot = runtime.boot(); need(BOOT_RE.fullmatch(boot), 'current boot ID required')
    source_tools = a.tools
    with runtime.lock(a):
        if not ACTION_ROOT.exists():
            a.durable.mkdir_new(ACTION_ROOT)
        a.c.private_dir(ACTION_ROOT)
        token = secrets.token_hex(6); root = ACTION_ROOT/('action_'+token); a.durable.mkdir_new(root)
        action = {'schema': 'opt50_b24_first50_action_v3', 'token': token, 'boot_id': boot,
                  'measurement_profile': PROFILE, 'source_model_sha256': SOURCE_MODEL,
                  'source_tools': source_tools, 'sample_start': start, 'maximum_new_images': count,
                  'total_START_budget': count*36, 'guardian_plan': str(guardian_plan),
                  'layer_plan': str(layer_plan), 'previous_model_plan': str(previous_model_plan),
                  'dataset': str(dataset), 'uart_log_confirmed': True, 'phase_plans': [],
                  'stage': 'ACTION_AUTHORIZED_BEFORE_PHASE_CHECK', 'automatic_retry': False,
                  'automatic_renewal': False, 'index_prepared_by_wrapper': False}
        a.durable.new_json(root/'action.json', action)
        print(json.dumps({'action': str(root), 'stage': action['stage'], 'total_START_budget': count*36}), flush=True)
        current_log = None
        try:
            completed = 0; pilot = None; timing = None
            phases = [(start, min(5, count)), (start+5, max(0, count-5))]
            for index, (phase_start, phase_count) in enumerate(phases, 1):
                if not phase_count:
                    break
                if index == 2 and not timing['fits_with_headroom']:
                    break
                need(runtime.boot() == boot, 'current boot changed before phase admission')
                need(runtime.api().tools == source_tools, 'public source release changed before the next phase check')
                current_log = root/('phase_%02d.stdout' % index)
                not_before = runtime.now()
                action.update(stage='PHASE_%02d_INTENT_BEFORE_CHECK' % index,
                              current_phase=index, current_log=str(current_log))
                a.durable.replace_json(root/'action.json', action)
                captured = {}
                def capture_plan_before_submit():
                    if captured:
                        return
                    raw = a.c.read_file(current_log, private=True, limit=LOG_LIMIT)
                    if NEW_PLAN.encode('utf-8') not in raw:
                        return
                    report = plan_metadata(raw, phase_start, phase_count)
                    action['phase_plans'].append({'phase': index, 'plan': report['plan'],
                                                  'sample_start': phase_start, 'sample_count': phase_count})
                    action['stage'] = 'PHASE_%02d_PLAN_CAPTURED_BEFORE_SUBMISSION' % index
                    a.durable.replace_json(root/'action.json', action)
                    captured['report'] = report
                with runtime.open_log(a, current_log) as stream:
                    tee = Tee(stream, sys.stdout, capture_plan_before_submit)
                    with redirect_stdout(tee):
                        code = runtime.front(guardian_plan=guardian_plan, layer_plan=layer_plan,
                            uart=True, authorized=True, previous_model_plan=previous_model_plan,
                            dataset=dataset, start=phase_start, count=phase_count,
                            pilot_plan=Path(pilot['plan']) if pilot is not None else None)
                    tee.flush()
                report = plan_metadata(a.c.read_file(current_log, private=True, limit=LOG_LIMIT), phase_start, phase_count)
                need(captured.get('report') == report, 'phase plan was not durably captured before front submission')
                need(type(code) is int and code == 0, 'phase front-end refused/failed; no following check, retry or renewal')
                proof = phase_proof(a, runtime, report, start=phase_start, count=phase_count, boot=boot,
                    source_tools=source_tools, guardian_plan=guardian_plan, not_before=not_before,
                    dataset_pin=pilot['plan_value']['dataset_manifest'] if pilot is not None else None)
                completed += phase_count
                if index == 1:
                    pilot = proof
                    timing = pilot_timing(a, runtime, proof, start, phase_count, max(0, count-phase_count))
                    action['pilot_prediction'] = timing
                action.update(stage='PHASE_%02d_CLOSED_VERIFIED' % index, completed_images=completed,
                              completed_PL_calls=completed*36)
                a.durable.replace_json(root/'action.json', action)
            stage = 'PASS_FIRST50_REQUESTED_RANGE_CLOSED' if completed == count else 'PASS_PILOT_ONLY_MAIN_NOT_SUBMITTED'
            result = {'schema': 'opt50_b24_first50_result_v3', 'stage': stage, 'action': str(root),
                      'sample_start': start, 'requested_maximum_images': count, 'completed_images': completed,
                      'completed_PL_calls': completed*36, 'total_START_budget': count*36,
                      'phase_plans': action['phase_plans'], 'pilot_prediction': timing,
                      'boot_id': boot, 'source_tools_manifest': source_tools['manifest'],
                      'measurement_profile': PROFILE, 'closed': True, 'new_plan_checks_submitted': len(action['phase_plans']),
                      'automatic_retry': False, 'automatic_renewal': False,
                      'scope': 'Only newly sealed disjoint images; existing accepted records remain untouched'}
            a.durable.new_json(root/'result.json', result)
            action.update(stage=stage, result=result); a.durable.replace_json(root/'action.json', action)
            return result
        except BaseException as error:
            failure = {'schema': 'opt50_b24_first50_result_v3', 'stage': 'FIRST50_STOPPED_NO_RETRY_OR_RENEWAL',
                       'action': str(root), 'reason': type(error).__name__+': '+str(error),
                       'phase_plans': action['phase_plans'], 'current_phase_log': str(current_log),
                       'completed_images_verified_by_wrapper': action.get('completed_images', 0),
                       'total_START_budget': count*36, 'automatic_retry': False, 'automatic_renewal': False,
                       'warning': 'Any submitted coordinator is independent; inspect the saved phase log and exact plan, do not replay'}
            try:
                a.durable.new_json(root/'failure.json', failure)
                action.update(stage=failure['stage'], failure=failure); a.durable.replace_json(root/'action.json', action)
            except BaseException:
                pass  # Preserve the earlier durable intent and log; never pretend the save succeeded.
            print(json.dumps(failure), flush=True)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('guardian-plan', 'layer-plan', 'previous-model-plan', 'dataset'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--start', type=int, default=20)
    parser.add_argument('--count', type=int, default=50)
    parser.add_argument('--uart-log-confirmed', action='store_true')
    parser.add_argument('--authorized-up-to-50-images', action='store_true')
    args = parser.parse_args()
    try:
        result = run_first50(args.guardian_plan, args.layer_plan, args.previous_model_plan, args.dataset,
                            start=args.start, count=args.count, uart=args.uart_log_confirmed,
                            authorized=args.authorized_up_to_50_images)
        print(json.dumps(result, indent=2), flush=True)
        return 0
    except BaseException as error:
        print(json.dumps({'stage': 'FIRST50_ENTRY_STOPPED_NO_AUTOMATIC_RETRY',
                          'reason': type(error).__name__+': '+str(error)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
