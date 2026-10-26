"""Freeze the user-selected c062 MLP12 candidate; no search, training or board use.

Writes only a NEW directory below candidates/. Reuses the audited masks and
requires exact graph/logit identity with the previously tested c062 evidence.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

import numpy as np
import onnx
import onnxruntime as ort
from PIL import __version__ as pillow_version

from diagnose_expanded_pruning import build_combination
from diagnose_layer_sensitivity import changed_weight_name
from diagnose_pruning import (audit_candidate, independent_positions,
                              mask_from_positions, verify_receipt)
from download_imagenet100 import model_classes
from eval_models import PREPROCESS_SPEC, preprocess_image, sha_file
from prune_h576 import (arrays, compare_unmodified, ort_session, read_source,
                        write_json)
from sparse24 import dense_golden, pack, quantize_weight, slot_gemm, unpack
from verify_partial_search import accepted, recalculate

ROOT = Path(__file__).resolve().parent
SELECTED = (26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47)
EXPECTED_GRAPH_SHA = {
    'fp32': 'c09874e92c8c0cde012e1a999cb885eb5ca4caba74ca89c8a8ba025dab0c74d8',
    'w8a8': '50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49',
}
SEARCH = ROOT / 'eval_results/mlp_partial523_search_20261022_v1'
JOINT = ROOT / 'eval_results/mlp_partial523_jointcheck_20261022_v1'
PRIOR = ROOT / 'candidates/h576_sparse24_pl36_v1'
PRIOR_SUMMARY_SHA = {
    SEARCH: 'b698b2f2c41182e226cf72b91da3355d627422688e389ea7519a78587561b6eb',
    JOINT: '9a362815255cfdede634078000b8ce6a110e4c3c5122cfd41a777b5ad2e3055e',
}
MODEL_FILES = {'fp32': 'pruned_fp32.onnx', 'w8a8': 'pruned_w8a8_reference.onnx'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def json_read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def new_output(path):
    path = path.resolve()
    require(path.is_relative_to((ROOT / 'candidates').resolve())
            and path != (ROOT / 'candidates').resolve(), 'Output must be below candidates/')
    require(not path.exists(), 'Output exists; choose a NEW directory, never overwrite')
    return path


def selection_inventory(layers, selected=SELECTED):
    require(tuple(selected) == SELECTED, 'Only the user-selected c062 twelve layers are authorized')
    require(len(layers) == 49 and {r['layer_index'] for r in layers} == set(range(49)),
            'Expected exact 49-layer inventory')
    records = []
    for layer in sorted(layers, key=lambda r: r['layer_index']):
        i = layer['layer_index']
        family = 'head' if i == 48 else ('qkv', 'proj', 'fc1', 'fc2')[i % 4]
        suffix = 'attn' if family in {'qkv', 'proj'} else 'mlp'
        expected_id = 'head' if i == 48 else f'blocks.{i // 4}.{suffix}.{family}'
        require(layer['family'] == family and layer['layer_id'] == expected_id,
                'Layer ID/family disagrees with fixed H576 topology')
        sparse = i in selected
        records.append({
            'layer_index': i, 'layer_id': expected_id, 'family': family,
            'block_zero_based': None if i == 48 else i // 4,
            'shape_MKN': [layer[k] for k in ('M', 'K', 'N')],
            'mode': 'sparse24_w8a8_logical' if sparse else 'dense_w8a8',
            'original_route': 'PS' if family in {'proj', 'head'} else 'PL',
            'weight_changed': sparse,
            'weights': f'weights/layer_{i:02d}.npz' if sparse else None,
            'integer_case': f'cases/layer_{i:02d}.npz' if sparse else None,
            'deployment_route_modified': False,
        })
    return records


def build_export(base, layers, weights, precision):
    selection_inventory(layers)
    require(precision in MODEL_FILES, 'Unknown precision')
    return build_combination(base, layers, SELECTED, weights, precision)


def audit_weight(original, original_q, original_scale, original_bias, stored):
    """Independent six-pair selection plus full-K and two-slot reconstruction."""
    positions = independent_positions(original)
    mask = mask_from_positions(positions, original.shape)
    expected = np.where(mask, original, np.float32(0))
    wq, sw = quantize_weight(expected)
    expected_dtype = {'positions': np.uint8, 'mask': np.bool_, 'W_fp32': np.float32,
                      'Wq': np.int8, 'values': np.int8, 'sw': np.float32, 'bias': np.float32}
    require(set(stored) == set(expected_dtype), 'Unexpected weight NPZ fields')
    for name, dtype in expected_dtype.items():
        require(stored[name].dtype == dtype, 'Unexpected tensor dtype: ' + name)
    for name, value in {'positions': positions, 'mask': mask, 'W_fp32': expected,
                        'Wq': wq, 'sw': sw, 'bias': original_bias}.items():
        np.testing.assert_array_equal(stored[name], value)
    np.testing.assert_array_equal(mask.reshape(-1, 4, original.shape[1]).sum(axis=1), 2)
    np.testing.assert_array_equal(sw.ravel(), original_scale)
    np.testing.assert_array_equal(wq, np.where(mask, original_q, 0))
    np.testing.assert_array_equal(unpack(stored['values'], stored['positions']), wq)
    np.testing.assert_array_equal(pack(wq, positions), stored['values'])
    return {'groups_checked': original.size // 4, 'retained_slots': int(mask.sum()),
            'int8_nonzero_values': int(np.count_nonzero(wq)),
            'mask_sha256': hashlib.sha256(mask.tobytes()).hexdigest(),
            'positions_sha256': hashlib.sha256(positions.tobytes()).hexdigest(),
            'scale_bias_and_retained_int8_unchanged': True}


def protect(path, protected, expected=None):
    path = path.resolve(strict=True)
    digest = sha_file(path)
    require(expected is None or digest == expected, 'Input identity mismatch: ' + str(path))
    require(path not in protected or protected[path] == digest, 'Input changed during export')
    protected[path] = digest
    return digest


def prior_evidence(folder, protected):
    protect(folder / 'summary.json', protected, PRIOR_SUMMARY_SHA[folder])
    summary = json_read(folder / 'summary.json')
    for name, digest in summary['artifacts'].items():
        path = (folder / name).resolve(strict=True)
        require(path.is_relative_to(folder.resolve()), 'Prior artifact escapes its directory')
        protect(path, protected, digest)
    return summary


def load_dataset(records, base, count, protected):
    require(len(records) == count, 'Wrong frozen dataset length')
    tensors, identities = [], []
    for record in records:
        path = (base / record['image']).resolve(strict=True)
        require(path.is_relative_to(base.resolve()), 'Image path escape')
        digest = protect(path, protected, record['image_sha256'])
        tensors.append(preprocess_image(io.BytesIO(path.read_bytes())))
        identities.append({'image': record['image'], 'label': record['label'],
                           'image_sha256': digest, 'row_index': record.get('row_index')})
    require(len({r['image_sha256'] for r in identities}) == count, 'Duplicate frozen image')
    labels = np.array([r['label'] for r in identities], np.int64)
    require(np.all((labels >= 0) & (labels < 1000)), 'Invalid ImageNet1k label')
    return tensors, labels, identities


def infer(model, tensors, tag):
    engine = ort_session(model)
    require(len(engine.get_inputs()) == len(engine.get_outputs()) == 1,
            'Expected full classifier with one input/output')
    result = []
    for i, tensor in enumerate(tensors, 1):
        value = engine.run(None, {engine.get_inputs()[0].name: tensor})[0]
        require(value.shape == (1, 1000) and value.dtype == np.float32
                and np.isfinite(value).all(), 'Invalid classifier logits')
        result.append(value[0].copy())
        if i % 50 == 0 or i == len(tensors):
            print(f'{tag}: {i}/{len(tensors)}', flush=True)
    return np.stack(result)


def bitwise_equal(actual, expected):
    require(actual.shape == expected.shape and actual.dtype == expected.dtype
            and actual.tobytes() == expected.tobytes(), 'Logits differ from frozen c062 evidence')


def quality_flags(evaluation):
    gates = {p: all(accepted(evaluation[s][p], evaluation[s][p]['images'])
                    for s in ('old100', 'partial523')) for p in MODEL_FILES}
    return {'w8a8_both_development_sets_within_5pp': gates['w8a8'],
            'fp32_both_development_sets_within_5pp': gates['fp32'],
            'independent_quality_acceptance': 'NOT_RUN', 'fine_tuning': 'NOT_RUN',
            'sparse_HLS_and_board': 'NOT_IMPLEMENTED_OR_TESTED'}


def readme():
    return '''# H576：12 层 2:4 剪枝候选 c062

本目录是已选方案的正式文件导出，不是新一轮剪枝搜索，也没有进行训练。

## 剪了哪些层

仅对零起始编号 Block 6～11 的 `mlp.fc1`、`mlp.fc2` 做 2:4，共 12 个 Linear。
层索引：26、27、30、31、34、35、38、39、42、43、46、47。
其余 37 个 Linear 保持 Dense；qkv、proj、head 均未剪枝，bias、scale、图拓扑不变。
原部署路由仍为 qkv/fc1/fc2 在 PL、proj/head 在 PS；本导出没有修改部署。

## 主要文件

- `pruned_w8a8_reference.onnx`：完整的 W8A8 分类模型，优先用它做后续软件验证。
- `pruned_fp32.onnx`：相同掩码的完整 FP32 对照模型，不是 PyTorch 可训练 checkpoint。
- `layers.json`、`selection.json`：全部 49 层的模式/原路由，以及选中的 12 层。
- `weights/layer_XX.npz`：仅 12 层的 FP32/INT8 权重、掩码、位置、scale、bias。
- `cases/layer_XX.npz`：原公开 Dense 输入快照及边界输入的 INT32 算术对照；不是新模型逐层传播特征。
- `protocol.json`：逻辑数据格式；`preprocessing.json`：图像预处理；`labels_imagenet1k.json`：1000 类标签顺序。
- `evaluation.json`、`verification.json`、`evidence/`：新导出模型重跑与旧记录的逐位核对。
- `provenance.json`、`candidate.json`：来源、代码和文件 SHA256。只有最终 `candidate.json` 存在且校验通过才算导出完成。

## 当前精度范围

以下是已经用于筛选的开发数据，不是独立验收集。导出后重新推理全部 623 张，
两种精度的全部 1000 维 logits 均须与原筛选记录逐位相同。

| 开发集 | 模型 | Top1 | Top5 |
|---|---|---:|---:|
| 523 张 | Dense W8A8 | 279/523 | 427/523 |
| 523 张 | 本候选 W8A8 | 262/523 | 405/523 |
| 旧 100 张 | Dense W8A8 | 58/100 | 82/100 |
| 旧 100 张 | 本候选 W8A8 | 53/100 | 80/100 |

W8A8 的 Top1/Top5 损失在两组上均不超过 5 个百分点。
已知限制：FP32 在旧 100 张上为 52/100、80/100，Top1 损失 **6 个百分点**；
不能将本方案描述为双精度均通过 5 点门槛。523 张只覆盖 48 个来源类别且是下载前缀，
旧 100 张也是开发数据；下一步需要未参与选层的新图片验证，不能据此承诺总体精度。

## 2:4 格式和硬件边界

逻辑权重为 `[K,N]`，沿 K 每连续 4 个位置保留 2 个槽位；FP32 ONNX 原 initializer 为 `[N,K]`。
`positions` 是 `uint8[K/4,N,2]`，`values` 是 `int8[K/4,N,2]`，位置升序、范围 0～3。
保留槽位量化后允许为零，因此不是保证每组恰好两个非零 INT8 数。
W8 ONNX 用 FP32 张量表达 INT8 数值并执行数值参考运算，不是硬件跳零实现。
物理打包、AXI 接口和元数据位宽尚未冻结；NPZ 的压缩文件大小不代表板端带宽或加速效果。

本包不是 C 冻结 runtime 的可直接替换部署包，不能混用旧权重/模型身份。
尚未修改 HLS、测试稀疏 PL、进行微调或独立质量验收。旧 PL36 候选保持原样。

## 重新评估示例

在 `h576_sparse24` 的 PowerShell 中执行（只读取模型/图片，写新的评估结果文件）：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 -B .\\eval_models.py `
  --dense-model .\\inputs\\baseline\\h576_w8.onnx `
  --pruned-model .\\candidates\\h576_sparse24_mlp12_c062_v1\\pruned_w8a8_reference.onnx `
  --data-dir .\\inputs\\imagenet100_dev100 `
  --output-csv .\\eval_results\\mlp12_new_errors.csv
```

请不要再把旧 `h576_sparse24_pl36_v1` 中的 ONNX 当作本 12 层模型使用。
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=ROOT / 'candidates/h576_sparse24_mlp12_c062_v1')
    args = parser.parse_args(argv)
    output, created = args.output_dir.resolve(), False
    started = time.monotonic()
    try:
        output = new_output(output)
        require(np.__version__ == '1.26.4' and ort.__version__ == '1.22.1'
                and onnx.__version__ == '1.17.0' and pillow_version == '12.3.0',
                'Use the frozen project environment')
        protected = {}
        for pattern in ('*.py', '*.md', '*.txt', '.gitignore'):
            for path in ROOT.glob(pattern):
                protect(path, protected)
        archive = ROOT / 'inputs/h576-runtime-public-ui.tar.gz'
        protect(archive, protected)
        search, joint = (prior_evidence(folder, protected) for folder in (SEARCH, JOINT))
        require(joint['checked_groups']['c062']['layer_indices'] == list(SELECTED)
                and search['models']['c062_w8a8']['layer_indices'] == list(SELECTED),
                'Prior evidence is not the exact selected group')
        for precision, digest in EXPECTED_GRAPH_SHA.items():
            require(joint['model_identities']['c062_' + precision] == digest,
                    'Prior tested graph identity differs')
        protect(PRIOR / 'candidate.json', protected)
        verify_receipt(PRIOR, json_read(PRIOR / 'candidate.json'), protected)
        print('Auditing source archive and existing masks; no old files are changed ...', flush=True)
        payload, layers, fp32, w8 = read_source(archive)
        _, weights = audit_candidate(payload, layers, fp32, w8, PRIOR)
        inventory = selection_inventory(layers)
        models, graph_audit = {}, {}
        for precision, base in (('fp32', fp32), ('w8a8', w8)):
            model = build_export(base, layers, weights, precision)
            require(hashlib.sha256(model.SerializeToString()).hexdigest() == EXPECTED_GRAPH_SHA[precision],
                    'Rebuilt graph is not byte-identical to tested c062')
            models[precision] = model
            graph_audit[precision] = {'sha256': EXPECTED_GRAPH_SHA[precision],
                'changed_initializers': [changed_weight_name(r, precision) for r in layers
                                         if r['layer_index'] in SELECTED],
                'changed_weight_initializers': 12, 'unchanged_linear_weights': 37,
                'all_biases_scales_topology_metadata': 'UNCHANGED'}
        output.mkdir(parents=True, exist_ok=False)
        created = True
        for name in ('weights', 'cases', 'evidence'):
            (output / name).mkdir()
        for precision, model in models.items():
            path = output / MODEL_FILES[precision]
            onnx.save_model(model, path, save_as_external_data=False)
            require(sha_file(path) == EXPECTED_GRAPH_SHA[precision], 'Saved model identity differs')
            models[precision] = onnx.load(path, load_external_data=False)
            onnx.checker.check_model(models[precision])
            compare_unmodified((fp32 if precision == 'fp32' else w8).SerializeToString(),
                               models[precision], set(graph_audit[precision]['changed_initializers']))
        original_f, original_q = arrays(fp32), arrays(w8)
        layer_checks = []
        print('Models saved with the tested c062 hashes; checking 12 sparse packs and INT32 cases ...', flush=True)
        for layer in layers:
            i = layer['layer_index']
            if i not in SELECTED:
                continue
            for kind in ('weights', 'cases'):
                name = f'layer_{i:02d}.npz'
                shutil.copyfile(PRIOR / kind / name, output / kind / name)
                require(sha_file(output / kind / name) == sha_file(PRIOR / kind / name),
                        'Copied sparse data differs')
            with np.load(output / 'weights' / f'layer_{i:02d}.npz', allow_pickle=False) as saved:
                stored = {k: saved[k] for k in saved.files}
            check = audit_weight(np.ascontiguousarray(original_f[layer['layer_id'] + '.weight'].T),
                                 original_q[f'BQL_{i}_Wf'].astype(np.int8), original_q[f'BQL_{i}_sf'],
                                 original_q[f'BQL_{i}_bf'], stored)
            with np.load(output / 'cases' / f'layer_{i:02d}.npz', allow_pickle=False) as case:
                x = np.frombuffer(payload[f'assets/layer_inputs/{i}_Xq.bin'], np.int8).reshape(layer['M'], layer['K'])
                np.testing.assert_array_equal(case['Xq'], x)
                elements = 0
                for prefix in ('', 'edge_'):
                    x, expected = case[prefix + 'Xq'], case[prefix + 'Y_int32']
                    require(x.dtype == np.int8 and expected.dtype == np.int32, 'Case dtype differs')
                    np.testing.assert_array_equal(dense_golden(x, stored['Wq']), expected)
                    np.testing.assert_array_equal(slot_gemm(x, stored['values'], stored['positions']), expected)
                    elements += expected.size
            layer_checks.append(dict(check, layer_index=i, layer_id=layer['layer_id'],
                                     integer_output_elements_checked=elements, integer_mismatches=0))
        write_json(output / 'layers.json', inventory)
        write_json(output / 'selection.json', {
            'selection': 'USER_SELECTED_C062_MLP12', 'layer_indices': list(SELECTED),
            'block_indices_zero_based': list(range(6, 12)), 'families': ['fc1', 'fc2'],
            'pruned_linear_layers': 12, 'unchanged_linear_layers': 37,
            'original_route_counts': {'PL': 36, 'PS': 13}, 'routes_changed': False,
            'note': 'Explicit user selection; do not substitute search preferred c121 or joint preferred c087'})
        write_json(output / 'protocol.json', {
            'schema': 'h576_mlp12_sparse24_logical_v1', 'physical_hardware_format': 'NOT_FROZEN',
            'selected_layer_indices': list(SELECTED), 'families': ['fc1', 'fc2'],
            'W_layout': '[K,N] row-major; FP32 ONNX weight initializer is [N,K]',
            'grouping': 'For each n, W[4*g:4*g+4,n]; exactly 2 retained slots',
            'positions': 'uint8[K/4,N,2], distinct ascending offsets 0..3',
            'values': 'int8[K/4,N,2]; retained values may quantize to zero',
            'mask': 'bool[K,N]', 'Wq': 'int8[K,N]', 'W_fp32': 'float32[K,N]',
            'sw': 'float32[1,N]', 'bias': 'float32[N]',
            'quantization': 'Per output absmax/127, nearest-even, clip [-127,127], zero point 0; zero column scale 1',
            'activation_quantization': 'Unchanged original full W8A8 graph, not a new quantizer',
            'accumulation': 'INT8 x INT8, checked INT32 output; references accumulate INT64',
            'onnx_execution': 'CPU dense numerical reference, no sparse hardware performance claim',
            'metadata_packing': 'Logical uint8 offsets only; not compressed AXI/RTL encoding'})
        write_json(output / 'preprocessing.json', PREPROCESS_SPEC)
        classes, raw_classes_sha = model_classes(archive)
        write_json(output / 'labels_imagenet1k.json', classes)
        evaluation, evidence_checks, sample_sets = {}, {}, {}
        partial_records = json_read(SEARCH / 'dataset_snapshot.json')
        old_records = json_read(JOINT / 'old100_dataset.json')
        with np.load(SEARCH / 'logits.npz', allow_pickle=False) as p, \
                np.load(JOINT / 'additional_partial_fp32_logits.npz', allow_pickle=False) as extra, \
                np.load(JOINT / 'old100_logits.npz', allow_pickle=False) as old:
            for key, records, base, count in (
                ('old100', old_records, ROOT / 'inputs/imagenet100_dev100', 100),
                ('partial523', partial_records, ROOT / 'inputs/imagenet100_val1000_20261022', 523)):
                tensors, labels, identities = load_dataset(records, base, count, protected)
                sample_sets[key] = identities
                previous = old if key == 'old100' else p
                np.testing.assert_array_equal(labels, previous['labels'])
                if key == 'partial523':
                    np.testing.assert_array_equal(labels, extra['labels'])
                    np.testing.assert_array_equal(p['row_indices'], [r['row_index'] for r in records])
                print(f'Re-running EXPORTED classifiers on exact {key}; development regression only ...', flush=True)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    jobs = {precision: executor.submit(infer, model, tensors, key + '/' + precision)
                            for precision, model in models.items()}
                    fresh = {precision: job.result() for precision, job in jobs.items()}
                saved = {'labels': labels}
                evaluation[key], evidence_checks[key] = {}, {}
                for precision, logits in fresh.items():
                    expected = (extra['c062_fp32'] if key == 'partial523' and precision == 'fp32'
                                else previous['c062_' + precision])
                    bitwise_equal(logits, expected)
                    baseline = previous['dense_' + precision]
                    metrics = recalculate(logits, labels, baseline)
                    reference = joint['checked_groups']['c062']['old100' if key == 'old100' else 'partial'][precision]
                    for name, value in reference.items():
                        require(np.isclose(metrics[name], value, rtol=1e-12, atol=1e-12), 'Prior metric differs: ' + name)
                    evaluation[key][precision] = metrics
                    evaluation[key]['dense_' + precision] = recalculate(baseline, labels, baseline)
                    saved[precision], saved['dense_' + precision] = logits, baseline
                    evidence_checks[key][precision] = {'logits_bitwise_match_previous': True,
                        'images': count, 'logits_compared': count * 1000,
                        'logits_raw_sha256': hashlib.sha256(logits.tobytes()).hexdigest()}
                np.savez_compressed(output / 'evidence' / (key + '_logits.npz'), **saved)
                write_json(output / 'evidence' / (key + '_samples.json'), identities)
                del tensors
        require(not ({r['image_sha256'] for r in sample_sets['old100']}
                     & {r['image_sha256'] for r in sample_sets['partial523']}), 'Dataset overlap')
        flags = quality_flags(evaluation)
        require(flags['w8a8_both_development_sets_within_5pp']
                and not flags['fp32_both_development_sets_within_5pp'], 'Known c062 gate changed')
        write_json(output / 'evaluation.json', {
            'scope': 'Same selection-used development images, NOT independent quality acceptance',
            'baseline_logits': 'Reused hash-verified prior Dense arrays, NOT freshly rerun',
            'candidate_logits': 'Fresh full inference from saved-and-reloaded exported ONNX',
            'datasets': evaluation, 'acceptance': flags,
            'partial523_limit': 'Only 48 source classes; class-ordered incomplete download prefix',
            'no_additional_images_used': True})
        write_json(output / 'verification.json', {
            'status': 'PASS_EXPORT_STRUCTURE_ARITHMETIC_AND_DEVELOPMENT_REPRODUCTION',
            'graphs': graph_audit, 'layers': layer_checks, 'development_reexecution': evidence_checks,
            'integer_case_scope': 'Original public Dense Xq snapshots plus edge cases; NOT new-model propagated Xq',
            'hardware_tested': False, 'independent_quality_accepted': False})
        for path, digest in protected.items():
            require(sha_file(path) == digest, 'Protected input changed: ' + str(path))
        write_json(output / 'provenance.json', {
            'created_utc': datetime.now(timezone.utc).isoformat(), 'selection': 'c062',
            'source_hashes': {str(p): digest for p, digest in sorted(protected.items())},
            'original_label_json_raw_sha256': raw_classes_sha,
            'models_sha256': EXPECTED_GRAPH_SHA,
            'environment': {'python': sys.version, 'numpy': np.__version__, 'onnx': onnx.__version__,
                            'onnxruntime': ort.__version__, 'Pillow': pillow_version,
                            'threads_per_session': 4, 'concurrent_independent_sessions': 2,
                            'execution': 'SEQUENTIAL; graph optimization disabled; spinning 0; CPU'},
            'old_files_unchanged': True, 'training_download_HLS_board_git_mutations': 'NONE'})
        with (output / 'README.md').open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(readme())
        files = {path.relative_to(output).as_posix(): {'bytes': path.stat().st_size, 'sha256': sha_file(path)}
                 for path in sorted(output.rglob('*')) if path.is_file()}
        manifest = {'schema': 'h576_sparse24_mlp12_candidate_v1',
                    'status': 'EXPORTED_AND_REPRODUCED_DEVELOPMENT_ONLY',
                    'selection': 'c062', 'layer_indices': list(SELECTED), 'models': MODEL_FILES,
                    'source_archive_sha256': protected[archive.resolve()], 'acceptance': flags,
                    'implementation': {p.name: digest for p, digest in protected.items() if p.parent == ROOT and p.suffix == '.py'},
                    'files': files, 'elapsed_seconds': time.monotonic() - started}
        for name, record in files.items():
            require(sha_file(output / name) == record['sha256'], 'Export artifact changed')
        # Completion receipt is deliberately the LAST write, AFTER all checks.
        write_json(output / 'candidate.json', manifest)
        print(json.dumps({'status': manifest['status'], 'directory': str(output),
                          'sparse_layers': 12, 'unchanged_dense_layers': 37,
                          'images_reexecuted_per_precision': 623, 'acceptance': flags,
                          'seconds': manifest['elapsed_seconds']}, indent=2), flush=True)
        return 0
    except Exception:
        failure = traceback.format_exc()
        if created:
            write_json(output / 'failure.json', {'status': 'FAILED_NOT_A_COMPLETED_CANDIDATE', 'failure': failure})
        print(failure, file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
