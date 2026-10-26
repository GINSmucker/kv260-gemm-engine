# 三阶段源码快照：v1 / v2 / v3

整理日期：2026-10-26。此开发分支先保存已有成果，再继续重复性能与真实图片测试。
v1/v2/v3 是本次归档标签，不替换历史 Git tag，也不是三套已经完成同模型板端验收的发布版。
既有根目录计算基线与 H576 首页原样保留；本次没有重新综合、布局布线或加载板卡。

| 标签 | 精确实现 | 位置 | 当前用途 |
| --- | --- | --- | --- |
| v1 初级 Dense | 历史 `v0.2.0`，commit `cd0870dd642378719fd844f8cdbe4cacaae36609` 的 tiled INT8 核 | [源码](../../hls/versions/v1_dense_initial/SNAPSHOT_STATUS.md) | 保存早期分块、流水线实现；不是 FP32 核 |
| v2 优化 Dense | 本机 OPT46 原始源码，512 MAC、128-bit 接口、输出侧 dataflow 缓冲 | [源码](../../hls/versions/v2_dense_optimized/SNAPSHOT_STATUS.md) | 保存优化前驱；其支持尺寸不能冒充 H576 |
| v3 当前 Sparse | OPT50，单个共享乘加阵列，由 mode 选择 Dense / 2:4 Sparse | [HLS](../../hls/versions/v3_sparse_opt50/SNAPSHOT_STATUS.md)、[板端软件](../../board/versions/v3_sparse_opt50/SNAPSHOT_STATUS.md) | 当前 H576 十二层稀疏候选和固定路由运行软件 |

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
原 H576 运行包能追溯 PS 源码、模型、位流及 metadata，但其完整匹配 HLS 源码
在本次本地核对中未找到。OPT46 的原始形状包含 768，不接受 H576 fc2 的 K=576。
因此不将 172.206422 ms 或 main 所列稳态约 142.7 ms 标作本次 v2/OPT46 的板测数据。
要补齐三版本完整模型对照，须先取得匹配 H576 的 Dense HLS/platform 来源，或另建经过验证的适配版。

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

本次新目录原生 C++ 三核功能检查、模型工具 56 项测试、板端证据工具 101 项测试（98通过、3跳过）
见 [归档检查回执](../../results/versions/archive_checks_20261026.json)。这些检查不访问板卡。
原生 C++ 可用 `tools/run_version_native.py --vitis-root <已有Vitis目录> --out <全新输出目录>` 复现；
会编译本机程序并写入新目录，参数 rtl 仅表示用例集，不是 RTL 仿真。
