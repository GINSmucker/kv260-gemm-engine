# v1 初级 Dense：历史 v0.2 原样归档

本目录的 `v1` 是这次三阶段整理的编号，原计算核的历史版本是 **`dense_tiled_v0.2`**。
它不是 OPT38、OPT40，也不是 FP32 计算核。源码没有为了符合版本标签而修改。

## 来源与文件

- 原仓库：`GINSmucker/kv260-gemm-engine`。
- 历史标签：`v0.2.0`。
- 快照提交：`cd0870dd642378719fd844f8cdbe4cacaae36609`。
- 引入该计算核的提交：`5da766cb7ff34b2fa2c39f4735ccb786704f7294`（2026-10-03）。
- `src/`、`tb/`、`hls_config.cfg`、原 `README.md` 和 `docs/` 三份文档，均从上述快照提交的 Git blob 按原始字节恢复，未转码或统一换行。
- `SNAPSHOT_STATUS.md` 是本次新增的归档说明，不属于历史源码。

```text
src/gemm.cpp
src/gemm.h
tb/testbench.cpp
hls_config.cfg
README.md                              # 原历史说明，状态以本文件的限定为准
docs/dense_tiled_report_v0.2.md          # 对应本计算核的历史摘要
docs/naive_auto_report_v0.1.md           # 更早固定 8×8 版本的历史资料
docs/gemm_ip_interface_v0.1.md           # 更早接口资料，不是 v0.2 的寄存器契约
```

四份核心文件的 SHA256：

| 文件 | SHA256 |
|---|---|
| `src/gemm.cpp` | `a901dd0195af03a312fdbcfa5a9631220816edffd7a31abef501bf90fd6cc558` |
| `src/gemm.h` | `0572cba7f7618c206921144beceafe54c76e7e115899dc185c639d3100583721` |
| `tb/testbench.cpp` | `18c3b13c80e7d106f0dc449c4970f86444c1fb715f36e6907edf0837c0b5a10b` |
| `hls_config.cfg` | `39fe3c94c1f5fdee6c579fdda166d55bc5c566e14f03e90f877f11a3ce9dd73e` |

## 它实现了什么

- 计算 `Y[M×N] = X[M×K] × W[K×N]`，三个矩阵均为紧凑行主序。
- X/W 为 INT8，乘积 INT16，累加及 Y 为 INT32。
- 运行时支持 `1 <= M,N,K <= 1024`，包含不整除的尾块。
- 分块为 `TM=16、TN=16、TK=32`，K 方向 4 路并行。
- 计算循环显式请求 `PIPELINE II=1`；X/W 的 K 维分别做 4 路分 bank。
- 先加载，再计算，再写回；尚无输入/输出双缓冲或 DATAFLOW 重叠，也不支持 Sparse。

顶层 C++ 参数顺序是 **`gemm_top(X, W, Y, M, N, K)`**，不要与后续版本的 `M,K,N` 顺序混淆。
配置目标是 K26 `xck26-sfvc784-2LV-c`，时钟 10 ns（100 MHz）、不确定度 10%。

## AXI 位宽：目前不能写成“已确认 32 bit”

源码使用 INT8/INT32 元素和三路 `m_axi`，并未显式锁定导出端口的宽字类型或自动加宽上限。
历史 v0.2 摘要记载 X/W 为 8 bit、Y 为 32 bit，且未做总线加宽；但本次可恢复材料没有该版本的原始综合报告、导出 RTL 或配对 IP 包，**最终导出的有效 AXI 位宽在本次未确认，位宽复核状态为 NOT RUN**。
不能仅凭 C++ 元素位宽、工具默认行为或历史摘要，就把该版本统一标成 AXI32、AXI128。
需要实际导出该版本并查看接口参数后，才能补充这个结论；本次没有为此修改源码或运行工具。

## 验证状态与证据范围

| 项目 | 状态 | 范围 |
|---|---|---|
| 本次恢复文件与历史 Git blob 的逐字节比较 | PASS | 8 份原文件完全一致；这不是计算验证 |
| 本次原生 C++ testbench | PASS | 使用 Vitis 附带 clang 在 Windows 运行原合成用例；不是 Vitis CSim 或 RTL，见 results/versions/native_functional_20261026.json |
| 历史 C Simulation | PASS（历史记录） | 原摘要记录 7/7 小矩阵测试，本次未重跑 |
| 历史 C Synthesis / 计算循环 II=1 | PASS（历史记录） | 原摘要记录，未取得原始报告重新核验 |
| 历史 Verilog C/RTL Co-simulation | PASS（历史记录） | 原摘要记录 7/7 事务，本次未重跑 |
| 本次 CSim / 综合 / RTL 仿真 / IP 封装 | NOT RUN | 本次仅恢复文本源码和历史文档 |
| 本次系统集成 / 布局布线 / 板端验证 | NOT RUN | 未生成或加载任何硬件产物 |
| H576 整图适配、精度或性能测试 | NOT RUN | 此初级核未建立与当前模型的配对运行链 |

历史 7 个测试包括零矩阵、单位权重、1×1×1、8×8×8、完整 tile、尾块和多 tile；最大测试的 `M,N,K` 为 `30,20,50`。
原摘要的 DSP=7、BRAM_18K=14、FF=4304、LUT=6897，以及协同仿真延迟，均只代表当时摘要所述的配置与小矩阵事务，不能当作本次测量。
原 README 还将 v0.2 IP 封装和板端集成列为待进行，不应从更早 v0.1 的 F8 板测结果推定本核已上板。

## 使用与比较提示

1. 配置文件内的 `./src/`、`./tb/` 路径相对本目录；在独立的 Vitis 工作目录使用它，不把构建缓存提交到此归档。
2. `docs/gemm_ip_interface_v0.1.md` 仅为历史资料，不能复用其中固定 8×8 的寄存器偏移驱动本核；集成时应从实际导出的头文件重新核对 ABI。
3. 本核只有 Dense GEMM，不等于整个 H576 模型。不能为它直接挂上 H576 的整图毫秒数，也不能把不同模型、频率和测试范围的数字放在同一速度排行榜中。
4. 若以后做公平性能比较，先明确共同矩阵、输入权重、工具/时钟、接口和测量范围，再单独生成新的验证证据；旧文档保持原样。
