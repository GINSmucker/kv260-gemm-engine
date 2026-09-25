[README.md](https://github.com/user-attachments/files/32649218/README.md)
# KV260 GEMM Engine

面向 AMD Kria KV260 的 HLS 矩阵计算引擎项目，目标是逐步实现 INT8/INT4 GEMM、GEMV 和 2:4 结构化稀疏计算，并完成 PS/PL 集成与板端性能测试。

当前版本为第一周基线：固定规模的 8×8 INT8 Naive GEMM IP。

## 当前版本

**Release：v0.1.0**

当前实现：

- 计算定义：`Y = XW`
- `X`：8×8，INT8，row-major
- `W`：8×8，INT8，row-major
- `Y`：8×8，INT32，row-major
- 顶层函数：`gemm_top`
- 目标平台：AMD Kria KV260
- 目标器件：`xck26-sfvc784-2LV-c`
- 目标时钟：10 ns / 100 MHz

当前版本作为后续流水线、并行化、混合精度和稀疏优化的功能及性能基线。

## 验证状态

| 阶段 | 状态 |
|---|---|
| C Simulation | PASS |
| C Synthesis | PASS |
| C/RTL Co-simulation | PASS |
| IP Package | PASS |
| Vivado 系统集成 | 待进行 |
| KV260 板端验证 | 待进行 |

## 硬件接口

| 接口 | 协议 | 用途 |
|---|---|---|
| `m_axi_gmem0` | AXI4 Master | 读取输入矩阵 X |
| `m_axi_gmem1` | AXI4 Master | 读取权重矩阵 W |
| `m_axi_gmem2` | AXI4 Master | 写回输出矩阵 Y |
| `s_axi_control` | AXI4-Lite Slave | IP 控制与缓冲区地址配置 |

详细接口定义见：

- [GEMM IP 接口说明](docs/gemm_ip_interface_v0.1.md)
- [Naive GEMM 综合报告摘要](docs/naive_auto_report_v0.1.md)

## 目录结构

```text
kv260-gemm-engine/
├── src/
│   ├── gemm.cpp             # HLS计算核实现
│   └── gemm.h               # 类型、矩阵规模和顶层函数声明
├── tb/
│   └── testbench.cpp        # C仿真与Golden Reference
├── docs/
│   ├── gemm_ip_interface_v0.1.md
│   └── naive_auto_report_v0.1.md
├── hls_config.cfg           # Vitis HLS工程配置
├── .gitignore
└── README.md
```

Vitis HLS 自动生成的工作目录、缓存和编译中间文件不纳入 Git 版本管理。

## 获取可集成 IP

进入仓库的 [Releases](https://github.com/GINSmucker/kv260-gemm-engine/releases) 页面，选择 `v0.1.0`，下载：

```text
gemm_top.zip
```

该文件用于 Vivado IP Catalog 导入和后续系统集成。

源码、Testbench 和接口文档以对应 Release 标签下的版本为准。

## 当前限制

`v0.1.0` 目前存在以下限制：

- 矩阵规模固定为 8×8；
- 仅支持 INT8 输入与 INT32 输出；
- 当前为基础 Dense GEMM；
- 尚未实现动态矩阵尺寸；
- 尚未加入 2:4 结构化稀疏计算；
- 尚未完成板端性能测试。

## 开发路线

- [x] INT8 Naive GEMM 基线
- [x] C Simulation
- [x] C Synthesis
- [x] C/RTL Co-simulation
- [x] AXI 接口与 IP 打包
- [ ] Vivado Block Design 集成
- [ ] KV260 板端运行
- [ ] Pipeline、Unroll 与存储带宽优化
- [ ] INT4 计算支持
- [ ] GEMV 支持
- [ ] 2:4 结构化稀疏计算
- [ ] Python bit-true reference
- [ ] 板端性能与资源对比

## 项目文档

- [三人协作规范与六周计划](KV260_三人协作规范与六周计划_v0.1.md)
- [KV260 连接与使用指南](KV260_连接与使用指南_含FPGA运行环境状态.md)

## 版本说明

### v0.1.0

第一周基础版本：

- 完成 8×8 INT8 Naive GEMM；
- 跑通 C 仿真、C 综合与 C/RTL 协同仿真；
- 完成标准 AXI 接口定义；
- 导出 Vivado IP Catalog 包；
- 建立源码、接口文档和 Release 交付流程。
