# A · OPT50 HLS 计算核

活动代码为 [src/model_gemm_top.cpp](src/model_gemm_top.cpp)、[src/model_gemm_core.hpp](src/model_gemm_core.hpp) 和 [include/model_gemm_config.hpp](include/model_gemm_config.hpp)，来自归档 commit `00efbf17ec540610f3b1a3f33d05fdcb1ed508ae` 的 OPT50 快照。top/core/config/testbench 保持原字节，身份见 [VERSION.json](../VERSION.json)。

单个 `model_gemm_top(X,W_data,W_meta,Y,M,K,N,mode)` 使用共享 32×16 MAC 阵列，`mode=0/1` 选择 Dense/2:4 Sparse。输出侧 stream-of-blocks/dataflow 保留；没有新增输入侧 X/W 双缓冲。尺寸限制为 M 1..197、K/N 为16倍数且不超过576、K×N≤110592，详见 [共同 ABI](../docs/interface.md) 和 [数据格式](docs/sparse24_axi_format_v0.1.md)。

[hls_config.cfg](hls_config.cfg) 是活动配置，逐字节采用已提供的 portable 配置，保留器件 `xck26-sfvc784-2LV-c`、8 ns 时钟与同一综合源码，将 CSim/Cosim 用例集设为 `full`/`rtl`。工作目录设为本目录；这份新位置配置在本次整理中未运行 HLS。`rtl` 在 native testbench 中只是用例集名称，不能冒充 RTL 仿真。

本机 C++ 检查会编译程序并写入全新输出目录，不生成 IP。历史 `tools/run_native_tests.ps1` 要求外部 AXI golden 包，并会创建 `.build`；没有该包时应拒绝，不生成占位权重。实际导出 C++/RTL 的来源对齐记录见 [hls_source_alignment.json](../board/evidence/hls_source_alignment.json)，不能将文件对齐等同于本次重新综合。

原默认核已移到 [pre_v3_default/hls](../docs/provenance/pre_v3_default/hls/README.md)，历史 OPT50 原配置保留在 `docs/provenance/opt50_original_hls_config.cfg`（从仓库根目录定位）。平台、PS 软件与结果从 [C 入口](../board/README.md) 和 [结果入口](../results/README.md) 进入。
