# A/B/C交付职责

|方向|职责|集成输入|
|---|---|---|
|A|完整层GEMM、访存、公开TB/HLS/RTL|source/contract/IP清单、数值/AXI/时序/资源与未运行范围|
|B|冻结模型、数值等价、CPU/PL路由与线程|graph/插件/量化身份、逐位结果、固定输入和测量边界|
|C|平台、owner/BO、同步控制与恢复|配对metadata/平台/驱动/二进制、公开数值/完成/回退证据|

共享契约见[interface](../interface.md)，分支与评审见[workflow](../workflow.md)。共享板卡串行安排，独立签署由具备资格的维护者完成。文档说明当前状态，不保存Agent临时计划；原始证据保留自己的身份与范围。
