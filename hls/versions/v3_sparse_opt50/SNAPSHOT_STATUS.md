# v3 / OPT50 HLS 源码快照

本目录按字节保存 `D:/GEMM/model_gemm_opt50` 的 top/core/config、testbench、格式和设计说明。
不是从早期同名副本或失效的 resetfix 平台恢复。SHA256 见仓库的 `docs/versions/source_manifest.json`。
原 README/设计文档中的“尚未上板”是创建时的历史状态，不覆盖后来已取得的证据。

## 当前能力

- 唯一顶层 `model_gemm_top(X,W_data,W_meta,Y,M,K,N,mode)`。
- 一个共享 512-MAC 阵列；mode=0 Dense，mode=1 K 方向 2:4 Sparse；单个 IP。
- 四个指针按128bit字组织，W_data/W_meta 共用 gmem1；INT8 输入/权重，INT32 输出。
- `1<=M<=197`，K/N 为16的倍数且不超过576，K*N<=110592；M尾块支持。
- 输出 stream-of-blocks/dataflow；同一 MAC 数据选择路径，不是两个 Dense/Sparse 引擎。
- 没有新增 X/W 输入侧双缓冲；不接受原768维度模型、proj/head原样进入此核。

本机源码的 stream-of-blocks 单获取位置修复已包含在 core 中。HLS 配置与实际部署身份
的来源复核见板端快照。此前的 C/RTL 与系统/实板结果属于固定来源的历史证据；
本次归档重新运行 Vitis HLS/RTL/Vivado/板端状态均为 `NOT RUN`。

已提供单次同权重完整模型数值/耗时回执，但不能从它宣称所有合法输入、纯PL周期、
稳定性能、长期安全或真实图片精度均已验收。参见 `docs/versions/README.md`。

## 配置使用

`hls_config.cfg` 保留原字节，真实载荷参数引用原本机路径；它是来源快照。
独立合成数据可使用 `hls_config_portable.cfg`，将工作目录设置为本目录。
后者只把 CSim/Cosim 的测试集合改为 full/rtl，不改变综合源码、器件或目标时钟；
它在本次归档中未运行 HLS，不能继承原配置的测试覆盖。
真实权重 `golden` 测试需要另行取得 pinned AXI cases，Git 内不包含其二进制载荷。
