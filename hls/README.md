# A · v1 活跃 HLS 核

入口仅为 `src/gemm.cpp`、`src/gemm.h`、`tb/testbench.cpp` 和 `hls_config.cfg`。四文件原样来自历史 `v0.2.0` 提交 `cd0870dd642378719fd844f8cdbe4cacaae36609`；没有修改算法、数值或接口。

- 顶层 `gemm_top(X,W,Y,M,N,K)`；X/W INT8、Y INT32。
- M/N/K 各为 1～1024；TM16/TN16/TK32，四路 K 乘加，M/N/K 尾块零填充。
- K26 `xck26-sfvc784-2LV-c`，目标 10 ns、不确定度 10%。
- load/compute/store 顺序执行，没有 Sparse 或双缓冲。

在本目录使用配置，它只引用 `./src/` 和 `./tb/`。工具工作目录放 `work/` 的全新子目录；构建会写文件但不会自行上板。测试平台生成七个小型合成用例，原生 C++ 运行不是 RTL 验证。

`include/` 没有本版独立头文件；真实声明位于 `src/gemm.h`。不要恢复旧 OPT38 的 `model_gemm_config.hpp`。精确 ABI 见 [接口](../docs/interface.md)，历史证据见 [reports](reports/README.md)，源文件 SHA 见 [VERSION.json](../VERSION.json)。

`versions/` 只作来源归档，不是当前源码选择器；旧默认 HLS 树保留在 `docs/provenance/legacy_default_hls/`。历史 README 原文见 `versions/v1_dense_initial/README.md`，其中原根目录命令不能原样代替本分支入口。
