[README.md](https://github.com/user-attachments/files/32649218/README.md)
# KV260 GEMM Engine

面向 AMD Kria KV260 的 HLS 矩阵计算引擎项目，目标是逐步实现 INT8/INT4 GEMM、GEMV 和 2:4 结构化稀疏计算，并完成 PS/PL 集成与板端性能测试。

当前计算核仍为固定规模的 8×8 INT8 Naive GEMM IP。**2026-09-28 新增 F8 运行链开发验证版：Vivado 系统集成、真实 XRT 高地址 DDR BO、KV260 PL 计算、客户端断开恢复与 starter 回退均已验证。**

先读 [本次修改与实板交付报告](docs/F8_DELIVERY_20260928.md)、[F8 调用接口](docs/F8_RUNTIME.md)和[复现步骤](docs/F8_REPRODUCE.md)。本分支随附完整配对产物和原始证据；正式封存验收与性能 benchmark 尚未运行，计算核算法和发布 RTL 未改动。

## 当前版本

**计算 IP Release：v0.1.0；系统集成增量：F8 runtime / 2026-09-28（开发验证版）。** 原 Release 标签保留。

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
| Vivado 系统集成 | PASS，HPM0 / HP0 128 bit，100 MHz 约束 |
| KV260 板端开发验证 | PASS，9 次 / 576 个 INT32 输出逐元素一致 |
| 提交后客户端断开恢复 | PASS，同一原生进程恢复后继续计算 |
| 临时加载后的 starter 回退 | PASS，最终 ZOCL 未加载、XRT 0 devices |
| 独立封存验收 / 性能 benchmark | NOT RUN / NOT RUN |

前四项为原发布 IP 状态；本次新增平台/板测证据见 [2026-09-28 原始结果](results/c_integration/f8_runtime_20260928_RESULT.md)。

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

F8 增量目录：`board/` 为 Python/C++ host，`hardware/kv260_f8/` 为平台和 AXI 观测 RTL，`scripts/` 为构建/封装/完整性入口，`tests/` 为开发测试。`results/c_integration/` 仅归档选定的实测配对包、报告和日志；`results/external_ip_review_20260927/release/` 固定原 v0.1.0 IP ZIP 与解压源码，供未改动的构建脚本和静态契约测试使用。

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
- [x] Vivado Block Design 集成（F8 配对平台）
- [x] KV260 板端运行（公开开发用例）
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

### F8 runtime / 2026-09-28

- 修正 PS HPM0 32/128-bit 不匹配、AXI 观测接线和发布 IP burst 元数据；新增启动前位宽、ABI、构建 tag 及产物哈希检查。
- 新增真实 XRT BO/CU 所有者、64-bit 地址与缓存同步、chain 控制、输入/尾部 guard 校验、有界 RPC、显式恢复和临时加载/回退脚本。
- 实板 9/9 任务、576/576 输出一致；提交后断开恢复和安全回退 PASS。
- WNS=3.373 ns、WHS=0.010 ns；DRC 0 Error / 5 Warning。保留首次上板失败与内核告警，详细边界见交付报告。
- 本次没有实施 PIPELINE、UNROLL、tiling、动态尺寸、D64 或 P2 性能优化。

### v0.1.0

第一周基础版本：

- 完成 8×8 INT8 Naive GEMM；
- 跑通 C 仿真、C 综合与 C/RTL 协同仿真；
- 完成标准 AXI 接口定义；
- 导出 Vivado IP Catalog 包；
- 建立源码、接口文档和 Release 交付流程。
