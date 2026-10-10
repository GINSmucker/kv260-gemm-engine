# Tiled Dense GEMM 综合与验证摘要 v0.2

## 1. 版本信息

| 项目 | 内容 |
|---|---|
| 版本名 | `dense_tiled_v0.2` |
| 顶层函数 | `gemm_top` |
| 计算定义 | `Y[M x N] = X[M x K] * W[K x N]` |
| 数据类型 | X/W: INT8，Y/累加器: INT32 |
| 分块尺寸 | `TM=16`，`TN=16`，`TK=32` |
| K 方向并行度 | 4 路 |
| 工具 | Vitis HLS 2026.1，Build 6493734 |
| 目标器件 | `xck26-sfvc784-2LV-c`（KV260） |
| 目标时钟 | 10 ns（100 MHz） |
| 时钟不确定度 | 10% |

## 2. II=1 流水线结果

计算循环 `COMPUTE_K_GROUP_LOOP` 使用：

```cpp
#pragma HLS PIPELINE II=1
```

其内部 4 个 K-lane 完全展开，每拍接收一组 4 个 INT8 乘加。`x_tile`
沿 K 维进行 4 路 cyclic partition，`w_tile` 沿 K 维进行 4 路 cyclic
partition，因此每周期可以同时读取 4 个 X 和 4 个 W。

综合后的关键流水线如下：

| 流水线 | Final II | Depth / Iteration Latency | Trip Count |
|---|---:|---:|---:|
| 清零 Y tile | 1 | 1 | 256 |
| 清零 X tile | 1 | 1 | 512 |
| 清零 W tile | 1 | 1 | 512 |
| 读取 X 有效区域 | 1 | 11 | 最大 512 |
| 读取 W 有效区域 | 1 | 11 | 最大 512 |
| 4 路并行计算 | 1 | 5 | 2048 |
| 写回 Y 有效区域 | 1 | 11 | 最大 256 |

这里的 II=1 表示计算流水线每周期可以启动一组 4 路乘加，不表示每周期
完成一个完整的 Y 元素，也不表示整个矩阵只需要一个周期。

## 3. 综合 Latency

顶层 M/N/K 为运行时参数，因此 HLS 使用 `LOOP_TRIPCOUNT` 的最大值估算
1024 x 1024 x 1024 上界：

| 指标 | 周期 | 100 MHz 下时间 |
|---|---:|---:|
| 顶层最大 Latency 估算 | 407,969,921 | 4.07969921 s |
| 顶层最大启动 Interval 估算 | 407,969,922 | 4.07969922 s |

该上界用于综合报告展示，不等于常用小矩阵的实际运行时间。实际测试尺寸的
延迟以 C/RTL 协同仿真数据为准。

## 4. C/RTL 协同仿真 Latency

仿真工具为 XSIM，RTL 为 Verilog。所有测试均通过结果比对。

| 事务 | 测试用例 | M x N x K | Latency (cycles) | 100 MHz 下时间 |
|---:|---|---:|---:|---:|
| 0 | zero-boundary | 3 x 5 x 9 | 2,962 | 29.62 us |
| 1 | identity-boundary | 5 x 7 x 7 | 3,003 | 30.03 us |
| 2 | single-element | 1 x 1 x 1 | 2,870 | 28.70 us |
| 3 | week1-8x8 | 8 x 8 x 8 | 3,081 | 30.81 us |
| 4 | exact-tile | 16 x 16 x 32 | 4,069 | 40.69 us |
| 5 | partial-tile | 13 x 17 x 10 | 6,591 | 65.91 us |
| 6 | multi-tile | 30 x 20 x 50 | 26,863 | 268.63 us |

协同仿真汇总：

| 指标 | Min | Avg | Max |
|---|---:|---:|---:|
| Latency (cycles) | 2,870 | 7,062 | 26,863 |
| Interval (cycles) | 2,896 | 3,788 | 6,617 |

7 次调用总执行时间为 49,595 cycles，即 495.95 us（按 100 MHz 计算）。
Interval 只统计相邻事务；最后一次事务没有下一次启动，因此无 interval 值。

## 5. 资源使用

| 资源 | 使用量 | KV260 器件占比（HLS 估算） |
|---|---:|---:|
| BRAM_18K | 14 | 4% |
| DSP | 7 | <1% |
| FF | 4,304 | 1% |
| LUT | 6,897 | 5% |
| URAM | 0 | 0% |

7 个 DSP 的构成为：

- 4 个 DSP：计算流水线中的 4 路 INT8 MAC；
- 2 个 DSP：X/W 动态行地址计算；
- 1 个 DSP：Y 动态行地址计算与写回地址生成。

14 个 BRAM 的主要构成为：

- X tile：4 个 bank，共 4 BRAM；
- W tile：4 个 bank，共 4 BRAM；
- Y tile：2 BRAM；
- X/W 两个 AXI 读接口缓冲：各 2 BRAM，共 4 BRAM。

## 6. AXI 与数据搬运

三路主数据访问均成功推断出可变长度 Burst：

| 接口 | 方向 | Burst 循环 | 数据宽度 |
|---|---|---|---:|
| `m_axi_gmem0` / X | 读 | `LOAD_X_VALID_K_LOOP` | 8 bit |
| `m_axi_gmem1` / W | 读 | `LOAD_W_VALID_J_LOOP` | 8 bit |
| `m_axi_gmem2` / Y | 写 | `STORE_VALID_J_LOOP` | 32 bit |

当前 AXI 尚未做数据位宽加宽。报告中的外层循环 `Stride is incompatible`
表示跨行地址不连续，不能把多行合成同一个 Burst，不影响每一行的内层连续
访问已经形成 Burst。

## 7. 验证结论与当前瓶颈

| 阶段 | 结果 |
|---|---|
| C Simulation | PASS，7/7 用例 |
| C Synthesis | PASS |
| Verilog C/RTL Co-simulation | PASS，7/7 事务 |
| 计算流水线 Final II | 1 |
| 所有显式流水线约束 | 满足 |

当前版本已经完成“分块正确、K 方向 4 路并行、计算流水线 II=1、测量
Latency 与资源”四个目标。下一阶段的主要性能瓶颈不是计算循环 II，而是：

1. 每个 K tile 都要清零完整 X/W 本地缓存；
2. Load、Compute、Store 尚未使用 DATAFLOW 重叠执行；
3. X/W AXI 仍为 8-bit，Y AXI 为 32-bit，尚未做总线位宽加宽；
4. 边界很小的矩阵仍承担完整 tile 清零和控制开销。

## 8. 原始依据

- `rmp_sparse_gemm/reports/hls_compile.rpt`
- `rmp_sparse_gemm/reports/hls_cosim.rpt`
- `rmp_sparse_gemm/hls/sim/report/verilog/result.transaction.rpt`
