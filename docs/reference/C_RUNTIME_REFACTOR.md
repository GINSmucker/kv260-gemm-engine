# 运行时职责与资源所有权

owner独占CU、MMIO读清和原始BO，维持完整DMA生命周期；B导入固定X/Y，通过同步控制消息提交36个指定PL层。CPU路径按固定路由执行，不是故障fallback。

数值处理在B，硬件控制/Done/AXI排空在C；X TO_DEVICE和Y FROM_DEVICE保留，W每层复制和同步。配置/boot/模型/源码/平台通过完整身份绑定，不按wire版本号自动判断兼容。

提交后完成未知时保留owner/BO，不释放、重放或重新编程。断开恢复由QUERY完成、ABORT封闭及新会话完整图核实；安全停止后证明无使用者和静止再回退。Map/FD与协议字段由[interface](../interface.md)定义。

当前实现通过200连续/3重建/提交后断开的有界检查，client RSS增加256KiB；长期无泄漏不在已证明范围。构建及调用见[board](../../board/README.md)，原编译输入在opt45-source，准确运行文件在opt45-runtime。
