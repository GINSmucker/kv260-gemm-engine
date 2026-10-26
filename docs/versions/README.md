# 三阶段源码快照：v1 / v2 / v3

整理日期：2026-10-26。此开发分支先保存已有成果，再继续重复性能与真实图片测试。
v1/v2/v3 是本次归档标签，不替换历史 Git tag，也不是三套已经完成同模型板端验收的发布版。
既有根目录计算基线与 H576 首页原样保留；本次没有重新综合、布局布线或加载板卡。

| 标签 | 精确实现 | 位置 | 当前用途 |
| --- | --- | --- | --- |
| v1 初级 Dense | 历史 `v0.2.0`，commit `cd0870dd642378719fd844f8cdbe4cacaae36609` 的 tiled INT8 核 | [源码](../../hls/versions/v1_dense_initial/SNAPSHOT_STATUS.md) | 保存早期分块、流水线实现；不是 FP32 核 |
| v2 优化 Dense | C 已发布的真实 H576 Dense，512 MAC、128-bit 接口，支持 K=576 的 fc2 | [源码](../../hls/versions/v2_h576_dense/SNAPSHOT_STATUS.md) | 对应既有 Dense H576 模型与冻结运行包；不是 OPT46 |
| v3 当前 Sparse | OPT50，单个共享乘加阵列，由 mode 选择 Dense / 2:4 Sparse | [HLS](../../hls/versions/v3_sparse_opt50/SNAPSHOT_STATUS.md)、[板端软件](../../board/versions/v3_sparse_opt50/SNAPSHOT_STATUS.md) | 当前 H576 十二层稀疏候选和固定路由运行软件 |

三个独立版本分支采用相同的根目录入口：
[v1 初级 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v1-dense-initial)、
[v2 H576 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense)、
[v3 OPT50 Sparse](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v3-opt50-sparse)。
本分支保留三份对照源码；独立分支的 `hls/` 是各自活动入口，以对应 README 为准。

## 两种比较问题要分开

三阶段源码适合展示架构演进，但它们的形状限制、控制 ABI、平台和 PS 软件不完全相同。
在补齐同形状适配和相同条件测试之前，不能把历史数字直接排成三行端到端性能排名。
v1 参数顺序是 `M,N,K`，v2/v3 是 `M,K,N`；旧 runtime 不能只改核名称后复用。
v1 物理 AXI 宽度缺少对应导出 RTL 复核，不标作已确认 32-bit。

要单独证明稀疏收益，使用 v3 的同一电路、同一数学权重、同一 owner/plugin：

- `all_dense`：36 个 PL 层都用 Dense 表示；权重仍是剪枝后的同一数学权重。
- `mixed`：24 Dense + 12 Sparse。十三个 PS Linear 的路由和软件不变。
- 十二层索引：26、27、30、31、34、35、38、39、42、43、46、47。
- W8A8 输入/权重，INT32 累加；此版本不实现 W4 或板端剪枝。

## 已保存的 v3 板端证据

[正式对照回执](../../results/versions/v3_fair_pair_20261025.json) 来自用户提供的板卡
`compare.py` stdout。归档工具只解析、保存该回执，没有在本次整理中重新读取板卡 root 记录。
[来源记录](../../results/versions/evidence_provenance.json) 保存原始附件 SHA256。

| 同权重单次测量 | Dense | Mixed |
| --- | ---: | ---: |
| 完整模型 Runner.run | 145.727537 ms | 143.483705 ms |
| 十二个目标层的主机完成观察窗口合计 | 8.669497 ms | 5.549665 ms |
| 三十六个 PL 层的同类观察窗口合计 | 26.680320 ms | 23.528775 ms |

状态：`PASS`，范围仅为该单次样本对的数值一致、身份核对和正常关闭证据。
完整模型减少 1.5397446812%；目标层窗口减少约 35.99%。窗口包含轮询与完成观察开销，
不是独立测量的纯 PL 周期；各层、plugin、owner 和完整模型计时相互重叠，不可相加。
稳定性能：`NOT RUN`。当前 Sparse 候选的真实图片板端精度验收：`NOT RUN`。
这些限制不否定原 Dense H576 已有独立图片和连续运行证据。

## H576 与 OPT46 的来源边界

远端 main 的 H576 首页已经索引 C 的冻结运行包、图片质量和有限连续运行记录。
本分支不删除、不改写那些成果，也不把它们继承给 Sparse。
更新远端资料后，已在提交 `f506f14e44190d035842be6e4bbc0ff431592c5d` 的 `hls/h576/`
找到真实 H576 top/core/config 与 testbench，并按 `H576_SOURCE_MAP.json` 逐 SHA 核对。
其 `H576_PACKAGE.lock.json` 与本机原 H576 运行包 manifest 原字节相同，因此 v2 已改为该来源。
此前仅检查旧本地 refs 所得的“未找到 H576 源码”结论已被新资料修正。

H576 core 沿用 OPT45 Dense 路径；它不包含 OPT46 的输出侧双缓冲。OPT46 原始形状不接受
H576 fc2 的 K=576，不能仅换名称当作 H576。本分支旧 OPT46 快照保留于原提交历史，
不再占据活动 v2 目录。172.206422 ms 或 main 所列稳态约 142.7 ms 属于各自固定条件下的
Dense H576 记录，不是 OPT46 或本次新配置的复测。

H576 原目录没有配对 HLS 配置；本次新建 8 ns 相对路径入口，尚未重跑 HLS/RTL。
三版本完整性能排名仍需同条件测试；v2 与 v3 的模型权重、软件、接口和平台身份不同，
不能直接用各自历史端到端数字解释稀疏收益。

## 保存内容与外部资产

- A：三份 HLS 原始源码、配置、testbench；历史 README 保留原字节，以各目录 `SNAPSHOT_STATUS.md` 为当前解释。
- 原样历史文档可能链接未纳入本快照的旧团队/报告文件；这些引用不是当前目录可用性的承诺，优先使用本索引及外部资产入口。
- B：[数据格式和模型工具](../../model/versions/v3_sparse12/README.md)，含剪枝、打包、Golden、评估与测试源码。
- C：当前 PS 软件快照、自研平台脚本、测试、观察器及外部输入身份索引。
- 共同文档：本索引、[接口入口](../interface.md)、结果回执和 [SHA256 清单](source_manifest.json)。
- 不纳入 Git：图片、模型权重/ONNX、虚拟环境、Vitis/Vivado 工作目录、IP 导出大包和 FPGA 二进制。
- 模型/固件等资产见 [外部资产约定](external_assets.md)；取不到的材料明确标为外部依赖，不生成占位权重。

## 下一阶段

1. 保持 v3 源码与软件不变，交替执行 Dense/Mixed 和 Mixed/Dense，统计相同计时口径下的中位数与波动。
2. 使用固定、有标签且不用于选择/训练的真实图片，记录预处理、分类精度和完整推理延迟。
3. 补齐三阶段同形状平台适配后，单独评估架构优化；不重写已有历史快照来掩盖差异。
4. B 增加稀疏层/微调时另开模型候选，保留当前十二层模型身份和对照结果。

上板前必须重新观察 boot、驻留固件与持锁进程。此快照中的旧 boot/plan 路径是历史绑定，
不能复制旧命令当成新启动的运行入口。归档校验命令只读本地文件：

```bash
python tools/check_version_snapshot.py
```

当前 `native_functional_20261026.json` 保存三个独立分支活跃路径的新原生C++检查，均通过。
旧OPT46检查另存 `native_functional_opt46_historical_20261026.json`，不作为当前v2证据。
模型工具 56 项测试、板端证据工具 101 项测试（98通过、3跳过）及执行范围
见 [归档检查回执](../../results/versions/archive_checks_20261026.json)。这些检查不访问板卡。
原生 C++ 可用 `tools/run_version_native.py --vitis-root <已有Vitis目录> --out <全新输出目录>` 复现；
会编译本机程序并写入新目录，参数 rtl 仅表示用例集，不是 RTL 仿真。
