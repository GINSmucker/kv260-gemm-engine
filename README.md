# v1 · 初级 Tiled Dense GEMM

## 1. 版本与能力

本分支 `codex/v1-dense-initial` 保存早期 **Dense v0.2**，来源提交 `cd0870dd642378719fd844f8cdbe4cacaae36609`（历史 tag `v0.2.0`）。活跃核是 `hls/src/gemm.cpp`，不是 OPT38、H576 或 OPT50。

计算 `Y[M,N] = X[M,K] × W[K,N]`：X/W 为 INT8，完整 K 累加和 Y 为 INT32。支持 `1 <= M,N,K <= 1024`；TM16/TN16/TK32、K 方向四路并行，支持 M/N/K 尾块。目标 K26、10 ns。没有 Sparse、INT4、DATAFLOW 或双缓冲。

顶层为 **`void gemm_top(X,W,Y,M,N,K)`**；维度顺序与后续版本不同。非法维度直接返回、不访问矩阵；没有可据此假定的 ERROR 返回寄存器。精确身份和状态见 [VERSION.json](VERSION.json)。

## 2. 目录

| 路径 | 当前入口 |
|---|---|
| [hls/](hls/README.md) | A：本版源码、testbench、相对路径 HLS 配置 |
| [model/](model/README.md) | B：独立整数 Golden；没有冻结视觉模型或图片集 |
| [board/](board/README.md) | C：说明尚缺的匹配平台和运行时，不提供占位驱动冒充实现 |
| [docs/](docs/README.md) | 本版接口、状态和来源 |
| [results/](results/README.md) | 分范围保存验证证据，不给未测项目填数 |
| [tools/](tools/README.md) | 当前分支检查入口与历史工具边界 |

其他版本快照保留在原 archive 分支，本分支只保留当前版本的活动内容。旧默认 OPT38 文件已保留至 `docs/provenance/legacy_default_*`；不要以目录中仍有历史文件推断其为当前实现。

## 3. 构建与只读检查

只读结构/源文件身份检查，不编译、不访问设备：

```bash
python -B tools/check_version_layout.py
```

HLS 工具需使用已有 Vitis 2026.1。以下以 PowerShell 为例，须从 `hls/` 运行配置；**会写入全新 `hls/work/` 工作目录，不会加载板卡**。先确认示例输出目录不存在，后续执行使用新的目录名；不能覆盖旧报告。

```powershell
Push-Location ./hls
vitis-run.bat --mode hls --csim --config hls_config.cfg --work_dir ./work/v1_csim_new
Pop-Location
```

综合和 C/RTL Cosim 也会写工具工作目录，须依次使用与该源码匹配的同一新组件。当前不提供上板命令；没有匹配驱动时，不应套用 v2/v3 的寄存器或 systemd 命令。普通原生 C++ testbench 即使使用 Vitis 头文件，也不等于 HLS 或 RTL 仿真。

当前分支原生功能入口为 `tools/run_active_native.py`：只编译本版 `hls/src/gemm.cpp` 与 `hls/tb/testbench.cpp`，会写入全新输出目录并运行本机测试程序，不访问 FPGA；使用已有 Vitis 头文件不等于运行 Vitis CSim。

## 4. 验证

| 项目 | 状态 | 范围 |
|---|---|---|
| 活跃四个源/配置文件与冻结 v0.2 快照逐字节一致 | PASS | 文件身份，不是计算测试 |
| 历史 CSim、综合、RTL Cosim | PASS | 原 v0.2 摘要记载 7 个小矩阵用例；不冒充本次重跑或板测 |
| 新活跃目录 HLS/RTL/平台/板端验证 | NOT RUN | 本次目录规范化没有运行这些工具 |
| 完整视觉模型与真实图片精度/性能 | NOT RUN | 没有本版配对链路 |

原摘要及来源见 [hls/reports/README.md](hls/reports/README.md)、[docs/verification.md](docs/verification.md)。不将 H576 的整图 170 ms/142 ms 或 OPT50 Sparse 测量归属于此核。

## 5. 限制

- 四路并行的初级 Dense 核不等于整个分类模型，也不是“FP32 初版”。
- 最终导出 AXI 数据位宽、地址宽度及寄存器偏移尚需新导出文件核对；不能用后续版本的 ABI 填补。
- 历史摘要的 II=1 指每周期启动一组四路 K 乘加，不等于每周期完成一个输出或整个矩阵。
- 没有 v1 完整 KV260 平台、XRT runtime、位流与板端性能。历史材料和其他分支的 PASS 不转移给本分支。
- 不提交权重、图片、工具缓存和 FPGA 二进制；当前合成 testbench 自行生成小数据，不需要下载模型。

## 6. 三分支导航

- [v1 · 初级 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v1-dense-initial)：本分支。
- [v2 · H576 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense)：独立模型与配对来源；不是 OPT46 的替代名称。
- [v3 · OPT50 Dense/Sparse](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v3-opt50-sparse)：共享乘加阵列，运行时选择数据表示。

三分支采用相同 A/B/C 顶层组织，但函数、ABI、形状、模型和验收资格各自独立。导航不表示尚未推送的分支已经在远端可访问。
