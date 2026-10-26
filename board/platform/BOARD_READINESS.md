# OPT50 再次上板门槛

适用范围：修复版 OPT50 完整系统的下一次受控验证。本文是检查表，**不是已完成证明，也不授权加载、START 或恢复操作**。未取得对应运行的证据，方框必须留空。当前不以磁盘空间或工具安装检查代替以下门槛。

## 先分清三类修改与证据

| 项目 | 已知事实 | 不能据此宣称 |
|---|---|---|
| 复位硬件修复 | 原设计 `C_AUX_RESET_HIGH=0`、`aux_reset_in=0` 使共享复位持续有效；修复源码显式设定 ext 低有效、aux 高有效，aux/debug 仍接 0 | 修复版已经生成可部署位流、已经上板，或上次失联的全部原因均已查明 |
| 原版 PS VIP | 原整机仿真 DDR 输出检查 FAIL；无 HLS 的隔离测试也在本机原版模型中复现丢写/一笔滞后。原失败证据保留 | 真实 FPGA 算错，或 AMD 已官方确认通用缺陷 |
| 项目本地 PS VIP 修正 | 仅修正仿真环境中目标仲裁模块的更新时序；不得修改 AMD 安装目录、计算核或 Golden | 原版 VIP 已通过、真实 PS/DDR 已验证，或可跳过系统/实板检查 |

本地修正后的结果必须单独标注模型身份、变更差异、源码 SHA 和实际绑定证据。原模型 FAIL 不得覆盖或改写为 PASS。HLS CSim/Cosim、系统 RTL、实现时序、板端整数结果、模型准确率是不同层级的证据。

## G0：源码与系统 RTL（未通过不得开始新实现）

- [ ] 固定计算核、板卡 preset、F8 观察器、ABI、修复脚本及测试输入清单；保留旧失败目录，不复用旧 PASS。
- [ ] 实际生产 BD/HWH 的复位极性、常量、唯一驱动及连线检查通过：ext 低有效；aux/debug 高有效且接 0；`dcm_locked=1`；共享复位覆盖互联、GEMM 和 F8。
- [ ] 实际生产 BD 完成复位释放/重复复位、CSR、Dense/Sparse 整数输出、保护区、AXI 错误及 F8 排空测试；不得删比较、放宽 Golden、force 信号或靠任意延时制造 PASS。
- [ ] 如使用本地 VIP 修正：独立复现、修正对照、实际模块绑定检查及完整系统仿真全部通过，日志明确标注“本地修正模型”，保留原模型 FAIL。

## G1：完整实现与成套产物（未通过不得准备加载包）

- [ ] 同一次新构建通过完整实现、时序、DRC 和系统验证；审查实际报告，而非只看脚本退出码或“导出完成”。
- [ ] `.bit`、Linux 加载用 `.bit.bin`、`.hwh`、`.xsa`、系统验证回执来自同一构建；核对载荷/字节序、SHA、器件、时钟、复位、DDR preset、地址及本次 F8 tags。
- [ ] 按新系统生成并审查 DTBO、`shell.json`、metadata-only xclbin、Runtime 和测试任务；不得沿用旧 OPT50/H576 的位流、UUID、tags 或旧计划。
- [ ] xclbin 的六种 section、UUID、CU 名称/基址、内存及连接关系配套，**无 BITSTREAM section**；八参数与实际驱动头/HWH 一致：`X/W_data/W_meta/Y/M/K/N/mode` 偏移分别为 `0x18/0x24/0x30/0x3c/0x48/0x50/0x58/0x60`。

固定连接契约：一个 PL0 时钟域；HPM0/HP0 均 128 bit；GEMM/F8 分别位于 `0xA0000000/0xA0010000`、各 64 KiB；三路数据 master 共用 HP0。DDR 映射为低端 `[0,0x80000000)`、高端 `[0x800000000,0x880000000)`；这不意味着 Runtime 可以把全部窗口当作空闲内存。只使用 XRT 分配的、非零、已对齐、完整落入允许窗口且互不重叠的 BO。

## G2：Linux 平台与时钟契约（未解决不得加载）

- [ ] 确认新 PS preset 与现有启动平台兼容；Vivado/XSA 中的 PS 设置**不会自动替换已运行 Linux 的 PS 配置**。保留 KV260 DDR/MIO 配置，不运行整份 `psu_init`，不刷 QSPI、BOOT.BIN、FSBL 或基础设备树来“同步配置”。若发现必须改变启动平台，停止并另行设计/授权。
- [ ] 分别记录时钟请求值、PCW 计算值与预期 Linux 接受范围：本项目请求 `125000000 Hz`，已有 PCW 计算值为 `124998749 Hz`；两者不是当前板上频率实测，也不证明时钟已使能。
- [ ] 在加载前确定频率验证方法及有依据的白名单/容差；加载后必须核对实际 `pl0_ref` 频率和 enable/prepare 状态。旧 Runtime/恢复窗口仅接受 `125000000/124999999 Hz`，明确拒绝 `124998749 Hz`，**不得原样重试或为过关静默放宽**。任何契约修改需说明原因并测试；不得猜测写 PLL/分频寄存器。
- [ ] 新 DTBO 的 base-tree symbols、clock/reset 引用、firmware 名称和 zocl 节点经过审查；当前 boot 的 PS-PL 位宽、复位/隔离及 Linux 驱动需求有明确核对方案。发现不符即停止，不直接写 MMIO“修复”。

## G3：新部署窗口与可恢复取证（未通过不得加载）

- [ ] 重新读取当前 `boot_id`、内核/驱动身份、starter、overlay、设备占用及日志；旧 boot 的 plan/status 失效。新计划绑定本次 boot、产物 SHA、tags、任务和有效期，不允许旧窗口自启动或自动重试。
- [ ] 保持板卡独占锁与单一 owner，确认没有其他 XRT、`/dev/mem`、FPGA/DRM 使用者；恢复条件、临时文件归属和 starter/100 MHz 恢复材料已审查。
- [ ] 部署源、上传副本和 root-owned 执行副本逐一核对大小/SHA；不只校验传输归档。固件、KO、Runtime、xclbin、配置、任务及 Golden 在执行前再次核对，执行期间不得换源文件。
- [ ] 危险动作前将计划、执行副本、动作意图与阶段状态持久落盘：文件写入后 `flush/fsync`，原子替换后同步目录；固件复制和日志也有对应持久化策略。仅 close、重读 hash、rename 或“文件存在”不能证明掉电后证据保留。先验证记录机制，再启动控制命令；完成回执不得先于动作完成。
- [ ] PC 端持续保存串口启动/内核日志，独立于 SSH；危险阶段前即开始。控制命令的参数、PID、返回值及 stdout/stderr 与阶段记录持久保存；失联不能靠上次保存的 status 推断准确停止位置或 START 次数。
- [ ] 用户明确授权本次临时加载与故障处置边界；知道 watchdog/用户态超时**不能保证中断卡死的 AXI MMIO**。状态不明时不杀 owner、不释放 BO、不重试 START、不卸载；是否复位/断电按取证后重新授权的事故方案决定。

旧 `window_v1` 计划和加载命令不可直接复用：其固件/执行副本与 JSON 写入缺少上述 `fsync` 保证，首次事故后出现了零字节记录；这限制了故障归因，不证明文件在执行当时已是零字节。

## G4 → G6：授权加载后的顺序（禁止跨阶段）

| 阶段 | 必须得到的证据 | 本阶段禁止 |
|---|---|---|
| G4 临时加载，不 START | 新 firmware/overlay/驱动身份与实际设备状态正确；时钟 rate、enable/prepare、复位与 PS-PL 位宽符合已审查契约；无新增严重内核错误 | 未确认即读任意 PL 地址、换驱动试错、START |
| G5 只做 CSR | 在已知时钟/复位前提下，核对本次 F8 tags、ID/version、GEMM idle、AXI 无错误/已排空；再做已审查的参数写读。每个动作预先记录 | 用错误/未映射地址探测；无响应时自动重试；启动全模型 |
| G6 最小 PL | 使用已固定的小尺寸任务，Dense/Sparse 各一次；保留 TO_DEVICE，同一组 MAC 电路按 mode 选择；done、idle、F8 响应排空后 FROM_DEVICE，逐值 INT32/保护区/输入不变均通过 | 仅凭 done 或 beat 打印判定正确；删除 cache sync；失败后释放未证明安全的 BO |

`operating`、render 节点出现、XRT 注册成功、CSR 可读或单层 PASS 都不是全模型验收。完成 G6 并安全退出、核对恢复 starter/100 MHz 后，才另行决定更多形状、12 层回归及全模型测试。精度和性能验收另列范围，不能从最小任务外推。

## 本地仿真库的绑定审计

- 修正模块只编入独立的项目本地库；不改 AMD 安装文件或整套预编译库映射。
- UG900 的 Verilog 搜索顺序是 `uselib` → xelab 命令行各 `-L` → 父单元库 → work，首次匹配即停止。把源码加到 work 并不足以证明已覆盖原模型。
- 控制仿真 cwd 与实际 `xsim.ini`；映射文件定义逻辑库的位置，不决定重复模块优先级。归档实际 compile/elaborate 命令、有效映射 SHA、原版/本地模块 SHA，并检查实际绑定及目标模块的身份标记。
- 修正库不得进入综合/实现源集；部署硬件保持生产 BD/HLS RTL，而不是把仿真模型补丁带入位流。

## 官方依据（按本设计相关章节使用）

- [UG1089：Vivado Board Flow](https://docs.amd.com/r/en-US/ug1089-kv260-starter-kit/Vivado-Board-Flow)、[启动固件与应用隔离](https://docs.amd.com/r/en-US/ug1089-kv260-starter-kit/Boot-Devices-and-Firmware-Overview)：板卡 preset 与应用级更新的边界。
- [PG201：Output Generation](https://docs.amd.com/r/en-US/pg201-zynq-ultrascale-plus-processing-system/Output-Generation)：`psu_init` 涉及 DDR、PLL、时钟与 MIO；[Output Clocks](https://docs.amd.com/r/en-US/pg201-zynq-ultrascale-plus-processing-system/Output-Clocks)、[Requested/Actual 字段](https://docs.amd.com/r/en-US/pg201-zynq-ultrascale-plus-processing-system/Column-Descriptions-for-Output-Clocks)、[Fabric Reset Enable](https://docs.amd.com/r/en-US/pg201-zynq-ultrascale-plus-processing-system/Fabric-Reset-Enable)：时钟计算值与异步 fabric reset。
- [PG164，第 7–8、13–14 页](https://docs.amd.com/api/khub/documents/5VcEWg~hYG0gT~7WNDt64g/content)：ext/aux 极性、输入时钟、复位检测和释放。
- [PG247：Resets](https://docs.amd.com/r/en-US/pg247-smartconnect/Resets)、[Clocking](https://docs.amd.com/r/en-US/pg247-smartconnect/Clocking)、[Address Decode](https://docs.amd.com/r/en-US/pg247-smartconnect/Address-Decode)：共享复位、时钟域与地址译码。DECERR 适用于未映射请求，不能当作已映射但不响应从机的超时保护。
- [UG1085，第 35 章](https://docs.amd.com/api/khub/documents/xzMsp_c5sG9J6A3u7NkJYQ/content)：PS-PL AXI 与 HP 非一致性路径；位宽字段交叉参考 [UG1087 RDCTRL](https://docs.amd.com/r/en-US/ug1087-zynq-ultrascale-registers/RDCTRL-AFIFM-Register)、[WRCTRL](https://docs.amd.com/r/en-US/ug1087-zynq-ultrascale-registers/WRCTRL-AFIFM-Register)。
- [XRT 2024.2 User Managed Kernel](https://xilinx.github.io/XRT/2024.2/html/xrt_native_apis.html#user-managed-kernel)、[BO/Custom IP API](https://xilinx.github.io/XRT/2024.2/html/xrt_native.main.html)：独占 `xrt::ip`、寄存器控制、内存组及同步。当前保留无 IRQ 的手动轮询，不混用 interrupt 等待流程。
- [DS941，第 5–6、9–10 页](https://docs.amd.com/api/khub/documents/2yDNxBJf36TpTMmoA0X4sA/content)：PS VIP 复位/API 合同。`wait_mem_update` 是写事件监视，不是每次 PL 写后必需的内存屏障；`set_slave_profile` 只选响应延迟，不是排序缺陷修复。
- [UG900：Verilog Search Order](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Verilog-Search-Order)、[Instantiation Unit](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Verilog-Instantiation-Unit)、[xsim.ini](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Library-Mapping-File-xsim.ini)：项目本地仿真模型的搜索、映射和绑定机制。

以上资料指导审查，不替代本次产物、当前 Linux boot 与实测回执；不把其他板卡的参考流程直接作为 KV260 当前环境的执行命令。
