"""Read-only dispatch to the current or one explicitly pinned batch release.

The predecessor manifest is data, never an authority learned from a plan. All
root-copied source/model files are audited before executing its read-only entry.
Unknown releases, changed files, links, and non-private records fail closed.
"""
from __future__ import annotations

import hashlib
import ast
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import types

HERE = Path(__file__).absolute().parent
RECORDS = Path('/var/lib/opt50-b24-model-v1')
PREDECESSOR = {'bytes': 18332, 'sha256': '958721b66f4fee9f8be69dcb02f8eded7294c3bbf09689375c07414301a58b47'}
PREDECESSOR_V4 = {'bytes':21974,'sha256':'58580186e4540eeb33c97c411a2d517793e9f752d3fa5e58302cfab80e7a1407'}
PREDECESSOR_V4_FIX1 = {'bytes':23152,'sha256':'03d0b13bb25bc04dbffd561e08267812db8bdd6697ad474ee0b2f40294773fb1'}
PREDECESSOR_H1 = {'bytes':24547,'sha256':'8f256e8eca931b80ea9513c62f0921ec0700558c5604476ea68902833411bd1e'}
PREDECESSOR_V3 = {'bytes':20569,'sha256':'462729154ae3bb353cd8a88f238c5a9df400244d44913dbc81b1c0478a21ceb6'}
PREDECESSOR_V2 = {'bytes': 19255, 'sha256': '3935c6504c0b053a961036b729f967365c42c926ba75f73adb07d5807420926d'}
MANIFEST_SCHEMA = 'opt50_b24_batch_tools_v1'
PLAN_SCHEMA = 'opt50_b24_acceptance_batch_plan_v1'
PROFILE = 'b24-batch-acceptance-cpu4-pl4-v1'
SOURCE_MODEL = 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
MODEL_PIN = {'bytes': 80704, 'sha256': '91ed1cd0a8b55cfde7e1a82403f879f1e225ae4c96cc47272f8dd92304a19f60'}
DENSE_FUNCTIONS = {
    ('cpu_dense_baseline.py', 'run'): 'c669e0c1b2a65120c8484cdcf476917b352cbdf3c5d79f6c20480d9eb87a31d8',
    ('model_runtime.py', 'dependencies'): '14766421f743ab78cfba69e7b2f164611061e30dbc02183ecaa91c3f993275b2',
    ('model_runtime.py', 'session_options'): '52e0414252bf41e634f79f8d710952496c391de66b1e54d036e8ba0a949e4a87',
}


def need(ok, why):
    if not ok:
        raise ValueError(why)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def decode(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'duplicate compatibility JSON key')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite compatibility JSON')))


def relative(name):
    need(type(name) is str and name and not any(c in name for c in '\\:\0\r\n'), 'unsafe release path')
    value = PurePosixPath(name)
    need(str(value) == name and not value.is_absolute() and
         all(part not in ('', '.', '..') for part in name.split('/')), 'unsafe release path')
    return value.parts


def ordinary(path):
    path = Path(path)
    need('..' not in path.parts, 'parent traversal refused')
    path = path.absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
             'linked compatibility path refused')
    return path


def private_dir(path):
    path = ordinary(path); info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
         'root private compatibility directory required')
    return path


def read_file(path, private=False, limit=64*1024*1024):
    path = ordinary(path); before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= limit,
         'single-link bounded compatibility input required')
    if private:
        need(before.st_uid == 0 and not before.st_mode & 0o077, 'root private compatibility input required')
    def key(value):
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                value.st_birthtime_ns if os.name == 'nt' else value.st_ctime_ns)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        need(key(os.fstat(stream.fileno())) == key(before), 'compatibility input replaced before read')
        raw = stream.read(limit+1)
        need(len(raw) == before.st_size and key(os.fstat(stream.fileno())) == key(before),
             'compatibility input changed during read')
    need(key(path.lstat()) == key(before), 'compatibility input replaced after read')
    return raw


def predecessor_manifest(folder=HERE, *, version='predecessor_v1'):
    need(version in ('predecessor_v1', 'predecessor_v2', 'predecessor_v3', 'predecessor_v4', 'predecessor_v4_fix1', 'predecessor_h1'), 'unknown predecessor version')
    name, expected = ('predecessor_tools_manifest.json', PREDECESSOR) if version == 'predecessor_v1' else ('predecessor_v2_tools_manifest.json', PREDECESSOR_V2)
    if version == 'predecessor_v3': name,expected='predecessor_v3_tools_manifest.json',PREDECESSOR_V3
    if version == 'predecessor_v4': name,expected='predecessor_v4_tools_manifest.json',PREDECESSOR_V4
    if version == 'predecessor_v4_fix1': name,expected='predecessor_v4_fix1_tools_manifest.json',PREDECESSOR_V4_FIX1
    if version == 'predecessor_h1': name,expected='predecessor_h1_tools_manifest.json',PREDECESSOR_H1
    raw = read_file(Path(folder)/name, limit=1024*1024)
    need(identity(raw) == expected, 'fixed predecessor manifest changed')
    value = decode(raw)
    validate_manifest(value)
    return value


def predecessor_version(pin):
    for version,expected in (('predecessor_v1',PREDECESSOR),('predecessor_v2',PREDECESSOR_V2),
                             ('predecessor_v3',PREDECESSOR_V3),('predecessor_v4',PREDECESSOR_V4),
                             ('predecessor_v4_fix1',PREDECESSOR_V4_FIX1),('predecessor_h1',PREDECESSOR_H1)):
        if pin==expected: return version
    raise ValueError('unknown pinned predecessor release')


def validate_manifest(value):
    need(type(value) is dict and value.get('schema') == MANIFEST_SCHEMA and
         value.get('hardware_executed') is False and value.get('source_model_sha256') == SOURCE_MODEL and
         type(value.get('files')) is dict and 1 <= len(value['files']) <= 256,
         'unknown compatibility tool inventory')
    needed = {'run_model.py', 'model_runtime.py', 'model_worker.py', 'dataset.py', 'accuracy_metrics.py',
              'support_py/resident.py', 'support_py/durable.py', 'support_py/model_proof.py', 'model/manifest.json'}
    need(needed <= value['files'].keys() and value['files']['model/manifest.json'] == MODEL_PIN,
         'compatibility model/profile inventory differs')
    for name, pin in value['files'].items():
        relative(name)
        need(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and type(pin['bytes']) is int and
             0 <= pin['bytes'] <= 64*1024*1024 and type(pin['sha256']) is str and
             re.fullmatch('[a-f0-9]{64}', pin['sha256']) is not None, 'invalid compatibility file pin')


def dense_source(pin, current_tools, *, folder=HERE):
    """Admit v1 CPU results only when the numerical contract is unchanged.

    The current caller has already audited its release. The predecessor JSON
    supplies exact model/C++/preprocessing/metric pins; three fixed AST digests
    bind the unchanged inference loop and ORT dependency/thread configuration.
    This grants only read-only result review, never old-plan execution.
    """
    if pin == current_tools['manifest']:
        return 'current'
    need(pin in (PREDECESSOR, PREDECESSOR_V2, PREDECESSOR_V3, PREDECESSOR_V4, PREDECESSOR_V4_FIX1, PREDECESSOR_H1), 'Dense CPU result uses unknown coordinator release')
    version = predecessor_version(pin)
    old = predecessor_manifest(folder, version=version)
    stable = lambda name: name.startswith(('model/', 'source/headers/')) or name in {'source/quant_neon.h','source/route.h','source/gelu4.h','source/layout4.h','source/qkv_dequant.h','source/picosha2.h'} or name in {
        'dataset.py', 'accuracy_metrics.py'}
    expected = {name: value for name, value in old['files'].items() if stable(name)}
    actual = {name: value for name, value in current_tools['files'].items() if stable(name)}
    need(actual == expected, 'Dense predecessor numerical model/source/preprocessing differs')
    parsed = {}
    for (name, function), wanted in DENSE_FUNCTIONS.items():
        if name not in parsed:
            raw = read_file(Path(folder)/name)
            need(identity(raw) == current_tools['files'][name], 'Dense numerical helper changed')
            parsed[name] = ast.parse(raw)
        matches = [node for node in parsed[name].body if isinstance(node, ast.FunctionDef) and node.name == function]
        need(len(matches) == 1 and hashlib.sha256(ast.dump(matches[0], include_attributes=False).encode()).hexdigest() == wanted,
             'Dense inference/thread contract differs from predecessor')
    return version


def load_closed_entry(plan_path, current_tools, *, access=None):
    """Return a fully audited read-only entry, root, and small release identity.

    current_tools is supplied by the already audited caller. The sole accepted
    alternative is PREDECESSOR; neither plans nor user paths can add versions.
    Access injection is for the caller's existing strict private reader.
    """
    path = Path(plan_path)
    need('..' not in path.parts, 'parent traversal refused')
    path = path.absolute(); root = path.parent
    records = RECORDS.absolute()
    need(path.name == 'plan.json' and root.parent == records and
         re.fullmatch('plan_[a-f0-9]{12}', root.name) is not None, 'canonical private chunk plan required')
    reader = access.read_file if access is not None else read_file
    directory = access.private_dir if access is not None else private_dir
    directory(records); directory(root); directory(root/'tools'); directory(root/'model')
    tools = root/'tools'
    raw_manifest = reader(tools/'tools_manifest.json', private=True, limit=1024*1024)
    pin = identity(raw_manifest)
    need(type(current_tools) is dict and set(current_tools) == {'manifest', 'files'}, 'audited current tools required')
    if pin == current_tools['manifest']:
        version = 'current'
    else:
        need(pin in (PREDECESSOR, PREDECESSOR_V2, PREDECESSOR_V3, PREDECESSOR_V4, PREDECESSOR_V4_FIX1, PREDECESSOR_H1), 'chunk used unknown coordinator release')
        version = predecessor_version(pin)
    manifest = decode(raw_manifest); validate_manifest(manifest)
    if version == 'current':
        need(manifest['files'] == current_tools['files'], 'current tool inventory differs')
    # Validate every file, including tests/docs and the entire numerical model,
    # before allowing even top-level predecessor Python code to execute.
    checked_dirs = {records, root, tools, root/'model'}
    entry_raw = None
    for name, expected in manifest['files'].items():
        parts = relative(name)
        target = root.joinpath(*parts) if parts[0] == 'model' else tools.joinpath(*parts)
        for parent in target.parents:
            if parent == root: break
            if parent not in checked_dirs:
                directory(parent); checked_dirs.add(parent)
        raw = reader(target, private=True, limit=64*1024*1024)
        need(identity(raw) == expected, 'root-copied release changed: '+name)
        if name == 'run_model.py': entry_raw = raw
    need(reader(tools/'tools_manifest.json', private=True, limit=1024*1024) == raw_manifest,
         'tool inventory changed during compatibility audit')
    entry = types.ModuleType('_b24_closed_'+root.name)
    entry.__file__ = str(tools/'run_model.py')
    exec(compile(entry_raw, entry.__file__, 'exec'), entry.__dict__)
    need(entry.PLAN_SCHEMA == PLAN_SCHEMA and entry.PROFILE == PROFILE and entry.SOURCE_MODEL == SOURCE_MODEL and
         entry.RECORDS.absolute() == records and callable(entry.read_completed_records), 'read-only release contract differs')
    return entry, root, {'release': version, 'tools_manifest': pin}
