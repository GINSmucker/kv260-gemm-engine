# KV260 GEMM Engine

面向 AMD Kria KV260 的矩阵乘法加速项目。计算统一定义为：

```text
Y[M x N] = X[M x K] * W[K x N]
```

当前仓库按成员职责拆分，目标是让 HLS 计算核、软件参考模型和 XRT 板端运行时可以独立开发、共同验证。

## 目录

```text
hls/       A：HLS 计算核、测试平台和综合摘要
model/     B：量化、Golden Reference 和测试数据
board/     C：Vivado 平台与 XRT runtime
docs/      公共接口、进度、验证与协作规范
results/   跨版本结果汇总
```

## 当前状态

- 当前 HLS 候选：`OPT38 full-K v1`
- 计算结构：8×16 外积阵列，共 128 个并行 INT8 MAC
- 支持范围：当前仅支持配置文件中登记的 5 组 M/K/N，不是任意尺寸引擎
- 顶层函数：`model_gemm_top`
- 目标器件：`xck26-sfvc784-2LV-c`（KV260）
- 目标时钟：100 MHz
- 板端接口：XRT

OPT38 源码来自团队交接版本 `team-handoff-20261009`。它已在新目录下通过 Vitis 2026.1 C Simulation；C Synthesis、C/RTL Co-simulation 和板端验证尚未执行。各项状态以 [验证清单](docs/verification.md) 为准。

## 快速入口

- [HLS 说明](hls/README.md)
- [PS/PL 接口](docs/interface.md)
- [项目进度](docs/progress.md)
- [验证清单](docs/verification.md)
- [协作流程](docs/workflow.md)

历史版本仍由 Git tag 保存，例如 `v0.2.0`；不要为了保留旧版本而在当前目录复制多套源码。
