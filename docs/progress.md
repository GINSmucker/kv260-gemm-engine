# v1 当前状态

分支 `codex/v1-dense-initial` 的活跃 HLS 文件已恢复为历史 v0.2 原始字节，并与归档身份比较通过。没有修改算法、增加模式或运行硬件。

当前可阅读/构建的内容是动态尺寸 INT8 Dense 算子与七例合成 testbench。初级四路 K 并行和顺序 load/compute/store 是本版能力边界。

本次活跃目录原生检查、HLS/RTL、IP、完整平台、XRT runtime 和板测状态分别由 [verification.md](verification.md) 与 `VERSION.json` 记录。未执行项保留 **NOT RUN**。H576 和 Sparse 的归档资料不是本版里程碑。
