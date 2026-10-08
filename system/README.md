# KV260 完整模型协作入口

这里提供队友接手所需的完整层GEMM源码、实际运行时、DeiT数值软件、界面和精简证据。首先阅读[接手步骤](docs/START_HERE.md)及[当前状态](docs/STATUS.md)。文件身份固定在[BASELINE_LOCK](BASELINE_LOCK.json)、[SOURCE_MAP](SOURCE_MAP.json)和[PACKAGE_MANIFEST](PACKAGE_MANIFEST.json)。

当前采用父版为 **OPT35a + V2A 的 system_a3_20261005_02**。10月8日F05 ABI4/wire4的D1～D4功能/恢复通过；D5三图比较有效，完整结果中位1465.884971ms、较两侧父中位均值减少77.635%。软件/C/B成本下界仍超600/100/30ms，G5关闭，未替换正式父版。源码和运行候选入口见[F05接续](docs/F05_HANDOFF.md)。正式性能、PL质量、稳定性与发布Gate仍未通过。

|需要了解|入口|
|---|---|
|最新进度与Gate|[STATUS](docs/STATUS.md)|
|板卡、环境、SSH和回退|[BOARD_AND_TOOLS](docs/BOARD_AND_TOOLS.md)|
|五shape与PL数据流|[ALGORITHM](docs/ALGORITHM.md)|
|寄存器、ABI与恢复|[INTERFACES](docs/INTERFACES.md)|
|模型、量化、CPU/PL调用|[MODEL_AND_RUNTIME](docs/MODEL_AND_RUNTIME.md)|
|测量口径、已关闭实验、下一步|[OPTIMISATION_AND_ISSUES](docs/OPTIMISATION_AND_ISSUES.md)|
|多端分工与整合|[TEAM_WORKFLOW](docs/TEAM_WORKFLOW.md)|
|C重构进展与下一边界|[C_RUNTIME_REFACTOR](docs/C_RUNTIME_REFACTOR.md)、[F05](docs/F05_HANDOFF.md)|
|原始公共回执的用途|[EVIDENCE](docs/EVIDENCE.md)|

源码和小回执随Git拉取；[资产锁](ASSETS.lock.json)指定板测包、IP包和完整冻结模型，按需下载。它们是协作开发快照，不是正式性能发布。

在选定Python3.12环境后，从本目录执行 `python -B tools/check.py --cpp --node`。这会执行普通C++公开TB及现有UI软件测试，0次PL启动。缺工具时先按文档解决依赖，不把未运行写为PASS。
