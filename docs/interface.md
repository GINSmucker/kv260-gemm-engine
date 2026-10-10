# 公共接口：矩阵、运行配置与生命周期

接口说明版本KV260_GEMM_SHARED_MAP_V1。确认规范是完整K归约、紧凑矩阵、逐位数值一致与明确设备所有权；使用方必须选择匹配的运行配置和完整包身份。

## 矩阵与shape

Y[M,N]=X[M,K]×W[K,N]。X/W为signed INT8，Y及完整K累加为INT32；紧凑row-major，不修改PS矩阵布局。B采用RNE、[-127,127]/zp0、逐行sx及逐输出通道sw；scale/bias在PS，浮点顺序固定。

|family|M|K|N|研究每图路由|
|---|---:|---:|---:|---|
|head|1|192|1000|1 CPU|
|proj|197|192|192|12 CPU|
|qkv|197|192|576|12 PL|
|fc1|197|192|768|12 PL|
|fc2|197|768|192|12 PL|

硬件白名单支持以上五shape，一次start完成整个Linear。非法shape返回INVALID_SHAPE=1，合法返回OK=0；不从CPU补K或逐tile start。

## 配置绑定

|配置|AXI数据宽度X/W/Y|时钟|传输与路由|
|---|---|---|---|
|默认OPT38|32/32/32bit|100MHz|载荷型wire5，49层PL|
|研究OPT45|128/128/128bit|125MHz|共享Map扩展、固定36层PL|

相同wire版本号不保证实现兼容。默认源码为hls/src、board/runtime与model/v5；研究源码由opt45-source资产提供，实际运行身份由opt45-runtime的33成员manifest固定。不得混用两套owner、插件、图或metadata。

OPT45 FPGA用128bit整字读取紧凑INT8，X/W每拍16B、Y每拍4个INT32；X行bank32×64×128bit，Y累加器32×16×48×INT32。MMIO始终写64bit字节地址，不除4或16。数据master经3入1出的SmartConnect进入128bit HP0；HPM0/HP0位宽一致。

## 控制寄存器

|寄存器|offset|
|---|---|
|CTRL/GIE/IER/ISR|0x00/04/08/0c|
|ERROR|0x10|
|X low/high|0x18/1c|
|W low/high|0x24/28|
|Y low/high|0x30/34|
|M/K/N|0x3c/44/4c|

ap_ctrl_hs：start bit0、Done bit1读清、idle bit2/ready bit3；无Continue。owner独占读清并latch Done，关闭auto_restart，参数运行中稳定。控制base0xA0000000、observer0xA0010000；新导出须核对驱动头/component/RTL与实际地址分配。

研究配对平台model_system_20261010_184640_939：UUID8aec6c74-0818-f958-4716-0a6c9d3b1cc1、observer tags2391141641/3180551808。源码的EXPECTED_UUID和tag检查是身份依据，错误提示中的型号文字不构成平台选择规则。

## 共享内存与wire

头固定32B little-endian：magic0x0035564b、version5、op16、run64，sequence/layer/bytes/status各32。研究扩展op Map=9；SCM_RIGHTS传递两个X/Y FD及SharedInfo，导入XRT对象先于接收FD释放。owner原始BO覆盖整个DMA生命周期。

BO容量X151552/W192512/Y606208B。B直接量化写X并TO_DEVICE，RUN只发控制且body长度0；owner写参数/start、观察Done及AXI排空；B对Y FROM_DEVICE并直接反量化。CPU整数输出使用151296B普通工作区，必要cache sync不可删除。W逐层host→BO复制和同步，PL每start读取本层完整W。

49个Linear依次在B处理，固定路由header约束36个PL层的索引、顺序和sequence。默认配置的49载荷请求不能直接发送到共享owner。HELLO绑定权重摘要，BEGIN/END定义整图；同步调用复用单组BO，无队列、双缓冲或失败后CPU fallback。

## 完成、断开与资源

START后完成未知或硬件仍活动时保留BO/owner，不强杀、不释放、不重放RUN或重新编程。已提交客户端断开需要QUERY确认完成、ABORT封闭旧会话，再用新会话验证；STOP仅在真实静止后退出。排空、设备无使用者和starter回退由实际探针确认，退出码不足以证明。

研究证据覆盖200次连续调用、三次重建及提交后断开恢复；长期泄漏、永久AXI故障或任意切换不在已证明范围。运行许可来自维护者当前安排，仓库和资产不转移其他会话授权。正式资格见[验证](verification.md)，数学配置、源码及资产身份见[SOURCE_MAP](SOURCE_MAP.json)。
