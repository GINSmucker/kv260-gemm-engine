"""Extract read-only display evidence from the pinned paired archive and local photos."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA = '3dd66ac7e784153ce6d479be2017f54d678870cbc02d4f02dbdc681d366f591b'
SPARSE_SHA = 'f14b1a7e3ec26fd7205e4b32ca57b0ae5ab55972cbcb353fbcb406003bc182fb'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare(archive_path, portrait, guitar, guitar_receipt):
    out = ROOT / 'results/roadshow'
    out.mkdir(exist_ok=True)
    files = {}

    def register(key, path, origin):
        raw = path.read_bytes()
        files[key] = {'path': path.relative_to(ROOT).as_posix(), 'bytes': len(raw),
                      'sha256': sha(raw), 'origin': origin}

    for key, path, origin in (
        ('portrait-image', portrait, 'Matplotlib sample_data/grace_hopper.jpg; original JPEG bytes'),
        ('guitar-image', guitar, 'Original 05_acoustic_guitar.jpg used by the saved board receipt'),
    ):
        target = out / (key + '.jpg')
        target.write_bytes(path.read_bytes())
        register(key, target, origin)

    register('portrait-report', ROOT / 'results/live_demo/sparse24_lab_20261101.json',
             'Saved local LAB check; receipt field is the original board result')
    target = out / 'guitar-report.json'
    target.write_bytes(guitar_receipt.read_bytes())
    register('guitar-report', target, 'Saved original historical board request receipt; no new execution or current deployment claim')
    register('layers', ROOT / 'board/runtime/snapshots/sparse24/model/source/B_LAYERS.json',
             'Frozen Sparse24 model layer specification')
    register('layer-case', ROOT / 'board/runtime/snapshots/layer02/data/manifest.json',
             'Original layer02 input identity; not itself a board result')
    target = out / 'verification.md'
    target.write_bytes((ROOT / 'docs/verification.md').read_bytes())
    register('verification', target,
             'Snapshot of docs/verification.md acceptance summary; original standalone layer02 result JSON is not included')

    raw = archive_path.read_bytes()
    if sha(raw) != ARCHIVE_SHA:
        raise ValueError('Paired archive SHA256 differs')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as outer:
        sparse_raw = outer.extractfile('sparse_reference/evidence.tar.gz').read()
        if sha(sparse_raw) != SPARSE_SHA:
            raise ValueError('Nested Sparse archive SHA256 differs')
        with tarfile.open(fileobj=io.BytesIO(sparse_raw), mode='r:gz') as inner:
            for model, tar in (('Dense', outer), ('Sparse24', inner)):
                prefix = next(n.rsplit('/', 1)[0]+'/' for n in tar.getnames()
                              if n.endswith('/samples/000305/accuracy.json'))
                for kind in ('accuracy', 'pending', 'commit'):
                    member = prefix + kind + '.json'
                    target = out / f'{model}-306-{kind}.json'
                    target.write_bytes(tar.extractfile(member).read())
                    register(f'{model}-{kind}', target,
                             {'archive_sha256': ARCHIVE_SHA, 'member': member,
                              'nested_archive_sha256': SPARSE_SHA if model == 'Sparse24' else None})

    manifest = {
        'schema': 'kv260_roadshow_saved_evidence_v1',
        'source': 'SAVED_BOARD_EVIDENCE', 'live': False, 'new_board_execution': False,
        'source_revision': 'bd5d8cc056a12b19f757949324986079ea675b59',
        'paired_archive_sha256': ARCHIVE_SHA,
        'files': files,
        'inference': [
            {'id': 'guitar', 'title': '吉他照片', 'image': 'guitar-image', 'report': 'guitar-report', 'receipt_field': None},
            {'id': 'portrait', 'title': '人物照片', 'image': 'portrait-image', 'report': 'portrait-report', 'receipt_field': 'receipt'},
        ],
        'layer_sample_1based': 306,
        'standalone_layer_receipt_available': False,
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return {'files': len(files), 'images': 2, 'paired_layer_sample': 306, 'hardware_accessed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paired-archive', type=Path, required=True)
    parser.add_argument('--portrait', type=Path, required=True)
    parser.add_argument('--guitar', type=Path, required=True)
    parser.add_argument('--guitar-receipt', type=Path, default=ROOT / 'results/roadshow/guitar-report.json')
    args = parser.parse_args()
    print(json.dumps(prepare(args.paired_archive, args.portrait, args.guitar, args.guitar_receipt)))
