# B24 layer02：ARM 适配与首层测试包（启动适配 v2）

本目录独立适配成员 B 的 `B_FINAL_H576_24L_100K_20261026_v1.zip`。现有 12 层版本及其模型、程序和测试记录不变。本包不含新位流，不需要重新运行 HLS/Vivado。本次启动适配 v2 加入精确锁定发布身份的 load-only-v3 guardian；整数运行器和数据未改变。

## 当前交付范围

- 模型来源：最终 24 层 MLP 2:4 W8A8 模型，SHA256 `ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43`。
- 本次用例：`dev0000` 的 `blocks.0.mlp.fc1`，层索引 02，M/K/N = 197/192/576。
- 同一剪枝矩阵分别用 Dense=0、Sparse=1 编码，同一个 CU 和四个缓冲区顺序执行；共两次 START，逐元素比对 226944 个 INT32 输出。
- `data/Y_golden.bin` 是 CPU 参考，不冒充硬件输出。Dense 也使用这份剪枝权重，不是原始未剪枝模型。
- 本包是新模型的第一项 ARM 单层适配，**不包含 24 层完整模型 Runner**，也不测分类精度或推理性能。

全部 24 个稀疏层可沿用 OPT50 的整数接口。但 B 的 qkv/proj/head、bias、scale、LayerNorm 等参数也已更新，后续完整模型适配必须同步导入新模型全部参数，不能只给旧 12 层图换权重。

## 文件说明

| 文件 | 功能 |
|---|---|
| `runtime/opt50_b24_layer02.cpp` | ARM/XRT 运行器，以及无设备访问的离线入口 |
| `runtime/opt50_b24_layer02_pure.hpp` | 同权重两模式、整数参考、编码和控制序列检查 |
| `run_layer02.py` | `check/run/status`，绑定实际 boot、驻留 guardian、编译及输入身份 |
| `support_py/` | 文件持久化及现有驻留 guardian 的只读复核 |
| `build_runtime.py` | 编译新程序；只运行 CPU 自测，不启动 PL |
| `offline_check.py` | 使用真实 layer02 数据运行 CPU 模拟后端 |
| `prepare_inputs.py` | 从指定 B ZIP 提取白名单数据；验证来源、格式及 INT64 golden |
| `data/` | 六个输入/参考文件、原始 B 清单及新的来源清单 |
| `verify_tools.py` / `tools_manifest.json` | 本包逐文件身份检查 |
| `tests/` | 输入损坏、错误任务、状态及资源生命周期等负例 |

不执行 B ZIP 中的脚本，不反序列化 `.pt`，不携带完整模型和训练图片。

## 数据与接口

采用 `H576_SPARSE24_SPLIT128_V1`，128-bit AXI：X 为 INT8 `[M,K]`，Dense W 为 INT8 `[K,N]`，Y 为 little-endian INT32 `[M,N]`。稀疏数据沿 K 每四取二，`p0<p1`，位置码为 `p0 | (p1<<2)`；先前记录位于 metadata 字节低半字节。保留的值允许为零，不通过扫描非零值重建掩码。

| 文件 | 字节 |
|---|---:|
| X.bin | 37824 |
| W_dense_data.bin | 110592 |
| W_dense_meta.bin | 16 |
| W_sparse_data.bin | 55296 |
| W_sparse_meta.bin | 13824 |
| Y_golden.bin | 453888 |

现有固件 UUID：`fd96539d-4cbc-f732-7694-0f0236ca1cf3`，125 MHz。XRT 元数据 8560 字节，SHA256 `715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551`。它由已有板端加载计划指向，本包不替换或重建它。

## 上板前的软件步骤

以下命令在 Ubuntu 板端执行。工作目录必须是本次新目录，不要在旧版本目录解压。包内没有预编译 ARM 二进制，须在板端编译。

```bash
# 修改：仅切换当前终端工作目录。
cd /home/ubuntu/fpga_project/opt50_integration/b24_reentry_20261027_v1/layer02 &&
# 只读：核对本包软件和数据文件。
/usr/bin/python3 -B ./verify_tools.py &&
# 只读：核对输入和2:4编码，默认不要求NumPy，不访问设备。
/usr/bin/python3 -B ./prepare_inputs.py verify --data ./data &&
# 修改：产生新ARM程序、CPU自测和编译收据，不访问PL。
/usr/bin/python3 -B ./build_runtime.py --with-xrt &&
# 修改：产生离线任务和结果日志，运行CPU模拟后端，不发START。
/usr/bin/python3 -B ./offline_check.py \
  --executable ./bin/opt50_b24_layer02_xrt --out ./offline_layer02_v1
```

通过上述步骤只表示 ARM 编译和离线验证通过，尚无新模型上板计算证据。不要手动启动 native `--run`。新编译/结果目录禁止覆盖，失败记录应保留。

可选：在装有 NumPy 的 Python 环境加做 `prepare_inputs.py verify --data ./data --arithmetic`，重新计算完整 K 维的 CPU 整数 golden。默认 verify 仅做文件与编码检查；本机准备数据时已做完整 INT64 复算。

## 当前板卡身份检查

```bash
# 只读：确认启动身份与运行时间。
uname -r
cat /proc/sys/kernel/random/boot_id
uptime
# 只读：确认当前overlay和DRM驱动，不访问PL寄存器。
ls -1 /sys/kernel/config/device-tree/overlays
ls -l /dev/dri
readlink -f /sys/class/drm/renderD128/device/driver
# 只读：列出相关驻留服务；不停止或重启任何服务。
systemctl list-units --all 'opt50-*' --no-pager
```

若已经重启且只有 starter overlay，先按组合包上一级 `README.md` 的 load_tools/v3 流程创建新加载计划。旧 boot 的 plan 不可复用。本入口同时支持固定发布身份的 v2/v3 guardian，严格按各自 namespace、schema、unit 和工具 SHA 核对，不能自学或接受未知加载工具。v3 的新计划冻结实时 boot；本测试包仍不自动加载固件、驱动或恢复 starter。

已有可信驻留 OPT50 后，使用**实际当前** guardian plan：

```bash
# 修改：只设置当前终端变量，将路径替换为当前启动下实际的load-only计划。
OPT50_GUARDIAN_PLAN=/var/lib/opt50-loadonly-fd96539d-v3/plan_实际令牌/plan.json
# 修改：创建新的root私有测试计划和副本；只读复核OS及guardian，不访问MMIO、不发START。
sudo /usr/bin/python3 -B ./run_layer02.py check \
  --guardian-plan "$OPT50_GUARDIAN_PLAN" --uart-log-confirmed
```

`check` 可加 `--metadata /绝对路径/metadata.xclbin` 显式指定已有元数据；仍必须满足固定长度和SHA。省略时从guardian源计划读取其已有路径。UART 参数表示已实际确认电脑端串口日志正在写入。

`check` 产生 900 秒有效、单次使用的计划，并打印对应 run/status 命令。计算授权范围是本次 layer02 的两次 START。执行前应核查这些输出及当前串口记录；本包准备本身不授予加载或计算权限。

## 运行与收尾边界

板端运行器先检查 PS 接口宽度、observer 身份、idle/DMA drain、DDR 地址范围、对齐及控制 ABI，然后才允许两次 START。每次完成后检查输出、输入未变及缓冲区边界，正常关闭 XRT 资源。驻留 guardian 和原板锁保持不变。

对不确定的 DMA/进程状态保留资源和锁，保存阶段与原因，不自动重试、强杀、复位、卸载或重启。`status` 只读取保存回执，不是新的硬件检查。

## 后续顺序

1. 新 layer02 两模式实板整数验证。
2. 扩展到 B 的 24 个稀疏层与两张真实传播输入的 PL 用例。
3. 用新完整图、全部新参数和统一线程配置适配 36 次 PL 调用；24 Sparse、12 Dense，proj/head 在 PS。
4. 完整模型数值一致性、重复延迟测试、真实图片精度与耗时验证。

B 的参考图使用单线程，旧 ARM 完整模型测试使用四线程。完整模型阶段应固定线程和图优化配置并重新生成参考，不能直接把旧线程配置的输出当作新golden。
