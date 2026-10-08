# PS PL 与软件接口

PL控制基址`0xA0000000`，observer基址`0xA0010000`。HPM0和HP0均128bit，三条32bit master经SmartConnect汇入同一HP0，不是三条独立DDR带宽。

|寄存器|offset|
|---|---|
|CTRL/GIE/IER/ISR|0x00/04/08/0c|
|ERROR|0x10|
|X low/high|0x18/1c|
|W low/high|0x24/28|
|Y low/high|0x30/34|
|M/K/N|0x3c/44/4c|

14寄存器，ap_ctrl_hs：start bit0，Done bit1读清，idle/ready bit2/3，无Continue。唯一owner读清并latch Done；必须确认AXI排空才释放。三指针为完整64bit字节地址；packed C指针不能让MMIO地址除4。

默认V2A为ABI2/wire2，四方法open/run/info/close。权威头：[gemm_backend_v2a.h](../board/model_backend/v2a/gemm_backend_v2a.h)，Python布局：[abi.py](../board/model_backend/v2a/abi.py)。不要重新手抄struct或自动猜ABI。

B动态量化与后处理→C ABI→socket/file owner→XRT BO复制/同步→MMIO一次start→Done/idle/drain→同步回Y→B消费。owner唯一持有BO/CU；三BO驻留，close仅detach。字节长度、SHA、shape、身份、guard和const检查属于接口，不为提速删除。

请求需同时绑定candidate/ready、owner/bridge/B插件/wheel、驱动SHA/srcversion、平台manifest、A2、B模型manifest、metadata UUID/tags、boot、window、owner PID/birth、注册层和request ID。静态锁见[BASELINE_LOCK](../BASELINE_LOCK.json)，注册表见[registered_layers](../contracts/registered_layers.json)。历史ready不是新的boot配置。

请求提交后断连不代表未执行，只能按原ID查询与ACK；不重发REQUEST。成功前需要提交记录、实际start/Done/完整输出检查、完成记录与结果消费。执行状态不确定进入FAULT/UNKNOWN并保留所有者，不能释放BO后让AXI写入新对象。

V2B CF1研究接口为ABI3/wire3，独立[头文件](../board/model_backend/v2b/gemm_backend_v2b.h)和[owner](../board/model_backend/v2b/owner.cpp)。正常2 RPC/层，49层98连接/98热记录同步；未知才QUERY。append记录两次热fsync、完整读回与失败封存保留。V2B运行源码位于独立research模型包，不能直接替换默认V2A。

错误及恢复约束：SUB同步失败零start；COMP失败不向调用者交付Y；RESULT/ACK丢失保留原ID；错身份/跨任务/坏长度/重复key/NUL/别名等拒绝；一个未ACK结果不能被新请求覆盖；STOP只在真实CU/AXI静止后成功。取消是等安全边界退出，不强杀硬件所有者。

10月8日[v3 G0/G1本机核心](G0_G1_LOCAL_20261008.md)复用ABI3/wire3外部语义，内部变为私有不可变Frame/Request/Result；`v3`是源码候选名，不表示已发布ABI4。Scope/generation和完整canonical request凭据仅在单一owner生命周期内流转。B trace/timing采用独立`*_v3_g1`诊断schema，collector仅允许CPU_REFERENCE/0 starts，生产ready和能力关闭。真实Linux socket/记录/恢复/C ABI/owner尚未接线，不能让原CF1 bridge或旧ready给新核心放行。

每次接口或硬件变更都需要自己的identity和Gate，精确未变模块证明可明确复用；不冒用旧A2或签署。公开旧回执引用路径的映射见[SOURCE_MAP](../SOURCE_MAP.json)，私有留出只提供汇总，详细审计仍由C维护者保管。
