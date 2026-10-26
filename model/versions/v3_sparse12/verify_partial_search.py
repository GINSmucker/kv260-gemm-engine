"""Recompute partial-search counts/gates/provenance without new predictions."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            result.update(chunk)
    return result.hexdigest()


def recalculate(values, labels, baseline):
    rank = np.argsort(-values, axis=1, kind='stable')[:, :5]
    original = np.argsort(-baseline, axis=1, kind='stable')[:, :5]
    n = len(labels)
    result = {'images': n}
    for k in (1, 5):
        good = (rank[:, :k] == labels[:, None]).any(axis=1)
        old = (original[:, :k] == labels[:, None]).any(axis=1)
        lost = int(old.sum())-int(good.sum())
        result.update({f'top{k}_correct': int(good.sum()), f'top{k}_pct': 100*int(good.sum())/n,
                       f'top{k}_lost_count': lost, f'top{k}_drop_pp': 100*lost/n,
                       f'top{k}_new_errors': int((old & ~good).sum()),
                       f'top{k}_recovered_errors': int((~old & good).sum()),
                       f'top{k}_both_correct': int((old & good).sum()),
                       f'top{k}_both_wrong': int((~old & ~good).sum())})
    result['top1_prediction_changes'] = int((rank[:, 0] != original[:, 0]).sum())
    result['logit_relative_l2_vs_dense'] = float(np.linalg.norm(values.astype(np.float64)-baseline)
                                               / np.linalg.norm(baseline.astype(np.float64)))
    return result


def accepted(m, n):
    return max(m['top1_lost_count'], m['top5_lost_count']) <= n//20


def verify(folder):
    summary = json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    assert summary['status'] == 'COMPLETE_PARTIAL_DEVELOPMENT_SEARCH'
    n = summary['images']
    policy = json.loads((folder/'experiment_policy.json').read_text(encoding='utf-8'))
    assert policy == summary['policy']
    assert policy['max_top1_drop_pp'] == policy['max_top5_drop_pp'] == 5
    assert policy['new_errors_cap'] is None
    assert policy['max_lost_correct_answers_at_this_N'] == n//20
    for name, expected in summary['source_hashes'].items():
        assert sha(name) == expected, ('source or input changed', name)
    for name, expected in summary['artifacts'].items():
        path = (folder/name).resolve(strict=True)
        assert path.is_relative_to(folder.resolve()) and sha(path) == expected, name
    records = json.loads((folder/'dataset_snapshot.json').read_text(encoding='utf-8'))
    audit = json.loads((folder/'dataset_audit.json').read_text(encoding='utf-8'))
    assert len(records) == audit['images'] == n
    assert len({r['image_sha256'] for r in records}) == len({r['decoded_rgb_sha256'] for r in records}) == n
    assert len({r['label'] for r in records}) == audit['source_classes_covered'] == summary['classes_covered']
    counts = dict(sorted(Counter(str(r['source_label']) for r in records).items()))
    assert counts == audit['class_counts']
    assert audit['overlap_with_old_dev100_raw_or_decoded'] == 0
    recalculated = {}
    with np.load(folder/'logits.npz', allow_pickle=False) as saved:
        assert set(saved.files) == set(summary['models']) | {'labels', 'row_indices'}
        labels = saved['labels']
        np.testing.assert_array_equal(labels, [r['label'] for r in records])
        np.testing.assert_array_equal(saved['row_indices'], [r['row_index'] for r in records])
        assert labels.shape == (n,) and np.all((labels >= 0) & (labels < 1000))
        for name, model in summary['models'].items():
            values = saved[name]
            assert values.shape == (n, 1000) and np.isfinite(values).all()
            metrics = recalculate(values, labels, saved['dense_'+model['precision']])
            for field, value in metrics.items():
                assert np.isclose(value, model['metrics'][field], rtol=1e-12, atol=1e-12), (name, field)
            recalculated[name] = metrics
    assert len(summary['groups']) == summary['unique_w8_groups'] <= policy['max_unique_w8_groups']
    seen, groups, both = set(), {}, []
    for g in summary['groups']:
        name, ids = g['name'], g['layer_indices']
        assert tuple(ids) not in seen and len(ids) == len(set(ids)) == g['layers']
        seen.add(tuple(ids))
        assert all(0 <= i < 48 and i % 4 in (2, 3) for i in ids), 'Non-MLP layer changed'
        assert ids == summary['models'][name+'_w8a8']['layer_indices']
        expected_w8 = accepted(recalculated[name+'_w8a8'], n)
        assert expected_w8 == g['w8_gate']
        for precision, m in g['metrics'].items():
            for field, value in m.items():
                assert np.isclose(value, recalculated[name+'_'+precision][field], rtol=1e-12, atol=1e-12)
        if 'fp32' in g['metrics']:
            good = expected_w8 and accepted(recalculated[name+'_fp32'], n)
            assert good == g['both_gate']
            if good:
                both.append(name)
        groups[name] = g
    passing = sum(g['w8_gate'] for g in groups.values())
    assert passing == summary['w8_passing_groups']
    # Reproduce the original W8-based ordering, including all deterministic ties.
    def key(name):
        g = groups[name]
        m = recalculated[name+'_w8a8']
        return (-len(g['layer_indices']), max(m['top1_drop_pp'], m['top5_drop_pp']),
                max(0, m['top1_drop_pp']), max(0, m['top5_drop_pp']),
                m['logit_relative_l2_vs_dense'], m['top1_new_errors'], tuple(g['layer_indices']))
    both.sort(key=key)
    preferred = both[0] if both else None
    assert preferred == summary['preferred']
    proposal = json.loads((folder/'proposal.json').read_text(encoding='utf-8'))
    assert (proposal['preferred']['name'] if proposal['preferred'] else None) == preferred
    if preferred:
        assert summary['reproducibility'] == {'status': 'PASS_BITWISE_REEXECUTION',
                                             'group': preferred, 'precisions': ['fp32', 'w8a8']}
    with (folder/'groups.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(groups)
    for r in rows:
        g, m = groups[r['group']], recalculated[r['group']+'_w8a8']
        assert json.loads(r['layer_indices']) == g['layer_indices'] and int(r['layers']) == g['layers']
        assert int(r['w8_top1_count']) == m['top1_correct'] and int(r['w8_top5_count']) == m['top5_correct']
        assert (r['w8_gate'] == 'True') == g['w8_gate']
    result = {'status': 'PASS_INDEPENDENT_RECOMPUTATION_NOT_INDEPENDENT_IMAGE_ACCEPTANCE',
              'images': n, 'classes_covered': summary['classes_covered'], 'groups_checked': len(groups),
              'logit_arrays_checked': len(recalculated), 'w8_passing_groups': passing,
              'preferred': preferred, 'both_precision_passes': both,
              'max_lost_correct_answers': n//20, 'image_receipt_source_artifact_hashes': 'PASS',
              'QKV_proj_head': 'DENSE_UNCHANGED', 'verifier_sha256': sha(__file__),
              'summary_sha256': sha(folder/'summary.json'),
              'limit': 'Verifies saved evidence. Does not make the 48-class search prefix an independent holdout.'}
    with (folder/'independent_verification.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.result_dir.resolve(strict=True)), ensure_ascii=False, indent=2))
