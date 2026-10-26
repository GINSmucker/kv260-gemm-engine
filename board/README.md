# C · v1 板端集成边界

本分支只恢复初级 Dense HLS 核，**没有本版配对的完整 KV260 平台、XRT 调用程序或位流**。板端正确性、性能和完整模型推理均为 **NOT RUN**。

- `runtime/`：说明未提供的匹配运行时；不再混放旧 OPT38 的占位 C++。
- `platform/`：说明尚缺的硬件平台；不把旧平台 Tcl 当作可复用 v1 系统。
- `deployment/`：不提供加载或计算命令。
- `tests/`：说明尚缺板测，而不是伪造测试 PASS。
- `versions/`：保留三阶段归档，不能用于选择本分支活跃 runtime。

旧默认平台和占位 runtime 原样保留在 `docs/provenance/legacy_default_board/`。集成 v1 前需从实际导出 IP 核对 `gemm_top(X,W,Y,M,N,K)` 的寄存器和接口，编写新适配并验证，不可套用三指针 OPT38 或四指针 OPT50 的控制代码。
