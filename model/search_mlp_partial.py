"""Bounded MLP-only 2:4 search on a frozen, verified partial image download.

The images become SEARCH/DEVELOPMENT data, not a holdout. No downloads,
training, HLS/runtime changes, mask changes or ONNX model-file writes.
Only new evidence under eval_results is created; source images remain in place.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import copy
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
from PIL import Image, __version__ as pillow_version

from diagnose_expanded_pruning import build_combination
from diagnose_layer_sensitivity import measure, run_model
from diagnose_mlp_only import select_mlp_indices
from diagnose_pruning import audit_candidate, verify_receipt
from download_imagenet100 import select_rows, validate_image
from eval_models import PREPROCESS_SPEC, preprocess_image, sha_file
from prune_h576 import ARCHIVE_SHA256, read_source, write_json
from screen_mlp_groups import compute_coverage

ROOT = Path(__file__).resolve().parent
PRECISIONS = ('fp32', 'w8a8')
ANCHOR = (38, 39, 42, 43, 46, 47)


def gate(metrics, count):
    """Exact five-percentage-point bound: at most floor(N/20) lost answers."""
    return all(20 * metrics[f'top{k}_lost_count'] <= count for k in (1, 5))


def annotate(metrics, baseline):
    result = dict(metrics)
    for k in (1, 5):
        result[f'top{k}_lost_count'] = baseline[f'top{k}_correct'] - result[f'top{k}_correct']
    return result


def risk(metrics, index=0):
    return (max(0, metrics['top1_drop_pp']), max(0, metrics['top5_drop_pp']),
            metrics['logit_relative_l2_vs_dense'], metrics['top1_new_errors'], index)


def pixel_sha(data):
    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert('RGB')
        return hashlib.sha256(str(rgb.size).encode('ascii') + b'\0' + rgb.tobytes()).hexdigest()


def read_partial(folder, count, old_folder, protected):
    plan_path = folder / 'download_plan.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    protected[plan_path.resolve()] = sha_file(plan_path)
    if plan['dataset'] != 'clane9/imagenet-100' or plan['split'] != 'validation':
        raise ValueError('Expected the requested public validation download')
    if plan['revision'] != '0519dc2f402a3a18c6e57f7913db059215eee25b':
        raise ValueError('Dataset revision differs from the pinned download')
    intended = select_rows(5000, plan['count'], plan['seed'])
    if intended != plan['row_indices']:
        raise ValueError('Download sampling plan is inconsistent')
    receipts = sorted((folder / '.state').glob('row_*.json'))
    if len(receipts) < count:
        raise ValueError(f'Need {count} completed receipts; found {len(receipts)}')
    # Freeze only the first count completed receipts. A concurrent downloader
    # may append more receipts, but those images are never consulted here.
    receipts = receipts[:count]
    mapped = {r['source_label']: r for r in plan['label_map']}
    old_path = old_folder / 'dataset_manifest.json'
    old = json.loads(old_path.read_text(encoding='utf-8'))
    protected[old_path.resolve()] = sha_file(old_path)
    old_hashes = {r['image_sha256'] for r in old['rows']}
    old_pixels = set()
    for r in old['rows']:
        path = (old_folder / r['image']).resolve(strict=True)
        if not path.is_relative_to(old_folder.resolve()) or sha_file(path) != r['image_sha256']:
            raise ValueError('Old development image identity changed')
        old_pixels.add(pixel_sha(path.read_bytes()))
        protected[path] = r['image_sha256']
    records, tensors, seen_raw, seen_pixels = [], [], set(), set()
    for receipt in receipts:
        raw_receipt = receipt.read_bytes()
        r = json.loads(raw_receipt)
        protected[receipt.resolve()] = hashlib.sha256(raw_receipt).hexdigest()
        index = r['row_index']
        if index not in intended or receipt.name != f'row_{index:07d}.json':
            raise ValueError('Receipt is outside the frozen sampling plan')
        label = mapped[r['source_label']]
        if r['label'] != label['label'] or r['wnid'] != label['wnid']:
            raise ValueError('ImageNet100 to model1000 label mapping mismatch')
        path = (folder / r['image']).resolve(strict=True)
        if not path.is_relative_to((folder / 'images').resolve()):
            raise ValueError('Image path escapes the download directory')
        data = path.read_bytes()
        raw_sha = hashlib.sha256(data).hexdigest()
        if raw_sha != r['image_sha256'] or len(data) != r['bytes']:
            raise ValueError('Incomplete or changed image: ' + r['image'])
        validate_image(data, r['width'], r['height'])
        decoded_sha = pixel_sha(data)
        if raw_sha in seen_raw or decoded_sha in seen_pixels:
            raise ValueError('Duplicate image content inside the chosen partial set')
        if raw_sha in old_hashes or decoded_sha in old_pixels:
            raise ValueError('Overlap with the previous dev100; no silent replacement')
        seen_raw.add(raw_sha)
        seen_pixels.add(decoded_sha)
        protected[path] = raw_sha
        tensor = preprocess_image(io.BytesIO(data))
        tensor.flags.writeable = False
        tensors.append(tensor)
        records.append({**r, 'decoded_rgb_sha256': decoded_sha, 'source_image_path': str(path),
                        'receipt_sha256': protected[receipt.resolve()]})
    classes = Counter(r['source_label'] for r in records)
    audit = {'status': 'PASS_FROZEN_PARTIAL_PUBLIC_DOWNLOAD', 'images': len(records),
             'source_revision': plan['revision'], 'split': plan['split'],
             'source_classes_covered': len(classes), 'class_counts': dict(sorted(classes.items())),
             'row_indices': [r['row_index'] for r in records],
             'duplicate_raw_or_decoded_images': 0, 'overlap_with_old_dev100_raw_or_decoded': 0,
             'sampling_limit': 'Downloaded prefix of source-row-sorted random plan; incomplete and class-biased',
             'scope': 'Explicitly repurposed for search; NOT independent acceptance or full ImageNet-1k quality',
             'unseen_download_rows': 'Not opened, predicted, or used for selection',
             'original_download_directory': 'UNCHANGED'}
    return tensors, records, audit


def nested_sets(order):
    return [tuple(sorted(order[:n])) for n in range(1, len(order)+1)]


def evaluate_one(base, layers, weights, selected, tensors):
    model = build_combination(base, layers, list(selected), weights, 'w8a8')
    return run_model(model, tensors)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'inputs/imagenet100_val1000_20261022')
    parser.add_argument('--count', type=int, default=523)
    parser.add_argument('--max-w8-evaluations', type=int, default=160)
    parser.add_argument('--fp32-shortlist', type=int, default=12)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args(argv)
    output, created = args.output_dir.resolve(), False
    started = time.monotonic()
    try:
        if output.exists():
            raise ValueError('Choose a NEW output directory; no overwrite')
        if not output.is_relative_to((ROOT/'eval_results').resolve()):
            raise ValueError('Evidence must remain under eval_results')
        if args.count < 1 or args.max_w8_evaluations < 80 or args.workers not in (1, 2):
            raise ValueError('Invalid bounded-search parameters')
        if (np.__version__, ort.__version__, onnx.__version__, pillow_version) != ('1.26.4', '1.22.1', '1.17.0', '12.3.0'):
            raise ValueError('Use the frozen project environment')
        protected = {p.resolve(): sha_file(p) for pattern in ('*.py', '*.md', '*.txt', '.gitignore')
                     for p in ROOT.glob(pattern)}
        archive = ROOT/'inputs/h576-runtime-public-ui.tar.gz'
        candidate = ROOT/'candidates/h576_sparse24_pl36_v1'
        prior_folder = ROOT/'eval_results/mlp_group_screen_imagenet100_train100_20261022_v1'
        for path in (archive, candidate/'candidate.json', prior_folder/'summary.json', prior_folder/'experiment_policy.json'):
            protected[path.resolve()] = sha_file(path)
        prior = json.loads((prior_folder/'summary.json').read_text(encoding='utf-8'))
        if prior['status'] != 'COMPLETE_DEVELOPMENT_GROUP_SCREEN':
            raise ValueError('Unexpected old group screen')
        for name, expected in prior['artifacts'].items():
            path = (prior_folder/name).resolve(strict=True)
            if not path.is_relative_to(prior_folder.resolve()) or sha_file(path) != expected:
                raise ValueError('Old screen artifact changed')
            protected[path] = expected
        print('Verifying completed image receipts, source masks, and old evidence ...', flush=True)
        tensors, records, data_audit = read_partial(args.data_dir.resolve(), args.count,
                                                   ROOT/'inputs/imagenet100_dev100', protected)
        manifest = json.loads((candidate/'candidate.json').read_text(encoding='utf-8'))
        verify_receipt(candidate, manifest, protected)
        payload, layers, base_fp, base_q = read_source(archive)
        audit, weights = audit_candidate(payload, layers, base_fp, base_q, candidate)
        allowed = set(select_mlp_indices(layers))
        labels = np.array([r['label'] for r in records], np.int64)
        n = len(labels)
        policy = {'status': 'ADAPTIVE_RULES_FIXED_BEFORE_CURRENT_IMAGE_PREDICTIONS',
                  'images': n, 'pruned_families': ['fc1', 'fc2'], 'kept_dense_families': ['qkv', 'proj', 'head'],
                  'max_top1_drop_pp': 5, 'max_top5_drop_pp': 5,
                  'exact_gate': '20 * (Dense correct - candidate correct) <= N for BOTH Top1 and Top5',
                  'max_lost_correct_answers_at_this_N': n//20, 'new_errors_cap': None,
                  'stage_order': ['Dense and fixed six-layer anchor', '24 current-set W8 singleton probes',
                                  'Current-risk singleton prefixes, paired-block prefixes, late-block pairs',
                                  'All 44 earlier dev100 groups as fixed alternative seeds',
                                  'Beam width 2, add-one growth; if stuck at a size, bounded one-swap probes'],
                  'max_unique_w8_groups': args.max_w8_evaluations,
                  'current_risk': 'nonnegative Top1 loss, nonnegative Top5 loss, logit relative L2, new errors, layer index',
                  'search_goal': 'Maximum tested passing MLP layer count, then lower worst Top1/Top5 loss, logit displacement, indices',
                  'fp32_validation': 'Check highest-ranked W8-passing candidates until both-precision pass; at most configured shortlist, plus anchor',
                  'fp32_shortlist_max': args.fp32_shortlist, 'selection_set': 'ALL current images; NOT a holdout',
                  'workers': args.workers, 'each_session_threads': 4,
                  'mask_rule': 'Existing audited magnitude K-axis two retained slots out of four, UNCHANGED',
                  'training': False, 'model_files_written': False, 'board_access': False, 'route_changes': False,
                  'limit': 'Heuristic finite search, NOT all 2^24 subsets or proof of global optimum'}
        output.mkdir(parents=True, exist_ok=False)
        created = True
        write_json(output/'experiment_policy.json', policy)
        write_json(output/'dataset_snapshot.json', records)
        write_json(output/'dataset_audit.json', data_audit)
        write_json(output/'layer_inventory.json', [
            {k: r[k] for k in ('layer_index', 'layer_id', 'family', 'M', 'K', 'N')} for r in layers])
        write_json(output/'mask_audit.json', {'status': audit['status'],
            'independent_check': audit['independent_check'],
            'layers': [r for r in audit['layers'] if r['family'] in ('fc1', 'fc2')]})
        print(f'INPUT_FROZEN images={n}, classes={data_audit["source_classes_covered"]}, '
              f'max_lost_answers={n//20}; new download rows ignored', flush=True)
        logits, models, groups, cache = {}, {}, {}, {}
        baseline = {}
        trace = (output/'search_trace.jsonl').open('x', encoding='utf-8', newline='\n')

        def event(value):
            trace.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
            trace.flush()

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {p: pool.submit(run_model, b, tensors) for p, b in (('fp32', base_fp), ('w8a8', base_q))}
            for precision in PRECISIONS:
                values, identity = futures[precision].result()
                name = 'dense_'+precision
                logits[name] = values
                m, _ = measure(values, labels, values, values, 'control')
                baseline[precision] = annotate(m, m)
                models[name] = {'precision': precision, 'layer_indices': [], 'metrics': baseline[precision],
                                'serialized_model_sha256': identity}
                print(f'DENSE {precision}: Top1={m["top1_correct"]}/{n} Top5={m["top5_correct"]}/{n}', flush=True)

            def key_for_group(g):
                m = g['metrics']['w8a8']
                return (-len(g['layer_indices']), max(m['top1_drop_pp'], m['top5_drop_pp']),
                        *risk(m)[:4], tuple(g['layer_indices']))

            def evaluate(requests, stage):
                unique = []
                seen = set()
                for selected in requests:
                    selected = tuple(sorted(selected))
                    if not selected or len(selected) != len(set(selected)) or not set(selected).issubset(allowed):
                        raise ValueError('MLP whitelist/duplicate violation')
                    if selected not in cache and selected not in seen:
                        unique.append(selected)
                        seen.add(selected)
                unique = unique[:max(0, args.max_w8_evaluations-len(cache))]
                # Only two simultaneous sessions: bounded memory and CPU use.
                for offset in range(0, len(unique), args.workers):
                    batch = unique[offset:offset+args.workers]
                    futures = [(selected, pool.submit(evaluate_one, base_q, layers, weights, selected, tensors))
                               for selected in batch]
                    for selected, future in futures:
                        values, identity = future.result()
                        name = f'c{len(cache)+1:03d}'
                        cache[selected] = name
                        m, _ = measure(values, labels, logits['dense_w8a8'], logits['dense_w8a8'], 'combined')
                        m = annotate(m, baseline['w8a8'])
                        logits[name+'_w8a8'] = values
                        models[name+'_w8a8'] = {'precision': 'w8a8', 'layer_indices': list(selected),
                                               'metrics': m, 'serialized_model_sha256': identity}
                        g = {'name': name, 'stage_first_evaluated': stage, 'layer_indices': list(selected),
                             'layers': len(selected), 'metrics': {'w8a8': m}, 'w8_gate': gate(m, n),
                             'compute': compute_coverage(layers, selected)}
                        groups[name] = g
                        event({'kind': 'group', **g})
                        print(f'W8 {len(cache):03d}/{args.max_w8_evaluations} {stage} {name} layers={len(selected):02d} '
                              f'Top1={m["top1_correct"]}/{n} Top5={m["top5_correct"]}/{n} '
                              f'drops={m["top1_drop_pp"]:+.3f}/{m["top5_drop_pp"]:+.3f}pp gate={g["w8_gate"]}', flush=True)

            evaluate([ANCHOR], 'fixed_anchor')
            # Preserve the initial candidate's result before adapting the grouping.
            anchor_name = cache[ANCHOR]
            anchor_model = build_combination(base_fp, layers, list(ANCHOR), weights, 'fp32')
            values, identity = run_model(anchor_model, tensors)
            logits[anchor_name+'_fp32'] = values
            m, _ = measure(values, labels, logits['dense_fp32'], logits['dense_fp32'], 'combined')
            m = annotate(m, baseline['fp32'])
            groups[anchor_name]['metrics']['fp32'] = m
            groups[anchor_name]['both_gate'] = gate(m, n) and groups[anchor_name]['w8_gate']
            models[anchor_name+'_fp32'] = {'precision': 'fp32', 'layer_indices': list(ANCHOR), 'metrics': m,
                                         'serialized_model_sha256': identity}
            event({'kind': 'anchor_fp32', 'group': anchor_name, 'metrics': m})
            evaluate([(i,) for i in sorted(allowed)], 'current_singletons')
            order = sorted(allowed, key=lambda i: risk(groups[cache[(i,)]]['metrics']['w8a8'], i))
            block_order = sorted(range(12), key=lambda b: (
                max(risk(groups[cache[(4*b+2,)]]['metrics']['w8a8'])[:-1],
                    risk(groups[cache[(4*b+3,)]]['metrics']['w8a8'])[:-1]), b))
            pair_order = [i for b in block_order for i in (4*b+2, 4*b+3)]
            suffix_order = [i for b in reversed(range(12)) for i in (4*b+2, 4*b+3)]
            plans = nested_sets(order)
            plans += [tuple(sorted(pair_order[:k])) for k in range(2, 25, 2)]
            plans += [tuple(sorted(suffix_order[:k])) for k in range(2, 25, 2)]
            for family in ('fc1', 'fc2'):
                family_order = [i for i in order if i % 4 == (2 if family == 'fc1' else 3)]
                plans += nested_sets(family_order)
            write_json(output/'current_ranked_plan.json', {'from': 'All current-set singletons; data now used for selection',
                       'singleton_order': order, 'block_order': block_order, 'groups_before_combination_inference': plans})
            evaluate(plans, 'current_ranked_prefixes')
            old_plans = [tuple(g['layer_indices']) for g in prior['groups']]
            evaluate(old_plans, 'old_dev100_fixed_groups')
            # Adaptive beam growth. Each neighbor is still evaluated on ALL N
            # images. No hidden subset screening, threshold changes or training.
            tried_beams = set()
            while len(cache) < args.max_w8_evaluations:
                passing = sorted((g for g in groups.values() if g['w8_gate']), key=key_for_group)
                if not passing:
                    break
                best_count = passing[0]['layers']
                beam = [g for g in passing if g['layers'] == best_count][:2]
                beam_signature = tuple(tuple(g['layer_indices']) for g in beam)
                if beam_signature in tried_beams or best_count == 24:
                    break
                tried_beams.add(beam_signature)
                additions = []
                for g in beam:
                    present = set(g['layer_indices'])
                    additions += [tuple(sorted(present | {i})) for i in order if i not in present]
                before = len(cache)
                event({'kind': 'beam_add_plan', 'seeds': [g['name'] for g in beam], 'requests': additions})
                evaluate(additions, 'beam_add_one')
                grown = any(g['w8_gate'] and g['layers'] > best_count for g in groups.values())
                if grown:
                    continue
                swaps = []
                for g in beam:
                    present = set(g['layer_indices'])
                    for remove in reversed(order):
                        if remove in present:
                            swaps += [tuple(sorted((present-{remove}) | {add})) for add in order if add not in present]
                event({'kind': 'beam_swap_plan', 'seeds': [g['name'] for g in beam], 'requests': swaps})
                evaluate(swaps, 'beam_one_swap')
                if len(cache) == before:
                    break
            ranked = sorted((g for g in groups.values() if g['w8_gate']), key=key_for_group)
            checks = 0
            preferred = None
            for g in ranked:
                if 'fp32' not in g['metrics']:
                    if checks >= args.fp32_shortlist:
                        break
                    checks += 1
                    model = build_combination(base_fp, layers, g['layer_indices'], weights, 'fp32')
                    values, identity = run_model(model, tensors)
                    name = g['name']+'_fp32'
                    logits[name] = values
                    m, _ = measure(values, labels, logits['dense_fp32'], logits['dense_fp32'], 'combined')
                    m = annotate(m, baseline['fp32'])
                    g['metrics']['fp32'] = m
                    g['both_gate'] = gate(m, n) and g['w8_gate']
                    models[name] = {'precision': 'fp32', 'layer_indices': g['layer_indices'], 'metrics': m,
                                    'serialized_model_sha256': identity}
                    event({'kind': 'fp32_confirmation', 'group': g['name'], 'metrics': m, 'both_gate': g['both_gate']})
                    print(f'FP32_CONFIRM {g["name"]}: layers={g["layers"]} '
                          f'Top1={m["top1_correct"]}/{n} Top5={m["top5_correct"]}/{n} both_gate={g["both_gate"]}', flush=True)
                if g.get('both_gate'):
                    preferred = g
                    break
            if preferred is None:
                eligible = [g for g in groups.values() if g.get('both_gate')]
                preferred = min(eligible, key=key_for_group) if eligible else None
            repeated = {'status': 'NO_BOTH_PRECISION_PASS'}
            if preferred:
                repeated = {'status': 'PASS_BITWISE_REEXECUTION', 'group': preferred['name'], 'precisions': []}
                repeat_futures = {p: pool.submit(run_model, build_combination(b, layers, preferred['layer_indices'], weights, p), tensors)
                                  for p, b in (('fp32', base_fp), ('w8a8', base_q))}
                for p in PRECISIONS:
                    values, identity = repeat_futures[p].result()
                    name = preferred['name']+'_'+p
                    np.testing.assert_array_equal(values, logits[name])
                    if identity != models[name]['serialized_model_sha256']:
                        raise ValueError('Repeated graph identity mismatch')
                    repeated['precisions'].append(p)
        trace.close()
        for path, expected in protected.items():
            if sha_file(path) != expected:
                raise ValueError('Protected source/image changed: ' + str(path))
        with (output/'logits.npz').open('xb') as stream:
            np.savez_compressed(stream, **logits, labels=labels, row_indices=np.array([r['row_index'] for r in records]))
        with (output/'groups.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            fields = ['group', 'stage', 'layers', 'layer_indices', 'w8_top1_count', 'w8_top5_count',
                      'w8_top1_pct', 'w8_top5_pct', 'w8_top1_drop_pp', 'w8_top5_drop_pp', 'w8_gate',
                      'fp32_top1_count', 'fp32_top5_count', 'both_gate']
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for g in groups.values():
                m, f = g['metrics']['w8a8'], g['metrics'].get('fp32', {})
                writer.writerow({'group': g['name'], 'stage': g['stage_first_evaluated'], 'layers': g['layers'],
                                 'layer_indices': json.dumps(g['layer_indices']), 'w8_top1_count': m['top1_correct'],
                                 'w8_top5_count': m['top5_correct'], 'w8_top1_pct': m['top1_pct'], 'w8_top5_pct': m['top5_pct'],
                                 'w8_top1_drop_pp': m['top1_drop_pp'], 'w8_top5_drop_pp': m['top5_drop_pp'],
                                 'w8_gate': g['w8_gate'], 'fp32_top1_count': f.get('top1_correct'),
                                 'fp32_top5_count': f.get('top5_correct'), 'both_gate': g.get('both_gate', 'NOT_CHECKED')})
        write_json(output/'proposal.json', {'status': 'SEARCH_SHORTLIST_NOT_INDEPENDENTLY_ACCEPTED_NOT_DEPLOYED',
            'preferred': preferred, 'anchor': groups[anchor_name], 'new_unseen_images_needed': True,
            'all_523_images_used_for_selection': True, 'model_and_HLS_runtime_files_written': False})
        summary = {'schema': 'h576_partial_mlp_search_v1', 'status': 'COMPLETE_PARTIAL_DEVELOPMENT_SEARCH',
                   'created_utc': datetime.now(timezone.utc).isoformat(), 'images': n,
                   'classes_covered': data_audit['source_classes_covered'], 'policy': policy, 'dataset_audit': data_audit,
                   'source_archive_sha256': ARCHIVE_SHA256, 'baseline': baseline, 'groups': list(groups.values()),
                   'models': models, 'unique_w8_groups': len(cache), 'w8_passing_groups': len(ranked),
                   'preferred': preferred['name'] if preferred else None, 'anchor': anchor_name,
                   'reproducibility': repeated, 'preprocessing': PREPROCESS_SPEC,
                   'source_hashes': {str(p): expected for p, expected in protected.items()},
                   'elapsed_seconds': time.monotonic()-started,
                   'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'onnx': onnx.__version__,
                                   'onnxruntime': ort.__version__, 'Pillow': pillow_version,
                                   'concurrent_sessions': args.workers, 'each_session_intra_threads': 4,
                                   'inter_threads': 1, 'optimization': 'disabled', 'spinning': False},
                   'artifacts': {p.name: sha_file(p) for p in sorted(output.iterdir())},
                   'limits': ['All current images are search data, not independent statistical acceptance',
                              'Downloaded prefix covers only part of the 100 classes and is class-biased',
                              '5pp bounds apply to empirical counts on this snapshot, not guaranteed population accuracy',
                              'Finite adaptive search, no global maximum guarantee',
                              'No training, new masks, ONNX model files, HLS, runtime, routing or board changes']}
        write_json(output/'summary.json', summary)
        print(json.dumps({'status': summary['status'], 'images': n, 'classes': summary['classes_covered'],
                          'unique_w8_groups': len(cache), 'preferred': preferred,
                          'seconds': summary['elapsed_seconds'], 'output': str(output)}, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as error:
        if created and not (output/'summary.json').exists() and not (output/'failure.json').exists():
            write_json(output/'failure.json', {'status': 'FAIL', 'traceback': traceback.format_exc()})
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
