"""Expanded H576 coverage and fixed-order pruning-budget experiments.

No training, model-file writes, routing/HLS changes, downloads or old-file edits.
Adds 13 CPU-route singleton software probes; reuses 36 audited PL singleton
results; tests nested PL budgets 4/8/12/18/24/30/36 and all-49 software stress.
All graph changes occur in temporary in-memory copies, not deployment files.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import traceback

import numpy as np
import onnx
import onnxruntime as ort

from diagnose_layer_sensitivity import changed_weight_name, measure, run_model, select_shortlist
from diagnose_pruning import audit_candidate, independent_positions, mask_from_positions, verify_receipt
from eval_models import PREPROCESS_SPEC, load_samples, preprocess_image, rank_logits, sha_file
from prune_h576 import (ARCHIVE_SHA256, arrays, compare_unmodified, read_source,
                        replace_initializers, write_json)
from sparse24 import prune, quantize_weight

ROOT = Path(__file__).resolve().parent
PRECISIONS = ("fp32", "w8a8")
BUDGETS = (4, 8, 12, 18, 24, 30, 36)


def risk_key(record):
    singles = [record['single'][p] for p in PRECISIONS]
    restores = [record['restore'][p] for p in PRECISIONS]
    return (max(s['top1_new_errors'] for s in singles),
            max([0] + [s['top1_drop_pp'] for s in singles] + [r['top1_gain_vs_pl36_pp'] for r in restores]),
            max([0] + [s['top5_drop_pp'] for s in singles] + [r['top5_gain_vs_pl36_pp'] for r in restores]),
            max(s['logit_relative_l2_vs_dense'] for s in singles), record['layer_index'])


def fixed_order(records, eligible):
    ids = [record['layer_index'] for record in records]
    if len(ids) != len(set(ids)) or len(eligible) != len(set(eligible)) or not set(eligible).issubset(ids):
        raise ValueError('Invalid layer/ranking inventory')
    remaining = sorted((r for r in records if r['layer_index'] not in eligible), key=risk_key)
    return list(eligible) + [r['layer_index'] for r in remaining]


def build_combination(base, layers, selected, weights, precision):
    if len(selected) != len(set(selected)) or not set(selected).issubset({r['layer_index'] for r in layers}):
        raise ValueError('Invalid combination layer inventory')
    model = onnx.ModelProto()
    model.CopyFrom(base)
    replacements = {}
    for layer in layers:
        i = layer['layer_index']
        if i in selected:
            replacements[changed_weight_name(layer, precision)] = (
                np.ascontiguousarray(weights[i]['W_fp32'].T) if precision == 'fp32'
                else weights[i]['Wq'].astype(np.float32))
    replace_initializers(model, replacements)
    compare_unmodified(base.SerializeToString(), model, set(replacements))
    return model


def make_cpu_weights(layers, base_fp, base_q):
    fp, q = arrays(base_fp), arrays(base_q)
    weights, audit = {}, []
    for layer in layers:
        i = layer['layer_index']
        original = np.ascontiguousarray(fp[layer['layer_id'] + '.weight'].T)
        wp, positions, mask = prune(original)
        independent = independent_positions(original)
        np.testing.assert_array_equal(positions, independent)
        np.testing.assert_array_equal(mask, mask_from_positions(independent, original.shape))
        wq, scale = quantize_weight(wp)
        np.testing.assert_array_equal(scale.ravel(), q[f'BQL_{i}_sf'])
        np.testing.assert_array_equal(wq, np.where(mask, q[f'BQL_{i}_Wf'].astype(np.int8), 0))
        weights[i] = {'W_fp32': wp, 'Wq': wq}
        audit.append({'layer_index': i, 'layer_id': layer['layer_id'], 'family': layer['family'],
                      'original_route': 'CPU_UNCHANGED_IN_DEPLOYMENT', 'shape_MKN': [layer[k] for k in ('M', 'K', 'N')],
                      'groups_checked': original.size // 4, 'retained_slots': int(mask.sum()),
                      'mask_sha256': hashlib.sha256(mask.tobytes()).hexdigest(),
                      'positions_sha256': hashlib.sha256(positions.tobytes()).hexdigest(),
                      'weight_scale_identical': True, 'retained_INT8_values_identical': True,
                      'rule': 'Same magnitude K-axis 2-of-4; temporary software probes only'})
    return weights, audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--archive', type=Path, default=ROOT / 'inputs/h576-runtime-public-ui.tar.gz')
    parser.add_argument('--candidate', type=Path, default=ROOT / 'candidates/h576_sparse24_pl36_v1')
    parser.add_argument('--previous', type=Path, default=ROOT / 'eval_results/layer_sensitivity_imagenet100_train100_v1')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'inputs/imagenet100_dev100')
    args = parser.parse_args(argv)
    output, created = args.output_dir.resolve(), False
    try:
        if output.exists():
            raise ValueError('Choose a NEW output directory; no overwrite')
        if ort.__version__ != '1.22.1' or np.__version__ != '1.26.4':
            raise ValueError('Use the frozen project environment')
        protected = {p.resolve(): sha_file(p) for pattern in ('*.py', '*.md', '*.txt', '.gitignore')
                     for p in ROOT.glob(pattern)}
        for path in (args.archive, args.candidate / 'candidate.json', args.previous / 'summary.json',
                     args.data_dir / 'labels.csv', args.data_dir / 'dataset_manifest.json'):
            protected[path.resolve(strict=True)] = sha_file(path)
        previous = json.loads((args.previous / 'summary.json').read_text(encoding='utf-8'))
        if previous['status'] != 'COMPLETE_DIAGNOSIS_PROPOSAL_NOT_APPLIED':
            raise ValueError('Unexpected prior layer diagnosis')
        for filename, expected in previous['source_hashes'].items():
            path = Path(filename)
            if sha_file(path) != expected:
                raise ValueError('Previous protected source changed: ' + filename)
            protected[path] = expected
        for name, expected in previous['artifacts'].items():
            path = (args.previous / name).resolve(strict=True)
            if not path.is_relative_to(args.previous.resolve()) or sha_file(path) != expected:
                raise ValueError('Previous artifact identity mismatch')
            protected[path] = expected
        records = json.loads((args.previous / 'layers.json').read_text(encoding='utf-8'))
        selection = select_shortlist(records)
        order = fixed_order(records, selection['eligible_in_risk_order'])
        if len(order) != 36:
            raise ValueError('Expected 36 PL target layers')
        policy = {
            'status': 'FIXED_BEFORE_NEW_INFERENCE_RESEARCH_ONLY',
            'budgets': list(BUDGETS), 'PL_layer_order': order,
            'order_rule': 'Prior eligible eight in prior risk order, then the other 28 sorted by the SAME risk tuple; no new-outcome reordering',
            'risk_tuple': 'worst new Top1 errors; worst nonnegative Top1 single-loss/restore-gain; worst nonnegative Top5 effect; worst relative-logit-L2; layer index',
            'scope': 'Nested prefixes only, NOT exhaustive subset search or globally optimal selection',
            'additional_probes': 'All 13 previously CPU-route Linear layers, singly, plus all-49 software stress',
            'deployment_changes': 'NONE; original CPU/PL routes remain untouched',
            'quality_limit': 'Same selection-used 100 train dev images, not independent acceptance; no training',
        }
        manifest = json.loads((args.candidate / 'candidate.json').read_text(encoding='utf-8'))
        verify_receipt(args.candidate, manifest, protected)
        print('Verifying frozen source and previous 36-layer mask audit ...', flush=True)
        payload, layers, base_fp, base_q = read_source(args.archive)
        _, weights = audit_candidate(payload, layers, base_fp, base_q, args.candidate)
        cpu_layers = [layer for layer in layers if layer['family'] in {'proj', 'head'}]
        if len(cpu_layers) != 13 or {layer['layer_index'] for layer in cpu_layers} & set(order):
            raise ValueError('Unexpected route inventory')
        cpu_weights, cpu_audit = make_cpu_weights(cpu_layers, base_fp, base_q)
        weights.update(cpu_weights)
        samples = load_samples(args.data_dir, expected_count=100)
        with (args.data_dir / 'labels.csv').open(encoding='utf-8-sig', newline='') as stream:
            expected_images = {row['image']: row['image_sha256'] for row in csv.DictReader(stream)}
        tensors, identities = [], []
        for sample in samples:
            data = sample.path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha != expected_images[sample.image]:
                raise ValueError('Image identity mismatch')
            tensor = preprocess_image(io.BytesIO(data))
            tensor.flags.writeable = False
            tensors.append(tensor)
            identities.append({'image': sample.image, 'label': sample.label, 'sha256': sha})
            protected[sample.path] = sha
        dataset_sha = hashlib.sha256(json.dumps(identities, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()
        if dataset_sha != previous['ordered_dataset_sha256']:
            raise ValueError('Not the exact same ordered development dataset')
        labels = np.array([s.label for s in samples], np.int64)
        output.mkdir(parents=True, exist_ok=False)
        created = True
        write_json(output / 'experiment_policy.json', policy)
        write_json(output / 'cpu_mask_audit.json', cpu_audit)
        base = {'fp32': base_fp, 'w8a8': base_q}
        logits_by_name, models, stages = {}, {}, {}
        with np.load(args.previous / 'logits.npz', allow_pickle=False) as old:
            for precision in PRECISIONS:
                name = 'dense_' + precision
                logits, sha = run_model(base[precision], tensors)
                np.testing.assert_array_equal(logits, old[name])
                logits_by_name[name] = logits
                metric, _ = measure(logits, labels, logits, logits, 'control')
                models[name] = {'mode': 'dense_control', 'precision': precision,
                                'layer_indices': [], 'serialized_model_sha256': sha, 'metrics': metric}
            for budget in BUDGETS:
                stage_metrics = {}
                for precision in PRECISIONS:
                    name = f'prefix{budget:02d}_{precision}'
                    selected = order[:budget]
                    model = build_combination(base[precision], layers, selected, weights, precision)
                    logits, sha = run_model(model, tensors)
                    if budget in (4, 36):
                        old_name = ('combined_screen_' if budget == 4 else 'pl36_') + precision
                        np.testing.assert_array_equal(logits, old[old_name])
                    logits_by_name[name] = logits
                    metric, _ = measure(logits, labels, logits_by_name['dense_' + precision],
                                        logits_by_name['dense_' + precision], 'combined')
                    models[name] = {'mode': 'PL_budget', 'budget': budget, 'precision': precision,
                                    'layer_indices': selected, 'serialized_model_sha256': sha, 'metrics': metric}
                    stage_metrics[precision] = metric
                stages[str(budget)] = {'layer_indices': order[:budget], 'metrics': stage_metrics}
                print(f"BUDGET {budget:02d}: FP32 {stage_metrics['fp32']['top1_correct']}/{stage_metrics['fp32']['top5_correct']}, "
                      f"W8 {stage_metrics['w8a8']['top1_correct']}/{stage_metrics['w8a8']['top5_correct']} (Top1/Top5 of 100)", flush=True)
        cpu_records = []
        for number, layer in enumerate(cpu_layers, 1):
            i = layer['layer_index']
            record = {'layer_index': i, 'layer_id': layer['layer_id'], 'family': layer['family'],
                      'scope': 'CPU_ROUTE_SOFTWARE_PROBE_NO_ROUTING_CHANGE', 'single': {}}
            for precision in PRECISIONS:
                name = f'cpu_single_L{i:02d}_{precision}'
                model = build_combination(base[precision], layers, [i], weights, precision)
                logits, sha = run_model(model, tensors)
                metric, _ = measure(logits, labels, logits_by_name['dense_' + precision],
                                    logits_by_name['dense_' + precision], 'single')
                record['single'][precision] = metric
                logits_by_name[name] = logits
                models[name] = {'mode': 'CPU_single_software', 'precision': precision, 'layer_indices': [i],
                                'serialized_model_sha256': sha, 'metrics': metric}
            cpu_records.append(record)
            print(f"CPU SINGLE {number:02d}/13 {layer['layer_id']}: W8 "
                  f"{record['single']['w8a8']['top1_correct']}/{record['single']['w8a8']['top5_correct']}", flush=True)
        for precision in PRECISIONS:
            name = 'all49_' + precision
            indices = [layer['layer_index'] for layer in layers]
            model = build_combination(base[precision], layers, indices, weights, precision)
            logits, sha = run_model(model, tensors)
            metric, _ = measure(logits, labels, logits_by_name['dense_' + precision],
                                logits_by_name['dense_' + precision], 'combined')
            logits_by_name[name] = logits
            models[name] = {'mode': 'ALL49_SOFTWARE_STRESS', 'precision': precision, 'layer_indices': indices,
                            'serialized_model_sha256': sha, 'metrics': metric}
        single49 = [{'layer_index': r['layer_index'], 'layer_id': r['layer_id'], 'family': r['family'],
                     'scope': 'PRIOR_VERIFIED_PL_SINGLETON', 'single': r['single']} for r in records] + cpu_records
        single49.sort(key=lambda r: r['layer_index'])
        if [r['layer_index'] for r in single49] != list(range(49)):
            raise ValueError('Incomplete all-49 singleton coverage')
        if any(sha_file(path) != sha for path, sha in protected.items()):
            raise ValueError('A protected input/source changed')
        with (output / 'logits.npz').open('xb') as stream:
            np.savez_compressed(stream, **logits_by_name, labels=labels,
                                images=np.array([i['image'] for i in identities]),
                                image_sha256=np.array([i['sha256'] for i in identities]))
        with (output / 'predictions.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['model', 'image', 'image_sha256', 'label', 'top1', 'top5', 'top1_correct', 'top5_correct'])
            writer.writeheader()
            for name, values in logits_by_name.items():
                for j, row in enumerate(values):
                    rank = rank_logits(row[None])
                    writer.writerow({'model': name, 'image': identities[j]['image'], 'image_sha256': identities[j]['sha256'],
                                     'label': int(labels[j]), 'top1': int(rank[0]), 'top5': json.dumps(rank.tolist()),
                                     'top1_correct': int(rank[0] == labels[j]), 'top5_correct': int(labels[j] in rank)})
        write_json(output / 'single49.json', single49)
        write_json(output / 'budget_stages.json', stages)
        summary = {'schema': 'h576_expanded_pruning_diagnosis_v1',
                   'status': 'COMPLETE_EXPANDED_DIAGNOSIS_NO_SELECTION_APPLIED',
                   'created_utc': datetime.now(timezone.utc).isoformat(), 'source_archive_sha256': ARCHIVE_SHA256,
                   'images': 100, 'classes_covered': len(set(labels.tolist())), 'ordered_dataset_sha256': dataset_sha,
                   'single_layer_coverage': {'total': 49, 'PL_previous_verified': 36, 'CPU_new': 13},
                   'controls_bitwise_reproduced': ['dense_fp32', 'dense_w8a8', 'prefix04_fp32', 'prefix04_w8a8', 'prefix36_fp32', 'prefix36_w8a8'],
                   'policy': policy, 'models': models,
                   'source_hashes': {str(p): sha for p, sha in protected.items() if p not in {s.path for s in samples}},
                   'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'onnx': onnx.__version__,
                                   'onnxruntime': ort.__version__, 'intra_threads': 4, 'inter_threads': 1,
                                   'graph_optimization': 'disabled', 'spinning': False},
                   'preprocessing': PREPROCESS_SPEC, 'onnx_model_files_written': 0,
                   'artifacts': {p.name: sha_file(p) for p in sorted(output.iterdir())},
                   'limits': ['Same selection-used public train dev images; not independent quality acceptance',
                              'Only fixed-order prefixes, not all subsets or a global optimum',
                              'CPU probes/all49 stress are model-software studies, not changes to board execution routes',
                              'No training, hardware validation, model-file writes, latency measurement or speedup proof']}
        write_json(output / 'summary.json', summary)
        print(json.dumps({'status': summary['status'], 'new_model_executions': len(models),
                          'budget_stages': {k: {p: {t: m[t] for t in ('top1_correct', 'top5_correct')} for p, m in v['metrics'].items()} for k, v in stages.items()},
                          'all49': {p: models['all49_' + p]['metrics'] for p in PRECISIONS},
                          'output': str(output)}, indent=2), flush=True)
        return 0
    except Exception as error:
        if created and not (output / 'failure.json').exists() and not (output / 'summary.json').exists():
            write_json(output / 'failure.json', {'status': 'FAIL', 'traceback': traceback.format_exc()})
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
