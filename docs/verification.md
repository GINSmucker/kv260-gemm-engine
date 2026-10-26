# v1 验证范围

| 对象 | 状态 | 依据及限定 |
|---|---|---|
| 活跃 top/header/TB/config 文件身份 | PASS | SHA256 与历史提交 `cd0870dd642378719fd844f8cdbe4cacaae36609` 的冻结快照一致；见 VERSION.json |
| 历史 C Simulation | PASS | 历史 v0.2 摘要记载 7/7 小型合成用例；本次未重新跑 Vitis |
| 历史 C Synthesis | PASS | 历史摘要记录 II=1 和资源估计；没有原始报告供当前完整复核 |
| 历史 Verilog RTL Cosim | PASS | 历史摘要记载 7/7 事务；不是板端测量 |
| 新活跃目录原生 C++ | PASS | 本机clang运行该版原testbench，绑定四文件身份；见results/active_native_20261026.json，不是HLS/RTL |
| 新 HLS/RTL/IP 与导出 ABI | NOT RUN | 没有新工具运行；不能从历史摘要假定寄存器/AXI 位宽 |
| 完整 KV260 平台、ARM runtime 和板端测试 | NOT RUN | 尚未提供本版配对实现 |
| 完整模型精度、性能、长期稳定性 | NOT RUN | 不借用 H576/OPT50 数字 |

历史原文在归档分支 `archive/dense-sparse-v1-v3-20261026` 的 v1 快照中。
本分支当前活跃路径已单独做原生功能检查，结果不继承旧路径或板端资格。

普通 C++ 使用 `ap_int.h`、忽略 HLS pragma 的结果不能标作 C/RTL 联合仿真。真实板测和恢复许可由维护者针对当次硬件/启动身份另行安排，本分支不自动执行加载或计算。
