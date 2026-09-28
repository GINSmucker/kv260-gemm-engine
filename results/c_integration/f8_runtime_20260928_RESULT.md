# F8 运行链补全结果（2026-09-28，Asia/Shanghai）

当前结论：**F8 最小 XRT/DDR/PL 运行链已在 KV260 打通：9 次计算、576 个输出比较 PASS；客户端断开恢复 PASS；starter 回退 PASS。**
用户已明确授权临时加载、检查并回退。独立封存验收仍 NOT RUN，本报告为开发阶段结果。

## 成功实板运行

最终构建为 `f8_system_20260928_004840_982`，最终配对包为 `f8_bundle_20260928_012117_473`；板端运行 ID **262414267686403**，加载脚本 exit=0。日志采用板端 monotonic 时间；板卡墙上时钟仍为 2025 年，报告日期使用本机 2026-09-28 / Asia/Shanghai。

- [机器可读汇总](f8_bundle_20260928_012117_473/board_validation_summary.json)
- [原始计算/恢复日志](f8_bundle_20260928_012117_473/board_smoke_262414267686403.jsonl)、[加载/回退日志](f8_bundle_20260928_012117_473/board_load_262414267686403.jsonl)、[原生服务日志](f8_bundle_20260928_012117_473/native_owner_262414267686403.log)
- [回退后只读复核](f8_bundle_20260928_012117_473/final_board_snapshot.json)、[管理服务/权限复核](f8_bundle_20260928_012117_473/final_manager_check.json)

真实调用链为四方法后端 → chain 状态机 → Unix socket → ARM XRT 原生所有者 → DDR BO/同步 → PL GEMM → BO 回读 → 独立整数 checker。原生服务 uid=1000、PID=224571，临时附加 render 组；没有 CPU fallback。

零值、单位矩阵、INT8 极值、非对称符号矩阵各两次，再加恢复后一次，**9/9 任务、576/576 输出一致**。各任务的 X/W 输入不可改、三个 BO 尾部 guard、输出 poison 替换、连续任务与旧结果拷贝检查通过。

| BO | 本次实际设备地址 | 分配 / 有效字节 |
|---|---|---|
| X | `0x846f0c000` | 4096 / 64 |
| W | `0x845735000` | 4096 / 64 |
| Y | `0x846f11000` | 4096 / 256 |

三个 BO 均在高地址 DDR；本轮实际覆盖了高 32 bit 非零的地址配置和 PL 访存。每次完整计算的 AXI 观测相同：X AR/R=64/128，W AR/R=512/512，Y AW/W/B=4/64/4；错误锁存与未完成事务均为 0，最终 CTRL=4、quiet=1。GEMM 三个数据 master 为 32 bit，观测点数据量为 `(128+512+64)×4=2816 B`；它不等于 HP0 128-bit 总线拍数或物理 DDR 控制器流量。

另一次任务提交后断开客户端 socket，原生 PID 保持 224571；RECOVER 确认完成/Continue、idle 与 drain，再清除 submitted/quarantine。之后的第 9 次计算仍 PASS。这覆盖“提交后断开、未消费完成结果”的恢复，不证明断开恰好发生在 DMA 活动期间，也不代表永久 AXI 卡死或强杀进程已验证。

原生服务记录 `F8_XRT_SERVICE_SAFE_STOP` 后 exit=0，设备使用者为空，再卸载 F8、卸载 ZOCL、恢复 starter，before/after 快照逐项一致。最终复核无原生 PID、socket、render 节点或临时应用目录；FPGA operating、DFX manager active、XRT 0 devices，31 个原包文件运行后哈希不变。默认 ubuntu 仍无 render 组；其 DFX 管理 socket 查询返回 EACCES（exit=255），授权的 sudo 加载/回退均成功。

记录的 `board_call_us` 为 **17,012.470～17,858.899 μs**，包括 Python/RPC/同步；**kernel latency、吞吐/加速比、性能 benchmark 均 NOT RUN**。

## 首次实板执行与修正

`f8_bundle_20260928_003336_214` / `board_load_260053649355199` 实际执行：F8 overlay、ZOCL renderD128 和 PL0=99,999,999 Hz / enable_count=1 均建立；服务报 `F8 status ABI mismatch`，尚未分配 BO 或提交 GEMM。随后模块卸载、starter 恢复、临时应用移除、状态对比和 XRT 0 devices 均 PASS。[原始加载/回退 JSONL](f8_bundle_20260928_003336_214/board_load_260053649355199.jsonl)、[服务错误](f8_bundle_20260928_003336_214/native_owner.log)。

只读核验发现 `FPD_SLCR.afi_fs=0xa00`，HPM0=128 bit；第一版平台设为 32 bit。HP0 读写均为 128 bit，匹配设计。修正 F8 平台 HPM0 为 128 bit，新增加载前的只读位宽门槛与启动寄存器记录，不写 PS 配置、启动固件或 QSPI。新构建为 `f8_system_20260928_004840_982`。[实际位宽](f8_runtime_research/ps_widths_20260928.json)、[AMD 字段定义](https://docs.amd.com/r/en-US/ug1087-zynq-ultrascale-registers/afi_fs-FPD_SLCR-Register)。

内核另有 ZOCL IRQ 缺失（本链显式轮询）、overlay property 内存泄漏提示及 `zyxclmm_drm` node refcount 告警；[首次日志](f8_bundle_20260928_003336_214/kernel_after_attempt.txt)与[成功运行后的日志](f8_bundle_20260928_012117_473/kernel_after_validation.txt)均保留。功能性回退 PASS 不代表内核完全无残留分配；本轮不扩展修改内核/驱动。

## 交付与验证

| 项目 | 本轮实际结果 | 原始证据 |
|---|---|---|
| 固定 F8、chain 主机契约与 RPC 边界 | 20 项软件开发测试 PASS | [测试输出](f8_runtime_research/host_runtime_tests_final.txt) |
| 原 C1 软件链 | 7 项回归 PASS | [回归输出](f8_runtime_research/c1_regression.txt) |
| ARM 原生 XRT 服务 | g++-13 编译/链接、严格 metadata-only 文件检查 PASS | [最终源码/程序哈希与检查](f8_runtime_research/native_compile_startup_diagnostic_hashes.txt) |
| AXI 观测模块 | 新构建中 114 项 RTL 检查 PASS | [XSim 输出](f8_system_20260928_004840_982/status_sim/status_sim.sim/sim_1/behav/xsim/simulate.log) |
| K26 PS/AXI/DDR/F8 平台 | 综合、实现、bit/bin/hwh/xsa PASS，HPM0/HP0 均 128 bit | [构建标记](f8_system_20260928_004840_982/build_pass.txt) |
| 布线后时序 | WNS **3.373 ns**、WHS **0.010 ns**；所有约束满足；未约束内部端点 0 | [时序报告](f8_system_20260928_004840_982/timing_summary.rpt) |
| DRC / 布线 | **0 Error、5 Warning**；15,572 条可布线网全部完成，路由错误 0 | [DRC](f8_system_20260928_004840_982/drc.rpt)、[布线](f8_system_20260928_004840_982/route_status.rpt) |
| XSA 内嵌顶层 bit/HWH | 与外置产物逐字节一致，PS 接口位宽核验 PASS | [配对检查](f8_system_20260928_004840_982/xsa_pairing.json) |
| Linux BIN | Bootgen 2020.2.2 转换 PASS；完整 32-bit word 反序核验 PASS | [封装清单](f8_bundle_20260928_012117_473/bundle_manifest.json)、[日志](f8_bundle_20260928_012117_473/bootgen.log) |
| 板卡只读状态与 DTBO | HP0 128 bit、PL0=99,999,999 Hz；DTBO 对 base 副本离线合并 PASS | [预检](f8_runtime_research/privileged_preflight.json)、[离线检查](f8_runtime_research/dtbo_offline_validation.txt) |
| 板端部署 | 31 文件 SHA-256 运行前后全部匹配，metadata 解析 PASS | [部署核验](f8_runtime_research/deploy_verify_retry.txt)及上方最终快照 |
| 真实 BO/PL | **PASS**，9 次 / 576 输出、实际高地址 DDR | 上方成功运行原始证据 |
| 客户端断开恢复 / 最终回退 | **PASS / PASS**，同一 native PID 恢复后继续计算 | 同上 |
| 首次 F8 加载 / 回退 | **PASS / PASS**，计算服务启动 FAIL 单列 | 同上 |

DRC 的 4 条 DPOP-4 是原发布 IP 的 DSP MREG 流水建议；1 条 RTSTAT-10 指向 SmartConnect 内部 15 个无负载 reset 网络。未掩盖这些警告，未为此实施架构优化。
资源为整个 PS/AXI/F8/status 系统：CLB LUT **7,510**、FF **11,496**、BRAM tile **1**、DSP **4**；不是单个 GEMM 内核的 HLS 资源结果。
100 MHz 是硬件时序约束；F8 加载期间 Linux clock framework 报告 PL0 **99,999,999 Hz**、enable_count=1，回退后 enable_count=0。没有使用外部仪器测量频率或 Fmax。

## 已解决的实际问题

1. `f8_system_20260927_233525_262`：Vivado 模块参数不接受 `0x...` 字符串，改为十进制 wide 值后通过。
2. `f8_system_20260927_233943_964`：AXI 标量观测线覆盖原 interface 成员，造成握手/响应悬空；综合警告后在 opt_design 报 `Opt 31-67`。改为 GEMM、SmartConnect、status 三端完整连接，并增加连接数/唯一驱动与综合悬空检查。成功运行是新的 `f8_system_20260928_000320_202`，旧运行未标 PASS。
3. ARM SDK 的 xclbin 头位于 `experimental/`；编译改用实际路径并显式链接 libuuid。
4. XRT `get_axlf_section()` 对不存在的 section 会抛异常；纯文件检查提前发现后，改用 `get_axlf()` 遍历并严格限定六种元数据 section。[失败输出](f8_runtime_research/native_compile_final.txt) 与最终成功均保留。
5. 原作者 component.xml 的三个 HAS_BURST=0 与发布 RTL 的 INCR burst 不符；只改隔离构建副本元数据为 1，原始 ZIP、RTL 和上游计算源文件保持原样。F8 平台/运行时作为本次仓库增量提交。
6. 首次实板暴露 HPM0 位宽不匹配；按实际 128 bit 重建后，magic/ABI/两段构建 tag 全部正确读回，完整计算与回退通过。首版的本机构建 PASS 与实板启动 FAIL 分别保留。

## 固定产物与执行边界

本机 bundle：`results/c_integration/f8_bundle_20260928_012117_473`。
板端同名目录：`/home/ubuntu/fpga_project/deploy/f8_bundle_20260928_012117_473`。
硬件构建指纹 `19ff2553bd1377d19048d59835a1225cb10cfa1a6cd8715bca36c31ce992572d`。
配对清单 SHA-256 `bafb429c5262cea3d6c53b8eac9f86e9e37d18540839cbb050a638bf4e06734d`。
接口 `kv260.gemm.f8.chain.v1`，固定 8×8、INT8/INT32、行主序；无 CPU fallback、无补零/裁剪/tiling。

独立原生服务持有实际 CU/BO，客户端断开不会直接析构它们。已完成任务可在硬件证明 idle/drained 后显式恢复；永久 AXI 卡死的硬件 reset/isolation 恢复仍未实现。
当前 guard 为 BO 尾部保护及输入不可改检查，没有前部 guard；物理错误注入、永久 AXI 阻塞后的硬件恢复、长期重复加载卸载和性能 benchmark 未验证。本次实际 BO 均位于高 DDR，未强制分配低 DDR 作对照。

原包的 `board_execution=NOT_RUN` 与 capability 验证字段记录封装时状态；实板结果在独立日志/summary 中，不回写冻结文件。运行链就绪不等于正式封存验收已通过。

P1 标签仍解析为 `ce17306e9726c3cd6d89db02f62c611de686b14f`；未改 P1 kernel，未执行 D64/P2。
本轮已临时加载自定义 F8 PL 并回退，未改持久账户权限、`/boot`、`/etc` 或 QSPI。
完整接口与调用方式见 [F8_RUNTIME.md](../../docs/F8_RUNTIME.md)；学习交接已同步 [LEARNING_HANDOFF.md](../../docs/LEARNING_HANDOFF.md)。
