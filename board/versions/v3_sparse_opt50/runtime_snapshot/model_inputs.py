"""Verify the NEW low-I/O software build and copy the unchanged model/input.

No executable, XRT device, socket, signal, module or firmware operation here.
The dynamic ARM binary identities are frozen into the NEW root-private plan.
"""
from pathlib import Path
import struct

HERE = Path(__file__).resolve().parent
SOURCE = Path('/home/ubuntu/fpga_project/opt50_integration/fair_compare_fd96539d_v1_fix1')
ORIGINAL = Path('/home/ubuntu/fpga_project/opt50_integration/full_model_fd96539d_v1_fix1')
BUILD = 'build_arm_fair_v1'
PROFILE = 'same-weights-fair-single-shot-v1'
RELEASE = {'bytes': 13476, 'sha256': 'f1891fd4def1eab03c3a838d6da4718c226d1c98eeb5d19408e5a8679cc156d8'}
RECEIPT = {'bytes': 7871, 'sha256': '2084d4b1dd3f9d36b0a7e3c64aa444698d53fbd4c379bc1ceaec76071914224a'}
MODEL = {'bytes': 41541, 'sha256': 'ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb'}
METADATA = {'bytes': 8560, 'sha256': '715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551'}
INPUT_SHA = '2449003209f501e928cd67ed0335614accf844bdf3432a3e6146b1f836249dc9'


def need(ok, why):
    if not ok:
        raise ValueError(why)


def pins(c):
    value = c.read_json(HERE/'pins.json')
    need(value.get('schema') == 'opt50_fair_window_pins_v1' and value.get('measurement_profile') == PROFILE and
         value.get('original_source_release') == RELEASE and value.get('baseline_arm_build_receipt') == RECEIPT and
         value.get('model_manifest') == MODEL and value.get('metadata') == METADATA,
         'fixed performance/source/model pins differ')
    release_raw = c.read_file(HERE/'expected_source_release.json')
    baseline_raw = c.read_file(HERE/'expected_arm_build_receipt.json')
    perf_raw = c.read_file(HERE/'expected_performance_release.json')
    need(c.identity(release_raw) == RELEASE and c.identity(baseline_raw) == RECEIPT and
         c.identity(perf_raw) == value['performance_release'], 'source/compiler evidence changed')
    old, baseline, perf = map(c.decode_json, (release_raw, baseline_raw, perf_raw))
    need(old.get('schema') == 'opt50_model_software_release_v1' and len(old['files']) == 90 and
         baseline.get('status') == 'ARM_BUILD_PASS_NOT_BOARD_RUN' and
         perf.get('schema') == 'opt50_fair_software_release_v1' and perf.get('measurement_profile') == PROFILE and
         perf.get('hardware_executed') is False and type(perf.get('files')) is dict,
         'old model or new performance software schema differs')
    return value, old, baseline, perf


def elf(raw):
    need(len(raw) >= 64 and raw[:7] == b'\x7fELF\x02\x01\x01' and
         struct.unpack_from('<H', raw, 18)[0] == 183 and struct.unpack_from('<H', raw, 16)[0] in (2, 3),
         'AArch64 ELF64 little-endian binary required')


def validate_build(c, raw):
    value, _, baseline, perf = pins(c)
    build = c.decode_json(raw)
    need(build.get('supported_execution_modes') == ['all_dense', 'mixed'] and
         build.get('active_data_capacity_bytes') == 3981312, 'same binary/two modes/equal BO capacity build required')
    sources = {name: pin for name, pin in perf['files'].items()
               if name.startswith('source/') or name in ('test_model_core.cpp', 'test_performance_core.cpp')}
    need(build.get('schema') == 'opt50_fair_arm_build_v1' and
         build.get('status') == 'ARM_PERFORMANCE_BUILD_PASS_NOT_BOARD_RUN' and
         build.get('source_manifest') == value['performance_release'] and build.get('sources') == sources and
         build.get('measurement_profile') == PROFILE and build.get('hot_path_disk_logging') is False and
         build.get('poll_delay_us') == 50 and build.get('per_layer_deadline_seconds') == 120 and
         build.get('graph_owner_sync_calls') == 0 and build.get('graph_owner_payload_access') is False and
         build.get('full_validation_scope') == 'Initialization and post-timer INFO after client close; not per-layer or END' and
         build.get('model_manifest') == MODEL and build.get('hardware_executed') is False and
         build.get('native_owner_executed') is False and build.get('compiler') == baseline['compiler'],
         'performance ARM build does not bind the uploaded source, unchanged model and established compiler')
    commands = build.get('commands')
    need(type(commands) is list and len(commands) >= 3 and all(type(row) is list and row and
         row[0] == baseline['compiler']['path'] and
         all(flag in row for flag in ('-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                                     '-DKV5_DIAGNOSTIC', '-DOPT50_PERFORMANCE_LIGHT=1', '-DOPT50_H576_ALIGNED=1', '-DOPT50_FAIR_COMPARE=1', '-fno-fast-math', '-ffp-contract=off')) and
         not any(arg in row for arg in ('-ffast-math', '-Ofast')) for row in commands),
         'strict arithmetic/low-I/O performance compile flags differ')
    need(type(build.get('binaries')) is dict and
         {'opt50_model_owner', 'libopt50_model.so', 'model_core_selftest', 'performance_core_selftest'} == set(build['binaries']) and
         all(type(pin) is dict and set(pin) == {'bytes', 'sha256'} for pin in build['binaries'].values()),
         'performance binary inventory differs')
    # The build script's CPU-only tests must have run, not its Native owner.
    test = build.get('CPU_core_self_test')
    need(type(test) is dict and test.get('returncode') == 0 and test.get('stderr') == '' and
         'PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS' in test.get('stdout', '') and
         'MODEL_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS' in test.get('stdout', ''),
         'successful unchanged-pack CPU self-test is required')
    extra = build.get('performance_core_self_test')
    need(type(extra) is dict and extra.get('returncode') == 0 and extra.get('stderr') == '' and
         extra.get('stdout') == 'PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n',
         'successful CPU-only performance/deadline/receipt self-test is required')
    return build


def validate_source(c, tools, folder=None):
    source = SOURCE if folder is None else Path(folder)
    value, old, baseline, perf = pins(c)
    need(c.identity(c.read_file(source/'source_manifest.json')) == value['performance_release'],
         'new performance source manifest changed')
    for name, pin in perf['files'].items():
        need(c.identity(c.read_file(source/name)) == pin, 'new performance source changed: '+name)
    found = set()
    for path in (source/'source').rglob('*'):
        c.no_links(path)
        need(path.is_file() or path.is_dir(), 'nonregular performance source entry')
        if path.is_file():
            found.add(path.relative_to(source).as_posix())
    need(found == {n for n in perf['files'] if n.startswith('source/')}, 'extra performance source entry')
    build_raw = c.read_file(source/BUILD/'build_receipt.json')
    build = validate_build(c, build_raw)
    compiler = Path('/usr/bin/g++-13').resolve(strict=True)
    info = compiler.stat()
    need(str(compiler) == baseline['compiler']['path'] and info.st_uid == 0 and not info.st_mode & 0o022 and
         c.identity(c.read_file(compiler)) == {k: baseline['compiler'][k] for k in ('bytes', 'sha256')},
         'installed established compiler changed')
    for name, pin in build['binaries'].items():
        raw = c.read_file(source/BUILD/name)
        need(c.identity(raw) == pin, 'compiled ARM binary changed: '+name)
        elf(raw)
    need(c.identity(c.read_file(ORIGINAL/'tools_manifest.json')) == RELEASE,
         'original model source manifest changed')
    for name, pin in old['files'].items():
        if name.startswith('model/') or name == 'inputs/zero_input.npy':
            need(c.identity(c.read_file(ORIGINAL/name)) == pin, 'unchanged model/input changed: '+name)
    metadata = Path(c.read_json(c.OLD_PLAN, private=True)['artifacts']['source_paths']['metadata'])
    need(c.identity(c.read_file(metadata)) == METADATA, 'resident design metadata identity differs')
    return {'source_root': str(source), 'metadata_source': str(metadata),
            'source_release': value['performance_release'], 'original_model_release': RELEASE,
            'board_build': {'receipt': c.identity(build_raw), 'binaries': build['binaries']}}


def layout(c, root, build_raw):
    root = Path(root)
    value, old, _, _ = pins(c)
    build = validate_build(c, build_raw)
    files = {name: pin for name, pin in old['files'].items() if name.startswith('model/')}
    need(len(files) == 58 and files['model/manifest.json'] == MODEL and
         old['files']['inputs/zero_input.npy']['sha256'] == INPUT_SHA, 'fixed 58-file model or identical input differs')
    files.update({'native_owner': build['binaries']['opt50_model_owner'],
                  'plugin.so': build['binaries']['libopt50_model.so'], 'metadata.xclbin': METADATA,
                  'input_zero.npy': old['files']['inputs/zero_input.npy'],
                  'arm_build_receipt.json': c.identity(build_raw),
                  'performance_source_manifest.json': value['performance_release']})
    return {'model_root': str(root/'model'), 'plugin': str(root/'plugin.so'), 'input': str(root/'input_zero.npy'),
            'metadata': str(root/'metadata.xclbin'), 'owner': str(root/'native_owner'), 'files': files,
            'board_build': {'receipt': c.identity(build_raw), 'binaries': build['binaries']},
            'source_release': value['performance_release']}


def copy_inputs(c, durable, summary, plan_root):
    root, source = Path(plan_root), Path(summary['source_root'])
    build_raw = c.read_file(source/BUILD/'build_receipt.json')
    expected = layout(c, root, build_raw)
    need(summary['board_build'] == expected['board_build'] and summary['source_release'] == expected['source_release'],
         'source build changed before root copies')
    durable.mkdir_new(root/'model')
    for name, pin in sorted(expected['files'].items(), key=lambda row: (row[0].count('/'), row[0])):
        target = root/name
        for parent in reversed(target.parents):
            if parent.is_relative_to(root) and not parent.exists():
                durable.mkdir_new(parent)
        if name.startswith('model/'):
            origin = ORIGINAL/name
        else:
            origin = {'native_owner': source/BUILD/'opt50_model_owner', 'plugin.so': source/BUILD/'libopt50_model.so',
                      'metadata.xclbin': Path(summary['metadata_source']), 'input_zero.npy': ORIGINAL/'inputs/zero_input.npy',
                      'arm_build_receipt.json': source/BUILD/'build_receipt.json',
                      'performance_source_manifest.json': source/'source_manifest.json'}[name]
        durable.copy_new(origin, target, pin, mode=0o700 if name == 'native_owner' else 0o600)
    return expected


def validate_copied(c, plan):
    root = Path(plan['plan_path']).parent
    need(plan.get('measurement_profile') == PROFILE, 'not the one-shot low-I/O profile')
    expected = layout(c, root, c.read_file(root/'arm_build_receipt.json', private=True))
    need(plan['inputs'] == expected, 'copied performance inputs/build evidence differs')
    for name, pin in expected['files'].items():
        raw = c.read_file(root/name, private=True)
        need(c.identity(raw) == pin, 'copied performance input changed: '+name)
        if name in ('native_owner', 'plugin.so'):
            elf(raw)
    found = set()
    for path in (root/'model').rglob('*'):
        c.no_links(path)
        need(path.is_file() or path.is_dir(), 'nonregular copied model entry')
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
    need(found == {n for n in expected['files'] if n.startswith('model/')}, 'copied model inventory changed')
