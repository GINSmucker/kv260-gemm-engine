[README.md](https://github.com/user-attachments/files/32999740/README.md)
# KV260 GEMM Engine

2026-10-08 完整模型协作入口已增量更新到 **[system/](system/README.md)**。新成员和零历史上下文Codex从[接手步骤](system/docs/START_HERE.md)、[当前状态](system/docs/STATUS.md)及[身份锁](system/BASELINE_LOCK.json)开始。F05已完成功能/恢复及有效三图比较，完整结果中位约1.466秒、较两侧父版均值减少77.635%，但软件/C/B预算全部失败、G5关闭；采用父版仍OPT35a/V2A。F05小型源码/运行候选按需下载，原模型/IP/基础运行资产复用10月7日Release。

以下保留原作者v0.2通用GEMM介绍，`src/`与`tb/`继续对应该实现，其接口和状态不自动等同`system/`完整模型。

面向 AMD Kria KV260 的 HLS 矩阵计算引擎项目。项目以可综合、可验证、可集成
为目标，逐步实现动态尺寸 Dense GEMM、INT8/INT4 混合精度和 2:4 结构化稀疏
计算，并完成 PS/PL 集成与板端性能测试。

当前 `main` 分支对应第二周 Dense 引擎 v0.2：支持运行时矩阵尺寸、分块计算、
尾块处理、K 方向 4 路并行和 II=1 计算流水线。

## 当前功能

计算定义：

```text
Y[M x N] = X[M x K] * W[K x N]
```

| 项目 | 当前实现 |
|---|---|
| 输入 X | INT8，row-major |
| 权重 W | INT8，row-major，不预转置 |
| 输出 Y | INT32，row-major |
| 累加器 | INT32 |
| 运行时尺寸 | `1 <= M,N,K <= 1024` |
| 分块尺寸 | `TM=16`，`TN=16`，`TK=32` |
| K 方向并行度 | 4 路 INT8 MAC |
| 计算流水线 | Final II=1 |
| 尾块 | 支持 M/N/K 非整除尺寸 |
| 目标平台 | AMD Kria KV260 |
| 目标器件 | `xck26-sfvc784-2LV-c` |
| 目标时钟 | 10 ns / 100 MHz |

## 架构概览

计算核按照 `16 x 16 x 32` 对矩阵分块：

1. 从外部存储器连续读取一个 X tile 和一个 W tile；
2. 将 tile 保存到片上分 bank 缓冲区；
3. 每周期启动一组 4 路 K 方向乘加；
4. 在 K tile 之间累加部分和；
5. 将有效的 Y 区域连续写回外部存储器。

边界 tile 会先进行零填充，只加载和写回有效区域，因此不要求 M、N、K 是
16 或 32 的整数倍。

片上数组划分：

```cpp
#pragma HLS ARRAY_PARTITION variable=x_tile cyclic factor=4 dim=2
#pragma HLS ARRAY_PARTITION variable=w_tile cyclic factor=4 dim=1
```

计算流水线：

```cpp
#pragma HLS PIPELINE II=1
```

这里的 II=1 表示每周期可以启动一组 4 路乘加，不表示每周期完成一个完整的
Y 元素，也不表示整个矩阵只需要一个周期。

## 硬件接口

| 接口 | 协议 | 方向 | 用途 |
|---|---|---|---|
| `m_axi_gmem0` | AXI4 Master | Read | 读取 X |
| `m_axi_gmem1` | AXI4 Master | Read | 读取 W |
| `m_axi_gmem2` | AXI4 Master | Write | 写回 Y |
| `s_axi_control` | AXI4-Lite Slave | R/W | 配置地址、M/N/K并控制IP |
| `interrupt` | Interrupt | Output | 完成中断 |

X、W、Y 三路主数据访问均已成功推断出可变长度 AXI Burst。当前数据端口宽度
分别为 8 bit、8 bit 和 32 bit，尚未进行总线位宽加宽。

详细接口定义见：

- [GEMM IP 接口说明](docs/gemm_ip_interface_v0.1.md)
- [Dense GEMM v0.2 综合与验证摘要](docs/dense_tiled_report_v0.2.md)
- [Naive GEMM v0.1 综合摘要](docs/naive_auto_report_v0.1.md)

## 验证状态

| 阶段 | 状态 |
|---|---|
| C Simulation | PASS，7/7 用例 |
| C Synthesis | PASS |
| Verilog C/RTL Co-simulation | PASS，7/7 事务 |
| AXI Burst | X/W/Y 均成功推断 |
| 计算流水线 Final II | 1 |
| Vivado IP Package v0.2 | 待生成 |
| Vivado 系统集成 | 待进行 |
| KV260 板端验证 | 待进行 |

Testbench 覆盖以下情况：

- 零矩阵；
- 单位权重；
- 1×1×1 最小尺寸；
- 8×8×8 基线尺寸；
- 16×16×32 完整 tile；
- M/N/K 尾块；
- 多个 M/N/K tile。

## 综合结果

Vitis HLS 2026.1 针对 KV260 的估算结果：

| 资源 | 使用量 | 器件占比 |
|---|---:|---:|
| BRAM_18K | 14 | 4% |
| DSP | 7 | <1% |
| FF | 4,304 | 1% |
| LUT | 6,897 | 5% |
| URAM | 0 | 0% |

7 个 DSP 中，4 个用于并行 INT8 MAC，另外 3 个用于动态矩阵行地址计算。
综合估算 Fmax 为 111.11 MHz。

部分 RTL 协同仿真延迟如下，时间按 100 MHz 换算：

| M×N×K | Latency | 时间 |
|---:|---:|---:|
| 8×8×8 | 3,081 cycles | 30.81 µs |
| 16×16×32 | 4,069 cycles | 40.69 µs |
| 13×17×10 | 6,591 cycles | 65.91 µs |
| 30×20×50 | 26,863 cycles | 268.63 µs |

这些数据来自 HLS RTL 仿真，不代表 KV260 板端实测性能。

## 目录结构

```text
kv260-gemm-engine/
├── src/
│   ├── gemm.cpp                      # HLS Dense GEMM 计算核
│   └── gemm.h                        # 类型、上限、tile参数与顶层声明
├── tb/
│   └── testbench.cpp                 # Golden Reference与多尺寸测试
├── docs/
│   ├── gemm_ip_interface_v0.1.md     # IP接口说明
│   ├── naive_auto_report_v0.1.md     # 第一周Naive基线报告
│   └── dense_tiled_report_v0.2.md    # 第二周Dense引擎报告
├── hls_config.cfg                    # Vitis HLS工程配置
├── .gitignore
└── README.md
```

Vitis HLS 自动生成的工作目录、缓存、RTL 和编译中间文件不纳入 Git。

## 使用方法

环境：Vitis HLS 2026.1，目标器件 `xck26-sfvc784-2LV-c`。

在仓库根目录运行 C Simulation：

```powershell
vitis-run.bat --mode hls --csim --config hls_config.cfg --work_dir rmp_sparse_gemm
```

运行 C Synthesis：

```powershell
v++.bat -c --mode hls --config hls_config.cfg --work_dir rmp_sparse_gemm
```

运行 C/RTL Co-simulation：

```powershell
vitis-run.bat --mode hls --cosim --config hls_config.cfg --work_dir rmp_sparse_gemm
```

也可以在 Vitis GUI 的 Flow Navigator 中依次运行 C Simulation、C Synthesis、
C/RTL Co-simulation 和 Package。

## Release

已发布版本可在仓库的
[Releases](https://github.com/GINSmucker/kv260-gemm-engine/releases) 页面下载。

- `v0.1.0`：固定 8×8 INT8 Naive GEMM 基线及可集成 IP；
- `v0.2.0`：动态尺寸 Tiled Dense GEMM，IP 包将在重新 Package 后上传。

Release 中的 IP 压缩包用于 Vivado IP Catalog 导入。源码、Testbench、文档和
IP 包应以同一个 Release 标签对应的版本为准。

## 当前限制

- 目前只支持 INT8 Dense GEMM 和 INT32 输出；
- 最大 M/N/K 均为 1024；
- X/W AXI 仍为 8-bit，Y AXI 为 32-bit；
- Load、Compute、Store 尚未通过 DATAFLOW 重叠执行；
- 每个 K tile 仍需要清零完整 X/W 本地缓存；
- 尚未实现 INT4、GEMV 和 2:4 结构化稀疏计算；
- 尚未完成 KV260 板端性能测试。

## 开发路线

- [x] INT8 Naive GEMM 基线
- [x] 标准 AXI 接口与 IP 打包流程
- [x] 运行时 M/N/K 与尾块支持
- [x] 16×16×32 分块 Dense GEMM
- [x] K 方向 4 路并行
- [x] 计算流水线 II=1
- [x] AXI Burst 访问
- [x] C Simulation、C Synthesis 与 C/RTL Co-simulation
- [ ] v0.2 IP Package 与 Vivado 集成
- [ ] KV260 板端运行与性能测试
- [ ] DATAFLOW 与 AXI 位宽优化
- [ ] INT4 计算支持
- [ ] GEMV 支持
- [ ] 2:4 结构化稀疏计算
- [ ] Python bit-true reference

## 项目文档

- [三人协作规范与六周计划](KV260_三人协作规范与六周计划_v0.1.md)
- [KV260 连接与使用指南](KV260_连接与使用指南_含FPGA运行环境状态.md)
