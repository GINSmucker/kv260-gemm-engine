"""Independent saved-evidence checks; no inference, training or model changes.

Ranks with Python sorting (not eval_models helpers) and reconciles all CSV rows.
Writes one new verification receipt, refusing to overwrite prior evidence.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(folder):
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    if summary['status'] != 'COMPLETE_DEVELOPMENT_DIAGNOSIS':
        raise ValueError('No complete diagnosis')
    for name, value in summary['artifacts'].items():
        if digest(folder / name) != value:
            raise ValueError('Artifact hash mismatch: ' + name)
    for path, value in summary['source_hashes'].items():
        if digest(Path(path)) != value:
            raise ValueError('Source hash mismatch: ' + path)
    with (folder / 'predictions.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    records, ranks, per_model = {}, {}, {}
    with np.load(folder / 'logits.npz', allow_pickle=False) as saved:
        labels, images, hashes = saved['labels'], saved['images'], saved['image_sha256']
        if len(labels) != 100 or len(set(images.tolist())) != 100:
            raise ValueError('Unexpected image inventory')
        identities = [{'image': str(image), 'label': int(label), 'sha256': str(sha)}
                      for image, label, sha in zip(images, labels, hashes)]
        ordered_hash = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()
        if ordered_hash != summary['ordered_dataset_sha256']:
            raise ValueError('Dataset identity mismatch')
        for name in summary['models']:
            values = saved[name]
            if values.shape != (100, 1000) or values.dtype != np.float32 or not np.isfinite(values).all():
                raise ValueError('Invalid logits: ' + name)
            ranks[name] = [sorted(range(1000), key=lambda c: (-float(row[c]), c))[:5] for row in values]
            per_model[name] = {'top1': [rank[0] == label for rank, label in zip(ranks[name], labels)],
                               'top5': [int(label) in rank for rank, label in zip(ranks[name], labels)]}
        for name, model in summary['models'].items():
            base = per_model['dense_' + model['precision']]
            counts = {}
            for top in ('top1', 'top5'):
                good, baseline = per_model[name][top], base[top]
                correct = sum(good)
                new_errors = sum(a and not b for a, b in zip(baseline, good))
                recovered = sum(not a and b for a, b in zip(baseline, good))
                if (correct != model[top + '_correct'] or new_errors != model[top + '_new_errors']
                        or recovered != model[top + '_recovered_errors']
                        or sum(baseline) - correct != new_errors - recovered):
                    raise ValueError('Metric reconciliation failed: ' + name)
                counts[top + '_correct'] = int(correct)
            records[name] = counts
        if len(rows) != len(summary['models']) * 100:
            raise ValueError('Missing/extra prediction rows')
        seen = set()
        image_numbers = {str(image): i for i, image in enumerate(images)}
        for row in rows:
            name, image = row['model'], row['image']
            i = image_numbers[image]
            key = name, image
            if key in seen:
                raise ValueError('Duplicate prediction row')
            seen.add(key)
            if (int(row['label']) != labels[i] or row['image_sha256'] != hashes[i]
                    or int(row['top1']) != ranks[name][i][0]
                    or json.loads(row['top5']) != ranks[name][i]
                    or int(row['top1_correct']) != per_model[name]['top1'][i]
                    or int(row['top5_correct']) != per_model[name]['top5'][i]):
                raise ValueError('CSV/logits mismatch')
    return {'status': 'PASS_INDEPENDENT_SAVED_EVIDENCE_RECOMPUTATION',
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'summary_sha256': digest(folder / 'summary.json'), 'verifier_sha256': digest(Path(__file__)),
            'checked_prediction_rows': len(rows), 'checked_logit_elements': len(summary['models']) * 100000,
            'models': records, 'ordered_dataset_sha256': ordered_hash,
            'limits': 'Saved outputs and source hashes only; not a second inference, quality acceptance or board verification'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = args.result_dir / 'independent_verification.json'
        if receipt.exists():
            raise ValueError('Verification receipt exists; refusing overwrite')
        result = verify(args.result_dir)
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
