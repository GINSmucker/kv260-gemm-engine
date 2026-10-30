# OPT50 完整系统工程

此目录用于把已经验证过的 OPT50 HLS IP 接入 KV260 的 PS/DDR 系统，**不是重新开发计算核，也不是再创建一个 HLS Component**。

本目录是 2026-10-24 修复版的可复现源码。旧 `D:\GEMM\opt50_system`、`opt50_system_resetfix_20261024_v3` 及失败证据保持冻结；新整机仿真快照使用 `D:\GEMM\opt50_system_simfix_20261024_v4`，simfix v1/v2 参数/API 兼容性和 v3 初始化失败记录保留，不覆盖旧工程。本次不改 HLS 算法、模型权重、DSP 数量或数据格式，也不连接板卡。

当前明确区分两项修改：**共享复位极性属于真实电路修复**；**PS VIP 写仲裁的本地修正只属于仿真环境**，不进入综合/布局布线。后者不是 AMD 发布的补丁，也不能解释真实板卡故障。原版模型失败记录始终保留。

已确认旧系统的一项逻辑错误：`C_AUX_RESET_HIGH=0`（低有效），但 `aux_reset_in` 接常量 0，导致共享复位持续有效。修复显式设定 `C_EXT_RESET_HIGH=0`、`C_AUX_RESET_HIGH=1`，保持原连线，并同时核对参数、实际端口极性及连接图。该错误足以造成 AXI 从机不响应；没有故障现场内核栈，不能据此断言上次挂起的确切指令。

新版本必须先通过实际生产 BD 的 RTL 逻辑仿真，才能开始综合/布局布线。纯软件单元测试通过不等于 RTL 通过，RTL 通过也不等于板端通过。

## 目录用途

| 路径 | 作用 |
|---|---|
| `project_manifest.json` | 准备完成后生成的源码清单、接口与校验身份；`PREPARED_NOT_RUN` 不是构建通过 |
| `input/ip/` | OPT50 导出 IP 的独立副本；只校正 component.xml 的 Burst 声明，RTL 保持原字节 |
| `input/observer/` | C 的 F8 观察器及辅助 testbench，保持冻结源字节，运行时注入新构建 tags |
| `input/board_files/` | C 已使用的 KV260/K26 板卡 preset，保留来源和许可证 |
| `input/opt50_abi.json` | 与实际 HLS 导出绑定的八参数寄存器快照 |
| `input/sim_model/` | 本地 PS VIP 仲裁副本、精确差异及原版 SHA；只供仿真 |
| `input/sim_cases/` | 固定 12 层的 48 项输入/Golden、SV include 和身份清单 |
| `scripts/run_vivado.ps1` | 默认仅检查文件；通过明确阶段参数才启动 Vivado |
| `scripts/create_system.tcl` | 组装、验证 Block Design；另有辅助观察器仿真及完整实现阶段 |
| `scripts/logic_sim.tcl` | 使用实际生产 BD 和 AMD PS VIP 执行 XSim，不使用简化替代电路 |
| `scripts/board_logic.py` | 从实际 HWH 复核时钟/复位连接图、驱动唯一性与复位常量/极性 |
| `scripts/verify_logic.py` | 独立复核实际系统仿真及 HWH，生成仅仿真资格记录 |
| `scripts/verify_system.py` | 对实际系统产物、HWH、位流配对、DDR 配置和身份进行交叉核对 |
| `scripts/make_metadata.py` | 仅在系统验证后生成八参数 XML/JSON 源文件；不打包或加载 xclbin |
| `config/kv260_opt50.dts` | 新固件名称和 125 MHz 的设备树源码模板，尚未编译/上板 |
| `tests/` | Python 单元测试，以及独立的板级 RTL testbench；两种证据分开记录 |
| `build/` | 每次运行的新目录；保留失败记录，不清空或覆盖旧工程 |
| `BOARD_READINESS.md` | 下次上板的硬门槛；未验项不能视为完成 |

## 已固定的系统连接

- 使用现有 Vivado 2026.1，器件 `xck26-sfvc784-2LV-c`。
- PL0 目标 125 MHz；HPM0、HP0 均为 128 bit。
- 三个数据 master 经 3→1 SmartConnect 进入 HP0。W_data/W_meta 都走 gmem1，不增加第四个 AXI master。
- PS 经控制 SmartConnect 分别访问计算核和观察器，目标地址为 `0xA0000000`、`0xA0010000`，各 64 KiB。
- 三路数据窗口明确限制为低端 `[0,0x80000000)`、高端 `[0x800000000,0x880000000)`；Runtime 还会拒绝零地址、越界与未对齐 BO。
- 保持板卡 DDR preset；PS 的 MIO/DDR 不是本次性能修改对象。构建后重新核对实际配置，不能只相信脚本意图。
- 一个 PL0 时钟域，共享复位覆盖两个 SmartConnect、计算核和观察器；外部复位低有效、辅助和 debug 复位高有效，DCM locked 接 1。观察器通过 RLAST/B 响应计数排空，而不是仅等待写数据结束。
- HP 为非缓存一致性路径，保留 Runtime 的 TO_DEVICE、完成/排空检查及 FROM_DEVICE；不在本次切换 HPC 或删除同步。
- GIE/IER 关闭，Runtime 手动轮询，与无 IRQ 的 PS 配置一致；不能用未映射地址探测或超时猜测代替正确的复位。

旧 C 模板要求 2020.2.2，新脚本明确面向 2026.1，但 **Vivado 命令兼容性仍需首次实际执行确认**；文件语法检查通过不构成系统构建通过。不会自动安装板卡文件或修改全局工具设置。

## 为什么校正 Burst 声明

本次 OPT50 原导出 component.xml 将三个 master 的 `HAS_BURST` 声明为 0，但实际导出 RTL 的 X/W 读、Y 写均驱动 INCR Burst，数据宽度为 128 bit。

准备脚本先核对导出组件和驱动头文件 SHA、实际 RTL，再只在 `input/ip/component.xml` 副本把三个声明改为 1，记录修改前后 SHA。没有修改原导出目录、C++ 计算核或 RTL；若原导出身份变化会拒绝继续。

## 操作入口：Windows PowerShell 7

在新准备的修复版目录使用以下命令；每次运行写入新目录，不覆盖旧运行。

### 仅检查文件，默认安全入口

```powershell
& 'D:\GEMM\opt50_system_simfix_20261024_v4\scripts\run_vivado.ps1'
```

功能：核对项目所有固定文件。修改：无；不启动 Vivado。预期 `PROJECT_SNAPSHOT_VALID_NO_VIVADO_STARTED`。

### 只生成 Block Design

```powershell
& 'D:\GEMM\opt50_system_simfix_20261024_v4\scripts\run_vivado.ps1' -Stage bd
```

功能：创建 Vivado 工程、组装并验证连接。修改：在新的 `build/bd_.../` 写工程、BD、日志和配置报告。**不启动综合/布局布线，不执行计算核，也不加载板卡。**

成功后打开该次运行的 `project/opt50_kv260.xpr` 查看 `kv260_opt50.bd`。这一步不需要重新在 Vitis 中创建或综合 OPT50。

### 布局布线前的实际系统 RTL 仿真

```powershell
& 'D:\GEMM\opt50_system_simfix_20261024_v4\scripts\run_vivado.ps1' -Stage logic
```

功能：生成实际生产 BD，验证复位释放/重复复位、GEMM/F8 寄存器读写、M1/K16/N16 的两次整数计算，再执行固定 12 层 × public/edge × Dense/Sparse 的 48 项真实层回归（1,870,848 个 INT32 逐项比较）。检查输入不变、四个缓冲区保护区、输出 beat 和总线排空。没有 `force` 绕过复位。仿真有有限 watchdog，失败不会产生 PASS。

本流程显式使用 `opt50_ps_vip_fixed` 本地仿真库，并核对 DDR/OCM/REG 三个准确实例的绑定标记，不允许任意新增或遗漏实例。同一仲裁模块的 36 处 request 发布移到 payload 准备之后；保留状态机、ACK、复位和 delta 更新。完整回归只验本设计的 DDR 通路，不声称验收 OCM/REG 业务流量。不是替换真实 PS、DDR 或计算核，不修改 AMD 安装目录。

实际执行方式：先用 Vivado 为生产 BD 生成脚本，再依次真正执行 compile、本地仲裁模块编译、elaborate、simulate。保存自动生成的原始 elaboration 命令，只在新运行命令中前置本地库，解决工具自动省略重复模块、把附加库放在原版库之后的问题。单纯 `-scripts_only` 不会产生 PASS。

修改：仅新运行目录中的 Vivado 工程、生成模型和仿真日志；**不综合、不布局布线、不生成新位流、不访问板卡**。成功必须有本次 tags 对应的完整日志、PASS、模型绑定和作用范围证据；再运行 `scripts/verify_logic.py --run <本次logic目录>` 复核。它不覆盖实板回归或 Linux/XRT 行为。

### 辅助观察器测试与完整系统实现（另行决定）

```powershell
& 'D:\GEMM\opt50_system_simfix_20261024_v4\scripts\run_vivado.ps1' -Stage observer
& 'D:\GEMM\opt50_system_simfix_20261024_v4\scripts\run_vivado.ps1' -Stage build -Jobs 2
```

`observer` 只仿真 F8 辅助模块。`build` 在新运行目录先验证观察器，再生成 BD 并完成上述系统 RTL 仿真，只有同次运行通过后才能综合/布局布线和导出。不能复制旧 PASS 文件绕过。两者均不会访问板卡。**本次诊断阶段不执行 build，先审查逻辑仿真结果。**

build 必须实际得到四份 `artifacts/kv260_opt50.{bit,bit.bin,hwh,xsa}`。之后用现有 Python 运行 `scripts/verify_system.py --run <该次完整build目录>`；只有实际报告和产物核对通过，才能运行 `make_metadata.py --abi input/opt50_abi.json --receipt <run>/system_verified.json --out <run>/metadata`。

`.bit.bin` 会与 `.bit` 配置载荷配对检查，并记录原始/32-bit字节交换形式；这只证明文件来源一致，不证明当前 Linux FPGA manager 接受其加载形式。真正的加载字节序仍须部署阶段单独核对。

## 尚未完成的步骤

- 修复版的实际生产 BD RTL 仿真与结果审查；通过后另行决定综合/实现。
- 新 `.dts` 编译、metadata-only xclbin 打包并再次检查实际六种 section/UUID/八参数；禁止包含 BITSTREAM section。
- 当前 boot 的驱动、独占、临时加载窗口和 starter/100MHz 恢复方案。
- 新 Runtime 的实际 PL 调用、Dense/Sparse Golden 比较和性能测试。

本目录不提供自动加载器，不生成“硬件已就绪”的授权记录，不复用旧 H576 bitstream/UUID/tags。即使完整系统构建通过，也不能替代板端计算正确性或模型分类精度验收。

## 依据与复现

本次审查按职责查阅相关手册章节，而不是把“综合/时序通过”视为逻辑正确：

- [UG1089 板卡流程](https://docs.amd.com/r/en-US/ug1089-kv260-starter-kit/Vivado-Board-Flow)：保留 KV260 板卡 DDR/MIO preset，不修改启动固件或运行整份 `psu_init`。
- [PG164 Processor System Reset](https://docs.amd.com/api/khub/documents/5VcEWg~hYG0gT~7WNDt64g/content)：逐项核对 ext/aux/debug/DCM 输入的极性、常量和复位释放链。
- [PG247 复位](https://docs.amd.com/r/en-US/pg247-smartconnect/Resets)：互联与关联主从复位须重叠，不支持独立复位域；本设计保持同域共享复位。
- [PG247 AXI4-Lite 转换](https://docs.amd.com/r/en-US/pg247-smartconnect/Conversion-to-AXI4-Lite)、[PG201 时钟](https://docs.amd.com/r/en-US/pg201-zynq-ultrascale-plus-processing-system/Output-Clocks)：核对 HPM128→控制32、HP128、64 位地址及单时钟域。
- [DS941 PS VIP](https://docs.amd.com/api/khub/documents/2yDNxBJf36TpTMmoA0X4sA/content)：结合本机 2026.1 生成源码核对仿真 API，执行真实 BD，不能用 Python mock 充当 RTL 证据。

时钟契约：125 MHz 是请求值，PCW 可能派生 124.998749 MHz，实现仍按 8 ns 约束。它们不是 Linux 实测频率或时钟已启用的证明。后续上板须独立核对实际频率、enable/prepare、fabric reset 和 AFI 配置，不能为让检查通过随意放宽 Runtime 频率白名单。

警告必须按当前冻结 RTL 逐项解释：HLS 的 2-bit LOCK 恒为 `00`，裁低位仍为 0；HP0 USER 明确接 0，当前不承载一致性语义。PS VIP 中部分宽度警告来自禁用口或 slave agent 不使用的 master 半边，不能推广为所有警告无害。换 IP 或改变 USER/LOCK 合约必须重审。

v2 已实际通过复位释放、CSR、HLS 完成及 F8 beat/排空检查，但 Dense 输出 backdoor 读回失败，未生成逻辑 PASS。v3 增加初始化读回、4B/64B/192B 对照与 vendor DDR 提交观测；不改 golden、不删保护区、不通过改硬件来迎合仿真。该问题查清前禁止综合/布局布线。源码解析发现 vendor 写仲裁同周期字段更新的调度风险，但尚不能把它断言为根因。

### 2026-10-24 本机验证结果

- Python：system 58 项、verification 57 项，共 115 项通过，仅软件证据。
- v3 实际运行：`build/logic_20261024_003605_631_93e5ea40/`；没有综合、布局布线或板卡操作。
- v3 实际 HWH 的 17 个寄存器、128-bit AXI、DDR/CSR 地址窗口、本次 tags 及完整时钟/复位图检查通过；HWH SHA256 为 `6a17ee2603edfed3f8df44c7a9e15080f03f5bbf7e4009fa130356a6b627754a`。这是静态语义检查，不是整机 PASS。
- 实际 RTL：复位释放/再次复位、CSR、Dense START/done、F8 流量及排空通过；初始化 192B 读回一致。Dense 最终 4B/64B/192B 读回仍为 `CD`，故 **整体 FAIL，Sparse 尚未执行**。
- v3 的 vendor DDR 控制器接收侧 trace 在 25070 ns 观察到 request 有效而地址、字节数、data/strobe 为 X，且之后无第二次接收 trace。这与源码中 request 先更新、payload 延迟 delta 更新的风险相符；单靠同沿 trace 尚不足以归因，后续独立复现见下文。上游 HP 写首值为预期的 `FFFFFFF4`，不能把该打印当作 DDR 提交成功。
- 所有失败目录保留，未生成 `board_logic_pass.txt`，没有新的可部署位流。本次不修改 AMD 安装目录，不使用 force 或放宽黄金结果绕过失败。
- 独立复现现已完成。下一步可评估项目本地仿真模型的最小修正，但尚未修改；它属于验证环境实质变化，需单独说明并取得授权。修后结果必须与原 vendor 模型 FAIL 区分，不能冒充原模型通过。

### PS VIP 无 HLS 隔离诊断

新增源码位于工作区 `opt50_board_integration/ps_vip_diagnosis/`，不改生产工程：

- `tb_vendor_ddrc.sv`：直接实例化安装目录的原版 DDR controller（内部带原版仲裁器和内存），另有原版 memory 的直接写读对照。无 OPT50、CSR 测试逻辑、AXI 驱动或 force；输入在下降沿驱动，跨采样沿保持已知且稳定。
- `diagnostic_variants.sv`：显式配置输入口和数据提前周期，避免 Windows 命令行拆分 `NAME=VALUE` 参数。
- `run_diagnosis.py`：只在全新 ASCII 输出目录编译/运行上述仿真，固定源码、安装库及映射文件 SHA，保存独立日志/结果。输入变化或仿真启动错误会记为 INVALID，不能冒充复现。

使用的安装原库为 `zynq_ultra_ps_e_vip_v1_0_23`，运行没有覆盖源码或修改 AMD 安装目录。

| 有效诊断归档（D:\GEMM 下） | 输入口 | 数据提前 | 展开线程 | 结果 |
|---|---:|---:|---:|---|
| `opt50_ps_vip_diag_20261024_v1` | 3 | 0 周期；下降沿设置 | 2 | 复现丢写/一笔滞后 |
| `opt50_ps_vip_diag_20261024_v4` | 3 | 2 周期 | 关闭并行 | 同样复现 |
| `opt50_ps_vip_diag_20261024_v5` | 0 | 2 周期 | 2 | 同样复现 |

三次均是：直接 memory 对照通过；两次请求、两次 ACK；A 首次写后 16 个 INT32 全为初始化值；B 写后仍未更新，但 A 此时完整变成第一笔数据。额外等待 32 周期后结果不变；4B/64B/192B 读回互相一致，前后 guards 不变。输入及安装库在每次运行前后 SHA 一致。v1 使用当时的归档源码，不能拿更新后的 workspace 字节冒充它的输入。v2/v3 是命令行参数兼容性导致的 INVALID 启动记录，已保留，未计入有效诊断。

有界结论：本机 Vivado 2026.1 原版 PS VIP DDR 仲裁/提交路径与 XSim 调度组合，独立复现了错误，已经不依赖 OPT50 或原整机测试台。源码的同沿 request/payload 更新风险与“一笔滞后”吻合；这不是 AMD 官方确认的通用缺陷，也不证明真实 ARM/DDR 出错或此前板端挂起的完整原因。**原版系统 RTL gate 保持历史 FAIL；项目本地模型的修正另行标识，布局布线和板端加载仍暂停。**

### 本地仿真修正与当前验证计划

已授权制作项目内副本；不更改安装库。`scripts/model_patch.py` 对原版源码 SHA、36 处改动及参数文件 SHA 做严格检查。新副本在 sim_1 独立 library 中使用，明确禁止用于 synthesis/implementation。

- `D:\GEMM\opt50_ps_vip_fixed_20261024_v1`：两次写入立即逐字正确；直接内存对照通过，实际绑定标记唯一。
- `D:\GEMM\opt50_ps_vip_fixed_stress_20261024_v1`：30 次写/ACK/完整读回通过，包含端口 0/3、4/16/64B、完整/交替 strobe、6 组跨端口连续请求、4 次复位。未声称覆盖所有 6 端口和 36 个分支。
- 上述归档仅为隔离诊断，不是生产系统、实现或板端验收。完整系统须另外完成 2 个小矩阵及 48 项真实层回归；结果以本次实际日志和 `logic_verified.json` 为准，不能拿 Python 单元测试代替。
- 后续上板必须先逐项完成 `BOARD_READINESS.md`。本地模型 PASS 不证明 Linux 驱动、时钟或真实 DDR 已通过。

simfix v3 实际运行已验证两个小矩阵 Dense/Sparse 的全部输出，DDR 正确提交；但第一项真实层在初始化写后读回时安全停止，未 START 该真实任务，整体仍 FAIL。原因是本机原版 `all_strb_valid` 仅 2048 bit（每 bit 控制一字节），而测试生成器一次 backdoor 写 4096B。v4 将所有 backdoor 分块限制到 2048B，并继续读回核对输入、输出预填和所有保护区；不修改 vendor 参数、Golden 或硬件 RTL。XSim `%m` 还会打印顶层 TAG 参数装饰，绑定审计逐位核对这些参数，不任意剥离层级。

库绑定依据 AMD [UG900 Verilog Search Order](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Verilog-Search-Order) 与 [Library Mapping](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Library-Mapping-File-xsim.ini)：仅写入映射不等于改变搜索顺序，须保留实际 elaboration 命令和运行时绑定证据。

官网核对：[DS941 第 5、6、9–10 页](https://docs.amd.com/api/khub/documents/2yDNxBJf36TpTMmoA0X4sA/content) 要求 API/读写前完成复位，`wait_mem_update` 是可选的写事件监视（同时只能一个），并非强制屏障；`set_slave_profile` 设置响应延迟，不是仲裁排序修复。此次未找到对应的 AMD 官方修复说明，不能把论坛用户发言当作官方承认。

平台结构参考已核验的 `opt45-source-20261010` 资产；归档 SHA 为 `c217eb521cbf4c7e04e80ef6c52d452c70ae1b35a83f2bd365112c33c9778ef0`，112 个索引成员全部核对。新构建使用自己的文件清单和身份，不继承原实板资格。

工具命令参考 AMD [write_bitstream](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/write_bitstream)、[write_hw_platform](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/write_hw_platform)；控制方式参考 [XRT Native API](https://xilinx.github.io/XRT/2024.2/html/xrt_native_apis.html)。
