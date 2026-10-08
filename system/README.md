# KV260 完整模型协作入口

这里提供队友接手所需的完整层GEMM源码、实际运行时、DeiT数值软件、界面和精简证据。首先阅读[接手步骤](docs/START_HERE.md)及[当前状态](docs/STATUS.md)。文件身份固定在[BASELINE_LOCK](BASELINE_LOCK.json)、[SOURCE_MAP](SOURCE_MAP.json)和[PACKAGE_MANIFEST](PACKAGE_MANIFEST.json)。

正式父版锁为 **OPT35a + V2A 的 system_a3_20261005_02**。默认开发运行包 **AB1 OPT38 + 最小v5**，20图有效core438.384590ms、下降50.632344%。最新AB2/3两次同v5比较有效，431.021/430.068ms；源码包含三项delta和准确复建配方。第二窗CPU公平性、1/2线程及GELU失败保留，正式性能/质量/稳定性/UI/发布未签。见[V5/AB接续](docs/V5_AB_HANDOFF.md)及[开发锁](research/DEVELOPMENT_LOCK.json)。

|需要了解|入口|
|---|---|
|最新进度与Gate|[STATUS](docs/STATUS.md)|
|板卡、环境、SSH和回退|[BOARD_AND_TOOLS](docs/BOARD_AND_TOOLS.md)|
|五shape与PL数据流|[ALGORITHM](docs/ALGORITHM.md)|
|寄存器、ABI与恢复|[INTERFACES](docs/INTERFACES.md)|
|模型、量化、CPU/PL调用|[MODEL_AND_RUNTIME](docs/MODEL_AND_RUNTIME.md)|
|测量口径、已关闭实验、下一步|[OPTIMISATION_AND_ISSUES](docs/OPTIMISATION_AND_ISSUES.md)|
|多端分工与整合|[TEAM_WORKFLOW](docs/TEAM_WORKFLOW.md)|
|当前开发实现与边界|[V5/AB](docs/V5_AB_HANDOFF.md)、[重构历史](docs/C_RUNTIME_REFACTOR.md)、[F05历史](docs/F05_HANDOFF.md)|
|原始公共回执的用途|[EVIDENCE](docs/EVIDENCE.md)|

源码和小回执随Git拉取；[资产锁](ASSETS.lock.json)指定板测包、IP包和完整冻结模型，按需下载。它们是协作开发快照，不是正式性能发布。

在选定Python3.12环境后，从本目录执行 `python -B tools/check.py --cpp --node`。这会执行普通C++公开TB及现有UI软件测试，0次PL启动。缺工具时先按文档解决依赖，不把未运行写为PASS。
