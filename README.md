# v3 · OPT50 可重构 Dense / 2:4 Sparse

## 版本能力

本分支 `codex/v3-opt50-sparse` 的活动计算核是 OPT50：一个共享的 512-MAC W8A8 阵列，通过 `mode` 在 Dense 与 K 方向 2:4 Sparse 之间切换。只有一个顶层 IP，输入/权重为 INT8、输出为 INT32，AXI 数据通路为 128 bit。它不实现板端剪枝或 W4 混合精度。

完整 H576 候选保留 49 个 Linear：36 个进入 PL、13 个保留 PS。当前十二层剪枝索引是 26、27、30、31、34、35、38、39、42、43、46、47；QKV 在 PL 按 Dense 运行，proj/head 在 PS。同权重对照使用同一组剪枝后的数学权重：`all_dense` 为 36D/0S，`mixed` 为 24D/12S。

## 目录

| 责任 | 活动入口 | 内容 |
|---|---|---|
| A | [hls/](hls/README.md) | OPT50 top/core/config、testbench、可移植 HLS 配置 |
| B | [model/](model/README.md) | 十二层候选、2:4 打包、Golden、评估与导出工具源码 |
| C | [board/](board/README.md) | `board/runtime` 冻结 PS 包、平台脚本、测试和固件源描述 |
| 共同 | [docs/](docs/README.md) | [真实 OPT50 ABI](docs/interface.md)、来源与外部资产 |
| 共同 | [results/](results/README.md) | 有范围说明的历史结果和当前分支检查记录 |

[VERSION.json](VERSION.json) 固定本分支的活动路径与源码身份。其他版本快照保留在原 archive 分支，原默认 OPT38 路径移到 [来源存档](docs/provenance/README.md)，不会在活动 `hls/src` 或 `board/runtime` 中混用。

## 构建检查与操作影响

在已有 Python 环境中，从仓库根目录可执行：

```powershell
python -X utf8 -B board/runtime/verify_tools.py
python -X utf8 -B board/run_cpu_tests.py
```

第一条只读核对原 46 文件清单。第二条需要 NumPy，只运行合成 Python 测试，会在测试自有临时目录写入并清理数据；不启动 owner、XRT、Vivado 或设备。

Vitis HLS 使用 [hls/hls_config.cfg](hls/hls_config.cfg)，工作目录设为 `hls`；该配置将 CSim/Cosim 用例集设为 `full`/`rtl`，本次整理没有执行它。综合/仿真会写入新工具工作目录。真实权重 `golden` 另需取得已固定身份的 AXI 包。板端 ARM 编译、平台重建的依赖和影响分别见 [C 入口](board/README.md) 与 [外部资产](docs/external_assets.md)；任何加载/START 都需要新的、与设备当前状态匹配的运行流程。

## 验证

[历史同权重单次对照](results/v3_fair_pair_20261025.json) 的完整模型 `Runner.run` 为 Dense 145.727537 ms、Mixed 143.483705 ms；两个组均正常关闭并与同一 CPU 参考逐位一致。十二个目标层的主机完成观察窗口合计由 8.669497 ms 降至 5.549665 ms。该窗口包含轮询与完成观察，不能称为纯 PL 周期，也不能与其他重叠区间相加。

这些数据来自固定 fd96539d 系统、125 MHz 目标和同一模型/输入/二进制/线程配置，归档来源见 [结果说明](results/README.md)。本次目录激活没有重跑 HLS、RTL、Vivado、ARM 编译或实板。重复统计、Sparse 候选真实图片板端准确率均为 `NOT RUN`。

## 限制

仓库不包含模型权重/ONNX、图片、ARM ELF/SO/KO、位流、IP 导出包或完整 AMD PS VIP 模型。`board/runtime` 按字节保留原 47 文件发布包及 pins，仍绑定历史 boot_id、guardian 计划、编译器和板端路径；clone 后通过文件校验不等于可以直接加载或启动计算。新设备/启动/平台必须另行审查，不删除门禁来适配路径。ORT 头文件及其 [MIT 许可](board/licenses/ONNXRuntime-MIT.txt) 保留。

## 三分支导航

| 分支 | 对应阶段 |
|---|---|
| [codex/v1-dense-initial](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v1-dense-initial) | 初级 tiled Dense 核 |
| [codex/v2-h576-dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense) | 优化 Dense H576 系统 |
| [codex/v3-opt50-sparse](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v3-opt50-sparse) | 当前 OPT50 Dense/Sparse 可重构系统 |

三分支用于保存实际演进阶段；历史模型、ABI 和测试条件不完全相同，不把它们的旧数值直接作为同条件性能排名。
