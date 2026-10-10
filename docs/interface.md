# A/C唯一公共接口：OPT38 + v5

接口版本 KV260_MODEL_GEMM_OPT38_V5_LAYOUT_1。路径迁移不改寄存器、量化、wire或恢复承诺；修改接口需同步代码/契约、重新编译和资格验证。

## 数学与白名单

Y[M,N]=X[M,K]×W[K,N]，compact row-major；X/W为signed INT8，Y/完整K累加INT32。B范围[-127,127]、zp0/RNE，sx/sw/bias由PS处理，PL不做浮点scale。

|family|M|K|N|
|---|---:|---:|---:|
|head|1|192|1000|
|proj|197|192|192|
|qkv|197|192|576|
|fc1|197|192|768|
|fc2|197|768|192|

唯一配置源[hls/src/model_gemm_config.hpp](../hls/src/model_gemm_config.hpp)，五shape之外返回INVALID_SHAPE=1，合法返回OK=0。HLS顶层model_gemm_top(X,W,Y,M,K,N)，packed32输入每word四个little-endian INT8；MMIO永远是64bit字节地址，不除4。

## 14寄存器与平台

|寄存器|offset|
|---|---|
|CTRL/GIE/IER/ISR|0x00/04/08/0c|
|ERROR|0x10|
|X low/high|0x18/1c|
|W low/high|0x24/28|
|Y low/high|0x30/34|
|M/K/N|0x3c/44/4c|

ap_ctrl_hs：start0、Done1读清、idle2/ready3，无Continue。owner独占读清并latch Done，确认observer AXI排空后才能释放；每start完成完整M/N/K层，不从F8 chain沿用状态机。

已发布平台控制0xA0000000/observer0xA0010000；HPM0/HP0均128bit，三路32bit master汇入同一HP0。源/配对参数在[开发锁](locks/DEVELOPMENT_LOCK.json)、[机器契约](../hls/contracts/register_map.json)。新IP导出/平台须再次核对驱动头和地址分配，旧A2不转移。

## 真实运行时与B适配

[C内核接口](../board/runtime/model_gemm_runtime.hpp)与[wire5](../board/runtime/protocol.h)来自已发布v5-source原字节；owner.cpp唯一持有CU和BO。B [b_api.h](../model/v5/b_api.h)、client和插件使用同一protocol.h。源码绑定OPT38 UUID/tags，正式身份与开发身份分开，不能混用旧35a metadata。

BO容量151552/192512/606208B；同步有效范围按64B取整，输入TO/输出FROM；地址只初始化，层形状逐请求写入。实际START之后发生异常或完成未知时保留BO/owner，不能强杀、释放、重放或重编程。

wire5为固定32B little-endian header：magic0x0035564b、version5、op16、run64、sequence/layer/bytes/status各32。HELLO一次、BEGIN+49RUN+END=51RPC/图，一计算连接与一管理连接；49层按注册序列，最后head。下一完整RUN消费上一Y，仅可QUERY当前run最近Y；重复RUN拒绝。无旧ACK、逐层SHA、持久账本或COMMIT重开承诺，STOP延后到真实DMA完成；诊断与生产分别编译。

## 版本与验证边界

正式冻结V2A为ABI2/wire2，历史V2B为3、F05为4，均保存在legacy或既有Release；它们有不同持久/恢复合同，不能混接或按v5名义沿用。[历史接口快照](history/interfaces_pre_layout_20261010.md)只用于审计，当前共同依据是本文。

新构建用新owner/B插件SHA、驱动/平台/metadata/boot/window及公开Gate；旧二进制或签署只是其原身份范围。目录软件检查不证明ARM/PL/HLS/RTL、质量或性能；实际状态见[verification](verification.md)与[progress](progress.md)。旧文档路径通过[MIGRATION](MIGRATION.md)解析，私有留出只由C维护者管理。
