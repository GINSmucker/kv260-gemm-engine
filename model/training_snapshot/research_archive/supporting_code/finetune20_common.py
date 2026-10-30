"""Paths and frozen reference settings for the bounded 20-layer recovery run."""
from exhaustive20_common import (ROOT, read, save, sha, digest, image_identity,
    read_rows, arrays, topology, quantize, inputs, infer, metrics, session,
    np, onnx, PREPROCESS_SPEC)
from pathlib import Path

RUN = ROOT/'experiments/B_mlp20_finetune_20261025_run01'
DATA = ROOT/'data/finetune16_imagenet1k_20261024'
PARENT = ROOT/'candidates/B_mlp20_exhaustive70_20261024_run01'
PKG = ROOT/'candidates/B_mlp20_finetuned_20261025_run01'
OUT = ROOT/'reports/B_mlp20_finetune_20261025_run01'
FRESH = ROOT/'data/finetune20_acceptance1800_20261025'
SOURCE = Path('D:/FPGA/AI_Operator_Optimization/h576_sparse24/inputs/baseline')
EXPECTED_FP = '7212e84d01ac83fee5720eefe66cd04742a8a9b9af7c1a28b4ddd337dd8a6be6'
EXPECTED_Q = 'b13160858f26e1afcd24d042eefd84f4e127f08e02e9910de6f1548babe3d3f9'
SELECTED = (6,7,10,11,15,18,19,23,26,27,30,31,34,35,38,39,42,43,46,47)
DS = 'evanarlian/imagenet_1k_resized_256'
REV = '8de107e08d1f61884d5a4ec2dea8740667264a60'
SEED = 2026102501
