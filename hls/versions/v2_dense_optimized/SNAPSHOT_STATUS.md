# v2：OPT46 优化 Dense 源码快照

本目录固定的是 **OPT46 / Vitis 2026.1 / 2026-10-15** 本机实验源码，不是 H576 运行包的已配对 HLS 源码。本次归档源码和既有报告，没有重新运行 Vitis HLS/RTL、布局布线或上板；额外原生 C++ 功能检查单独列在下方。

`README.md` 按原始字节保存，是当时实验建立时的说明；其中的 `NOT_RUN` 是历史记录，不在本次归档中改写。后续找到的报告及其范围见下表。原始源码目录为 `D:/GEMM/model_gemm_opt46`，未取得对应源码 Git 提交，故以逐文件 SHA256 固定身份。

## 能力与机制

- `Y = XW`，X/W 为 signed INT8，完整 K 归约及 Y 为 INT32；三个 128-bit AXI master、64-bit 字节地址，`ap_ctrl_hs`。
- 同一套 32×16 外积阵列，设计并行度 512 MAC；TM32/TN768。8 ns 是目标时钟，不是本快照的实测板频。
- 输出使用两个 `hls::stream_of_blocks` 缓冲块；`compute_and_pack` 与 `write_output_blocks` 在 `dense_layer` 中 DATAFLOW 重叠。512-bit 字仅用于片内结果交接，外部 AXI 仍为 128 bit。
- W 每次调用重新从 DDR 装载；X 没有双缓冲。本版本没有 2:4 数据选择、INT4 或运行时 Dense/Sparse 模式。
- 固定支持五种 M×K×N：`1×192×1000`、`197×192×192`、`197×192×576`、`197×192×768`、`197×768×192`。其他尺寸返回 `INVALID_SHAPE`。

## 验证状态与边界

| 项目 | 状态 | 可核验范围 |
|---|---|---|
| 本目录源码/报告逐字节复制 | PASS | 2026-10-26 归档时与下列原路径逐项 SHA256 相同；不代表执行了计算 |
| 本次原生 C++ testbench | PASS | Windows clang 运行 rtl 合成用例集；rtl 只是参数名称，不调用 RTL 仿真器，见 results/versions/native_functional_20261026.json |
| 历史 Vitis C Simulation | PASS | 原日志末尾为 `MODEL_GEMM_RESULT PASS valid=160 rejected=8 outputs=10925312`，CSim 0 errors；原日志索引见下文 |
| 历史 C Synthesis | PASS | 报告已生成；目标 8 ns、uncertainty 1 ns，估计周期 8.237 ns，不宣称 HLS 估计满足目标 |
| 历史 Verilog C/RTL Cosimulation | PASS | `reports/hls_cosim.rpt`，2026-10-15 21:06:07，xsim，Verilog Pass；摘要平均周期包含非法参数事务，不能当作代表性 GEMM 延迟 |
| 历史 HLS 导出级 Place & Route | PASS | `reports/hls_impl_pnr.rpt`，2026-10-15 22:16:11，目标 8.000 ns，Post-Route 7.468 ns，报告写明 Timing was met |
| 本归档目录重新 Vitis HLS 构建/RTL仿真 | NOT RUN | 未重新运行 HLS/RTL 工具；已有报告不能转移成新构建资格 |
| 本 OPT46 快照的完整 KV260 平台及板端测试 | NOT RUN | 导出级 P&R 不等于 PS/DDR/驱动/运行时组成的完整系统验收；未找到与此源码身份配对的板端回执 |
| 本 OPT46 快照的完整模型性能/真实图片精度 | NOT RUN | 没有本版本的对应测量，不填入 H576 或 OPT50 数值 |

导出级 P&R 报告资源为 LUT 51,929、FF 59,030、DSP 521、BRAM 166、URAM 0。此处沿用报告自身字段和单位，不改写为完整板级系统资源，也不把 DSP 总数与 512 MAC 的设计并行度混为一谈。

已检查原工具目录的 `hls/csim/build/model_gemm_core.hpp`、`hls/sim/wrapc/model_gemm_core.hpp` 与本目录 core 字节一致；对应 config 副本也与本目录 config 一致。这支持历史资料的来源核对，但本次没有重新证明全部构建输入、IP 与板级产物的闭环。

## 不与 H576 的时间混写

H576 的 MLP 隐藏维为 576，fc2 为 `197×576×192`；该尺寸不在本 OPT46 白名单中。H576 冻结运行包另有 bitstream、metadata、PS 源码和模型身份，不能因为同为 512 MAC/125 MHz/128 bit 就视为本目录源码的构建产物。

此前 H576 零输入单次约 `172.206422 ms`，以及当前主分支引用的 H576 稳态约 `142.78/142.68 ms`，均不属于本 OPT46 源码的性能证据。本目录不得据此标记“OPT46 上板耗时 170 ms/142 ms”，也不得将这些时间与 OPT50 直接作为同模型、同软件的受控对照。

H576 原始归档的本机只读来源为 `h576_sparse24/inputs/h576-runtime-public-ui.tar.gz`，43,423,483 B，SHA256 `c29a006fe472fd2bfb545d7bbf77f34a5090ff9315043cdd7e5830e518d61f7c`，根目录 `v5_h576_ui_20261017_01/`。该包没有附上 HLS top/core/config 或完整平台重建 Tcl；其 `source/kernel.cpp` 是 ARM/XRT 调用代码，不是 HLS GEMM 核。运行包内容不在本目录重复上传。

`D:/GEMM/hls_component/vitis-comp.json` 当前指向 OPT50，不是本版本的构建入口；也不能把其他目录中保留的 OPT30/OPT45 旧源码文件混入本快照。若重建，应使用本目录 `hls_config.cfg` 的相对源文件并创建新的输出目录。原 README 中关于旧本机组件描述的说明只针对原目录。

## 源码身份

以下七个文件保留原始字节；没有整理换行、修改注释或调整参数。
本目录的 `.gitattributes` 仅关闭本快照内的文本换行归一化，避免提交时改变原文件/报告 SHA；不改变仓库其他目录的规则。

| 本目录文件 | 原始 SHA256 |
|---|---|
| `hls_config.cfg` | `021085f4072399a57f6c4d32f4ce87010cf5f2826fbb5f7ec8514c7e8e300ef9` |
| `src/model_gemm_top.cpp` | `4f855e84019f1fbb4f6663d6f3eb46cf8a8c2c0d49f2346090fee42ade731d16` |
| `src/model_gemm_core.hpp` | `46946029d99b4621ab906f1183650a93f2d4268d4d7bb297d5fcaa2b82e6df42` |
| `include/model_gemm_config.hpp` | `cd00720d34854a2539a0cf3bff4ed4530ea4fbdc4dc401a3c28b69edbb7d26ac` |
| `tb/testbench.cpp` | `df40492f612d1ebe74aebef7f341017a04910dfff2b87873fb0878bccfb33dab` |
| `tb/public_cases.hpp` | `6541ae949d5b71083f64e7f20dd23bfcf15c0c4c1a9213c677bc72996212c9f0` |
| `README.md` | `b139b3308ded222a7b81c0916f00d6f0b37a9b5fa964c2200df9aefa37f72410` |

## 历史报告身份

三份小报告原样保存于 `reports/`，不包含 Vitis/Vivado 工作目录、IP 压缩包、RTL 网表、二进制或模型数据。

| 本目录报告 | 字节数 | 原始路径（相对 `D:/GEMM/model_gemm_opt46/model_gemm_opt46/`） | SHA256 |
|---|---:|---|---|
| `reports/model_gemm_top_csynth.rpt` | 23,281 | `hls/syn/report/model_gemm_top_csynth.rpt` | `3a3b99851428199d8ef1999d7c6465ee14ee32ec7fb38e36dab12e276392ecd7` |
| `reports/hls_cosim.rpt` | 1,258 | `reports/hls_cosim.rpt` | `18c347d471ea18f5451a0c09a30e230c76fd7506ac73bd2543a6f012b7049822` |
| `reports/hls_impl_pnr.rpt` | 194,113 | `reports/hls_impl_pnr.rpt` | `ca23170c861af2b6c12591283f001c40ceb883f8f42eeda64d063618b798263d` |

原 CSim 日志没有重复保存：`hls/csim/report/model_gemm_top_csim.log`，SHA256 `4ecfbf9105427dfb0016748505bb6e1ba4486bccbd0e670d1ff477c50e1be787`。原全模块综合报告 `reports/hls_compile.rpt` 为 371,629 B，SHA256 `87751b5c7685b48489b7eb526e225d8d5bbcc2198e69a438326e186fa36ccc5b`；本目录采用同次生成的较小 top-level 综合报告，未裁剪或改写原报告。
