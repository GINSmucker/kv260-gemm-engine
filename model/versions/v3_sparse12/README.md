# v3 十二层 Sparse 模型工具快照

此处保存当前 H576 稀疏研究的 Python 源码，不包含模型、图片、训练输出或虚拟环境。
当前板端候选固定十二层 26/27/30/31/34/35/38/39/42/43/46/47，其他 PL 层 Dense；
proj/head 保持 PS。不能将早期全36层剪枝候选的状态当作当前十二层状态。

- `sparse24.py`：逻辑 values/positions、重建和整数参考。
- `pack_sparse24_axi.py` 与 docs：`H576_SPARSE24_SPLIT128_V1` 的128bit打包/验证。
- `export_mlp12.py`：冻结已选择的 c062 掩码，不执行训练；依赖既有搜索证据和候选。
- `eval_models.py`、`download_imagenet100.py`：统一预处理、Top1/Top5、新错例和开发集取得。
- 其余诊断/选择源码按原名保存，提供导出脚本完整的本地 Python import 链。
- `full_model_adapter/`：固定十二层完整模型导出和 CPU reference 工具；保留原来源路径假设。

原 requirements 为 Windows CPython3.12 CPU 环境锁，含特定 PyTorch wheel，
不能在板卡 Ubuntu aarch64 上直接使用。本次归档复用已有本机 Python 环境运行无设备单元测试，
没有重新安装依赖、训练、改动原模型或访问网络数据集。

原脚本的一些 source/search/candidates 目录是固定来源假设，不因为移动到本目录就变为可独立导出。
外部输入缺失时应拒绝，不跳过哈希。先阅读原参数和仓库的外部资产清单；
不要直接运行导出/下载命令来替代只读检查。

当前模型身份：

| 项目 | SHA256 |
| --- | --- |
| 数学权重 weights.bin | `88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6` |
| W8A8 ONNX 参考 | `50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49` |
| custom graph | `8a462d73b5e8bb6544f65c6187dc621b560ae1329520f5556430ff42c97f0dfa` |
| AXI cases manifest | `6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf` |

数据格式解码和同权重算术一致，不等于剪枝模型与未剪枝模型分类精度相同。
真实图片板端精度、微调与新扩层候选属于后续独立验证，见当前版本总索引。
