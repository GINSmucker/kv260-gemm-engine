# KV260 F8 配对平台

当前版本对应 2026-09-28 实板开发运行 `262414267686403`：9 次 / 576 输出、客户端断开恢复及 starter 回退 PASS。
计算接口固定 `X[8,8] × W[8,8] = Y[8,8]`，signed INT8 × INT8 → INT32，row-major。发布 GEMM RTL 保持原样。

| 文件 | 作用 |
|---|---|
| [create_system.tcl](create_system.tcl) | PS HPM0 128 bit、控制 SmartConnect、三路 32-bit GEMM master、HP0 128 bit、100 MHz 时钟与复位、连接/综合/实现门槛 |
| [f8_status.v](f8_status.v) | AXI 错误、outstanding、事务/数据拍计数、quiet 与构建身份 |
| [tb_f8_status.sv](tb_f8_status.sv) | 114 项 RTL 检查 |
| [kv260_f8.dts](kv260_f8.dts) | 临时 PL_FLAT overlay、PL0 时钟消费者与 ZOCL 节点 |
| [make_metadata.py](make_metadata.py) | metadata-only xclbin 输入；两个 user-managed CU |
| [capability.json](capability.json) | 固定形状、布局、寄存器、地址、所有权和恢复约束 |

GEMM `ap_ctrl_chain` 由 host 独占驱动，不能混用 `xrt::run`。元数据中的 AP_CTRL_NONE 表示 user-managed 调度方式。
capability 内验证字段是封装前状态；实际板测结果保存在独立的不可回写证据中。

入口：[构建与复现](../../docs/F8_REPRODUCE.md)、[运行接口](../../docs/F8_RUNTIME.md)、[修改及实板报告](../../docs/F8_DELIVERY_20260928.md)、[原始结果](../../results/c_integration/f8_runtime_20260928_RESULT.md)。
