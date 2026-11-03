# H576 Sparse24模型与Dense对照

默认模型为B交付的24个MLP Linear固定2:4掩码版本，W8A8 SHA为`ea501779...`；原Dense对照SHA为`f2f20336...`。完整身份见 [来源](../docs/provenance.md)。两模型均为49层Linear、36PL/13PS；Sparse24为12D/24S，Dense对照为36D/0S。

`training_snapshot/`按原字节保存B的训练／导出代码、冻结policy和历史报告。原交付以10万张、5轮、7815次更新继续训练父24层，Dense冻结教师蒸馏；按独立开发集选择epoch5，验收只作预定采用判断。

[模型报告](training_snapshot/docs/FINAL_REPORT.md)与[训练策略](training_snapshot/research_archive/policy.json)说明选层、训练参数和评估口径。

`prepare_dense_model.py`、`prepare_sparse24_model.py`保存本次模型适配来源，保留历史workspace假设。它们不是改路径即可随意运行的训练程序。受检模型的完整生成目录可由两个外部payload和对应运行快照精确还原。

物理格式见 [SPLIT128](docs/sparse24_axi_format_v0.1.md)。训练量化协议、CPU参考与当前500张／历史1800张结果分别见 [测量](../docs/measurement.md)、[验证](../docs/verification.md)。
