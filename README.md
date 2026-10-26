# v2 — H576 优化 Dense

## 版本与能力

分支 `codex/v2-h576-dense` 复制自 `codex/h576-summary-20261019`，
来源提交 `f506f14e44190d035842be6e4bbc0ff431592c5d`。
H576 的 HLS 算法、模型协议和 PS 运行源码保持原身份，不是 OPT46 的改名版。
活跃 `hls/src/`、`hls/include/`、`hls/tb/` 的五份源码与保留的 `hls/h576/` 原文件逐字节相同。

单引擎512 MAC、三路128bit、125MHz目标，Dense W8A8→INT32。
H576共49个Linear：36个PL、13个PS。没有OPT46的输出stream-of-blocks双缓冲，
没有2:4跳零；共享BO和静态权重由原H576 PS软件调度。

## 目录

| 目录 | 当前用途 |
| --- | --- |
| [hls/](hls/README.md) | 当前H576计算核、testbench和相对路径配置 |
| [model/](model/README.md) | H576数值合同、CPU参考与资产身份 |
| [board/](board/README.md) | `runtime/h576` 的原owner/plugin/client及工作台 |
| [docs/](docs/README.md) | 接口、验证范围、来源锁和分支说明 |
| [results/](results/README.md) | 既有公开H576回执 |
| [tools/](tools/README.md) | 只读结构检查及原生C++检查 |

[VERSION.json](VERSION.json) 定义本分支活跃路径。旧默认核保存在
`docs/provenance/legacy_default_hls/`，不参与当前配置。

## 构建与检查

只读，不写文件、不访问板卡：

```bash
python -B tools/check_version_layout.py
```

已有Windows Vitis环境可做原生C++检查；会新建输出目录、本机程序和日志，不启动HLS/RTL/板卡：

```powershell
python -B tools/run_active_native.py --vitis-root 'D:\Vivado\2026.1\Vitis' --out 'C:\Temp\v2-native-new'
```

当前8ns HLS配置为路径适配而新建，目标来自配对系统约束，不是恢复的原HLS配置。
本次未重新运行Vitis CSim/综合/RTL。模型和固件按[资产锁](docs/locks/ASSETS.lock.json)取得；
新板卡窗口须重新绑定身份，不能重放历史启动命令。

## 验证

原H576公开证据见[验证范围](docs/verification.md)：图片质量、完整性能和有限连续运行各有独立记录。
原142.78/142.68ms为原环境预热后中位数，不能与v3零输入单次143.48ms直接排名。
本次复制、源码检查和原生功能检查单列在VERSION及results，不增加板测资格。

## 限制

两千张新质量保留集未使用；跨主机逐值差异和正式发布范围保持原结论。
H576_PACKAGE/SOURCE_MAP锁保持不变，新路径配置不冒充原构建输入。
必要缓存同步、完成和安全退出约束保留。原首页保存在 `docs/provenance/H576_UPSTREAM_README.md`。

## 三分支导航

- [v1 初级Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v1-dense-initial)
- [v2 H576 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense)
- [v3 OPT50 Sparse](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v3-opt50-sparse)

顶层结构和说明入口统一；ABI、驱动和验证成熟度分别记录。
