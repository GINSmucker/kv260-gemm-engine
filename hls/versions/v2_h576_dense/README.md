# v2：H576 优化 Dense

本目录保存 C 已发布 H576 Dense 引擎的原始 top/core/config、testbench 和 AXI monitor 源码，
来源为 `codex/h576-summary-20261019` 的提交 `f506f14e44190d035842be6e4bbc0ff431592c5d`。
九份原文件逐字节保留；来源、验证边界与配置说明见 [SNAPSHOT_STATUS.md](SNAPSHOT_STATUS.md)。

这是单个 512-MAC、三路 128-bit AXI 的 W8A8 Dense 核，支持 H576 的 `197×576×192` fc2。
参数顺序为 `model_gemm_top(X,W,Y,M,K,N)`。它没有 OPT46 的输出侧双缓冲，也不支持 2:4 Sparse。

`hls_config.cfg` 是本次为统一目录新建的相对路径入口，与独立 v2 分支的新配置字节相同。
8 ns 来自配对平台的目标时钟；它不是恢复出的原 HLS 配置，尚未重新运行 Vitis HLS/RTL。
在本目录作为源码根目录创建独立组件，构建产物保存在新的工作目录。

AXI monitor 的四份文件同样保留原始字节；其中原工程路径假设未作迁移，不能直接视为
已经适配此归档目录的自动验证入口。原生 C++ 检查使用仓库根目录 `tools/run_version_native.py`，
它只验证合成用例功能，不运行 HLS、RTL 或板卡。

完整 H576 平台、PS 软件和历史结果见
[独立 v2 分支](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense)。
