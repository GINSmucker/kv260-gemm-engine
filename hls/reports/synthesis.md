# OPT40 综合与协同仿真摘要

## 候选信息

- 顶层：`model_gemm_top`
- 源码基线：`79ac98b250a0d90e94ec94c1cd388682cc3f4140`
- 已测来源：独立工作区 `D:\GEMM\model_gemm_opt40`，2026-10-10；导入本分支的五个源码/配置文件 SHA-256 与之逐字节一致
- 工具与目标：Vitis HLS 2026.1 / KV260 `xck26-sfvc784-2LV-c` / 10 ns 目标时钟
- 结构：16×16 外积，设计并行度 256 MAC/cycle；`TM=16`、`TN=16`、`TK=32`
- 测量位置：下表资源和顶层延迟是 HLS 综合估计，下面的实测周期来自 XSIM C/RTL 协同仿真；均非 KV260 板端计时

## 独立 OPT40 工作区结果

| 指标 | 结果 |
|---|---|
| C Simulation | PASS；5 个合法尺寸各 1 组输入，1 个非法尺寸 |
| C Synthesis | PASS；2026-10-10 22:21 |
| C/RTL Co-simulation | PASS；Verilog / XSIM，2026-10-10 22:26 |
| 计算主体 K 循环 | `II=1`，约 256 个 DSP 用于计算 |
| AXI | X/W/Y 均 128 位；X/W 读 Burst、Y 写 Burst 均推断成功 |
| DSP | 260 / 1248（20%） |
| BRAM | 174 / 288（60%） |
| FF | 47,124 / 234,240（20%） |
| LUT | 26,529 / 117,120（22%） |
| HLS 顶层延迟估计 | 14,141,806 周期；动态 Y 写循环边界使该上界过于保守，不是实测耗时 |
| XSIM 单次调用延迟 | 最小 3、平均 112,452、最大 236,656 周期；报告没有按尺寸拆分 |
| XSIM 全测试执行 | 674,842 周期；包含当前 testbench 的全部调用 |
| 实现后 100 MHz 时序 | NOT RUN |
| KV260 板端正确性与性能 | NOT RUN |

按 10 ns 目标时钟换算，协同仿真最大单次延迟约 2.37 ms；这只是 RTL 模拟周期的换算，不包含板端 XRT/DDR/系统计时，亦未证明时序收敛。最小 3 周期很可能来自非法尺寸快速返回，平均值不能代表任一指定矩阵。HLS 估计的 Y 写循环 trip count 为 16,368，而实际一个完整 16×16 输出 tile 仅写 64 个 128 位字；因此不能把 14,141,806 周期当作 OPT40 的实测性能，也不能与 OPT38 不同测试条件的数字直接比较。

原始报告位于已测工作区的 `model_gemm_opt40/model_gemm_top/reports/hls_compile.rpt` 及 `model_gemm_opt40/model_gemm_top/hls/sim/report/model_gemm_top_cosim.rpt`；本分支只保存人工摘要，不提交 Vitis 生成目录。下一轮需在本分支源码路径重跑，并记录五个尺寸各自的周期和板端性能，才可作同条件对比。
