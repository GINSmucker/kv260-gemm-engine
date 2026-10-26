"""Fixed MLP-only 2:4 control: prune fc1/fc2, keep qkv/proj/head Dense.

Offline development-set diagnosis, NOT training or a board test. Builds two
temporary in-memory graphs and re-executes both Dense precision controls.
Reuses hash-pinned fc1-only/fc2-only/PL36 evidence, not their model selection.
Writes a NEW evidence directory only; no ONNX, HLS or routing files are written.
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
import traceback

import numpy as np
import onnx
import onnxruntime as ort
from PIL import __version__ as pillow_version

from diagnose_expanded_pruning import build_combination
from diagnose_layer_sensitivity import changed_weight_name, measure, run_model
from diagnose_pruning import audit_candidate, metric_counts, verify_receipt
from eval_fp32_control import error_rows
from eval_models import PREPROCESS_SPEC, load_samples, preprocess_image, save_errors, sha_file
from prune_h576 import ARCHIVE_SHA256, read_source, write_json

ROOT = Path(__file__).resolve().parent
PRECISIONS = ('fp32', 'w8a8')
CONTEXTS = ('dense', 'fc1_only', 'fc2_only', 'pl36')


def select_mlp_indices(layers):
    """Choose by fixed architectural roles, never by evaluation outcomes."""
    indices = [layer['layer_index'] for layer in layers]
    if len(indices) != 49 or sorted(indices) != list(range(49)):
        raise ValueError('Expected each of the 49 Linear layers exactly once')
    for layer in layers:
        index = layer['layer_index']
        expected = 'head' if index == 48 else ('qkv', 'proj', 'fc1', 'fc2')[index % 4]
        if layer['family'] != expected:
            raise ValueError('Layer family does not match the frozen H576 inventory')
    return sorted(layer['layer_index'] for layer in layers if layer['family'] in {'fc1', 'fc2'})


def build_mlp_variant(base, layers, weights, precision):
    if precision not in PRECISIONS:
        raise ValueError('Unexpected precision')
    selected = select_mlp_indices(layers)
    before = base.SerializeToString()
    model = build_combination(base, layers, selected, weights, precision)
    if base.SerializeToString() != before:
        raise ValueError('Dense source graph was mutated')
    # build_combination independently checks exact initializer changes AND the
    # serialized topology/metadata skeleton, including all biases and scales.
    names = sorted(changed_weight_name(layer, precision) for layer in layers
                   if layer['layer_index'] in selected)
    return model, {
        'precision': precision, 'pruned_layer_indices': selected,
        'kept_dense_layer_indices': sorted(set(range(49)) - set(selected)),
        'changed_initializer_names': names,
        'graph_topology_metadata_biases_scales_and_other_initializers': 'UNCHANGED',
        'source_graph': 'UNCHANGED',
    }


def pin_previous(folder, protected):
    path = (folder / 'summary.json').resolve(strict=True)
    previous = json.loads(path.read_text(encoding='utf-8'))
    if previous['status'] != 'COMPLETE_DEVELOPMENT_DIAGNOSIS':
        raise ValueError('Unexpected prior family evidence')
    protected[path] = sha_file(path)
    for filename, expected in previous['source_hashes'].items():
        source = Path(filename).resolve(strict=True)
        if sha_file(source) != expected:
            raise ValueError('Previous source changed: ' + filename)
        protected[source] = expected
    for filename, expected in previous['artifacts'].items():
        artifact = (folder / filename).resolve(strict=True)
        if not artifact.is_relative_to(folder.resolve()) or sha_file(artifact) != expected:
            raise ValueError('Previous artifact changed: ' + filename)
        protected[artifact] = expected
    for record in previous['model_identities'].values():
        model = Path(record['path']).resolve(strict=True)
        if sha_file(model) != record['sha256']:
            raise ValueError('Previous model identity changed')
        protected[model] = record['sha256']
    return previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--archive', type=Path, default=ROOT / 'inputs/h576-runtime-public-ui.tar.gz')
    parser.add_argument('--candidate', type=Path, default=ROOT / 'candidates/h576_sparse24_pl36_v1')
    parser.add_argument('--previous', type=Path, default=ROOT / 'eval_results/family_sensitivity_imagenet100_train100_v1')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'inputs/imagenet100_dev100')
    args = parser.parse_args(argv)
    output, created = args.output_dir.resolve(), False
    try:
        if output.exists():
            raise ValueError('Output exists; choose a NEW directory, no overwrite')
        if not output.is_relative_to((ROOT / 'eval_results').resolve()):
            raise ValueError('New evidence must stay inside this project eval_results directory')
        if (np.__version__, ort.__version__, onnx.__version__, pillow_version) != ('1.26.4', '1.22.1', '1.17.0', '12.3.0'):
            raise ValueError('Use the frozen project NumPy/ORT/ONNX/Pillow environment')
        protected = {p.resolve(): sha_file(p) for pattern in ('*.py', '*.md', '*.txt', '.gitignore')
                     for p in ROOT.glob(pattern)}
        for path in (args.archive, args.candidate / 'candidate.json',
                     args.data_dir / 'labels.csv', args.data_dir / 'dataset_manifest.json'):
            protected[path.resolve(strict=True)] = sha_file(path)
        previous = pin_previous(args.previous, protected)
        if previous['preprocessing'] != PREPROCESS_SPEC:
            raise ValueError('Preprocessing differs from the previous control')
        manifest = json.loads((args.candidate / 'candidate.json').read_text(encoding='utf-8'))
        verify_receipt(args.candidate, manifest, protected)
        print('Checking frozen source, all existing masks, and MLP-only scope ...', flush=True)
        payload, layers, base_fp, base_q = read_source(args.archive)
        selected = select_mlp_indices(layers)
        audit, weights = audit_candidate(payload, layers, base_fp, base_q, args.candidate)
        selected_audit = [record for record in audit['layers'] if record['layer_index'] in selected]
        if len(selected_audit) != 24:
            raise ValueError('Expected 12 fc1 and 12 fc2 masks')
        samples = load_samples(args.data_dir, expected_count=100)
        with (args.data_dir / 'labels.csv').open(encoding='utf-8-sig', newline='') as stream:
            expected_images = {row['image']: row['image_sha256'] for row in csv.DictReader(stream)}
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
        if dataset_sha != previous['ordered_dataset_sha256']:
            raise ValueError('Not the same ordered development dataset')
        labels = np.array([sample.label for sample in samples], np.int64)
        policy = {
            'status': 'FIXED_ARCHITECTURAL_WHITELIST_BEFORE_NEW_INFERENCE',
            'pruned_families': ['fc1', 'fc2'], 'pruned_layer_indices': selected,
            'kept_dense_families': ['qkv', 'proj', 'head'],
            'kept_dense_layer_indices': sorted(set(range(49)) - set(selected)),
            'pruning_rule': 'Reuse existing audited K-axis magnitude 2-of-4 masks; no mask search or retraining',
            'precision_controls': list(PRECISIONS), 'new_model_executions': 4,
            'historical_context': 'Hash-pinned fc1-only, fc2-only and PL36 saved logits; not new inference',
            'hardware_route': 'UNCHANGED: qkv/fc1/fc2 PL; proj/head PS in frozen deployment',
            'execution': 'All new evaluations are full-model ORT CPU numerical references, NOT PL Sparse execution',
            'quality_limit': 'Same selection-used 100 public train development images; not independent acceptance',
        }
        output.mkdir(parents=True, exist_ok=False)
        created = True
        write_json(output / 'experiment_policy.json', policy)
        logits, models, graph_audits = {}, {}, []
        base = {'fp32': base_fp, 'w8a8': base_q}
        with np.load(args.previous / 'logits.npz', allow_pickle=False) as old:
            np.testing.assert_array_equal(old['labels'], labels)
            np.testing.assert_array_equal(old['images'], np.array([item['image'] for item in identities]))
            np.testing.assert_array_equal(old['image_sha256'], np.array([item['sha256'] for item in identities]))
            for precision in PRECISIONS:
                name = 'dense_' + precision
                logits[name], identity = run_model(base[precision], tensors)
                np.testing.assert_array_equal(logits[name], old[name])
                models[name] = {'family': 'none', 'precision': precision, 'pruned_layers': 0,
                                'origin': 'FRESH_INFERENCE_BITWISE_REPRODUCED', 'serialized_model_sha256': identity}
                print(f'DENSE {precision}: 100/100, previous logits reproduced bitwise', flush=True)
            for family in CONTEXTS[1:]:
                for precision in PRECISIONS:
                    name = family + '_' + precision
                    logits[name] = old[name].copy()
                    prior = previous['model_identities'][name]
                    models[name] = {'family': prior['family'], 'precision': precision,
                                    'pruned_layers': prior['pruned_layers'], 'origin': 'REUSED_HASH_PINNED_SAVED_LOGITS',
                                    'historical_model_file_sha256': prior['sha256']}
        for precision in PRECISIONS:
            model, graph_audit = build_mlp_variant(base[precision], layers, weights, precision)
            graph_audits.append(graph_audit)
            name = 'mlp24_' + precision
            logits[name], identity = run_model(model, tensors)
            models[name] = {'family': 'fc1+fc2', 'precision': precision, 'pruned_layers': 24,
                            'layer_indices': selected, 'origin': 'FRESH_INFERENCE_IN_MEMORY_GRAPH',
                            'serialized_model_sha256': identity}
            ranks = np.stack([np.argsort(-row, kind='stable')[:5] for row in logits[name]])
            print(f'MLP24 {precision}: Top1 {int((ranks[:, 0] == labels).sum())}/100, '
                  f'Top5 {int((ranks == labels[:, None]).any(axis=1).sum())}/100', flush=True)
        rankings = {}
        for name, metadata in models.items():
            baseline = logits['dense_' + metadata['precision']]
            metric, rankings[name] = measure(logits[name], labels, baseline, baseline, 'combined')
            models[name] = {**metric, **metadata}
            if not name.startswith('mlp24_'):
                for key, value in previous['models'][name].items():
                    if key in metric and metric[key] != value:
                        raise ValueError('Reused/fresh context metric differs from prior evidence')
        comparisons = {}
        for precision in PRECISIONS:
            pair, _ = metric_counts(logits['mlp24_' + precision], labels, logits['pl36_' + precision])
            comparisons['mlp24_vs_pl36_' + precision] = {
                'reference': 'pl36_' + precision, 'comparison': 'mlp24_' + precision,
                'top1_gain_pp': -pair['top1_drop_pp'], 'top5_gain_pp': -pair['top5_drop_pp'],
                'top1_recovered': pair['top1_recovered_errors'], 'top1_new_errors': pair['top1_new_errors'],
            }
        pair, _ = metric_counts(logits['mlp24_w8a8'], labels, logits['mlp24_fp32'])
        comparisons['mlp24_quantization'] = pair
        for path, digest in protected.items():
            if sha_file(path) != digest:
                raise ValueError('Protected input/source changed: ' + str(path))
        with (output / 'logits.npz').open('xb') as stream:
            np.savez_compressed(stream, **logits, labels=labels,
                                images=np.array([item['image'] for item in identities]),
                                image_sha256=np.array([item['sha256'] for item in identities]))
        with (output / 'predictions.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['model', 'image', 'image_sha256', 'label',
                                                       'top1', 'top5', 'top1_correct', 'top5_correct'])
            writer.writeheader()
            for name, ranks in rankings.items():
                for i, item in enumerate(identities):
                    writer.writerow({'model': name, 'image': item['image'], 'image_sha256': item['sha256'],
                                     'label': item['label'], 'top1': int(ranks[i, 0]),
                                     'top5': json.dumps(ranks[i].tolist()),
                                     'top1_correct': int(ranks[i, 0] == labels[i]),
                                     'top5_correct': int(labels[i] in ranks[i])})
        for precision in PRECISIONS:
            errors = error_rows(identities, logits, rankings, 'dense_' + precision, 'mlp24_' + precision)
            save_errors(output / (precision + '_mlp24_new_errors.csv'), errors)
        write_json(output / 'audit_mlp.json', {
            'status': 'PASS_EXACT_24_MLP_WEIGHTS_CHANGED_ONLY', 'graphs': graph_audits,
            'mask_layers': selected_audit,
            'selected_family_counts': dict(Counter(layer['family'] for layer in layers if layer['layer_index'] in selected)),
            'source_rule_audit': audit['independent_check'],
        })
        summary = {
            'schema': 'h576_mlp_only_diagnosis_v1', 'status': 'COMPLETE_DEVELOPMENT_DIAGNOSIS',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'images': 100,
            'classes_covered': len(set(labels.tolist())), 'ordered_dataset_sha256': dataset_sha,
            'source_archive_sha256': ARCHIVE_SHA256, 'policy': policy, 'models': models,
            'comparisons': comparisons, 'controls_bitwise_reproduced': ['dense_' + p for p in PRECISIONS],
            'new_model_executions': 4, 'reused_saved_model_outputs': 6, 'onnx_model_files_written': 0,
            'preprocessing': PREPROCESS_SPEC,
            'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'onnx': onnx.__version__,
                            'onnxruntime': ort.__version__, 'Pillow': pillow_version, 'intra_threads': 4,
                            'inter_threads': 1, 'execution': 'sequential', 'optimization': 'disabled', 'spinning': False},
            'source_hashes': {str(p): digest for p, digest in protected.items() if p not in {s.path for s in samples}},
            'artifacts': {p.name: sha_file(p) for p in sorted(output.iterdir())},
            'limits': ['Same selection-used public train images; not independent or formal quality acceptance',
                       'No training, mask re-selection, HLS/RTL, board access, routing changes or speed measurements',
                       'MLP24 is an architectural whitelist, not the earlier mixed prefix24',
                       'Historical contexts reuse identical-image saved outputs, with their provenance retained'],
        }
        write_json(output / 'summary.json', summary)
        print(json.dumps({'status': summary['status'],
                          'results': {name: {k: record[k] for k in ('top1_correct', 'top5_correct', 'top1_new_errors', 'top1_recovered_errors')}
                                      for name, record in models.items()},
                          'comparisons': comparisons, 'output': str(output)}, indent=2), flush=True)
        return 0
    except Exception as error:
        if created and not (output / 'summary.json').exists() and not (output / 'failure.json').exists():
            write_json(output / 'failure.json', {'status': 'FAIL', 'traceback': traceback.format_exc()})
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
