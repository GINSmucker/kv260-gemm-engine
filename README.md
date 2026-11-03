# KV260 可重构 Dense / 2:4 Sparse 矩阵计算引擎

本项目在 KV260 上实现 W8A8 稠密／结构化稀疏矩阵计算引擎，并完成 H576 视觉模型的真实图片推理验证。同一个 OPT50 计算核通过 `mode` 切换 Dense 和 K 方向 2:4 Sparse 路径。

完整模型包含 49 个线性层：36 个进入 FPGA PL，13 个保留在 ARM PS。当前默认模型对 24 个 MLP 线性层剪枝并微调，每次推理在 PL 执行 12 次 Dense 和 24 次 Sparse 调用；原始未剪枝模型通过同一核执行 36 次 Dense 调用。

## 已取得的结果

两个模型均完成相同 500 张真实图片的板端验收，各自 CPU 参考和 PL 输出的 **500,000 个 FP32 logits 逐位一致**。500 张图片覆盖 250 类，预测在完整 1000 类输出上排名。

下表精度使用相同 500 张；耗时统一使用软件调度版本一致的第 306～500 张，共 195 张。CPU 为 ORT W8A8 **数值语义参考**，没有声明为专门优化的原生 INT8 CPU 性能上限。

| 模型与执行方式 | Top1 / Top5（500张） | 平均耗时（195张） | 中位数 | P95 | 相对自身CPU参考 |
|---|---:|---:|---:|---:|---:|
| 原始 Dense，ARM CPU | 70.6% / 90.4% | 308.541 ms | 300.069 ms | 366.493 ms | 1× |
| 原始 Dense，ARM＋FPGA | 70.6% / 90.4% | **143.494 ms** | 140.688 ms | 163.435 ms | **2.150×** |
| 剪枝微调 Sparse24，ARM CPU | 71.4% / 92.2% | 303.003 ms | 295.141 ms | 355.918 ms | 1× |
| 剪枝微调 Sparse24，ARM＋FPGA | 71.4% / 92.2% | **136.588 ms** | 133.128 ms | 160.299 ms | **2.218×** |

同调度版本的 Dense / Sparse 完整模型平均延迟由 143.494 ms 降至 136.588 ms，降低 **4.81%**，约 **1.051×**。这是原始 Dense 与剪枝微调模型的系统对照，权重不同，不能单独归因为稀疏 MAC 核加速。

计时为完整 `Runner.run`，包含 PS 工作、图 BEGIN/END 和必要 client 缓存同步；排除初始化、图片预处理和 CPU 参考验算。每张图片只保留本次记录，批次采用有界常驻会话；没有由此声明稳态吞吐、长期尾延迟或纯 PL 周期性能。[统计与逐图数据](results/README.md) · [测量口径](docs/measurement.md)

## 架构与配置

```mermaid
flowchart LR
    A[真实图片] --> B[PS 预处理和完整模型]
    B --> C[PS 量化与共享缓冲]
    C --> D[OPT50 单核 Dense / 2:4 Sparse]
    D --> E[PS 反量化和其余算子]
    E --> F[1000类 logits / Top1 / Top5]
```

| 项目 | 交付配置 |
|---|---|
| 平台 | KV260 / Kria K26 |
| 数值路径 | INT8 输入与权重、INT32 累加输出，PS 保留既定浮点后处理 |
| 计算核 | 单一共享 512-MAC 阵列、8 参数 OPT50 ABI |
| 时钟 / AXI | 125 MHz 目标、128 bit；OS 记录时钟为 124,999,999 Hz |
| 模型 | H576、12 个 Transformer 块、49 个线性层 |
| Sparse24 路由 | PL 12 Dense＋24 Sparse；PS 13 Linear |
| 软件 | Ubuntu 24.04.2 / kernel 6.8.0-1015-xilinx；ORT 1.22.1、NumPy 1.26.4、g++13 |
| 推理配置 | batch=1、ORT intra=4 / inter=1、关闭图优化 |

## 验证范围

- 当前板端分类精度范围是固定 500 张子集，不是完整 ImageNet 验证集。
- B 的历史 1800 张 CPU 单线程报告中，Dense Top1/Top5 为 65.83%/87.50%，Sparse24 为 64.56%/87.00%。该记录单列保留，不能由本次 500 张优势宣称总体精度提高。
- 精度对照包含剪枝和额外微调／蒸馏的共同影响，没有同训练预算的 Dense 微调消融。
- 原始运行包和其板测证明按 SHA256 冻结保存。此次 Git 目录整理的离线检查与原包板测身份分别记录；没有冒充一次新的上板、HLS、RTL 或 Vivado 验证。
- 板端运行使用匹配模型、固件、编译器和当前启动身份；旧boot、计划和token不能复用。

详见 [验证与限制](docs/verification.md) 和 [证据来源](docs/provenance.md)。

## 阅读与复现

### KV260 LAB 统一展示入口

使用已有PowerShell 7和Node.js 24或更高版本，在仓库根目录运行`pwsh -File ./start-final.ps1`，打开 **http://127.0.0.1:4179/#overview**。KV260 LAB的“成果总览”“性能与耗时”“系统架构”展示当前500张精度、195张曲线、原始回执耗时分解和架构图，支持数据及报告SVG下载。H576旧数据保留在“历史实测”；“样例演示”与真实推理分开标识。

在“连接与诊断”输入已有Sparse24板端HTTP服务地址（例如`http://192.168.137.2:8080`），连接后可在“推理工作台”上传JPEG并显式启动真实计算。界面核对输入／模型／请求身份、Native完成字段和CPU／PL摘要，提供Top5、计时和原始回执下载；这些现场记录与固定500张验收结果分开。详见[网页使用指南](docs/workbench.md)和[一次现场验证回执](results/live_demo/sparse24_lab_20261101.json)。

### 工作台启动命令

也可使用工作台入口启动同一套界面：

```powershell
pwsh -File ./start-workbench.ps1
```

打开 **http://127.0.0.1:4178/#overview**，按Ctrl+C关闭服务。两个启动命令仅默认端口不同，均显示KV260 LAB。浏览归档不需要板卡或模型／固件下载；实时推理使用已经部署并报告就绪的Sparse24服务，不通过网页加载固件或修改运行配置。

### 板端直接访问

已有展示板卡通过网线提供 **http://192.168.137.2:8080/#overview**，电脑有线网卡使用`192.168.137.1/24`。以太网设置、JPEG推理和正常关机见[板端使用指南](docs/boot-demo.md)。

### 代码与数据入口

- [系统架构与技术实现](docs/architecture.md)：当前 OPT50 的系统整体、硬件通路、软件流程及 Dense／Sparse 共用阵列；[交互图阅读入口](docs/diagrams/opt50/index.html)支持源码追踪和导出。
- [快速开始](docs/quickstart.md)：离线源码检查、还原受检运行包、CPU 自测和板端使用前提。
- [共同接口](docs/interface.md)：Dense/Sparse 数值、布局、尺寸和寄存器约定。
- [模型与训练](model/README.md)：24 层掩码、原始模型身份、训练来源和外部数据。
- [ARM 与平台](board/README.md)：冻结运行包、平台源码、构建和执行职责。
- [外部资产清单](assets_manifest.json)：模型、平台和完整证据包的精确身份；下载见匹配 Release。
- [最终Release下载](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/opt50-sparse24-final-20261030)：剪枝／Dense模型、固件及完整证据。Git源码下载不包含这些大文件。
- [版本能力与证据范围](docs/version-history.md)：v1、H576、Sparse12、Sparse24及研究分支的用途；附H576交互式架构图入口。

```text
hls/       OPT50 源码、HLS配置与合成测试用例
model/     模型准备和原始B训练/导出源码快照
board/     平台脚本、部署源描述、冻结ARM运行包
results/   原始摘要、逐图CSV、CPU/PL统计和检查记录
docs/      接口、环境、复现、测量与验证边界
tools/     身份校验、运行包还原、离线证据复核
```

## 版本演进

[v1 初级 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v1-dense-initial) · [v2 H576 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense) · [v3 OPT50 / 12层 Sparse](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v3-opt50-sparse)。历史分支保持独立身份；当前交付为 24 层 Sparse 和原始 Dense 的真实图片对照。

原OPT50的时序、资源、时钟、DRC和布线报告见[硬件实现证据](results/README.md#原opt50硬件实现报告)。它们与已验收fd96539d系统绑定，保留原始SHA，区别于本轮源码整理的检查结果。
