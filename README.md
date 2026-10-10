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

## 当前状态（OPT40 开发分支）

- HLS 候选：`OPT40`，源码基线 `79ac98b250a0d90e94ec94c1cd388682cc3f4140`
- 计算结构：16×16 外积阵列，设计并行度 256 个 INT8 MAC/周期
- X/W/Y AXI 数据口均为 128 位；仅支持配置文件中登记的 5 组 M/K/N
- 顶层：`model_gemm_top`；目标器件：`xck26-sfvc784-2LV-c`（KV260）；目标时钟：100 MHz
- 板端软件接口：XRT，需与新生成的 IP 重新核对

相同源码在独立 OPT40 工作区中已通过 Vitis 2026.1 C Simulation、C Synthesis 和 C/RTL Co-simulation；仓库路径下的重新运行及板端验证均未完成。测试覆盖和综合数据见[验证清单](docs/verification.md)与[综合摘要](hls/reports/synthesis.md)。本分支不提交 Vitis 生成目录或 IP 压缩包，也不改变 `main`。

## 快速入口

- [HLS 说明](hls/README.md)
- [PS/PL 接口](docs/interface.md)
- [项目进度](docs/progress.md)
- [验证清单](docs/verification.md)
- [协作流程](docs/workflow.md)

历史版本仍由 Git tag 保存，例如 `v0.2.0`；不要为了保留旧版本而在当前目录复制多套源码。
