"""Prepare B's exact 1800 JPEGs; no model execution, XRT, MMIO or START.

Only JPEGs and their manifest are stored. Float32 tensors are reconstructed one
at a time, so the board does not need two copies of a 1.08 GB tensor dataset.
The original CSV order, image bytes, decoded RGB and full preprocessing digest
are all checked. Archive code and pickle objects are never executed.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
SCHEMA = 'opt50_b24_acceptance_dataset_v1'
COUNT = 1800
ARCHIVE_PIN = {'bytes': 32050705, 'sha256': '7814dfca58983d9d94cfa2920f4ccc537e1f9257a18e4852c25d0682e6e48f2a'}
LABEL_PIN = {'bytes': 339510, 'sha256': '358b50d3fd45f4d29fe4b2596f04a4db7bdd5d41a9a84f86b6616f29217cd889'}
INPUTS_SHA = '1794d6798d7b9d1856fd79ecbe2d92bf96ecef1397d6b454a2bd0eb84468131c'
SOURCE_MODEL = 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'
PREPROCESSING = {'pillow': '12.3.0', 'numpy': '1.26.4', 'RGB': True, 'EXIF_transpose': False,
                 'resize_short_edge': 248, 'long_edge_rounding': 'int_truncate', 'resample': 'BICUBIC',
                 'center_crop': 224, 'crop_rounding': 'Python_round_ties_to_even',
                 'mean': [0.485, 0.456, 0.406], 'std': [0.229, 0.224, 0.225],
                 'normalization_dtype': 'float32', 'layout': 'NCHW', 'shape': [1, 3, 224, 224]}
MAX_FILE = 64 * 1024 * 1024


def need(ok, why):
    if not ok:
        raise ValueError(why)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def decode(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            need(k not in out, 'duplicate JSON key')
            out[k] = v
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def ordinary(path):
    path = Path(path).absolute()
    need('..' not in path.parts, 'parent traversal refused')
    for part in (path, *path.parents):
        info = part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
             'linked/reparse path refused')
    return path


def read(path, limit=MAX_FILE):
    path = ordinary(path)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= limit,
         'bounded single-link regular file required')
    key = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns,
                     x.st_birthtime_ns if os.name == 'nt' else x.st_ctime_ns)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        need(key(os.fstat(stream.fileno())) == key(before), 'input replaced before read')
        raw = stream.read(limit + 1)
        need(len(raw) == before.st_size and key(os.fstat(stream.fileno())) == key(before), 'input changed during read')
    need(key(path.lstat()) == key(before), 'input replaced after read')
    return raw


def labels():
    raw = read(HERE / 'data/acceptance.csv', 1024 * 1024)
    need(identity(raw) == LABEL_PIN, 'frozen B labels changed')
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    need(len(rows) == COUNT, 'exactly 1800 frozen labels required')
    seen_images, seen_raw, seen_rgb = set(), set(), set()
    result = []
    for i, row in enumerate(rows):
        need(set(row) == {'image', 'label', 'split', 'row_index', 'image_sha256', 'decoded_rgb_sha256', 'bytes', 'source_role'},
             'frozen label schema differs')
        need(re.fullmatch(r'images/val_[0-9]{7}\.jpg', row['image']) is not None and row['split'] == 'val' and
             row['source_role'] == 'new_acceptance', 'fixed acceptance identity required')
        label, index, size = int(row['label']), int(row['row_index']), int(row['bytes'])
        need(str(label) == row['label'] and 0 <= label < 1000 and str(index) == row['row_index'] and
             row['image'] == f'images/val_{index:07d}.jpg' and 0 < size < 2*1024*1024, 'invalid acceptance row')
        for field in ('image_sha256', 'decoded_rgb_sha256'):
            need(re.fullmatch('[0-9a-f]{64}', row[field]) is not None, 'invalid label SHA')
        need(row['image'] not in seen_images and row['image_sha256'] not in seen_raw and
             row['decoded_rgb_sha256'] not in seen_rgb, 'duplicate image identity')
        seen_images.add(row['image']); seen_raw.add(row['image_sha256']); seen_rgb.add(row['decoded_rgb_sha256'])
        result.append({'ordinal': i, 'id': PurePosixPath(row['image']).stem, 'label': label,
                       'image': {'file': row['image'], 'bytes': size, 'sha256': row['image_sha256'],
                                 'decoded_rgb_sha256': row['decoded_rgb_sha256']}})
    counts = Counter(row['label'] for row in result)
    need(len(counts) == 900 and set(counts.values()) == {2}, '900 classes with two images each required')
    return result


def dependencies():
    import numpy as np
    import PIL
    from PIL import Image
    need(np.__version__ == PREPROCESSING['numpy'] and PIL.__version__ == PREPROCESSING['pillow'],
         'frozen NumPy 1.26.4 and Pillow 12.3.0 required; do not upgrade the board environment automatically')
    need(sys.byteorder == 'little', 'little-endian float32 preprocessing required')
    return np, Image


def resize_crop_box(width, height):
    need(type(width) is int and type(height) is int and width > 0 and height > 0 and width*height <= 40000000,
         'bounded decoded image dimensions required')
    if width < height:
        resized = (248, int(248*height/width))
    else:
        resized = (int(248*width/height), 248)
    need(resized[0]*resized[1] <= 40000000, 'resized image exceeds pixel bound')
    left, top = (round((dim-224)/2) for dim in resized)
    return resized, (left, top, left+224, top+224)


def preprocess(raw):
    np, Image = dependencies()
    with Image.open(io.BytesIO(raw)) as source:
        need(source.format == 'JPEG' and source.width*source.height <= 40000000, 'bounded JPEG required')
        rgb = source.convert('RGB')  # Deliberately no ImageOps.exif_transpose.
        try:
            rgb_sha = hashlib.sha256(str(rgb.size).encode() + b'\0' + rgb.tobytes()).hexdigest()
            resized, crop = resize_crop_box(*rgb.size)
            with rgb.resize(resized, Image.Resampling.BICUBIC) as scaled:
                with scaled.crop(crop) as clipped:
                    array = np.asarray(clipped, dtype=np.float32) / np.float32(255.0)
            array = (array - np.asarray(PREPROCESSING['mean'], dtype=np.float32)) / np.asarray(PREPROCESSING['std'], dtype=np.float32)
            tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None], dtype=np.float32)
            need(tensor.shape == (1, 3, 224, 224) and bool(np.isfinite(tensor).all()), 'invalid preprocessed tensor')
            stream = io.BytesIO(); np.save(stream, tensor, allow_pickle=False)
            return stream.getvalue(), tensor.tobytes(order='C'), rgb_sha
        finally:
            rgb.close()


def validate_manifest(raw):
    value = decode(raw)
    need(type(value) is dict, 'dataset manifest must be a JSON object')
    need(value.get('schema') == SCHEMA and type(value.get('count')) is int and value['count'] == COUNT and
         value.get('source_model_sha256') == SOURCE_MODEL and value.get('labels') == LABEL_PIN and
         value.get('preprocessing') == PREPROCESSING and value.get('all_inputs_raw_sha256') == INPUTS_SHA and
         value.get('hardware_executed') is False, 'fixed dataset schema/provenance differs')
    archive = value.get('source_archive', {})
    need(set(archive) == {'bytes', 'sha256'} and type(archive['bytes']) is int and 0 < archive['bytes'] <= MAX_FILE and
         re.fullmatch('[0-9a-f]{64}', archive['sha256']) is not None, 'invalid transport identity')
    need(value.get('original_archive_exact') is (archive == ARCHIVE_PIN), 'archive exact/repacked claim differs')
    samples = value.get('samples')
    need(type(samples) is list and len(samples) == COUNT, 'complete frozen selection required')
    for sample, expected in zip(samples, labels()):
        need(type(sample) is dict and set(sample) == {'ordinal', 'id', 'label', 'image', 'input'} and
             all(sample.get(k) == v and type(sample.get(k)) is type(v) for k, v in expected.items()),
             'sample identity/CSV order differs')
        need(type(sample['image']['bytes']) is int, 'JPEG size must be an integer')
        pin = sample['input']
        need(type(pin) is dict and set(pin) == {'bytes', 'sha256', 'raw_sha256'} and pin['bytes'] == 602240 and
             type(pin['bytes']) is int and all(type(pin[k]) is str and re.fullmatch('[0-9a-f]{64}', pin[k]) is not None
                                              for k in ('sha256', 'raw_sha256')), 'invalid tensor pin')
    return value


def verify_dataset(root, verify_payloads=True):
    root = ordinary(root)
    manifest_raw = read(root/'manifest.json', 4*1024*1024)
    value = validate_manifest(manifest_raw)
    files = {'manifest.json': identity(manifest_raw)}
    for item in value['samples']:
        pin = {k: item['image'][k] for k in ('bytes', 'sha256')}
        files[item['image']['file']] = pin
        if verify_payloads:
            need(identity(read(root/item['image']['file'], 2*1024*1024)) == pin, 'JPEG content differs: '+item['id'])
    if verify_payloads:
        # No extra input may silently enlarge or alter the selection.
        paths = []
        for directory, folders, names in os.walk(root, followlinks=False):
            for name in folders:
                ordinary(Path(directory)/name)
            for name in names:
                path = ordinary(Path(directory)/name)
                paths.append(path.relative_to(root).as_posix())
        need(set(paths) == set(files), 'missing or additional dataset files')
    return {'root': root, 'manifest_pin': identity(manifest_raw), 'manifest': value, 'files': files}


def materialize_input(root, sample):
    need(type(sample) is dict and type(sample.get('ordinal')) is int and 0 <= sample['ordinal'] < COUNT,
         'valid frozen sample ordinal required')
    expected = labels()[sample['ordinal']]
    need(all(sample.get(k) == v for k, v in expected.items()), 'sample selection differs from B labels')
    raw = read(Path(root)/sample['image']['file'], 2*1024*1024)
    need(identity(raw) == {k: sample['image'][k] for k in ('bytes', 'sha256')}, 'encoded JPEG changed')
    npy, pixels, rgb_sha = preprocess(raw)
    need(rgb_sha == sample['image']['decoded_rgb_sha256'], 'decoded RGB differs')
    need(identity(npy) == {k: sample['input'][k] for k in ('bytes', 'sha256')} and
         hashlib.sha256(pixels).hexdigest() == sample['input']['raw_sha256'], 'preprocessed tensor differs')
    return npy


def zip_inventory(archive, selection):
    """Reject duplicates, links, unknown members and unbounded expansion first."""
    expected = {s['image']['file']: s['image']['bytes'] for s in selection}
    expected['labels.csv'] = LABEL_PIN['bytes']
    members, seen = {}, set()
    for info in archive.infolist():
        name = info.filename
        need(name not in seen and name.casefold() not in {s.casefold() for s in seen}, 'duplicate ZIP member')
        seen.add(name)
        part = PurePosixPath(name)
        need(not any(ch in name for ch in '\\:\0\r\n') and not part.is_absolute() and
             all(x not in ('', '.', '..') for x in name.rstrip('/').split('/')), 'unsafe ZIP filename')
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        need(not info.flag_bits & 1, 'ZIP encryption refused')
        if info.is_dir():
            need(name == 'images/' and info.file_size == 0 and kind in (0, stat.S_IFDIR), 'unexpected ZIP directory')
            continue
        need(kind in (0, stat.S_IFREG), 'ZIP links and special file modes refused')
        need(name in expected and info.file_size == expected[name] and
             info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'ZIP selection/size differs')
        members[name] = info
    need(set(members) == set(expected), 'ZIP must contain the exact 1800 images and labels.csv')
    return members


def publish(path, raw):
    with Path(path).open('xb') as stream:
        need(stream.write(raw) == len(raw), 'short dataset write')
        stream.flush(); os.fsync(stream.fileno())


def prepare(archive_path, out, allow_repacked=False):
    raw = read(archive_path)
    pin = identity(raw)
    need(pin == ARCHIVE_PIN or allow_repacked, 'original acceptance.zip identity differs; repacking needs explicit --allow-repacked')
    out = Path(out).absolute()
    ordinary(out.parent)
    need(not out.exists() and not out.is_symlink(), 'new dataset directory required; do not overwrite/retry partial output')
    selection = labels()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        inventory = zip_inventory(archive, selection)
        need(identity(archive.read(inventory['labels.csv'])) == LABEL_PIN, 'ZIP labels differ from frozen CSV')
        dependencies()
        out.mkdir(mode=0o700); (out/'images').mkdir(mode=0o700)
        aggregate = hashlib.sha256(); samples = []
        for original in selection:
            item = dict(original); image = original['image']
            encoded = archive.read(inventory[image['file']])  # CRC checked by zipfile.read.
            need(identity(encoded) == {k: image[k] for k in ('bytes', 'sha256')}, 'encoded JPEG SHA differs')
            npy, pixels, rgb_sha = preprocess(encoded)
            need(rgb_sha == image['decoded_rgb_sha256'], 'decoded RGB differs from B identity')
            aggregate.update(pixels)
            item['input'] = dict(identity(npy), raw_sha256=hashlib.sha256(pixels).hexdigest())
            samples.append(item)
            publish(out/image['file'], encoded)
            if (original['ordinal']+1) % 100 == 0:
                print(json.dumps({'prepared': original['ordinal']+1, 'expected': COUNT, 'hardware_executed': False}), flush=True)
        need(aggregate.hexdigest() == INPUTS_SHA, '1800-image FP32 preprocessing aggregate differs from B')
        value = {'schema': SCHEMA, 'count': COUNT, 'source_model_sha256': SOURCE_MODEL,
                 'labels': LABEL_PIN, 'source_archive': pin, 'original_archive_exact': pin == ARCHIVE_PIN,
                 'preprocessing': PREPROCESSING, 'all_inputs_raw_sha256': aggregate.hexdigest(),
                 'samples': samples, 'hardware_executed': False}
        raw_manifest = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()
        validate_manifest(raw_manifest)
        publish(out/'manifest.json', raw_manifest)  # Final receipt appears only after ALL images/digest pass.
    result = verify_dataset(out)
    return {'status': 'DATASET_1800_IDENTITIES_AND_PREPROCESSING_PASS_NO_MODEL_OR_HARDWARE_ACCESS',
            'dataset': str(out), 'manifest': result['manifest_pin'], 'images': COUNT,
            'preprocessed_inputs_sha256': INPUTS_SHA, 'original_archive_exact': pin == ARCHIVE_PIN,
            'stored_tensor_files': 0, 'hardware_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare_cmd = sub.add_parser('prepare')
    prepare_cmd.add_argument('--archive', type=Path, required=True)
    prepare_cmd.add_argument('--out', type=Path, required=True)
    prepare_cmd.add_argument('--allow-repacked', action='store_true')
    audit_cmd = sub.add_parser('verify'); audit_cmd.add_argument('--dataset', type=Path, required=True)
    sub.add_parser('describe')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            result = prepare(args.archive, args.out, args.allow_repacked)
        elif args.action == 'verify':
            result = verify_dataset(args.dataset)
            result = {'status': 'JPEG_IDENTITIES_AND_MANIFEST_PASS_NO_HARDWARE_ACCESS', 'images': COUNT,
                      'manifest': result['manifest_pin'], 'scope': 'Encoded image and manifest audit; preprocessing was checked when prepared'}
        else:
            selected = labels()
            result = {'status': 'LABELS_READY_IMAGES_REQUIRED', 'count': len(selected), 'archive_required': ARCHIVE_PIN,
                      'labels': LABEL_PIN, 'preprocessing': PREPROCESSING, 'hardware_executed': False}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, ImportError) as error:
        print('DATASET_REFUSED_NO_MODEL_OR_HARDWARE_ACCESS: '+str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
