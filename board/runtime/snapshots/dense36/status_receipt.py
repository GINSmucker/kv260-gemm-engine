"""Read a bound saved DENSE receipt without importing runtime or inspecting hardware.

This is a status-only reader. It grants no check/run/owner authority and does
not revalidate model contents, device state, processes, or native completion.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

RECORDS = Path('/var/lib/opt50-dense-model-v1')
PROFILE = 'dense500-same-pl-cpu4-pl4-v1'
PLAN_SCHEMA = 'opt50_dense_acceptance_batch_plan_v1'
SCOPE = 'SEALED_DENSE_RESIDENT_BATCH_ONE_OWNER_BOUNDED_GRAPHS_36_STARTS_PER_IMAGE'
SOURCE_MODEL = 'f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415'
MODEL_MANIFEST = 'f86d25186f583c54fa7739af93756c229dd35047b044ebb3ecf2fd43a107d384'
PASS = 'PASS_DENSE_BATCH_ALL_IMAGES_BITWISE_CLOSED'
WARNING = 'Saved receipt only; not fresh hardware inspection or retry/recovery permission'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate saved receipt JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite saved receipt JSON')))


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def ordinary(path):
    source = Path(path)
    need('..' not in source.parts, 'saved receipt parent traversal refused')
    path = source.absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
             'linked saved receipt path refused')
    return path


def private_metadata(info, directory=False):
    need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
         info.st_uid == 0 and not info.st_mode & 0o077 and (directory or info.st_nlink == 1),
         'root private saved receipt directory/file required')


def private_dir(path):
    path = ordinary(path)
    private_metadata(path.lstat(), directory=True)
    return path


def read_file(path, limit=1024*1024):
    path = ordinary(path)
    before = path.lstat()
    private_metadata(before)
    need(0 <= before.st_size <= limit, 'bounded saved receipt file required')
    key = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                       info.st_birthtime_ns if os.name == 'nt' else info.st_ctime_ns)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        need(key(os.fstat(stream.fileno())) == key(before), 'saved receipt replaced before read')
        raw = stream.read(limit+1)
        need(len(raw) == before.st_size and key(os.fstat(stream.fileno())) == key(before),
             'saved receipt changed while reading')
    need(key(path.lstat()) == key(before), 'saved receipt replaced after read')
    return raw


def validate_pin(pin):
    need(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and type(pin['bytes']) is int and
         0 <= pin['bytes'] <= 64*1024*1024 and type(pin['sha256']) is str and
         re.fullmatch('[a-f0-9]{64}', pin['sha256']), 'invalid saved tool identity')


def tool_inventory(folder, plan):
    raw = read_file(folder/'tools_manifest.json', 1024*1024)
    tools = decode(raw)
    required = {'run_model.py', 'status_receipt.py', 'model_runtime.py', 'model_worker.py',
                'support_py/resident.py', 'support_py/durable.py', 'support_py/model_proof.py',
                'model/manifest.json', 'build_arm.py', 'dataset.py', 'accuracy_metrics.py'}
    need(type(tools) is dict and tools.get('schema') == 'opt50_dense_batch_tools_v1' and
         tools.get('hardware_executed') is False and tools.get('source_model_sha256') == SOURCE_MODEL and
         type(tools.get('files')) is dict and required <= set(tools['files']) and
         len(tools['files']) <= 256, 'saved tool inventory differs')
    for name, pin in tools['files'].items():
        need(type(name) is str and name and not any(ch in name for ch in '\\:\0\r\n'),
             'unsafe saved tool filename')
        path = PurePosixPath(name)
        need(str(path) == name and not path.is_absolute() and
             all(part not in ('', '.', '..') for part in name.split('/')), 'unsafe saved tool filename')
        validate_pin(pin)
    need(plan.get('tool_files') == {'manifest': identity(raw), 'files': tools['files']},
         'saved plan/tool inventory binding differs')
    need(tools['files']['model/manifest.json']['sha256'] == MODEL_MANIFEST,
         'saved tool model manifest identity differs')
    for name in ('run_model.py', 'status_receipt.py'):
        need(identity(read_file(folder/name)) == tools['files'][name], 'saved status source changed: '+name)


def validate_plan(path, folder):
    path = Path(path)
    need(path.is_absolute() and path.name == 'plan.json' and '..' not in path.parts and
         path.parent.parent == RECORDS and re.fullmatch('plan_[a-f0-9]{12}', path.parent.name),
         'canonical saved batch plan required')
    root = path.parent
    need(Path(folder).absolute() == root/'tools', 'status reader must use frozen private tools')
    for directory in (RECORDS, root, root/'tools', root/'model', root/'dataset'):
        private_dir(directory)
    plan = decode(read_file(path, 65536))
    token = root.name[5:]
    fixed = {'schema': PLAN_SCHEMA, 'token': token, 'unit': 'opt50-dense-model-'+token,
             'plan_path': str(path), 'scope': SCOPE, 'measurement_profile': PROFILE,
             'expires_seconds': 900, 'expected_PL_starts': 36, 'expected_Dense_calls': 36,
             'expected_Sparse_calls': 0, 'run_id': 1, 'source_model_sha256': SOURCE_MODEL,
             'model_manifest_sha256': MODEL_MANIFEST, 'old_board_lock_transferred': False,
             'uart_logging_operator_confirmed': True, 'model_root': str(root/'model'),
             'dataset_root': str(root/'dataset'), 'execution': str(root/'execution'),
             'socket': str(root/'execution/runtime.sock'), 'plugin': str(root/'plugin.so'),
             'python': '/home/ubuntu/fpga_project/model_b/.venv/bin/python'}
    need(type(plan) is dict and all(type(plan.get(key)) is type(value) and plan[key] == value
                                   for key, value in fixed.items()), 'sealed saved plan identity/scope differs')
    need(type(plan.get('boot_id')) is str and re.fullmatch(
        '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', plan['boot_id']),
        'saved plan boot identity differs')
    start, count = plan.get('sample_start'), plan.get('sample_count')
    need(type(start) is int and type(count) is int and 0 <= start < 1800 and
         1 <= count <= min(50, 1800-start) and type(plan.get('total_START_budget')) is int and
         plan['total_START_budget'] == 36*count and type(plan.get('guardian_pid')) is int and
         plan['guardian_pid'] > 0, 'saved sample/START/guardian bounds differ')
    validate_pin(plan.get('dataset_manifest'))
    tool_inventory(root/'tools', plan)
    return path, plan


def validate_status(value, plan):
    fixed = {'schema': 'opt50_dense_acceptance_batch_status_v1', 'boot_id': plan['boot_id'],
             'plan_token': plan['token'], 'unit': plan['unit'], 'guardian_pid': plan['guardian_pid'],
             'measurement_profile': PROFILE, 'sample_start': plan['sample_start'],
             'sample_count': plan['sample_count'], 'total_START_budget': plan['total_START_budget']}
    need(type(value) is dict and all(type(value.get(key)) is type(expected) and value[key] == expected
                                    for key, expected in fixed.items()), 'foreign or malformed saved batch state')
    need(type(value.get('stage')) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,127}', value['stage']),
         'invalid saved batch stage')
    committed = value.get('committed_samples')
    need(type(committed) is int and 0 <= committed <= plan['sample_count'] and
         type(value.get('completed_PL_calls')) is int and value['completed_PL_calls'] == 36*committed,
         'saved batch counters differ')
    for key in ('owner_pid', 'child_pid'):
        need(value.get(key) is None or type(value[key]) is int and value[key] > 0,
             'invalid saved child identity')
    done = value.get('completion')
    if 'completion' in value:
        need(type(done) is dict and done.get('schema') == 'opt50_dense_batch_completion_v1' and
             done.get('plan_token') == plan['token'] and done.get('stage') == value['stage'] and
             done.get('sample_start') == plan['sample_start'] and done.get('sample_count') == plan['sample_count'] and
             done.get('total_START_budget') == plan['total_START_budget'] and done.get('committed_samples') == committed and
             done.get('completed_PL_calls') == 36*committed and done.get('compared_logits') == 1000*committed and
             done.get('Dense_calls') == 36*committed and done.get('Sparse_calls') == 0*committed and
             done.get('dataset_manifest') == plan.get('dataset_manifest') and done.get('closed') is True and
             done.get('owner_pid') is None and done.get('child_pid') is None and
             value.get('owner_pid') is None and value.get('child_pid') is None,
             'saved completion identity/closure differs')
        for key in ('sample_start', 'sample_count', 'total_START_budget', 'committed_samples',
                    'completed_PL_calls', 'compared_logits', 'Dense_calls', 'Sparse_calls'):
            need(type(done[key]) is int, 'saved completion integer type differs')
    if value['stage'] == PASS:
        need(type(done) is dict and committed == plan['sample_count'] and
             type(done.get('accuracy')) is dict and done['accuracy'].get('numerical_status') == 'NUMERICAL_PASS' and
             type(done['accuracy'].get('images')) is int and done['accuracy']['images'] == committed and
             type(done['accuracy'].get('bitwise_mismatches')) is int and done['accuracy']['bitwise_mismatches'] == 0,
             'saved PASS lacks complete bound numerical receipt')


def read_saved(path, summary=False, folder=None):
    """Return only the frozen saved receipt; never acquire locks or import runtime."""
    folder = Path(folder) if folder is not None else Path(__file__).absolute().parent
    path, plan = validate_plan(path, folder)
    file = path.parent/'batch_status.json'
    try:
        value = decode(read_file(file))
    except FileNotFoundError:
        need(not file.exists() and not file.is_symlink(), 'saved status input disappeared during read')
        value = {'stage': 'NO_SAVED_STATUS_YET_SEE_SYSTEMD_JOURNAL'}
    else:
        validate_status(value, plan)
    value.update(saved_status_only=True, plan_boot_id=plan['boot_id'], warning=WARNING)
    if not summary:
        return value
    done = value.get('completion', {})
    return {'stage': value.get('stage'), 'reason': value.get('reason'), 'plan': str(path), 'saved_status_only': True,
            'measurement_profile': PROFILE, 'single_sample_only': False, 'sample_start': plan['sample_start'],
            'sample_count': plan['sample_count'], 'total_START_budget': plan['total_START_budget'],
            'lifecycle_profile':plan.get('lifecycle_profile'),
            'verified_pending_samples':value.get('verified_pending_samples',0),
            'committed_samples': value.get('committed_samples', 0), 'completed_PL_calls': value.get('completed_PL_calls', 0),
            'current_ordinal': value.get('current_ordinal'), 'current_id': value.get('current_id'),
            'image_stage': value.get('image_stage'), 'owner_pid': value.get('owner_pid'), 'child_pid': value.get('child_pid'),
            'closed': done.get('closed', False), 'accuracy': done.get('accuracy'),
            'timings':done.get('timings'),'coordinator_total_ms':value.get('coordinator_total_ms'),
            'ORT_intra_threads': 4, 'ORT_inter_threads': 1,
            'scope': 'One bounded Dense chunk; this paired experiment targets exactly500 unique images',
            'warning': value['warning']}
