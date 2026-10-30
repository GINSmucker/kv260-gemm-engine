# Naive GEMM 综合与验证摘要 v0.1

## 1. 版本信息

| 项目 | 内容 |
|---|---|
| 版本名 | `naive_auto_v0.1` |
| 顶层函数 | `gemm_top` |
| 工具 | Vitis HLS 2026.1，Build 6493734 |
| 目标器件 | `xck26-sfvc784-2LV-c` |
| 目标时钟 | 10 ns（100 MHz） |
| 时钟不确定度 | 10% |
| 输出格式 | Vivado IP Catalog |

## 2. 验证结果

| 阶段 | 结果 |
|---|---|
| C Simulation | PASS |
| C Synthesis | PASS |
| Verilog C/RTL Co-simulation | PASS |
| IP Package | PASS |

C Testbench 覆盖：零矩阵、单位权重、INT8 正负极值、固定随机数据和连续调用。

## 3. 综合结果

| 指标 | 结果 |
|---|---:|
| HLS Latency | 532 cycles / 5.32 µs |
| HLS Interval | 512 cycles / 5.12 µs |
| 主循环 II | 8 |
| 主循环 Depth | 27 |
| 估计 Fmax | 111.11 MHz |
| DSP | 4 |
| BRAM | 4 |
| LUT | 4037 |
| FF | 3409 |
| URAM | 0 |

4 个 BRAM 均由两个 AXI 读接口使用，不是矩阵本地缓存。8 个乘法运算单元中，4 个乘加映射到 DSP，其余乘法使用 Fabric/LUT。

## 4. C/RTL Co-simulation

| 指标 | Min | Avg | Max |
|---|---:|---:|---:|
| Latency (cycles) | 835 | 851 | 855 |
| Interval (cycles) | 791 | 806 | 811 |

五次调用总时间为 4131 cycles。按 100 MHz 计算，平均 RTL 延迟约为 8.51 µs。该数据来自 RTL 仿真，不代表 KV260 板上实测性能。

## 5. 工具自动优化

源码未手工添加 `PIPELINE`、`UNROLL`、`DATAFLOW` 或 `ARRAY_PARTITION`，但 Vitis 默认设置自动执行了：

- 流水化 `j` 循环；
- 扁平化 `i/j` 循环；
- 完全展开 `k` 循环，展开因子为 8。

因此本版本命名为 `naive_auto`，表示 Naive 算法在 Vitis 默认自动优化下的结果，不作为严格单乘法器串行基线。

## 6. AXI 访问与瓶颈

| 数据 | 访问结果 |
|---|---|
| X | 8-bit AXI，推断出长度 8 的突发读 |
| W | 8-bit AXI，列方向跨步访问，未形成突发 |
| Y | 32-bit AXI，推断出长度 64 的突发写 |
| 自动加宽 | 未启用，`max_widen_bitwidth=0` |

当前 II=8 的主要原因是外部存储器供数能力不足：计算循环虽展开 8 倍，但 8-bit AXI 接口和 W 的跨步访问无法持续供应并行计算单元。

## 7. 结论

`naive_auto_v0.1` 已完成 C 仿真、综合、RTL 协同仿真和 IP 封装，功能与 AXI 接口可以交付成员 C 进行 Vivado 集成。本版本作为后续片上缓存、AXI 加宽、数据复用、Tiling 和稀疏优化的比较基线。

原始依据：`hls_compile.rpt`、`hls_cosim.rpt`、`rmp_sparse_gemm.hlsrun_package_summary`。
