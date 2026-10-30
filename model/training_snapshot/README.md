# 成员B精简交付：H576 / 24层2:4 / 100k微调

本包是最终软件交付的精简版。模型版本与精度结论保持不变，文件内容以`RELEASE.json`和`MANIFEST.json`为准。

**不随包发送训练/开发/验收图片集、历史对照模型、大批量评估logits和重复的golden.npy。** 仅保留两张真实演示图片。完整研究数据和原始结果仍在本机C盘研究工作区，可按需引用，具体移除项及字节哈希见[docs/OMITTED_FILES.json](docs/OMITTED_FILES.json)。

## 交付内容

| 目录 | 内容 |
|---|---|
| `models/final/` | 最终FP32/W8A8 ONNX、可训练PyTorch检查点、49层逻辑权重与24层固定2:4位置 |
| `parameters/` | 全部152个FP32参数张量、其中54个非Linear参数、最新49层索引 |
| `weights_axi/` | 最新Dense/Sparse物理载荷、36层PL聚合权重BO及64字节对齐相对偏移 |
| `cases/` | 两张图片覆盖全部49个Linear，共98组真实样本、146次模式调用；golden保留`Y_golden.bin`和`tensors.npz`，不再重复提供`.npy` |
| `runtime/`、`samples/` | 真实网络layer02前后图、两张演示图片和完整1000类输出 |
| `tools/` | 编解码、独立C#参考、层替换、核验、训练与评估代码；训练数据和原Dense教师模型由外部提供 |
| `datasets/` | 标签CSV、抽样来源/哈希元数据；不含图片集ZIP |
| `evidence/`、`training/` | 精度汇总、原始报告、训练配置和日志；批量logits已移出交付 |
| `research_archive/`、`upstream/` | 冻结策略和历史代码、格式来源；旧c062工具仅作来源记录 |
| `validation/` | 软件验证凭据；历史完整包验证与当前精简包验证分开说明 |

## 使用入口

- [最终精度与交付报告](docs/FINAL_REPORT.md)：W8A8 Top-1/Top-5 **64.56% / 87.00%**，原Dense为65.83% / 87.50%，同批1800张比较。
- [A/C接入说明](docs/AC_HANDOFF.md)：位置码、AXI载荷、量化后处理和真实层替换接口。
- [复现与运行命令](docs/REPRODUCE.md)：无需完整数据即可核验交付模型和全部AXI用例；完整训练/评估使用外部数据。
- [验证范围](docs/VERIFICATION_SCOPE.md)：软件核验与未执行的硬件项目。

必须使用完整模型参数；本轮微调也更新patch卷积、cls/pos和LayerNorm等参数。只更新24个稀疏矩阵会混用版本。AXI只计算INT8×INT8→INT32，PS按FP32 `((Yi32*sx)*sw)+bias` 后处理；位置码`p0 | (p1<<2)`不是mask。

完整清单保护除`work/`外的文件。使用`python -B tools/verify_release.py --runtime`核验；新输出写入`work/`。实际HLS/RTL、KV260板端联调及性能测试仍由A/C完成，CPU参考通过不代表板端通过。
