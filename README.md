# KV260 GEMM Engine

A/B/C共用的完整模型计算工程，按职责直接从仓库根目录接手。计算定义为 Y[M,N]=X[M,K]×W[K,N]，INT8输入、INT32完整K归约。

## 目录与责任

```text
hls/       A：OPT38源码、配置、公开TB和综合摘要
model/     B：量化、INT64 Golden、小型完整head case、完整模型与v5适配
board/     C：Vivado平台、真实v5 XRT运行时、驱动补丁和可选UI
docs/      共享接口、进度、验证、工作流、资产锁与迁移映射
results/   性能/资源CSV、小型公开证据；生成长日志不入Git
```

主线PR #4的职责骨架与此前协作资料已整合。hls/src保留OPT38的128 MAC、五类完整M/K/N；board/runtime已导入已发布AB1 v5源码，模型使用完整DeiT/1000类。正式冻结父版仍system_a3_20261005_02（OPT35a/V2A）；开发组合OPT38/v5与其身份分开。

目录归位、复制源码和软件复验不继承新HLS、ARM、PL或公平性能Gate。[当前进度](docs/progress.md)、[验证范围](docs/verification.md)和[结果表](results/summary.csv)共同表达实际结果。旧tag/Release/签署保持，主线2026.1 C-sim和历史2020.2.2报告分开记录。

## 快速开始

使用已有Python3.12/NumPy1.26.4/Pillow12.3.0/ORT1.22.1环境，从仓库根目录执行：

```powershell
python -B docs/tools/check.py
python -B model/golden.py --check model/cases/tensors.npz
python -B docs/tools/assets.py dataset --download --extract
python -B docs/tools/dataset.py verify --root build/assets/dataset/dataset
```

HLS公开软件TB：python -B docs/tools/check.py --cpp --cxx <existing-compiler>。UI：同检查加--node。均不操作FPGA；旧Release大模型/IP/运行文件按需下载至忽略的build/assets/，不默认下载全部内容。

- [A：计算核与运行](hls/README.md)
- [B：量化、模型与测试数据](model/README.md)
- [C：平台与运行时](board/README.md)
- [唯一公共接口](docs/interface.md)
- [多端分支与整合](docs/workflow.md)
- [原路径到新位置](docs/MIGRATION.md)

共享板卡由维护者串行安排；新boot/构建/接口要有独立身份和验证，按AGENTS保留未运行、失败和测量无结论状态。界面和回执不能声称尚未测得的加速。
