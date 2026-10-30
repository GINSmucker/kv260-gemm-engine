"""Incremental admission cache of fully verified, closed acceptance transactions.

The caller holds the existing companion lock; this module takes its own flock.
The cache prevents replay without re-decoding every historical model at each
admission. Final acceptance must still independently review every saved logit
and closure record; this cache never constitutes final dataset acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
RECORDS = Path('/var/lib/opt50-b24-model-v1')
INDEX_DIR = Path('/var/lib/opt50-b24-history-v3')
SCHEMA = 'opt50_b24_verified_history_index_v1'
SNAPSHOT = 'opt50_b24_history_snapshot_v1'
ENVELOPE = 'opt50_b24_history_envelope_v1'
PLAN_SCHEMA = 'opt50_b24_acceptance_batch_plan_v1'
PROFILE = 'b24-batch-acceptance-cpu4-pl4-v1'
MODEL = 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
MODEL_MANIFEST = '91ed1cd0a8b55cfde7e1a82403f879f1e225ae4c96cc47272f8dd92304a19f60'
CLOSED = {'PASS_B24_BATCH_ALL_IMAGES_BITWISE_CLOSED', 'FAIL_B24_BATCH_NUMERICAL_NATIVE_CLOSED',
          'PARTIAL_CLOSED_PLAN_EXPIRED', 'PARTIAL_CLOSED_OPERATOR_STOP', 'REFUSED_BATCH_BEFORE_OWNER'}
MAX_INDEX_BYTES = 8*1024*1024


def need(ok, message):
    if not ok:
        raise ValueError(message)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate history JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite history JSON')))


def pin(value):
    need(type(value) is dict and set(value) == {'bytes', 'sha256'} and type(value['bytes']) is int and
         0 <= value['bytes'] <= 64*1024*1024 and type(value['sha256']) is str and
         re.fullmatch('[a-f0-9]{64}', value['sha256']), 'invalid history file pin')


def ordinary(path):
    source = Path(path)
    need('..' not in source.parts, 'history parent traversal refused')
    path = source.absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
             'linked history path refused')
    return path


def private_metadata(info, directory=False):
    need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
         info.st_uid == 0 and not info.st_mode & 0o077 and (directory or info.st_nlink == 1),
         'root private single-link history input required')


def private_dir(path):
    path = ordinary(path); private_metadata(path.lstat(), directory=True)
    return path


def read(path, limit=1024*1024):
    path = ordinary(path); before = path.lstat(); private_metadata(before)
    need(0 <= before.st_size <= limit, 'bounded history input required')
    key = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                         value.st_birthtime_ns if os.name == 'nt' else value.st_ctime_ns)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        need(key(os.fstat(stream.fileno())) == key(before), 'history file replaced before read')
        raw = stream.read(limit+1)
        need(len(raw) == before.st_size and key(os.fstat(stream.fileno())) == key(before),
             'history file changed during read')
    need(key(path.lstat()) == key(before), 'history file replaced after read')
    return raw


def require_root(a):
    a.c.root_linux()


def lock_fd(descriptor, acquire):
    import fcntl
    fcntl.flock(descriptor, (fcntl.LOCK_EX | fcntl.LOCK_NB) if acquire else fcntl.LOCK_UN)


@contextmanager
def locked(a, create=False):
    require_root(a)
    if not INDEX_DIR.exists():
        need(create and not INDEX_DIR.is_symlink(), 'history index not prepared; explicitly review closed plans first')
        a.durable.mkdir_new(INDEX_DIR)
    private_dir(INDEX_DIR)
    lock = INDEX_DIR/'index.lock'
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(lock, flags, 0o600)
    acquired = False
    try:
        private_metadata(os.fstat(descriptor)); private_metadata(lock.lstat())
        need((lock.stat().st_dev, lock.stat().st_ino) ==
             (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino), 'history lock replaced')
        lock_fd(descriptor, True); acquired = True
        if create:
            os.fsync(descriptor); a.durable.fsync_dir(INDEX_DIR)
        yield
    finally:
        if acquired:
            lock_fd(descriptor, False)
        os.close(descriptor)


def canonical(path):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and path.name == 'plan.json' and
         path.parent.parent == RECORDS.absolute() and re.fullmatch('plan_[a-f0-9]{12}', path.parent.name),
         'canonical acceptance history plan required')
    private_dir(RECORDS); private_dir(path.parent)
    return path


def small(path, require_status=True):
    path = canonical(path); root = path.parent
    private_dir(root/'tools')
    raw_plan = read(path, 65536); plan = decode(raw_plan)
    raw_tools = read(root/'tools/tools_manifest.json')
    raw_done = read(root/'batch_completion.json'); done = decode(raw_done)
    need(type(plan) is dict and plan.get('schema') == PLAN_SCHEMA and plan.get('measurement_profile') == PROFILE and
         plan.get('source_model_sha256') == MODEL and plan.get('model_manifest_sha256') == MODEL_MANIFEST and
         plan.get('plan_path') == str(path) and plan.get('token') == root.name[5:] and
         plan.get('dataset_manifest') is not None, 'historical plan identity differs')
    pin(plan['dataset_manifest'])
    need(plan.get('tool_files', {}).get('manifest') == identity(raw_tools), 'history plan/tool manifest changed')
    start, count, completed = plan.get('sample_start'), plan.get('sample_count'), done.get('committed_samples')
    need(type(start) is int and type(count) is int and 0 <= start < 1800 and 1 <= count <= min(50,1800-start) and
         type(completed) is int and 0 <= completed <= count and done.get('schema') == 'opt50_b24_batch_completion_v1' and
         done.get('plan_token') == plan['token'] and done.get('dataset_manifest') == plan['dataset_manifest'] and
         done.get('sample_start') == start and done.get('sample_count') == count and
         done.get('stage') in CLOSED and done.get('closed') is True and
         done.get('owner_pid') is None and done.get('child_pid') is None,
         'historical batch is not a bound closed transaction')
    for key, expected in (('expected_PL_starts',36),('expected_Dense_calls',12),
                          ('expected_Sparse_calls',24),('run_id',1)):
        need(type(plan.get(key)) is int and plan[key] == expected, 'historical per-owner budget differs')
    if plan.get('lifecycle_profile') is not None:
        need(plan['lifecycle_profile'] == 'resident-bounded-batch-v4' and
             plan.get('owner_graph_count') == count and plan.get('budget_scope') ==
             '36 per graph; bounded total for one resident owner', 'resident history budget differs')
    need(type(done.get('sample_start')) is int and type(done.get('sample_count')) is int,
         'historical range integer types differ')
    for key, expected in (('total_START_budget',36*count),('completed_PL_calls',36*completed),
                          ('Dense_calls',12*completed),('Sparse_calls',24*completed),('compared_logits',1000*completed)):
        need(type(done.get(key)) is int and done[key] == expected, 'historical completion counter differs: '+key)
    need(type(plan.get('total_START_budget')) is int and plan['total_START_budget'] == 36*count,
         'historical plan START budget differs')
    if require_status:
        state = decode(read(root/'batch_status.json'))
        need(type(state) is dict and state.get('completion') == done and state.get('stage') == done['stage'] and
             state.get('owner_pid') is None and state.get('child_pid') is None and
             state.get('plan_token') == plan['token'] and state.get('boot_id') == plan.get('boot_id') and
             state.get('unit') == plan.get('unit'), 'historical batch final state unresolved or changed')
    return plan, done, {'plan': identity(raw_plan), 'completion': identity(raw_done),
                        'tools_manifest': identity(raw_tools), 'stage': done['stage'],
                        'ordinals': list(range(start,start+completed))}


def validate(a, value):
    need(type(value) is dict and set(value) == {'schema','revision','dataset_manifest','measurement_profile',
         'source_model_sha256','model_manifest_sha256','batches','samples','images_count'} and value.get('schema') == SCHEMA and
         value.get('measurement_profile') == PROFILE and value.get('source_model_sha256') == MODEL and
         value.get('model_manifest_sha256') == MODEL_MANIFEST and type(value.get('revision')) is int and value['revision'] >= 1,
         'history index identity/structure differs')
    pin(value['dataset_manifest'])
    batches, samples = value['batches'], value['samples']
    need(type(batches) is dict and type(samples) is dict and len(batches) <= 1800 and len(samples) <= 1800 and
         type(value.get('images_count')) is int and value['images_count'] == len(samples), 'history index count differs')
    expected = set()
    for name, batch in batches.items():
        canonical(name)
        need(type(batch) is dict and set(batch) == {'plan','completion','tools_manifest','stage','ordinals'} and
             batch['stage'] in CLOSED and type(batch['ordinals']) is list, 'invalid indexed batch')
        for key in ('plan','completion','tools_manifest'):
            pin(batch[key])
        ordinals = batch['ordinals']
        need(all(type(n) is int and 0 <= n < 1800 for n in ordinals) and ordinals == sorted(set(ordinals)),
             'invalid indexed ordinals')
        need(len(ordinals) <= 50 and (not ordinals or ordinals == list(range(ordinals[0],ordinals[0]+len(ordinals)))),
             'indexed batch is not a bounded contiguous prefix')
        for ordinal in ordinals:
            key = str(ordinal)
            need(key not in expected and key in samples, 'duplicate/missing indexed image')
            expected.add(key)
            row = samples[key]
            need(type(row) is dict and set(row) == {'plan','commit','accuracy'} and row['plan'] == name,
                 'indexed image/batch binding differs')
            pin(row['commit']); a.metrics.validate_record(row['accuracy'])
            need(type(row['accuracy'].get('ordinal')) is int and row['accuracy']['ordinal'] == ordinal,
                 'indexed accuracy ordinal differs')
    need(set(samples) == expected, 'unlisted indexed images')


def load(a):
    raw = read(INDEX_DIR/'index.json', MAX_INDEX_BYTES); envelope = decode(raw)
    need(type(envelope) is dict and set(envelope) == {'schema','payload','payload_sha256'} and
         envelope.get('schema') == ENVELOPE and type(envelope.get('payload_sha256')) is str and
         identity(encoded(envelope['payload']))['sha256'] == envelope['payload_sha256'], 'history index checksum differs')
    validate(a, envelope['payload'])
    return envelope['payload'], raw


def snapshot(value, raw):
    return {'schema': SNAPSHOT, 'path': str(INDEX_DIR/'index.json'), 'revision': value['revision'],
            'pin': identity(raw), 'images_count': value['images_count']}


def persist(a, value):
    validate(a, value)
    envelope = {'schema':ENVELOPE,'payload':value,'payload_sha256':identity(encoded(value))['sha256']}
    need(len(encoded(envelope)) <= MAX_INDEX_BYTES, 'history index exceeds bound')
    a.durable.replace_json(INDEX_DIR/'index.json', envelope)
    actual, raw = load(a)
    need(actual == value, 'published history index differs')
    return snapshot(actual, raw)


def verify_known(value):
    for name, expected in value['batches'].items():
        plan, _, observed = small(name)
        need(observed == expected and plan['dataset_manifest'] == value['dataset_manifest'],
             'indexed batch small pins changed')


def merge(a, value, path, plan, done, batch, rows):
    need(plan['dataset_manifest'] == value['dataset_manifest'], 'history dataset differs')
    need([row['ordinal'] for row in rows] == batch['ordinals'], 'verified rows differ from closed prefix')
    name = str(path)
    need(name not in value['batches'], 'batch already indexed; no second commit')
    for row in rows:
        a.metrics.validate_record(row)
        ordinal = row['ordinal']; key = str(ordinal)
        need(key not in value['samples'], 'image already committed by another plan')
        commit_raw = read(path.parent/'samples'/('%06d'%ordinal)/'commit.json')
        commit = decode(commit_raw)
        need(commit.get('schema') == 'opt50_b24_batch_sample_commit_v1' and commit.get('closed') is True and
             commit.get('plan_token') == plan['token'] and commit.get('sample',{}).get('ordinal') == ordinal,
             'sample commit identity differs')
        value['samples'][key] = {'plan':name, 'commit':identity(commit_raw), 'accuracy':row}
    value['batches'][name] = batch
    value['images_count'] = len(value['samples'])


def recompute(a, root, rows):
    raw = a.c.read_file(HERE/'review_results.py', private=HERE.name == 'tools', limit=2*1024*1024)
    need(identity(raw) == a.tools['files']['review_results.py'], 'independent review source changed')
    module = types.ModuleType('_b24_index_logit_review'); module.__file__ = str(HERE/'review_results.py')
    exec(compile(raw,module.__file__,'exec'),module.__dict__)
    return module.recompute_records(root,rows)


def prepare(a, explicit_plan_paths, expected_count):
    """Deep-audit explicit closed plans once; idempotently retain previous rows."""
    need(type(expected_count) is int and 0 <= expected_count <= 1800 and
         type(explicit_plan_paths) in (list,tuple) and 1 <= len(explicit_plan_paths) <= 1800,
         'explicit closed plan list and expected unique image count required')
    paths = [canonical(path) for path in explicit_plan_paths]
    need(len(set(paths)) == len(paths), 'duplicate explicit history plans')
    with locked(a,create=True):
        if (INDEX_DIR/'index.json').exists():
            value, raw = load(a); verify_known(value); changed = False
        else:
            value = None; raw = None; changed = True
        for path in paths:
            plan, done, batch = small(path)
            if value is not None and str(path) in value['batches']:
                need(value['batches'][str(path)] == batch, 'indexed explicit plan changed')
                continue
            entry, root, _ = a.compat.load_closed_entry(path,a.tools,access=a.c)
            rows = entry.read_completed_records(path,allow_partial=True)
            rows = recompute(a,root,rows)
            if value is None:
                value = {'schema':SCHEMA,'revision':0,'dataset_manifest':plan['dataset_manifest'],
                         'measurement_profile':PROFILE,'source_model_sha256':MODEL,'model_manifest_sha256':MODEL_MANIFEST,
                         'batches':{},'samples':{},'images_count':0}
            merge(a,value,path,plan,done,batch,rows); changed = True
        need(value is not None and value['images_count'] == expected_count, 'expected unique history image count differs')
        if not changed:
            return snapshot(value,raw)
        value['revision'] += 1
        return persist(a,value)


def admit(a, history, dataset_pin, start, count):
    """Check cached closed transactions and replay bounds; no old model/log reads."""
    need(type(start) is int and type(count) is int and 0 <= start < 1800 and 1 <= count <= min(50,1800-start),
         'bounded new image range required')
    with locked(a):
        value, raw = load(a); verify_known(value)
        need(value['dataset_manifest'] == dataset_pin, 'admission dataset differs from history index')
        seen = set()
        for item in history:
            path = canonical(item['plan']); plan = decode(read(path,65536))
            need(type(plan) is dict, 'invalid history plan object')
            if plan.get('schema') == PLAN_SCHEMA:
                need(str(path) in value['batches'], 'closed batch is not indexed; explicitly prepare history first')
                need(str(path) not in seen, 'duplicate history batch')
                seen.add(str(path))
            else:
                need(plan.get('schema') == 'opt50_b24_model_plan_v1' and not (path.parent/'samples').exists(),
                     'unknown nonbatch history schema')
                state = decode(read(path.parent/'execution/status.json'))
                need(type(state) is dict and state.get('owner_pid') is None and state.get('child_pid') is None and
                     state.get('boot_id') == plan.get('boot_id') and state.get('unit') == plan.get('unit'),
                     'single-model history is unresolved')
                if state.get('stage') == 'REFUSED_BEFORE_OWNER_NO_MMIO':
                    native = path.parent/'execution/native_events'
                    need(state.get('hardware_executed') is False and not native.exists() and not native.is_symlink(),
                         'single-model refusal lacks no-owner proof')
                else:
                    complete = decode(read(path.parent/'execution/completion.json'))
                    need(state.get('stage') in {'PASS_B24_ALIGNED_MODEL_36_STARTS_BITWISE_SINGLE_INPUT',
                         'PASS_B24_FULL_MODEL_36_STARTS_REAL_INPUT_BITWISE_SINGLE_IMAGE',
                         'PASS_B24_SINGLE_IMAGE_BITWISE_NATIVE_CLOSED', 'FAIL_B24_FULL_MODEL_NUMERICAL_NATIVE_CLOSED'} and
                         complete.get('owner_exit_code') == 0 and complete.get('native',{}).get('device_closed') is True,
                         'single-model history is unresolved')
        need(seen == set(value['batches']), 'indexed historical batch missing from current history')
        need(not set(range(start,start+count)) & {int(key) for key in value['samples']},
             'selected image already has a committed execution')
        return snapshot(value,raw)


def append_closed(a, plan, records, completion):
    """Commit a newly durable closed prefix before publishing final batch status."""
    path = canonical(plan['plan_path'])
    with locked(a):
        value, raw = load(a)
        need(plan.get('history_index') == snapshot(value,raw), 'history index revision/pin changed; no automatic retry')
        stored_plan, done, batch = small(path,require_status=False)
        need(stored_plan == plan and done == completion, 'new plan/completion differ from durable files')
        need(plan.get('tool_files') == a.tools, 'append requires the current verified release')
        entry, root, _ = a.compat.load_closed_entry(path,a.tools,access=a.c)
        frozen_api = entry.api(private=True)
        frozen_plan = entry.load_plan(frozen_api,path,fresh=False)
        verified = entry.committed_records(frozen_api,frozen_plan,root,complete=False)
        need(verified == records, 'new records differ from complete sample closure verification')
        merge(a,value,path,plan,done,batch,records)
        value['revision'] += 1
        return persist(a,value)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare',))
    parser.add_argument('--plans',type=Path,nargs='+',required=True)
    parser.add_argument('--expected-count',type=int,required=True)
    args = parser.parse_args()
    import verify_tools
    verify_tools.verify()
    import run_model
    a = run_model.api(); a.c.root_linux()
    with a.c.companion_locked():
        result = prepare(a,args.plans,args.expected_count)
    print(json.dumps({'status':'CLOSED_HISTORY_INDEX_PREPARED_NO_NEW_COMPUTE','snapshot':result,
                      'hardware_accessed':False,'START_commands_issued':0},indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError,ValueError,KeyError,TypeError,ImportError) as error:
        raise SystemExit('HISTORY_INDEX_REFUSED_NO_RETRY: '+type(error).__name__+': '+str(error))
