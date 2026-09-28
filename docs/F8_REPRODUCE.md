# F8 交付检查、调用与重建

本页区分离线检查、使用已验证包，以及重新构建。新构建必须形成自己的产物清单和测试结果。
历史实板记录属于 2026-09-28 / `262414267686403`；执行离线测试不会产生新的实板 PASS。

## 1. 离线检查

在仓库根目录，用已有 Python 3 运行（只需标准库）：

```powershell
python scripts/verify_f8_delivery.py
```

该检查读取冻结清单、源码和证据，核对完整包、发布 IP 解压内容、board files 及原始日志；不访问设备。
`.gitattributes` 保留被 SHA-256 清单引用的原始字节，不要对这些文件批量改换行或格式。

软件测试需要 NumPy；实际验证环境为 Python 3.12.7 / NumPy 1.26.4。使用已配置环境的解释器：

```powershell
python -B -m unittest discover -s tests -p 'test_f8*.py' -v
python -B -m unittest discover -s tests -p 'test_c_integration.py' -v
```

前一入口 20 项 host/RPC/契约测试，后一入口 7 项 CPU 接口回归。它们不运行 PL。

## 2. 使用已经测试过的配对包

目录：`results/c_integration/f8_bundle_20260928_012117_473`。
其中 31 个清单文件包括 bit、Linux BIN、hwh、xsa、DTBO、metadata xclbin、ARM 程序和 Python 源码，另有运行后独立证据。
完整包必须保持配对，不单独替换 bitstream、DTBO、原生程序、metadata 或 capability。

已测试目标前提：

- commercial K26 / KV260；当前 HPM0/HP0 128 bit、PL0 100 MHz。
- Linux `6.8.0-1015-xilinx`，已安装匹配 ZOCL/XRT 2.18，DFX manager 可用。
- `/home/ubuntu/fpga_project/.venv/bin/python` 已配置 NumPy。
- 当前为已知 starter、ZOCL 未加载、无其他 FPGA/DRM/MMIO 使用者。
- starter 文件哈希与加载器硬编码值相符。

复制完整目录到 `/home/ubuntu/fpga_project/deploy/`，ARM 可执行文件需有执行位：

```powershell
scp -r results/c_integration/f8_bundle_20260928_012117_473 kv260:/home/ubuntu/fpga_project/deploy/
```

```sh
chmod u+x /home/ubuntu/fpga_project/deploy/f8_bundle_20260928_012117_473/f8_xrt_service
```

临时加载会实际切换 PL/DTBO 和驱动。维护者按 [具体加载/回退方案](F8_BOARD_CHANGE_PLAN.md)确认此次执行后，在板端 sudo 会话运行：

```sh
python3 /home/ubuntu/fpga_project/deploy/f8_bundle_20260928_012117_473/board/tools/run_f8_board_validation.py /home/ubuntu/fpga_project/deploy/f8_bundle_20260928_012117_473 --authorized-temporary-load
```

脚本自动执行公开开发测试和安全回退；每次使用新的 monotonic run ID，保留之前日志。
不能证明硬件静止时会保留 BO/所有者/overlay；不能通过强杀进程或直接重编程来代替 DMA 停止证明。

## 3. 客户端调用

在已加载正确平台、原生服务已就绪且由维护者独占管理生命周期的前提下，在配对包目录调用：

```python
import numpy as np
from board.backend import TaskSpec, select_backend

X = np.eye(8, dtype=np.int8)
W = np.arange(64, dtype=np.int8).reshape(8, 8)
backend = select_backend("fpga_f8", manifest_path="bundle_manifest.json", socket_path="f8.sock")
handle = backend.prepare(TaskSpec(8, 8, 8), W)
result = backend.run(handle, X)
print(result.Y)
backend.release(handle)
backend.close()
```

正常路径如上；异常路径按 [FAULT 与恢复](F8_RUNTIME.md)处理，不能无条件释放未证明静止的 DMA 资源。
四方法接口释放的是权重 handle；原生服务持有 CU/BO 直到受控安全退出。

## 4. 从源码重建平台

已测试 Windows 工具位置为 `C:\Xilinx\Vivado\2020.2\bin`，版本 2020.2.2，已安装 ZynqMP/K26 器件数据。
脚本使用 PowerShell 7、临时 ASCII drive alias 和进程级环境变量；无需修改全局 PATH。

```powershell
./scripts/run_f8_system.ps1 -Build
```

输入为固定原 IP ZIP/解压目录和已附带的官方 KV260/K26C 1.1 board files。
脚本在 `results/c_integration/f8_system_<时间>/` 保存源码副本、IP 元数据修改前后哈希、build fingerprint、报告及产物。
必须确认 status RTL / BD / build 三个 PASS 标记、时序/DRC/路由报告，不能仅根据进程结束判断成功。

## 5. 重新编译 ARM 原生程序和元数据

已有包可以直接使用；只有更改运行代码或重建交付时才需要本节。
在 KV260 的项目目录内部准备 staging 目录 `/home/ubuntu/fpga_project/deploy/f8_runtime_20260927`，放入本仓库的 native C++、`make_metadata.py` 和 DTS。封装脚本沿用该实际 staging 路径。

实际 ARM SDK 编译命令：

```sh
g++-13 -std=c++17 -O2 -Wall -Wextra -pthread \
  -I/usr/include/xrt \
  -I/home/ubuntu/fpga_project/deploy/c3_runtime_probe_20260925/sdk/uuid-dev/usr/include \
  f8_xrt_service.cpp -L/usr/lib -lxrt_coreutil -l:libuuid.so.1 -o f8_xrt_service
python3 make_metadata.py .
xclbinutil --add-section EMBEDDED_METADATA:RAW:embedded_metadata.xml \
  --add-section IP_LAYOUT:JSON:ip_layout.json \
  --add-section MEM_TOPOLOGY:JSON:mem_topology.json \
  --add-section CONNECTIVITY:JSON:connectivity.json \
  --output kv260_f8_metadata.xclbin
dtc -@ -I dts -O dtb -o kv260_f8.dtbo kv260_f8.dts
./f8_xrt_service --inspect kv260_f8_metadata.xclbin
```

UUID include 目录来自本板已有项目内 SDK；其他开发机应使用其实际匹配 SDK 的 `uuid/uuid.h` 路径。不要把头文件缺失与 XRT 设备枚举失败混为一类问题。
以上编译/metadata/DTC 步骤不加载 PL；输出目录须先处理旧文件，`xclbinutil` 不应静默覆盖旧候选。
新编译可能有不同二进制哈希和 xclbin UUID，应以新包清单和新的实际检查为准。

## 6. 封装新配对包

完成上述 staging 后，在 Windows 仓库根目录运行：

```powershell
./scripts/package_f8_runtime.ps1 -SystemRunName '<本次成功的 f8_system_运行名>'
```

脚本核验构建输入没有漂移、产物 SHA-256、Bootgen BIN 全量 word 字节序，并从板端 staging 获取原生程序、DTBO、metadata 和 xrt.ini。
构建来源 Git SHA 属于本次源码版本；本次历史包的 `git_head` 属于原本机开发仓库，保留该字段是为了追溯，不能把它误认为上游计算 IP 的 SHA。
新包打包时 `board_execution=NOT_RUN` 是正确状态。新实测结果写入独立日志，不能回写清单冒充已经测试过的旧包。

## 7. 本次仓库整理复核

交付完成后，离线完整性和 27 项软件测试从仓库副本重跑；结果归档于 [repository_delivery_20260928](../results/c_integration/repository_delivery_20260928/)。
这些是交付复核，本次没有重新运行 Vivado/RTL 仿真、ARM 编译或上板；这些层级的 PASS 来自保留原始运行 ID 的历史记录。
