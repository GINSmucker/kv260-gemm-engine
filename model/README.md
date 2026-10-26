# B · v1 数值参考与数据

本版是 INT8 Dense GEMM 算子，不含冻结视觉模型、剪枝权重、分类图片或微调成果。

- `golden.py`：独立 NumPy INT64 矩阵乘参考，检查紧凑 INT8 输入并导出 INT32 结果。输入尺寸还须符合 v1 的 M/N/K≤1024 限制。
- `cases/manifest.json`：沿用的外部数据清单为空，不表示有已验收的 H576 数据集。
- `../hls/tb/testbench.cpp`：自行生成七个小型合成用例，覆盖零值、单位权重、最小尺寸、完整 tile、尾块与多 tile；不需要图片或权重下载。
- `versions/`：历史模型工具归档，不是 v1 的活跃模型入口。

原默认 `quantize.py` 规定另一套模型的 K≤768 和精度阈值，已保留在 `docs/provenance/legacy_default_model/`，不把它当作 v1 的1024尺寸契约。当前核只接收整数；FP32 预处理、scale/bias、模型精度都不在本版硬件功能中。

真实模型集成、图片精度和板端性能：**NOT RUN**。任何后续对照先固定共同矩阵/权重和计时口径，不能给本算子直接挂上 H576 的整图毫秒数。
