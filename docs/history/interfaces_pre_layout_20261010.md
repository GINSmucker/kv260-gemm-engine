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

10月8日F05为独立ABI4/wire4、MODEL_TRANSACTION_V1。128B固定小端header/完整body SHA，唯一owner writer、4驻留连接/1执行线程、49层逐序号及一个未ACK Y。整图BEGIN在首次start前真实持久并读回，49层ACK完成和真实safe后COMMIT写入49完整native、7448B trace与4000B logits，成功晚于提交完成。正常每图2热fsync，层间结果易失；owner失效后不重放旧epoch。旧ABI2/3、ready或配置不能授权此接口，详见[F05合同与恢复](F05_HANDOFF.md)。

当前开发v5为独立32B little-endian wire5，HELLO一次、BEGIN+49RUN+END，一计算连接和一管理连接。无逐层SHA/proc身份读取/ACK/持久账本/JSON；下一完整RUN隐式消费旧Y，只能QUERY当前run的最近Y，重复RUN拒绝，传输失败关闭图且不重放。正常STOP延后至真实DMA完成，未知完成保留BO。它不承诺F05原日志/COMMIT重开保证。生产和诊断二进制分开，源码b_api.h及protocol.h随v5-source取得；接口不能混装，详见[V5_HANDOFF](V5_AB_HANDOFF.md)。

错误及恢复约束：SUB同步失败零start；COMP失败不向调用者交付Y；RESULT/ACK丢失保留原ID；错身份/跨任务/坏长度/重复key/NUL/别名等拒绝；一个未ACK结果不能被新请求覆盖；STOP只在真实CU/AXI静止后成功。取消是等安全边界退出，不强杀硬件所有者。

每次接口或硬件变更都需要自己的identity和Gate，精确未变模块证明可明确复用；不冒用旧A2或签署。公开旧回执引用路径的映射见[SOURCE_MAP](../SOURCE_MAP.json)，私有留出只提供汇总，详细审计仍由C维护者保管。
