"""CPU-only selection, JPEG, normalization and publication checks."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
import dataset as d
import numpy as np
from PIL import Image


def jpeg(size=(256, 260), orientation=None):
    y, x = np.indices((size[1], size[0]))
    arr = np.stack((x % 256, y % 256, (x+2*y) % 256), axis=-1).astype('uint8')
    stream = io.BytesIO()
    with Image.fromarray(arr) as image:
        exif = Image.Exif()
        if orientation is not None:
            exif[274] = orientation
        image.save(stream, format='JPEG', exif=exif)
    return stream.getvalue()


class DatasetTests(unittest.TestCase):
    def test_original_labels_order_and_class_mapping(self):
        rows = d.labels()
        self.assertEqual(len(rows), 1800)
        self.assertEqual([x['id'] for x in rows[:2]], ['val_0000040', 'val_0000014'])
        self.assertEqual(max(x['label'] for x in rows), 999)
        self.assertEqual(sum(x['image']['bytes'] for x in rows), 31495077)

    def test_crop_odd_sizes_ties_even_both_axes(self):
        self.assertEqual(d.resize_crop_box(256, 258), ((248, 249), (12, 12, 236, 236)))
        self.assertEqual(d.resize_crop_box(256, 260), ((248, 251), (12, 14, 236, 238)))
        self.assertEqual(d.resize_crop_box(260, 256), ((251, 248), (14, 12, 238, 236)))
        for size in ((0, 100), (100, -1), (100000, 100000), (1.0, 2), (1, 100000)):
            with self.assertRaises(ValueError):
                d.resize_crop_box(*size)

    def test_no_exif_rotation_and_rgb_size_in_hash(self):
        raw = jpeg((256, 260), orientation=6)
        npy, tensor, rgb_sha = d.preprocess(raw)
        with Image.open(io.BytesIO(raw)) as source:
            rgb = source.convert('RGB')
            self.assertEqual(rgb_sha, hashlib.sha256(b'(256, 260)\0'+rgb.tobytes()).hexdigest())
            self.assertNotEqual(rgb_sha, hashlib.sha256(rgb.tobytes()).hexdigest())
            resized = rgb.resize((248, 251), Image.Resampling.BICUBIC).crop((12, 14, 236, 238))
            # Independent scalar-channel normalization preserves FP32 operations.
            expected = np.array(resized, dtype=np.float32) / np.float32(255)
            for channel, (mean, std) in enumerate(zip((.485, .456, .406), (.229, .224, .225))):
                expected[:, :, channel] = (expected[:, :, channel]-np.float32(mean)) / np.float32(std)
            expected = np.ascontiguousarray(expected.transpose(2, 0, 1)[None])
        actual = np.load(io.BytesIO(npy), allow_pickle=False)
        self.assertEqual(actual.dtype, np.dtype('float32'))
        self.assertEqual(actual.shape, (1, 3, 224, 224))
        self.assertEqual(len(npy), 602240)
        self.assertEqual(actual.tobytes(), tensor)
        self.assertEqual(tensor, expected.tobytes())

    def test_png_not_accepted_as_jpeg(self):
        stream = io.BytesIO()
        with Image.new('RGB', (256, 256)) as image:
            image.save(stream, 'PNG')
        with self.assertRaises(ValueError):
            d.preprocess(stream.getvalue())

    def fixture(self, root):
        labels = b'fixture labels\n'
        selection, digest = [], hashlib.sha256()
        for i, size in enumerate(((256, 260), (260, 256))):
            raw = jpeg(size)
            npy, pixels, rgb_sha = d.preprocess(raw)
            image = dict(d.identity(raw), decoded_rgb_sha256=rgb_sha, file=f'images/val_{i:07d}.jpg')
            selection.append({'ordinal': i, 'id': f'val_{i:07d}', 'label': i, 'image': image})
            digest.update(pixels)
        archive = root/'acceptance.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as z:
            z.writestr('labels.csv', labels)
            for s, size in zip(selection, ((256, 260), (260, 256))):
                z.writestr(s['image']['file'], jpeg(size))
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(d, 'COUNT', 2))
        stack.enter_context(patch.object(d, 'labels', return_value=selection))
        stack.enter_context(patch.object(d, 'LABEL_PIN', d.identity(labels)))
        stack.enter_context(patch.object(d, 'ARCHIVE_PIN', d.identity(archive.read_bytes())))
        stack.enter_context(patch.object(d, 'INPUTS_SHA', digest.hexdigest()))
        return stack, archive, selection

    def test_prepare_all_identity_checks_and_lazy_materialization(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stack, archive, selection = self.fixture(root)
            with stack:
                result = d.prepare(archive, root/'dataset')
                self.assertFalse(result['hardware_executed'])
                bundle = d.verify_dataset(root/'dataset')
                self.assertEqual(len(bundle['files']), 3)
                self.assertFalse(list((root/'dataset').rglob('*.npy')))
                for s in bundle['manifest']['samples']:
                    npy = d.materialize_input(root/'dataset', s)
                    self.assertEqual(d.identity(npy), {k: s['input'][k] for k in ('bytes', 'sha256')})
                with self.assertRaises(ValueError):
                    d.prepare(archive, root/'dataset')
                (root/'dataset/extra').write_bytes(b'unknown')
                with self.assertRaisesRegex(ValueError, 'additional'):
                    d.verify_dataset(root/'dataset')

    def test_changed_jpeg_refused_and_no_silent_hash_replacement(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stack, archive, _ = self.fixture(root)
            with stack:
                d.prepare(archive, root/'dataset')
                bundle = d.verify_dataset(root/'dataset')
                item = bundle['manifest']['samples'][0]
                (root/'dataset'/item['image']['file']).write_bytes(jpeg((260, 256)))
                with self.assertRaises(ValueError):
                    d.materialize_input(root/'dataset', item)
                with self.assertRaises(ValueError):
                    d.verify_dataset(root/'dataset')

    def test_aggregate_mismatch_never_publishes_success_manifest(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stack, archive, _ = self.fixture(root)
            with stack, patch.object(d, 'INPUTS_SHA', 'f'*64):
                with self.assertRaisesRegex(ValueError, 'aggregate'):
                    d.prepare(archive, root/'partial')
                self.assertFalse((root/'partial/manifest.json').exists())

    def test_zip_member_safety_duplicates_extras_links_missing(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stack, archive, selection = self.fixture(root)
            with stack:
                raw = archive.read_bytes()
                with zipfile.ZipFile(io.BytesIO(raw)) as good:
                    contents = [(i.filename, good.read(i)) for i in good.infolist()]
                for bad in ('../escape', '/absolute', 'images/../escape', 'images\\escape', 'extra.jpg'):
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, 'w') as z:
                        for n, b in contents:
                            z.writestr(n, b)
                        z.writestr(bad, b'x')
                    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as z, self.assertRaises(ValueError):
                        d.zip_inventory(z, selection)
                buffer = io.BytesIO()
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    with zipfile.ZipFile(buffer, 'w') as z:
                        for n, b in contents+[contents[0]]:
                            z.writestr(n, b)
                with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as z, self.assertRaises(ValueError):
                    d.zip_inventory(z, selection)
                with zipfile.ZipFile(io.BytesIO(raw)) as z, self.assertRaises(ValueError):
                    d.zip_inventory(z, selection[:1])
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, 'w') as z:
                    for n, b in contents[1:]:
                        z.writestr(n, b)
                with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as z, self.assertRaises(ValueError):
                    d.zip_inventory(z, selection)
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, 'w') as z:
                    for n, b in contents:
                        info = zipfile.ZipInfo(n)
                        info.create_system = 3
                        info.external_attr = (0o120777 << 16)
                        z.writestr(info, b)
                with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as z, self.assertRaises(ValueError):
                    d.zip_inventory(z, selection)

    def test_manifest_changes_cannot_relabel_or_reorder_images(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stack, archive, _ = self.fixture(root)
            with stack:
                d.prepare(archive, root/'dataset')
                original = d.decode((root/'dataset/manifest.json').read_bytes())
                for kind in ('label', 'ordinal', 'file', 'encoded', 'rgb', 'order', 'preprocess', 'aggregate'):
                    bad = json.loads(json.dumps(original))
                    if kind == 'label': bad['samples'][0]['label'] = 99
                    if kind == 'ordinal': bad['samples'][0]['ordinal'] = True
                    if kind == 'file': bad['samples'][0]['image']['file'] = '../bad'
                    if kind == 'encoded': bad['samples'][0]['image']['sha256'] = 'f'*64
                    if kind == 'rgb': bad['samples'][0]['image']['decoded_rgb_sha256'] = 'f'*64
                    if kind == 'order': bad['samples'].reverse()
                    if kind == 'preprocess': bad['preprocessing']['EXIF_transpose'] = True
                    if kind == 'aggregate': bad['all_inputs_raw_sha256'] = 'f'*64
                    with self.subTest(kind=kind), self.assertRaises(ValueError):
                        d.validate_manifest(json.dumps(bad).encode())

    def test_repacked_archive_must_be_explicit_and_still_match_every_image(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stack, archive, _ = self.fixture(root)
            with stack:
                with zipfile.ZipFile(archive, 'a') as z:
                    z.comment = b'different transport only'
                with self.assertRaises(ValueError):
                    d.prepare(archive, root/'rejected')
                result = d.prepare(archive, root/'repacked', allow_repacked=True)
                self.assertFalse(result['original_archive_exact'])
                self.assertEqual(result['preprocessed_inputs_sha256'], d.INPUTS_SHA)

    def test_duplicate_json_and_nonfinite_refused(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(ValueError):
                d.decode(raw)
        for raw in (b'[]', b'null', b'123'):
            with self.assertRaises(ValueError):
                d.validate_manifest(raw)


if __name__ == '__main__':
    unittest.main()
