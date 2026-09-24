# KV260 团队连接与使用指南（含当前 FPGA/XRT 环境状态）

> 适用于本项目当前这块已完成基础初始化的 KV260。  
> 本文目标：让队友拿到板卡后能够快速完成上电、网络连接、SSH/VS Code/Codex 接入，并了解当前 FPGA/XRT 环境的真实状态与后续限制。

---

## 1. 当前板卡基础状态

当前已完成：

- 板卡：AMD/Xilinx KV260
- 设备树识别：`ZynqMP KV260 revB`
- 系统：Ubuntu 24.04.2 LTS
- 内核：`6.8.0-1015-xilinx`
- 架构：`aarch64`
- microSD 启动正常
- UART 串口正常
- Ethernet `eth0` 正常
- Windows ↔ KV260 网线直连已打通
- KV260 静态 IP 已持久化
- SSH 服务正常
- SSH Key 免密登录已配置
- SSH 别名 `kv260` 已配置
- VS Code Remote-SSH 已验证
- 本机 Codex 已验证可通过 `ssh kv260` 远程执行命令

当前 FPGA/XRT 状态：

- Linux FPGA Framework：**PASS**
- ZOCL/DRM：**FAIL**
- XRT 用户态 Runtime：**PARTIAL**
- Kria Platform Management：**PARTIAL**
- 首个 XRT/PL smoke test 准备度：**NOT READY**

因此目前板卡已经可以稳定作为 Linux 远程目标机使用，但 **尚未具备完整 XRT 计算设备运行条件**。

---

# 2. 供电与启动

使用：

- 12 V DC 电源
- microSD 系统卡
- 网线
- 推荐同时连接 Micro-USB 串口线

上电后串口正常应看到：

```text
Ubuntu 24.04.2 LTS kria ttyPS1
kria login:
```

默认登录用户：

```text
ubuntu
```

---

# 3. 当前网络拓扑

项目当前采用 Windows 与 KV260 网线直连：

```text
Windows PC
192.168.137.1
      │
      │ Ethernet
      │
      ▼
KV260 eth0
192.168.137.2
```

固定配置：

| 项目 | 配置 |
|---|---|
| Windows 以太网 IP | `192.168.137.1` |
| KV260 `eth0` IP | `192.168.137.2` |
| 子网 | `192.168.137.0/24` |
| 子网掩码 | `255.255.255.0` |
| DHCP | 不依赖 |
| 默认网关 | 当前未配置 |

---

# 4. KV260 网络持久化配置

KV260 使用 NetworkManager。

连接名称：

```text
kv260-static
```

关键配置：

```text
ipv4.method: manual
ipv4.addresses: 192.168.137.2/24
ipv4.never-default: yes
connection.autoconnect: yes
```

检查：

```bash
nmcli device status
```

应看到：

```text
eth0    ethernet    connected    kv260-static
```

检查 IP：

```bash
ip -4 addr show eth0
```

应看到：

```text
inet 192.168.137.2/24
```

检查路由：

```bash
ip route
```

应至少看到：

```text
192.168.137.0/24 dev eth0 proto kernel scope link src 192.168.137.2
```

当前无默认路由是有意设计的：这条网线主要用于 **Windows ↔ KV260 SSH/部署专线**。

---

# 5. Windows 端首次接入

先将 Windows 以太网口配置为：

```text
IP：192.168.137.1
Subnet Mask：255.255.255.0
```

然后测试：

```powershell
ping 192.168.137.2
```

再测试 SSH 端口：

```powershell
Test-NetConnection 192.168.137.2 -Port 22
```

正常：

```text
TcpTestSucceeded : True
```

---

# 6. SSH 登录

直接登录：

```powershell
ssh ubuntu@192.168.137.2
```

推荐使用别名：

```powershell
ssh kv260
```

---

# 7. SSH Config

Windows：

```text
C:\Users\<用户名>\.ssh\config
```

推荐内容：

```sshconfig
Host kv260
    HostName 192.168.137.2
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

之后统一使用：

```powershell
ssh kv260
```

---

# 8. SSH Key

每位队员应在自己的 Windows 设备生成独立 Key：

```powershell
ssh-keygen -t ed25519 -C "kv260-dev"
```

默认生成：

```text
C:\Users\<用户名>\.ssh\id_ed25519
C:\Users\<用户名>\.ssh\id_ed25519.pub
```

私钥：

```text
id_ed25519
```

只保存在个人电脑，不要共享。

将公钥加入 KV260：

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | ssh ubuntu@192.168.137.2 "mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys"
```

之后：

```powershell
ssh kv260
```

应可免密码登录。

---

# 9. VS Code 使用方式

推荐：

```text
VS Code
└── Remote-SSH
    └── kv260
```

可直接浏览：

```text
/home/ubuntu
```

但推荐团队约定远程工作目录：

```text
/home/ubuntu/fpga_project
```

注意：当前检查时该目录 **尚未创建**。

建议后续授权后建立：

```bash
mkdir -p ~/fpga_project/{deploy,bin,data,logs,scripts}
```

建议结构：

```text
fpga_project/
├── deploy/    # xclbin、可执行文件、部署产物
├── bin/       # 板端工具
├── data/      # 测试数据、模型、权重
├── logs/      # 日志、性能结果
└── scripts/   # 板端脚本
```

---

# 10. Codex 推荐架构

不要把 Codex 作为主要 Agent 环境放在 KV260 上运行。

推荐：

```text
Windows / WSL
│
├── Codex
├── VS Code
├── Git
├── 项目源码
├── Vivado / Vitis / Python
│
└── ssh / scp
        │
        ▼
      KV260
      Ubuntu
      XRT / PL
```

KV260 作为 Target。

Codex 通过：

```powershell
ssh kv260 "<command>"
```

执行远程命令。

示例：

```powershell
ssh kv260 "hostname; uname -a; free -h"
```

文件传输：

```powershell
scp .\file kv260:~/fpga_project/deploy/
```

后续目标工作流：

```text
Code
  ↓
Build
  ↓
Deploy
  ↓
Run
  ↓
Collect Logs
  ↓
Analyze
  ↓
Optimize
```

---

# 11. 串口调试

如果 SSH 不通，优先通过 UART 排查。

连接：

```text
Windows USB-A
   ↓
KV260 Micro-USB
```

串口参数：

```text
115200
8 data bits
1 stop bit
No parity
No flow control
```

即：

```text
115200 8N1
```

可使用：

- PuTTY
- Tera Term
- MobaXterm

---

# 12. 当前 FPGA Linux Framework 状态

当前 Linux FPGA Framework 检查结果：

```text
PASS
```

关键内核配置：

```text
CONFIG_FPGA=y
CONFIG_FPGA_REGION=y
CONFIG_FPGA_MGR_ZYNQMP_FPGA=y
```

当前 FPGA Manager：

```text
/sys/class/fpga_manager/fpga0
```

名称：

```text
Xilinx ZynqMP FPGA Manager
```

状态：

```text
operating
```

FPGA Region：

```text
/sys/class/fpga_region/region0
```

内核日志已确认：

```text
fpga_manager fpga0: Xilinx ZynqMP FPGA Manager registered
of-fpga-region fpga-region: FPGA Region probed
```

说明：

> Linux 内核本身已经具备并成功初始化 ZynqMP FPGA Manager / Region 框架。

---

# 13. 当前 ZOCL / DRM 状态

当前结论：

```text
FAIL
```

已确认：

```bash
modinfo zocl
```

返回：

```text
Module zocl not found
```

当前：

```bash
lsmod | grep zocl
```

无结果。

当前：

```text
/dev/dri/
```

仅有：

```text
card0
```

该设备绑定：

```text
zynqmp-display
```

它是 DisplayPort 显示设备，不是 XRT/ZOCL 计算节点。

当前没有：

```text
/dev/dri/renderD*
```

因此目前 **ZOCL 计算驱动链没有就绪**。

---

# 14. 当前 XRT 状态

已安装：

```text
xrt 2.18.0-0ubuntu1
```

命令存在：

```text
/usr/bin/xrt-smi
/usr/bin/xbutil
```

但：

```bash
xrt-smi examine
```

和：

```bash
xbutil examine
```

均显示：

```text
0 devices found
```

因此：

```text
XRT Userspace Runtime = PARTIAL
```

即：

- 用户态工具已存在
- XRT 软件包已安装
- 但当前未发现可用 FPGA/XRT 计算设备

不能因为命令退出码为 0 就认为 XRT 已可正常使用。

---

# 15. 当前 Kria Platform Management 状态

已确认：

```text
xmutil
```

存在：

```text
/usr/bin/xmutil
```

版本：

```text
1:1.2.1-0ubuntu1~xlnx2
```

`dfx-mgr.service`：

```text
active (running)
```

当前检测到默认 firmware：

```text
k26-starter-kits
```

设备树：

```text
/fpga-region/firmware-name
```

内容：

```text
k26-starter-kits.bit.bin
```

当前 `/lib/firmware/xilinx/k26-starter-kits/` 中存在：

```text
k26_starter_kits.bit.bin
k26_starter_kits.dtbo
shell.json
```

注意：

```text
设备树：k26-starter-kits.bit.bin
实际文件：k26_starter_kits.bit.bin
```

存在连字符与下划线差异。

当前尚未确认该差异是否会影响后续 PL 重新加载。

---

# 16. Boot Firmware 信息尚未完成检查

以下命令需要 sudo 密码：

```bash
sudo xmutil boardid
sudo xmutil bootfw_status
sudo xmutil listapps
```

Codex 的只读检查因为无法非交互输入 sudo 密码，因此没有获得：

- `boardid`
- Boot Firmware 版本
- A/B Slot 状态
- `listapps`

因此当前这些状态为：

```text
UNKNOWN
```

队友可以在交互 SSH / 串口中手动执行：

```bash
sudo xmutil boardid
sudo xmutil bootfw_status
sudo xmutil listapps
```

只做读取，不要执行升级。

---

# 17. 当前不要直接安装 `xrt-dkms`

当前：

```bash
apt-cache search zocl
```

能看到：

```text
xrt-dkms
```

但当前候选版本：

```text
2.8.743-0ubuntu9
```

而用户态 XRT 是：

```text
2.18.0
```

版本明显不同。

因此目前不要直接：

```bash
sudo apt install xrt-dkms
```

必须先确认：

- Ubuntu 24.04
- `6.8.0-1015-xilinx`
- XRT 2.18.0
- KV260
- ZOCL
- Device Tree

之间的兼容关系。

---

# 18. 当前板卡时间有问题

检查时远端：

```text
System clock synchronized: no
```

RTC：

```text
1970-01-01
```

远端 `date` 与真实日期不一致。

这不会直接导致 FPGA Driver 失败，但会影响：

- 日志时间
- benchmark 结果时间
- 自动化记录
- Git/脚本时间戳

在正式性能测试前需要处理时间同步。

当前先检查：

```bash
timedatectl status
timedatectl timesync-status
```

不要在未确认网络方案前自动修改。

---

# 19. 当前整体 FPGA/XRT 状态表

| 组件 | 当前状态 | 说明 |
|---|---|---|
| Ubuntu | PASS | 24.04.2 正常运行 |
| Kernel | PASS | 6.8.0-1015-xilinx |
| FPGA Manager | PASS | `fpga0` 存在，`operating` |
| FPGA Region | PASS | `region0` 存在 |
| ZynqMP FPGA Framework | PASS | 已注册 |
| XRT userspace | PARTIAL | 2.18.0 已安装 |
| `xrt-smi` | PARTIAL | 存在，但 0 devices |
| `xbutil` | PARTIAL | 存在，但 0 devices |
| ZOCL | FAIL | 模块不存在 |
| DRM compute node | FAIL | 无 `renderD*` |
| `xmutil` | PASS | 工具存在 |
| `dfx-mgr` | PASS | daemon 正常 |
| Boot Firmware | UNKNOWN | 需 sudo 手动读取 |
| `boardid` | UNKNOWN | 需 sudo 手动读取 |
| 首个 XRT/PL smoke test | NOT READY | ZOCL/XRT 计算设备未就绪 |

---

# 20. 当前严禁直接执行的操作

在确认驱动和版本关系前，不要执行：

```text
apt upgrade
apt full-upgrade
apt install xrt-dkms
bootfw_update
QSPI 刷写
设备树覆盖
手动替换 /lib/firmware
强制加载未知 bitstream
强制加载未知 xclbin
```

也不要让 Codex 自动执行这些操作。

---

# 21. 推荐后续排查顺序

下一阶段按以下顺序：

```text
1. sudo xmutil boardid
2. sudo xmutil bootfw_status
3. sudo xmutil listapps
        ↓
4. 确认 Boot Firmware / KV260 平台状态
        ↓
5. 确认 Ubuntu 24.04 + Kernel 6.8 + XRT 2.18 的 ZOCL 正确来源
        ↓
6. 解决 ZOCL / DRM render node
        ↓
7. xrt-smi examine
        ↓
8. 确认出现真实 XRT device
        ↓
9. 创建 ~/fpga_project
        ↓
10. 准备 KV260 对应 host + xclbin
        ↓
11. 首个 XRT/PL smoke test
        ↓
12. GEMM 部署
```

---

# 22. 团队统一约定

统一使用：

```text
SSH Alias: kv260
Remote User: ubuntu
Remote IP: 192.168.137.2
Remote Interface: eth0
Remote Workspace: /home/ubuntu/fpga_project
```

开发原则：

1. 本地仓库是源码唯一可信来源；
2. KV260 作为 Target，不作为主要开发主机；
3. Codex 在 Windows/WSL 运行；
4. 通过 `ssh kv260` 和 `scp` 控制板卡；
5. 每人使用独立 SSH Key；
6. 不共享私钥；
7. 不在未经确认时刷 Boot Firmware；
8. 不自动修改 `/boot`、`/etc`、Device Tree；
9. 不自动安装不匹配的 XRT/ZOCL 包；
10. 性能日志统一存放到 `~/fpga_project/logs/`；
11. 在 XRT device 正常枚举前，不进入 GEMM 板上性能测试。

---

# 23. 常用命令速查

连接：

```powershell
ssh kv260
```

远程执行：

```powershell
ssh kv260 "hostname; uname -a; free -h"
```

上传：

```powershell
scp .\file kv260:~/
```

网络：

```bash
nmcli device status
ip -4 addr show eth0
ip route
```

系统：

```bash
uname -a
cat /etc/os-release
free -h
df -h
```

FPGA：

```bash
cat /sys/class/fpga_manager/fpga0/name
cat /sys/class/fpga_manager/fpga0/state
ls /sys/class/fpga_region
```

XRT：

```bash
xrt-smi examine
xbutil examine
```

ZOCL：

```bash
modinfo zocl
lsmod | grep zocl
ls -l /dev/dri
```

Kria：

```bash
sudo xmutil boardid
sudo xmutil bootfw_status
sudo xmutil listapps
```

---

# 24. 当前里程碑

```text
KV260 上电                         PASS
Ubuntu 24.04.2                    PASS
microSD 文件系统                  PASS
UART                              PASS
Ethernet                          PASS
静态 IP 192.168.137.2             PASS
SSH                               PASS
SSH Key                           PASS
VS Code Remote-SSH                PASS
Codex → SSH → KV260               PASS

Linux FPGA Framework              PASS
XRT Userspace                     PARTIAL
Kria Platform Management          PARTIAL
ZOCL / DRM Compute                FAIL
XRT Device Enumeration            FAIL
XRT/PL Smoke Test Readiness       NOT READY
```

下一里程碑：

```text
补齐 ZOCL / Compute DRM
        ↓
XRT 枚举到设备
        ↓
KV260 XRT/PL Smoke Test
        ↓
Host → DDR → PL → DDR → Host
        ↓
GEMM Accelerator
```
