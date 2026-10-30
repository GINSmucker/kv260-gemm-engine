"""Fixed24-mask recovery with a balanced100k training set and a fresh development set."""
from exhaustive20_common import (ROOT, read, save, sha, digest, image_identity, read_rows,
    arrays, topology, quantize, layer_name, combo_name, inputs, infer, metrics,
    session, np, onnx, PREPROCESS_SPEC)
from pathlib import Path

EXP=ROOT/'experiments/B_mlp24_train100k_20261025_run01'
RUN=EXP
OUT=ROOT/'reports/B_mlp24_train100k_20261025_run01'
PARENT=ROOT/'candidates/B_mlp24_finetuned_continue10_20261025_run01'
ORIGINAL24=ROOT/'candidates/B_mlp24_finetuned_20261025_run01'
PARENT20=ROOT/'candidates/B_mlp20_finetuned_20261025_run01'
PARENT22=ROOT/'candidates/B_mlp22_best6_finetuned_20261025_run01'
PKG=ROOT/'candidates/B_mlp24_finetuned100k_20261025_run01'
DATA=ROOT/'data/large24_imagenet100k_20261025'
FRESH=ROOT/'data/large24_acceptance1800_20261025'
OLD_DATA=ROOT/'data/finetune16_imagenet1k_20261024'
SOURCE=Path('D:/FPGA/AI_Operator_Optimization/h576_sparse24/inputs/baseline')
EXPECTED_FP='9aee6be0491c9584716ed6871770b2ff2f65c1394ebd8e4de3c7c10c4aea0697'
EXPECTED_Q='4a4103cc7b06432d4f5c63c55f320189136ba6e84f2cfcb82832b4a26ace5e7e'
EXPECTED_PT='a2a5a2ef48a35d2b9f1930e9971ac9c970e452caa35722b08d335566583ee961'
SELECTED=tuple(i for i in range(48) if i%4 in (2,3))
SEED=2026102505
EPOCHS=5
TRAIN_IMAGES=100000
TUNE_IMAGES=4500
UPDATES_PER_EPOCH=1563
UPDATES=UPDATES_PER_EPOCH*EPOCHS
LR_PEAK=1e-5
LR_MIN=1e-6
DS='evanarlian/imagenet_1k_resized_256'
REV='8de107e08d1f61884d5a4ec2dea8740667264a60'
PARTS=('fresh900_1800','previous_continue1800_regression','previous24_1800_regression','exposed_development1000')
PREVIOUS_REPORT=ROOT/'reports/B_mlp24_continue10_20261025_run01'

def score_key(m,epoch=0):
    return (-m['top1_correct'],-m['top5_correct'],m['cross_entropy'],epoch)

def check_policy():
    p=read(EXP/'policy.json')
    assert sha(PARENT/'model_fp32.onnx')==EXPECTED_FP
    assert sha(PARENT/'model_w8a8.onnx')==EXPECTED_Q
    assert sha(PARENT/'model_trainable.pt')==EXPECTED_PT
    assert sha(DATA/'manifest.json')==p['training_data_manifest_sha256']
    assert sha(FRESH/'labels.csv')==p['fresh_labels_sha256']
    for name,h in p['scripts_sha256'].items():assert sha(ROOT/'scripts'/name)==h,name
    return p

def evaluation_folder(part):
    return {'fresh900_1800':FRESH,
        'previous_continue1800_regression':ROOT/'data/continue24_acceptance1800_20261025',
        'previous24_1800_regression':ROOT/'data/expansion24ft_acceptance1800_20261025',
        'exposed_development1000':ROOT/'data/evaluation1000'}[part]
