"""Independent saved-logit/count/scope verification; no new model inference."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def metrics(values, labels, baseline):
    ranks = np.argsort(-values, axis=1, kind='stable')[:, :5]
    original = np.argsort(-baseline, axis=1, kind='stable')[:, :5]
    result = {'images': len(labels)}
    for k in (1, 5):
        a = np.any(ranks[:, :k] == labels[:, None], axis=1)
        b = np.any(original[:, :k] == labels[:, None], axis=1)
        result.update({f'top{k}_correct': int(a.sum()), f'top{k}_pct': 100*int(a.sum())/len(a),
                       f'top{k}_drop_pp': 100*(int(b.sum())-int(a.sum()))/len(a),
                       f'top{k}_new_errors': int((b & ~a).sum()),
                       f'top{k}_recovered_errors': int((~b & a).sum()),
                       f'top{k}_both_correct': int((a & b).sum()),
                       f'top{k}_both_wrong': int((~a & ~b).sum())})
    result['top1_prediction_changes'] = int((ranks[:, 0] != original[:, 0]).sum())
    result['logit_relative_l2_vs_dense'] = float(np.linalg.norm(values.astype(np.float64)-baseline)
                                               / np.linalg.norm(baseline.astype(np.float64)))
    return result, ranks


def verify(folder):
    summary = json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    assert summary['status'] == 'COMPLETE_DEVELOPMENT_GROUP_SCREEN'
    for name, expected in summary['source_hashes'].items():
        assert digest(name) == expected, ('source changed', name)
    for name, expected in summary['artifacts'].items():
        path = (folder/name).resolve(strict=True)
        assert path.is_relative_to(folder.resolve()) and digest(path) == expected, name
    policy = json.loads((folder/'experiment_policy.json').read_text(encoding='utf-8'))
    identity = json.loads((folder/'dataset.json').read_text(encoding='utf-8'))
    dataset_sha = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert dataset_sha == summary['ordered_dataset_sha256']
    inventory = json.loads((folder/'layer_inventory.json').read_text(encoding='utf-8'))
    assert [r['layer_index'] for r in inventory] == list(range(49))
    inventory = {r['layer_index']: r for r in inventory}
    recalculated, ranks = {}, {}
    with np.load(folder/'logits.npz', allow_pickle=False) as saved:
        labels = saved['labels']
        np.testing.assert_array_equal(labels, [r['label'] for r in identity])
        np.testing.assert_array_equal(saved['images'], [r['image'] for r in identity])
        np.testing.assert_array_equal(saved['image_sha256'], [r['sha256'] for r in identity])
        assert labels.shape == (100,) and np.all((labels >= 0) & (labels < 1000))
        expected_names = set(summary['models']) | {'labels', 'images', 'image_sha256'}
        assert set(saved.files) == expected_names
        for name, record in summary['models'].items():
            values = saved[name]
            assert values.shape == (100, 1000) and np.isfinite(values).all()
            calculated, ranks[name] = metrics(values, labels, saved['dense_'+record['precision']])
            for field, value in calculated.items():
                assert np.isclose(value, record['metrics'][field], atol=1e-12, rtol=1e-12), (name, field)
            recalculated[name] = calculated
    planned = {g['name']: g for g in policy['groups']}
    assert len(planned) == len(summary['groups']) == policy['distinct_groups']
    passed = []
    by_id = {}
    for group in summary['groups']:
        name, ids = group['name'], group['layer_indices']
        by_id[name] = group
        for key in ('layer_indices', 'strategies', 'layers'):
            assert planned[name][key] == group[key], (name, 'posthoc plan change')
        assert len(ids) == len(set(ids)) == group['layers']
        assert all(inventory[i]['family'] in ('fc1', 'fc2') for i in ids)
        good = True
        for precision in ('fp32', 'w8a8'):
            m = recalculated[name+'_'+precision]
            assert summary['models'][name+'_'+precision]['layer_indices'] == ids
            for field, value in m.items():
                assert np.isclose(value, group['metrics'][precision][field], atol=1e-12, rtol=1e-12)
            good &= m['top1_drop_pp'] <= 1 and m['top5_drop_pp'] <= 2 and m['top1_new_errors'] <= 2
        assert good == group['passes_development_gate']
        if good:
            passed.append(name)
        covered = sum(inventory[i]['M']*inventory[i]['K']*inventory[i]['N'] for i in ids)
        total = sum(r['M']*r['K']*r['N'] for r in inventory.values())
        assert covered == group['compute']['dense_MACs_covered']
        assert np.isclose(covered/(2*total), group['compute']['ideal_skipped_fraction_of_all49_linear_MACs'])
    def order(name):
        g = by_id[name]
        ms = [recalculated[name+'_'+p] for p in ('fp32', 'w8a8')]
        return (-g['compute']['dense_MACs_covered'], max(m['top1_new_errors'] for m in ms),
                max(m['top1_drop_pp'] for m in ms), max(m['top5_drop_pp'] for m in ms),
                max(m['logit_relative_l2_vs_dense'] for m in ms), tuple(g['layer_indices']))
    passed.sort(key=order)
    preferred = passed[0] if passed else None
    assert preferred == summary['preferred_group'] and len(passed) == summary['passing_groups']
    proposal = json.loads((folder/'proposal.json').read_text(encoding='utf-8'))
    assert passed == proposal['passing_groups_in_selection_order']
    assert (proposal['preferred_group']['name'] if preferred else None) == preferred
    with (folder/'predictions.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(summary['models'])*len(identity)
    seen = set()
    image_indices = {r['image']: j for j, r in enumerate(identity)}
    for r in rows:
        name, j = r['model'], image_indices[r['image']]
        assert (name, j) not in seen
        seen.add((name, j))
        assert r['image_sha256'] == identity[j]['sha256'] and int(r['label']) == identity[j]['label']
        assert int(r['top1']) == ranks[name][j, 0]
        np.testing.assert_array_equal(json.loads(r['top5']), ranks[name][j])
    result = {'status': 'PASS_INDEPENDENT_SAVED_EVIDENCE_CHECK', 'images': len(identity),
              'models_checked': len(summary['models']), 'groups_checked': len(planned),
              'passing_groups': passed, 'preferred_group': preferred, 'prediction_rows_checked': len(rows),
              'source_and_artifact_hashes': 'PASS', 'QKV_proj_head_in_all_groups': 'DENSE_UNCHANGED',
              'summary_sha256': digest(folder/'summary.json'), 'verifier_sha256': digest(__file__),
              'scope': 'Recompute saved-logit metrics and selection, NOT a new independent image set or board test'}
    with (folder/'independent_verification.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.result_dir.resolve(strict=True)), ensure_ascii=False, indent=2))
