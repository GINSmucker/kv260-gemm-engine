"""Bounded, predeclared H576 MLP-only group screen on the existing dev100.

This is a candidate search, not independent quality acceptance. All models are
temporary full-classifier ONNX CPU references; no training, hardware, routing,
mask changes or model-file writes. Existing files are hash-pinned and preserved.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import time
import traceback

import numpy as np
import onnx
import onnxruntime as ort
from PIL import __version__ as pillow_version

from diagnose_expanded_pruning import build_combination
from diagnose_layer_sensitivity import changed_weight_name, measure, run_model
from diagnose_mlp_only import pin_previous, select_mlp_indices
from diagnose_pruning import audit_candidate, verify_receipt
from eval_models import PREPROCESS_SPEC, load_samples, preprocess_image, sha_file
from prune_h576 import ARCHIVE_SHA256, read_source, write_json

ROOT = Path(__file__).resolve().parent
PRECISIONS = ('fp32', 'w8a8')
GATE = {'max_top1_drop_pp': 1.0, 'max_top5_drop_pp': 2.0,
        'max_top1_new_errors': 2, 'both_precisions': True}
COUNTS = (1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24)


def single_risk(record):
    """Dense-context singleton risk only; no mixed-QKV restoration evidence."""
    values = [record['single'][p] for p in PRECISIONS]
    return (max(v['top1_new_errors'] for v in values),
            max(0, *(v['top1_drop_pp'] for v in values)),
            max(0, *(v['top5_drop_pp'] for v in values)),
            max(v['logit_relative_l2_vs_dense'] for v in values),
            record['layer_index'])


def group_plan(layers, records):
    allowed = set(select_mlp_indices(layers))
    mlp = [r for r in records if r['layer_index'] in allowed]
    if len(mlp) != 24 or {r['layer_index'] for r in mlp} != allowed:
        raise ValueError('Need precisely 24 existing MLP singleton records')
    if any(r['family'] not in {'fc1', 'fc2'} for r in mlp):
        raise ValueError('Singleton family mismatch')
    by_id = {r['layer_index']: r for r in mlp}
    order = [r['layer_index'] for r in sorted(mlp, key=single_risk)]
    # Pair risk is fixed before inference: worst risk of the two components.
    blocks = sorted(range(12), key=lambda b: (
        max(single_risk(by_id[4*b+2])[:-1], single_risk(by_id[4*b+3])[:-1]), b))
    pair_order = [i for b in blocks for i in (4*b+2, 4*b+3)]
    suffix_order = [i for b in reversed(range(12)) for i in (4*b+2, 4*b+3)]
    groups = {}

    def add(strategy, size, selected):
        key = tuple(sorted(selected))
        if len(key) != len(set(key)) or not set(key).issubset(allowed):
            raise ValueError('Combination violates the MLP-only whitelist')
        if key not in groups:
            groups[key] = {'name': f'g{len(groups)+1:02d}', 'layer_indices': list(key),
                           'layers': len(key), 'strategies': []}
        groups[key]['strategies'].append(f'{strategy}:{size}')

    for count in COUNTS:
        add('singleton_risk_prefix', count, order[:count])
    for count in range(2, 25, 2):
        add('paired_block_risk_prefix', count, pair_order[:count])
        add('late_block_suffix_pairs', count, suffix_order[:count])
    for family in ('fc1', 'fc2'):
        family_order = [i for i in order if by_id[i]['family'] == family]
        for count in (1, 2, 4, 6, 8, 10, 12):
            add(family + '_risk_prefix', count, family_order[:count])
    return list(groups.values()), {'singleton_risk_order': order,
                                  'paired_block_risk_order': blocks,
                                  'late_block_order': list(reversed(range(12)))}


def passes(metrics):
    return set(metrics) == set(PRECISIONS) and all(
        m['top1_drop_pp'] <= GATE['max_top1_drop_pp']
        and m['top5_drop_pp'] <= GATE['max_top5_drop_pp']
        and m['top1_new_errors'] <= GATE['max_top1_new_errors']
        for m in metrics.values())


def selection_key(group):
    """Maximize theoretical MAC coverage, then minimize quality disturbance."""
    values = list(group['metrics'].values())
    return (-group['compute']['dense_MACs_covered'],
            max(m['top1_new_errors'] for m in values),
            max(m['top1_drop_pp'] for m in values),
            max(m['top5_drop_pp'] for m in values),
            max(m['logit_relative_l2_vs_dense'] for m in values),
            tuple(group['layer_indices']))


def compute_coverage(layers, selected):
    chosen = [r for r in layers if r['layer_index'] in selected]
    total = sum(r['M'] * r['K'] * r['N'] for r in layers)
    pl = sum(r['M'] * r['K'] * r['N'] for r in layers
             if r['family'] in {'qkv', 'fc1', 'fc2'})
    covered = sum(r['M'] * r['K'] * r['N'] for r in chosen)
    return {'dense_MACs_covered': covered, 'all49_linear_dense_MACs': total,
            'original_PL36_dense_MACs': pl,
            'covered_fraction_of_all49_linear_MACs': covered / total,
            'ideal_skipped_MACs_at_2of4': covered // 2,
            'ideal_skipped_fraction_of_all49_linear_MACs': covered / (2 * total),
            'ideal_skipped_fraction_of_PL36_MACs': covered / (2 * pl),
            'selected_weight_elements': sum(r['K'] * r['N'] for r in chosen),
            'limit': 'Arithmetic upper bound only, excludes attention/nonlinear/PS/DDR; NOT measured speedup'}


def pin_layer_evidence(folder, protected):
    summary_path = folder / 'summary.json'
    previous = json.loads(summary_path.read_text(encoding='utf-8'))
    if previous['status'] != 'COMPLETE_DIAGNOSIS_PROPOSAL_NOT_APPLIED':
        raise ValueError('Unexpected prior singleton evidence')
    protected[summary_path.resolve()] = sha_file(summary_path)
    for name, digest in previous['source_hashes'].items():
        path = Path(name).resolve(strict=True)
        if sha_file(path) != digest:
            raise ValueError('Prior singleton source changed: ' + name)
        protected[path] = digest
    for name, digest in previous['artifacts'].items():
        path = (folder / name).resolve(strict=True)
        if not path.is_relative_to(folder.resolve()) or sha_file(path) != digest:
            raise ValueError('Prior singleton artifact changed: ' + name)
        protected[path] = digest
    return previous, json.loads((folder / 'layers.json').read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--archive', type=Path, default=ROOT / 'inputs/h576-runtime-public-ui.tar.gz')
    parser.add_argument('--candidate', type=Path, default=ROOT / 'candidates/h576_sparse24_pl36_v1')
    parser.add_argument('--previous', type=Path, default=ROOT / 'eval_results/family_sensitivity_imagenet100_train100_v1')
    parser.add_argument('--singletons', type=Path, default=ROOT / 'eval_results/layer_sensitivity_imagenet100_train100_v1')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'inputs/imagenet100_dev100')
    args = parser.parse_args(argv)
    output, created = args.output_dir.resolve(), False
    started = time.monotonic()
    try:
        if output.exists():
            raise ValueError('Output exists; choose a NEW directory, no overwrite')
        if not output.is_relative_to((ROOT / 'eval_results').resolve()):
            raise ValueError('Output must stay inside this project eval_results')
        if (np.__version__, ort.__version__, onnx.__version__, pillow_version) != ('1.26.4', '1.22.1', '1.17.0', '12.3.0'):
            raise ValueError('Use the frozen project environment')
        protected = {p.resolve(): sha_file(p) for pattern in ('*.py', '*.md', '*.txt', '.gitignore')
                     for p in ROOT.glob(pattern)}
        for path in (args.archive, args.candidate / 'candidate.json', args.data_dir / 'labels.csv',
                     args.data_dir / 'dataset_manifest.json'):
            protected[path.resolve(strict=True)] = sha_file(path)
        previous = pin_previous(args.previous, protected)
        prior_single, records = pin_layer_evidence(args.singletons, protected)
        if any(s['preprocessing'] != PREPROCESS_SPEC for s in (previous, prior_single)):
            raise ValueError('Prior preprocessing mismatch')
        manifest = json.loads((args.candidate / 'candidate.json').read_text(encoding='utf-8'))
        verify_receipt(args.candidate, manifest, protected)
        print('Auditing frozen source and existing 2:4 masks ...', flush=True)
        payload, layers, base_fp, base_q = read_source(args.archive)
        audit, weights = audit_candidate(payload, layers, base_fp, base_q, args.candidate)
        groups, orders = group_plan(layers, records)
        samples = load_samples(args.data_dir, expected_count=100)
        with (args.data_dir / 'labels.csv').open(encoding='utf-8-sig', newline='') as stream:
            expected_images = {r['image']: r['image_sha256'] for r in csv.DictReader(stream)}
        tensors, identities = [], []
        for sample in samples:
            data = sample.path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected_images[sample.image]:
                raise ValueError('Image identity mismatch: ' + sample.image)
            tensor = preprocess_image(io.BytesIO(data))
            tensor.flags.writeable = False
            tensors.append(tensor)
            identities.append({'image': sample.image, 'label': sample.label, 'sha256': digest})
            protected[sample.path] = digest
        dataset_sha = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()
        if any(s['ordered_dataset_sha256'] != dataset_sha for s in (previous, prior_single)):
            raise ValueError('Not the same ordered development dataset')
        labels = np.array([s.label for s in samples], np.int64)
        policy = {'status': 'PLAN_FIXED_BEFORE_NEW_INFERENCE', 'pruned_families': ['fc1', 'fc2'],
                  'kept_dense_families': ['qkv', 'proj', 'head'], 'gates': GATE,
                  'orders_from': 'Previous Dense-context singleton results only; no mixed-QKV restore ranking',
                  'orders': orders, 'groups': groups, 'distinct_groups': len(groups),
                  'search': 'Five deterministic nested group families, deduplicated; not exhaustive or globally optimal',
                  'mask': 'Existing audited K-axis magnitude 2-of-4, unchanged',
                  'selection': 'Passing groups: max covered Dense MACs, then fewer new errors, smaller drops/logit displacement, layer indices',
                  'quality_limit': 'Same selection-used 100 public train development images, NOT independent acceptance',
                  'model_file_writes': False, 'training': False, 'board_access': False,
                  'runtime_route_changes': False, 'physical_sparse_speed_measurement': False}
        output.mkdir(parents=True, exist_ok=False)
        created = True
        write_json(output / 'experiment_policy.json', policy)
        write_json(output / 'dataset.json', identities)
        # Copy only public layer geometry into this run for independently checking
        # MAC coverage; no private holdout, seeds or vectors are consulted.
        write_json(output / 'layer_inventory.json', [
            {k: r[k] for k in ('layer_index', 'layer_id', 'family', 'M', 'K', 'N')} for r in layers])
        write_json(output / 'mask_audit.json', {
            'status': 'PASS_EXISTING_RULE_UNCHANGED', 'independent_check': audit['independent_check'],
            'layers': [r for r in audit['layers'] if r['family'] in {'fc1', 'fc2'}]})
        logits, models, audits, rankings = {}, {}, {}, {}
        bases = {'fp32': base_fp, 'w8a8': base_q}
        with np.load(args.previous / 'logits.npz', allow_pickle=False) as saved:
            np.testing.assert_array_equal(saved['labels'], labels)
            np.testing.assert_array_equal(saved['images'], np.array([r['image'] for r in identities]))
            np.testing.assert_array_equal(saved['image_sha256'], np.array([r['sha256'] for r in identities]))
            for precision in PRECISIONS:
                name = 'dense_' + precision
                logits[name], identity = run_model(bases[precision], tensors)
                np.testing.assert_array_equal(logits[name], saved[name])
                metrics, rankings[name] = measure(logits[name], labels, logits[name], logits[name], 'control')
                models[name] = {'layer_indices': [], 'precision': precision, 'metrics': metrics,
                                'serialized_model_sha256': identity, 'origin': 'FRESH_DENSE_BITWISE_REPRODUCED'}
                print(f'DENSE {precision}: {metrics["top1_correct"]}/{metrics["top5_correct"]}, bitwise reproduced', flush=True)
        for number, group in enumerate(groups, 1):
            metrics = {}
            selected = group['layer_indices']
            for precision in PRECISIONS:
                name = group['name'] + '_' + precision
                model = build_combination(bases[precision], layers, selected, weights, precision)
                logits[name], identity = run_model(model, tensors)
                metrics[precision], rankings[name] = measure(
                    logits[name], labels, logits['dense_' + precision], logits['dense_' + precision], 'combined')
                # Existing graph builder checks exact initializer changes and
                # byte-identical topology, biases, scales and unselected weights.
                audits[name] = {'changed_initializer_names': sorted(
                    changed_weight_name(r, precision) for r in layers if r['layer_index'] in selected),
                    'unselected_initializers_biases_scales_topology_metadata': 'UNCHANGED'}
                models[name] = {'layer_indices': selected, 'precision': precision, 'metrics': metrics[precision],
                                'serialized_model_sha256': identity, 'origin': 'FRESH_IN_MEMORY_FULL_MODEL_CPU_REFERENCE'}
            group['metrics'] = metrics
            group['passes_development_gate'] = passes(metrics)
            group['compute'] = compute_coverage(layers, selected)
            group['family_counts'] = dict(Counter(r['family'] for r in layers if r['layer_index'] in selected))
            print(f'GROUP {number:02d}/{len(groups)} {group["name"]}: layers={len(selected):02d} '
                  f'FP32={metrics["fp32"]["top1_correct"]}/{metrics["fp32"]["top5_correct"]} '
                  f'W8={metrics["w8a8"]["top1_correct"]}/{metrics["w8a8"]["top5_correct"]} '
                  f'new={metrics["w8a8"]["top1_new_errors"]} gate={group["passes_development_gate"]}', flush=True)
        # Re-execute an exact duplicate after the screen to check session/run
        # reproducibility without introducing another search candidate.
        passing = sorted((g for g in groups if g['passes_development_gate']), key=selection_key)
        selected_group = passing[0] if passing else None
        repeat = {'status': 'NO_PASSING_GROUP'}
        if selected_group:
            repeat = {'status': 'PASS_BITWISE_REEXECUTION', 'group': selected_group['name'], 'precisions': []}
            for precision in PRECISIONS:
                model = build_combination(bases[precision], layers, selected_group['layer_indices'], weights, precision)
                values, identity = run_model(model, tensors)
                name = selected_group['name'] + '_' + precision
                np.testing.assert_array_equal(values, logits[name])
                if identity != models[name]['serialized_model_sha256']:
                    raise ValueError('Repeated graph identity changed')
                repeat['precisions'].append(precision)
        if any(sha_file(p) != digest for p, digest in protected.items()):
            raise ValueError('A protected input/source changed')
        with (output / 'logits.npz').open('xb') as stream:
            np.savez_compressed(stream, **logits, labels=labels,
                                images=np.array([r['image'] for r in identities]),
                                image_sha256=np.array([r['sha256'] for r in identities]))
        with (output / 'groups.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            fields = ['group', 'layers', 'layer_indices', 'strategies', 'fp32_top1', 'fp32_top5',
                      'w8_top1', 'w8_top5', 'w8_new_errors', 'gate', 'ideal_all49_linear_MAC_reduction_pct']
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for g in groups:
                writer.writerow({'group': g['name'], 'layers': g['layers'], 'layer_indices': json.dumps(g['layer_indices']),
                                 'strategies': '; '.join(g['strategies']),
                                 'fp32_top1': g['metrics']['fp32']['top1_correct'], 'fp32_top5': g['metrics']['fp32']['top5_correct'],
                                 'w8_top1': g['metrics']['w8a8']['top1_correct'], 'w8_top5': g['metrics']['w8a8']['top5_correct'],
                                 'w8_new_errors': g['metrics']['w8a8']['top1_new_errors'], 'gate': g['passes_development_gate'],
                                 'ideal_all49_linear_MAC_reduction_pct': 100*g['compute']['ideal_skipped_fraction_of_all49_linear_MACs']})
        with (output / 'predictions.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['model', 'image', 'image_sha256', 'label', 'top1', 'top5'])
            writer.writeheader()
            for name, ranks in rankings.items():
                for j, row in enumerate(ranks):
                    writer.writerow({'model': name, 'image': identities[j]['image'], 'image_sha256': identities[j]['sha256'],
                                     'label': int(labels[j]), 'top1': int(row[0]), 'top5': json.dumps(row.tolist())})
        write_json(output / 'graph_audits.json', audits)
        proposal = {'status': 'DEVELOPMENT_SHORTLIST_ONLY_NOT_DEPLOYED',
                    'preferred_group': selected_group, 'passing_groups_in_selection_order': [g['name'] for g in passing],
                    'next': 'Freeze before NEW independent validation. Do not tune on that validation set.',
                    'not_proven': ['independent accuracy', 'statistical noninferiority', 'Sparse PL correctness',
                                   'board timing/speedup', 'best possible subset']}
        write_json(output / 'proposal.json', proposal)
        summary = {'schema': 'h576_mlp_group_screen_v1', 'status': 'COMPLETE_DEVELOPMENT_GROUP_SCREEN',
                   'created_utc': datetime.now(timezone.utc).isoformat(), 'images': len(samples),
                   'classes_covered': len(set(labels.tolist())), 'ordered_dataset_sha256': dataset_sha,
                   'source_archive_sha256': ARCHIVE_SHA256, 'policy': policy, 'groups': groups, 'models': models,
                   'preferred_group': selected_group['name'] if selected_group else None,
                   'passing_groups': len(passing), 'reproducibility': repeat,
                   'source_hashes': {str(p): digest for p, digest in protected.items() if p not in {s.path for s in samples}},
                   'preprocessing': PREPROCESS_SPEC, 'elapsed_seconds': time.monotonic()-started,
                   'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'onnx': onnx.__version__,
                                   'onnxruntime': ort.__version__, 'Pillow': pillow_version, 'intra_threads': 4,
                                   'inter_threads': 1, 'execution': 'sequential', 'optimization': 'disabled', 'spinning': False},
                   'onnx_model_files_written': 0, 'artifacts': {p.name: sha_file(p) for p in sorted(output.iterdir())},
                   'limits': ['Same reused dev100; search and evaluation are NOT independent',
                              'Only the predeclared group families, NOT all subsets or optimality proof',
                              'No new masks, no training, no HLS/runtime/routing changes, no Sparse hardware execution',
                              'Theoretical MAC savings are not end-to-end latency improvements']}
        write_json(output / 'summary.json', summary)
        print(json.dumps({'status': summary['status'], 'groups': len(groups), 'passing': len(passing),
                          'preferred': selected_group, 'seconds': summary['elapsed_seconds'], 'output': str(output)},
                         ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as error:
        if created and not (output / 'summary.json').exists() and not (output / 'failure.json').exists():
            write_json(output / 'failure.json', {'status': 'FAIL', 'traceback': traceback.format_exc()})
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
