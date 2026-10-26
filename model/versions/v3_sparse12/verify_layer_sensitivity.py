"""Independently recompute saved layer tests and the provisional selection.

Reads evidence only; creates a NEW receipt. No inference or model modification.
Uses Python sorting rather than the evaluation/ranking helpers.
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
    summary_path = folder / 'summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    if summary['status'] != 'COMPLETE_DIAGNOSIS_PROPOSAL_NOT_APPLIED':
        raise ValueError('No complete layer diagnosis')
    for name, expected in summary['artifacts'].items():
        path = (folder / name).resolve(strict=True)
        if not path.is_relative_to(folder.resolve()) or digest(path) != expected:
            raise ValueError('Artifact identity mismatch: ' + name)
    for filename, expected in summary['source_hashes'].items():
        if digest(Path(filename)) != expected:
            raise ValueError('Protected source changed: ' + filename)
    ranks, correct, deviations = {}, {}, {}
    with np.load(folder / 'logits.npz', allow_pickle=False) as saved:
        labels, images, hashes = saved['labels'], saved['images'], saved['image_sha256']
        if len(labels) != 100 or len(set(images.tolist())) != 100:
            raise ValueError('Unexpected image inventory')
        identities = [{'image': str(image), 'label': int(label), 'sha256': str(sha)}
                      for image, label, sha in zip(images, labels, hashes)]
        dataset_hash = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()
        if dataset_hash != summary['ordered_dataset_sha256']:
            raise ValueError('Dataset identity mismatch')
        for name, record in summary['models'].items():
            values = saved[name]
            if values.dtype != np.float32 or values.shape != (100, 1000) or not np.isfinite(values).all():
                raise ValueError('Invalid logits: ' + name)
            ranks[name] = [sorted(range(1000), key=lambda c: (-float(row[c]), c))[:5] for row in values]
            correct[name] = {1: [rank[0] == label for rank, label in zip(ranks[name], labels)],
                             5: [int(label) in rank for rank, label in zip(ranks[name], labels)]}
            base = saved['dense_' + record['precision']].astype(np.float64)
            deviations[name] = float(np.linalg.norm(values.astype(np.float64) - base) / np.linalg.norm(base))
        inventory = [(r['mode'], r.get('layer_index'), r['precision']) for r in summary['models'].values()]
        for mode in ('single', 'restore'):
            for precision in ('fp32', 'w8a8'):
                entries = [i for m, i, p in inventory if m == mode and p == precision]
                if len(entries) != 36 or len(set(entries)) != 36:
                    raise ValueError('Incomplete layer intervention inventory')
        for name, record in summary['models'].items():
            metric = record['metrics']
            dense = correct['dense_' + record['precision']]
            for top in (1, 5):
                good, base = correct[name][top], dense[top]
                new = sum(a and not b for a, b in zip(base, good))
                recovered = sum(not a and b for a, b in zip(base, good))
                if (sum(good) != metric[f'top{top}_correct'] or new != metric[f'top{top}_new_errors']
                        or recovered != metric[f'top{top}_recovered_errors']
                        or sum(base) - sum(good) != metric[f'top{top}_drop_pp']):
                    raise ValueError('Dense comparison mismatch: ' + name)
                if record['mode'] == 'restore':
                    pl36 = correct['pl36_' + record['precision']][top]
                    if sum(good) - sum(pl36) != metric[f'top{top}_gain_vs_pl36_pp']:
                        raise ValueError('Restoration gain mismatch: ' + name)
            if not np.isclose(deviations[name], metric['logit_relative_l2_vs_dense'], rtol=1e-12, atol=0):
                raise ValueError('Logit deviation mismatch: ' + name)
    with (folder / 'predictions.csv').open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        seen, image_numbers = set(), {str(image): i for i, image in enumerate(images)}
        for row in reader:
            name, image = row['model'], row['image']
            index = image_numbers[image]
            key = name, image
            if key in seen:
                raise ValueError('Duplicate prediction row')
            seen.add(key)
            if (row['image_sha256'] != hashes[index] or int(row['label']) != labels[index]
                    or int(row['top1']) != ranks[name][index][0]
                    or json.loads(row['top5']) != ranks[name][index]
                    or int(row['top1_correct']) != correct[name][1][index]
                    or int(row['top5_correct']) != correct[name][5][index]):
                raise ValueError('Prediction CSV mismatch')
        if len(seen) != len(summary['models']) * 100:
            raise ValueError('Incomplete prediction CSV')
    policy = json.loads((folder / 'selection_policy.json').read_text(encoding='utf-8'))
    layers = json.loads((folder / 'layers.json').read_text(encoding='utf-8'))
    proposal = json.loads((folder / 'proposal.json').read_text(encoding='utf-8'))
    eligible, high_risk = [], []
    for layer in layers:
        i = layer['layer_index']
        s = [summary['models'][f'single_L{i:02d}_{p}']['metrics'] for p in ('fp32', 'w8a8')]
        r = [summary['models'][f'restore_L{i:02d}_{p}']['metrics'] for p in ('fp32', 'w8a8')]
        for p, single, restore in zip(('fp32', 'w8a8'), s, r):
            if single != layer['single'][p] or restore != layer['restore'][p]:
                raise ValueError('Layer/summary records differ')
        if any(max(a['top1_drop_pp'], b['top1_gain_vs_pl36_pp']) >= policy['dense_priority_top1_pp']
               or max(a['top5_drop_pp'], b['top5_gain_vs_pl36_pp']) >= policy['dense_priority_top5_pp']
               for a, b in zip(s, r)):
            high_risk.append(i)
        passed = all(a['top1_drop_pp'] <= policy['single_max_top1_drop_pp']
                     and a['top5_drop_pp'] <= policy['single_max_top5_drop_pp']
                     and a['top1_new_errors'] <= policy['single_max_top1_new_errors']
                     and b['top1_gain_vs_pl36_pp'] <= policy['restore_max_top1_gain_pp']
                     and b['top5_gain_vs_pl36_pp'] <= policy['restore_max_top5_gain_pp']
                     for a, b in zip(s, r))
        if passed:
            rank = (max(a['top1_new_errors'] for a in s),
                    max([0] + [a['top1_drop_pp'] for a in s] + [b['top1_gain_vs_pl36_pp'] for b in r]),
                    max([0] + [a['top5_drop_pp'] for a in s] + [b['top5_gain_vs_pl36_pp'] for b in r]),
                    max(a['logit_relative_l2_vs_dense'] for a in s), i)
            eligible.append((rank, i))
    eligible.sort()
    expected_indices = [i for _, i in eligible]
    initial = expected_indices[:policy['max_pilot_layers']]
    if (expected_indices != proposal['selection']['eligible_in_risk_order']
            or sorted(high_risk) != proposal['selection']['dense_priority_indices']
            or initial != proposal['selection']['initial_pilot_indices']):
        raise ValueError('Predeclared selection policy did not reproduce')
    combination = proposal['combined_screen']
    combination_ok = bool(initial) and all(
        metric['top1_drop_pp'] <= policy['single_max_top1_drop_pp']
        and metric['top5_drop_pp'] <= policy['single_max_top5_drop_pp']
        and metric['top1_new_errors'] <= policy['single_max_top1_new_errors']
        for metric in combination['metrics'].values())
    expected_proposal = initial if combination_ok else initial[:1]
    actual_proposal = [r['layer_index'] for r in proposal['proposed_sparse_finetune_layers']]
    if combination_ok != combination['passed_development_screen'] or expected_proposal != actual_proposal:
        raise ValueError('Proposed pilot/fallback mismatch')
    dense_ids = {r['layer_index'] for r in proposal['proposed_dense_layers']}
    if dense_ids & set(actual_proposal) or dense_ids | set(actual_proposal) != set(range(49)):
        raise ValueError('Dense/sparse proposed inventory invalid')
    if len(dense_ids & {1 + 4 * b for b in range(12)} | (dense_ids & {48})) != 13:
        raise ValueError('A CPU-route layer was proposed for pruning')
    return {'status': 'PASS_INDEPENDENT_OUTPUT_AND_SELECTION_RECOMPUTATION',
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'summary_sha256': digest(summary_path), 'verifier_sha256': digest(Path(__file__)),
            'models': len(summary['models']), 'checked_prediction_rows': len(seen),
            'checked_logits': len(summary['models']) * 100000,
            'eligible_in_risk_order': expected_indices, 'proposed_sparse_layer_indices': actual_proposal,
            'dense_priority_indices': sorted(high_risk), 'combination_screen_passed': combination_ok,
            'protected_source_hashes_match': True, 'proposal_not_applied': True,
            'limits': 'Saved evidence review, not a second inference, independent quality set, training result or board validation'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = args.result_dir / 'independent_verification.json'
        if receipt.exists():
            raise ValueError('Verification receipt exists; no overwrite')
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
