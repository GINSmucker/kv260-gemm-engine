"""Check searched groups against old dev100 without changing the first search.

Both image sets are DEVELOPMENT data. This is not an independent holdout test.
Search only among already evaluated groups, preserve their original evidence,
and require the five-point budget separately on each set and each precision.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np

from diagnose_expanded_pruning import build_combination
from diagnose_layer_sensitivity import measure, run_model
from diagnose_pruning import audit_candidate, verify_receipt
from eval_models import load_samples, preprocess_image, sha_file
from prune_h576 import read_source, write_json
from search_mlp_partial import annotate, gate
from verify_partial_search import recalculate

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.search_dir.resolve(strict=True), args.output_dir.resolve()
    if output.exists() or not output.is_relative_to((ROOT/'eval_results').resolve()):
        raise ValueError('Choose a new project eval_results directory; no overwrite')
    summary = json.loads((source/'summary.json').read_text(encoding='utf-8'))
    if summary['status'] != 'COMPLETE_PARTIAL_DEVELOPMENT_SEARCH':
        raise ValueError('The primary search is not complete')
    protected = {source/'summary.json': sha_file(source/'summary.json'), Path(__file__): sha_file(__file__)}
    protected.update({p.resolve(): sha_file(p) for p in ROOT.glob('*.py')})
    for name, expected in summary['artifacts'].items():
        path = (source/name).resolve(strict=True)
        if not path.is_relative_to(source) or sha_file(path) != expected:
            raise ValueError('Primary search artifact changed: '+name)
        protected[path] = expected
    archive = ROOT/'inputs/h576-runtime-public-ui.tar.gz'
    candidate = ROOT/'candidates/h576_sparse24_pl36_v1'
    protected[archive] = sha_file(archive)
    manifest = json.loads((candidate/'candidate.json').read_text(encoding='utf-8'))
    protected[candidate/'candidate.json'] = sha_file(candidate/'candidate.json')
    verify_receipt(candidate, manifest, protected)
    payload, layers, base_fp, base_q = read_source(archive)
    _, weights = audit_candidate(payload, layers, base_fp, base_q, candidate)
    samples = load_samples(ROOT/'inputs/imagenet100_dev100', expected_count=100)
    old_manifest_path = ROOT/'inputs/imagenet100_dev100/dataset_manifest.json'
    old_manifest = json.loads(old_manifest_path.read_text(encoding='utf-8'))
    protected[old_manifest_path] = sha_file(old_manifest_path)
    protected[ROOT/'inputs/imagenet100_dev100/labels.csv'] = sha_file(ROOT/'inputs/imagenet100_dev100/labels.csv')
    expected = {r['image']: r['image_sha256'] for r in old_manifest['rows']}
    old_tensors, old_rows = [], []
    for r in samples:
        data = r.path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected[r.image]:
            raise ValueError('Old development image changed')
        protected[r.path] = digest
        tensor = preprocess_image(io.BytesIO(data)); tensor.flags.writeable = False
        old_tensors.append(tensor)
        old_rows.append({'image': r.image, 'label': r.label, 'image_sha256': digest})
    old_labels = np.array([r.label for r in samples], np.int64)
    current_rows = json.loads((source/'dataset_snapshot.json').read_text(encoding='utf-8'))
    current_labels = np.array([r['label'] for r in current_rows], np.int64)
    current_tensors = []
    for r in current_rows:
        path = Path(r['source_image_path'])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != r['image_sha256']:
            raise ValueError('A frozen partial-set image changed')
        protected[path] = r['image_sha256']
        tensor = preprocess_image(io.BytesIO(data)); tensor.flags.writeable = False
        current_tensors.append(tensor)
    possible = [g for g in summary['groups'] if g['w8_gate']]
    def order(g):
        m = g['metrics']['w8a8']
        return (-g['layers'], max(m['top1_drop_pp'], m['top5_drop_pp']),
                max(0, m['top1_drop_pp']), max(0, m['top5_drop_pp']),
                m['logit_relative_l2_vs_dense'], m['top1_new_errors'], tuple(g['layer_indices']))
    possible.sort(key=order)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output/'experiment_policy.json', {
        'status': 'FIXED_BEFORE_OLD100_NEW_PREDICTIONS', 'input_primary_summary_sha256': protected[source/'summary.json'],
        'candidate_names_in_check_order': [g['name'] for g in possible],
        'gate': 'Top1 AND Top5 loss <=5 percentage points separately on old100 and partial523, in W8A8 AND FP32',
        'objective': 'Maximum layer count AMONG the first bounded search groups, then its frozen W8 ranking',
        'fp32_confirmation_budget': 12, 'datasets': 'BOTH used for selection; neither is an independent holdout',
        'model_files_written': False, 'training': False, 'hardware_changes': False})
    write_json(output/'old100_dataset.json', old_rows)
    started = time.monotonic()
    old_logits, current_extra, metrics, identity = {}, {}, {}, {}
    with np.load(source/'logits.npz', allow_pickle=False) as current_saved:
        np.testing.assert_array_equal(current_saved['labels'], current_labels)
        current_base = {p: current_saved['dense_'+p].copy() for p in ('fp32', 'w8a8')}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {p: pool.submit(run_model, b, old_tensors) for p, b in (('fp32', base_fp), ('w8a8', base_q))}
            baseline = {}
            for p in ('fp32', 'w8a8'):
                values, digest = futures[p].result()
                old_logits['dense_'+p] = values
                m, _ = measure(values, old_labels, values, values, 'control')
                baseline[p] = annotate(m, m)
                identity['dense_'+p] = digest
                print(f'OLD100_DENSE {p}: {m["top1_correct"]}/{m["top5_correct"]}', flush=True)
            previous = ROOT/'eval_results/family_sensitivity_imagenet100_train100_v1'
            protected[previous/'logits.npz'] = sha_file(previous/'logits.npz')
            with np.load(previous/'logits.npz', allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['labels'], old_labels)
                for p in ('fp32', 'w8a8'):
                    np.testing.assert_array_equal(old_logits['dense_'+p], saved['dense_'+p])
            for offset in range(0, len(possible), 2):
                batch = possible[offset:offset+2]
                jobs = [(g, pool.submit(run_model, build_combination(base_q, layers, g['layer_indices'], weights, 'w8a8'), old_tensors)) for g in batch]
                for g, future in jobs:
                    name = g['name']
                    values, digest = future.result()
                    old_logits[name+'_w8a8'] = values
                    identity[name+'_w8a8'] = digest
                    m, _ = measure(values, old_labels, old_logits['dense_w8a8'], old_logits['dense_w8a8'], 'combined')
                    m = annotate(m, baseline['w8a8'])
                    metrics[name] = {'layer_indices': g['layer_indices'], 'layers': g['layers'],
                                     'partial': {'w8a8': g['metrics']['w8a8']}, 'old100': {'w8a8': m},
                                     'both_sets_w8_gate': gate(m, 100)}
                    print(f'OLD100_W8 {offset+batch.index(g)+1:03d}/{len(possible)} {name} layers={g["layers"]} '
                          f'Top1/5={m["top1_correct"]}/{m["top5_correct"]} gate={gate(m,100)}', flush=True)
            preferred, checked = None, 0
            for g in possible:
                name = g['name']
                if not metrics[name]['both_sets_w8_gate']:
                    continue
                if checked >= 12:
                    break
                checked += 1
                model = build_combination(base_fp, layers, g['layer_indices'], weights, 'fp32')
                old_values, digest = run_model(model, old_tensors)
                identity[name+'_fp32'] = digest
                old_logits[name+'_fp32'] = old_values
                m, _ = measure(old_values, old_labels, old_logits['dense_fp32'], old_logits['dense_fp32'], 'combined')
                m = annotate(m, baseline['fp32'])
                metrics[name]['old100']['fp32'] = m
                if name+'_fp32' in current_saved.files:
                    current_values = current_saved[name+'_fp32'].copy()
                else:
                    current_values, _ = run_model(model, current_tensors)
                    current_extra[name+'_fp32'] = current_values
                partial_m, _ = measure(current_values, current_labels, current_base['fp32'], current_base['fp32'], 'combined')
                partial_m = annotate(partial_m, summary['baseline']['fp32'])
                metrics[name]['partial']['fp32'] = partial_m
                accepted = gate(m, 100) and gate(partial_m, len(current_labels))
                metrics[name]['both_sets_both_precision_gate'] = accepted
                print(f'JOINT_FP32 {name}: layers={g["layers"]} old100={m["top1_correct"]}/{m["top5_correct"]} '
                      f'partial={partial_m["top1_correct"]}/{partial_m["top5_correct"]} gate={accepted}', flush=True)
                if accepted:
                    preferred = {'name': name, **metrics[name]}
                    break
            # Repeat both precisions on both data sets for the final joint choice.
            repeat = {'status': 'NO_JOINT_CANDIDATE'}
            if preferred:
                name, ids = preferred['name'], preferred['layer_indices']
                for p, b in (('fp32', base_fp), ('w8a8', base_q)):
                    model = build_combination(b, layers, ids, weights, p)
                    old_again, _ = run_model(model, old_tensors)
                    np.testing.assert_array_equal(old_again, old_logits[name+'_'+p])
                    if name+'_'+p in current_saved.files:
                        values = current_saved[name+'_'+p]
                    else:
                        values = current_extra[name+'_'+p]
                    current_again, _ = run_model(model, current_tensors)
                    np.testing.assert_array_equal(current_again, values)
                repeat = {'status': 'PASS_BOTH_SETS_BOTH_PRECISIONS_BITWISE', 'group': name}
        # Recompute every saved result with an independent vectorized counter.
        for name, values in old_logits.items():
            p = 'fp32' if name.endswith('_fp32') else 'w8a8'
            calculated = recalculate(values, old_labels, old_logits['dense_'+p])
            reference = baseline[p] if name.startswith('dense_') else metrics[name.rsplit('_',1)[0]]['old100'][p]
            for field, value in calculated.items():
                if not np.isclose(value, reference[field], rtol=1e-12, atol=1e-12):
                    raise ValueError('Independent old100 metric check failed')
        if preferred:
            name = preferred['name']
            for p in ('fp32', 'w8a8'):
                values = current_saved[name+'_'+p] if name+'_'+p in current_saved.files else current_extra[name+'_'+p]
                calculated = recalculate(values, current_labels, current_base[p])
                for field, value in calculated.items():
                    if not np.isclose(value, preferred['partial'][p][field], rtol=1e-12, atol=1e-12):
                        raise ValueError('Independent partial-set metric check failed')
    if any(sha_file(p) != expected for p, expected in protected.items()):
        raise ValueError('A protected input was modified')
    with (output/'old100_logits.npz').open('xb') as stream:
        np.savez_compressed(stream, **old_logits, labels=old_labels)
    if current_extra:
        with (output/'additional_partial_fp32_logits.npz').open('xb') as stream:
            np.savez_compressed(stream, **current_extra, labels=current_labels)
    write_json(output/'proposal.json', {'status': 'JOINT_DEVELOPMENT_PROPOSAL_NOT_INDEPENDENT_ACCEPTANCE',
        'primary_523_preferred': summary['preferred'], 'preferred': preferred,
        'replication': repeat, 'all_other_layers': 'DENSE_UNCHANGED', 'model_or_runtime_files_written': False})
    write_json(output/'summary.json', {'status': 'COMPLETE_JOINT_DEVELOPMENT_REGRESSION_CHECK',
        'primary_search': str(source), 'primary_523_preferred': summary['preferred'],
        'images': {'old_development': 100, 'partial_validation_repurposed_for_search': len(current_labels)},
        'old100_baseline': baseline, 'checked_groups': metrics, 'preferred': preferred,
        'replication': repeat, 'independent_saved_metric_recalculation': 'PASS',
        'model_identities': identity, 'source_hashes': {str(p): h for p,h in protected.items()},
        'elapsed_seconds': time.monotonic()-started,
        'artifacts': {p.name: sha_file(p) for p in sorted(output.iterdir())},
        'limit': 'Both sets are selected-on development data, not proof of independent quality or global optimum'})
    print(json.dumps({'status': 'COMPLETE_JOINT_DEVELOPMENT_REGRESSION_CHECK',
                      'primary_523_preferred': summary['preferred'], 'preferred': preferred,
                      'seconds': time.monotonic()-started, 'output': str(output)}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
