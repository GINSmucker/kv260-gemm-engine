# v1 文档入口

- [interface.md](interface.md)：当前 `gemm_top(X,W,Y,M,N,K)` 与数值/非法参数语义；不借用旧寄存器表。
- [verification.md](verification.md)：文件身份、历史摘要与当前未执行测试的分层状态。
- [progress.md](progress.md)：本分支能力和缺失项。
- [provenance/](provenance/README.md)：保留的旧默认文件，只说明来源，不是活跃入口。
- `versions/`、`history/`、图片和早期工作台文档继续作为历史材料；以根 [VERSION.json](../VERSION.json) 识别当前分支。

本版没有改变数学实现，没有生成硬件，也没有把其他分支的板测/精度结论转移为 v1 PASS。
