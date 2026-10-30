"""Read completed chunks, recompute logits, and report full 1800-image accuracy.

No XRT, device access, child processes, signals, retries, loads or unloads. All
source records stay unchanged. --out publishes ONE new JSON report exclusively.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import types

sys.dont_write_bytecode = True
import accuracy_metrics as metrics
import dataset
import release_compat
from verify_tools import verify, ROOT


def need(ok, why):
    if not ok:
        raise ValueError(why)


def load_closed_entry(plan_path, release):
    """Use only the current release or the explicitly pinned v1 predecessor."""
    current = {'manifest': dataset.identity(dataset.read(ROOT/'tools_manifest.json', 1024*1024)),
               'files': release['files']}
    entry, root, _ = release_compat.load_closed_entry(plan_path, current)
    return entry, root


def recompute_records(root, records):
    """Re-read ALL 1000 logits; a matching Top5 is insufficient."""
    import numpy as np
    need(np.__version__ == '1.26.4', 'frozen NumPy 1.26.4 required for independent review')
    out = []
    for record in records:
        folder = Path(root)/'samples'/('%06d' % record['ordinal'])
        cpu = np.load(io.BytesIO(dataset.read(folder/'cpu_reference.npy', 16384)), allow_pickle=False)
        pl = np.load(io.BytesIO(dataset.read(folder/'model_logits.npy', 16384)), allow_pickle=False)
        compared = metrics.compare(cpu, pl, record['label'])
        for key in ('CPU_logits_sha256', 'PL_logits_sha256', 'CPU_top5', 'PL_top5',
                    'bitwise_mismatches', 'max_abs_difference'):
            need(compared[key] == record[key], 'record differs from saved logits: '+str(record['ordinal'])+' '+key)
        out.append(dict(record, **{k: compared[k] for k in ('CPU_true_label_rank', 'PL_true_label_rank',
                    'CPU_top1_hit', 'PL_top1_hit', 'CPU_top5_hit', 'PL_top5_hit')}))
    return out


def merge_records(groups, samples, *, progress=False):
    records = [row for group in groups for row in group]
    need(records, 'no completed images; do not report an empty acceptance')
    records.sort(key=lambda row: row['ordinal'])
    need(len({r['ordinal'] for r in records}) == len(records), 'overlapping/replayed chunk images')
    need(len(records) <= len(samples), 'excessive sample count')
    if not progress:
        need(len(records) == 1800, 'full acceptance requires 1800 images; use --progress only for an incomplete report')
    result = metrics.aggregate(records, expected_count=len(records), expected_samples=samples[:len(records)])
    if len(records) != 1800:
        result.update(status='PARTIAL_PROGRESS_ONLY', full_1800_acceptance=False,
                      missing_images=1800-len(records), accuracy_verified=False)
    else:
        result['accuracy_verified'] = result['full_1800_acceptance']
    return result


def compare_dense(result, baseline, max_drop_pp=5.0):
    need(result.get('full_1800_acceptance') is True and baseline.get('full_1800_complete') is True,
         'both matched CPU4 full datasets must be complete before a five-pp claim')
    drops = {}
    for name in ('top1_pct', 'top5_pct'):
        drops[name.replace('_pct', '_drop_pp')] = baseline['accuracy'][name]-result['accuracy']['PL'][name]
    return {'reference': 'Same-dataset ARM CPU4 Dense W8A8; freshly measured',
            'Dense_CPU4': baseline['accuracy'], 'B24_PS_PL4': result['accuracy']['PL'],
            **drops, 'max_allowed_drop_pp': max_drop_pp,
            'within_5pp_both_top1_and_top5': all(v <= max_drop_pp for v in drops.values()),
            'scope': 'Accuracy comparison of two model weights, not an isolated Sparse mode performance measurement'}


def review(plan_paths, *, progress=False, include_closed_prefix=False, dense_baseline=None):
    release = verify()
    need(os.name == 'posix' and os.geteuid() == 0, 'private board records must be reviewed on Linux as root')
    need(1 <= len(plan_paths) <= 1800 and len(set(map(str, plan_paths))) == len(plan_paths),
         'unique explicit chunk plans required; no latest-directory guessing')
    groups, pins, common_manifest, samples = [], [], None, None
    for path in plan_paths:
        entry, root = load_closed_entry(path, release)
        rows = entry.read_completed_records(path, allow_partial=include_closed_prefix)
        a = entry.api(private=True)
        plan = entry.load_plan(a, path, fresh=False)
        need(plan['measurement_profile'] == 'b24-batch-acceptance-cpu4-pl4-v1', 'CPU4/PL4 profile differs')
        raw = a.c.read_file(root/'dataset/manifest.json', private=True, limit=4*1024*1024)
        frozen = dataset.validate_manifest(raw); pin = dataset.identity(raw)
        need(pin == plan['dataset_manifest'], 'plan dataset identity changed')
        if common_manifest is None:
            common_manifest, samples = pin, frozen['samples']
        need(common_manifest == pin, 'chunks came from different prepared datasets')
        groups.append(recompute_records(root, rows))
        pins.append({'plan': str(path), 'plan_pin': dataset.identity(a.c.read_file(path, private=True)),
                     'batch_completion': dataset.identity(a.c.read_file(root/'batch_completion.json', private=True)),
                     'images': len(rows)})
    result = merge_records(groups, samples, progress=progress)
    historical = dataset.decode(dataset.read(ROOT/'data/B_cpu1_summary.json', 65536))
    need(dataset.identity(dataset.read(ROOT/'data/B_cpu1_summary.json', 65536)) ==
         {'bytes': 5897, 'sha256': '24cda5546f66980830fbe09fd5e4452bd72fc4411599819d7a096e6bdada5956'},
         'historical B summary changed')
    report = {'schema': 'opt50_b24_board_accuracy_review_v1', 'result': result,
            'source_model_sha256': dataset.SOURCE_MODEL, 'dataset_manifest': common_manifest,
            'source_tools_manifest': dataset.identity(dataset.read(ROOT/'tools_manifest.json', 1024*1024)),
            'chunks': pins, 'all_saved_logits_recomputed': True,
            'B_historical_CPU1_only': {k: historical['metrics'][k] for k in ('dense_w8a8', 'selected24_w8a8')},
            'five_pp_same_CPU4_dense_comparison': 'NOT_EVALUATED_USE_CPU_DENSE_BASELINE',
            'quality_loss_within_5pp_verified': False,
            'hardware_accessed_by_review': False, 'source_records_modified': False,
            'performance_verified': False,
            'scope': 'CPU4/PL4 batch accuracy on the exposed B1800 regression subset; not ImageNet full-validation accuracy or a steady-state benchmark'}
    if dense_baseline is not None:
        from cpu_dense_baseline import read_baseline
        baseline = read_baseline(dense_baseline, common_manifest, samples)
        report['same_CPU4_dense_baseline'] = baseline
        report['five_pp_same_CPU4_dense_comparison'] = compare_dense(result, baseline)
        report['quality_loss_within_5pp_verified'] = report['five_pp_same_CPU4_dense_comparison']['within_5pp_both_top1_and_top5']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plans', nargs='+', type=Path, required=True)
    parser.add_argument('--progress', action='store_true')
    parser.add_argument('--include-closed-prefix', action='store_true')
    parser.add_argument('--dense-baseline', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    try:
        report = review(args.plans, progress=args.progress, include_closed_prefix=args.include_closed_prefix,
                        dense_baseline=args.dense_baseline)
        raw = (json.dumps(report, indent=2, allow_nan=False)+'\n').encode()
        if args.out is not None:
            dataset.ordinary(args.out.parent)
            dataset.publish(args.out, raw)
        print(raw.decode(), end='')
        passed = report['result'].get('full_1800_acceptance') and (args.dense_baseline is None or report['quality_loss_within_5pp_verified'])
        return 0 if passed else 1
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print('BATCH_REVIEW_REFUSED_NO_HARDWARE_ACCESS: '+str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
