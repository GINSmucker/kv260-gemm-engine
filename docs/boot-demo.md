# Sparse24 板端网站：电脑接入与现场使用

本页用于移交已经配置好展示服务的 KV260 和现有系统卡。网站由板卡提供，Windows 浏览器访问 **http://192.168.137.2:8080/#overview**；现场图片通过 HTTP 提交，由板端执行真实 Sparse24 推理。日常演示不需要 SSH、Node.js 或本机启动网站。

展示部署程序和本轮网页验证证据尚未随仓库发布。本页是现有板卡的使用说明，不是新系统卡的安装教程。仓库原有的 `start-workbench.ps1` 仍启动旧 H576 本机工作台，使用方式见[历史网站说明](workbench.md)。

## Windows 首次设置以太网

将电脑与板卡用网线直接连接，为板卡接通电源并等待 Ubuntu 与展示服务启动。以下参数用于这条直连网线；只修改对应的有线网卡，Wi-Fi 可继续保留原设置。

1. 按 **Win+R**，输入 `ncpa.cpl`，按回车打开“网络连接”。
2. 找到接板卡的“以太网”适配器，右键选择“属性”。使用 USB 网卡时选择对应的 USB 以太网适配器。
3. 选中“Internet 协议版本 4（TCP/IPv4）”，点击“属性”。
4. 选择“使用下面的 IP 地址”，填写下表；DNS 输入框留空。
5. 点击“确定”保存并关闭属性窗口。

| 电脑有线网卡项目 | 设置 |
|---|---|
| IP 地址 | `192.168.137.1` |
| 子网掩码 | `255.255.255.0` |
| 默认网关 | 留空 |
| 首选／备用 DNS | 留空 |

板卡已使用固定地址 **192.168.137.2/24**，电脑与板卡的 IP 必须不同。若从 Windows“设置 → 网络和 Internet → 以太网 → IP 分配 → 编辑”进入，选择“手动”并启用 IPv4；显示“子网前缀长度”的版本填写 **24**，显示“子网掩码”的版本填写 **255.255.255.0**。系统设置入口参考 [Microsoft Windows 网络说明](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/essential-network-settings-and-tasks-in-windows)。

这条直连链路不提供互联网，Windows 显示“无 Internet”本身不影响访问板卡网站。电脑需要上网时可继续使用 Wi-Fi；展示无需配置 Internet 连接共享。

## 检查连接并打开网站

在 Windows PowerShell 中执行这一条只读命令：

```powershell
Test-NetConnection 192.168.137.2 -Port 8080
```

`TcpTestSucceeded : True` 表示已连通网站端口。然后在浏览器地址栏输入：

**http://192.168.137.2:8080/#overview**

正常开机后服务自动启动；等待页面显示“KV260 已就绪 · Sparse24”。网站不自动 SSH 登录板卡，浏览器也不要求输入 Ubuntu 密码。

## 运行一张真实图片

1. 打开“推理工作台”。
2. 选择一张不超过 8 MiB 的 JPEG，使用主体清楚的自然图片。
3. 点击“开始板端推理”，等待结果。页面显示 Top5、CPU/PL 位级比较、36 次 PL 调用及分项时间。
4. 使用“导出本次 JSON”保存结果，或在“运行记录”中查看。

模型输出为 ImageNet-1k 的 **1000 类**。完整模型的 49 个 Linear 中，PL 执行 12 个 Dense 和 24 个 Sparse，另外 13 个 Linear 及其余图算子由 ARM 执行。每张图片同时执行同权重 CPU4 参考并比较 1000 个 FP32 输出；没有人工标签的上传图片只提供预测和数值一致性，不产生分类准确率。

首次或空闲后需要建立推理会话，完整请求会比 `Runner_run_ms` 更长。`Runner_run_ms` 是 PS+PL 图调用时间，排除会话初始化、图片预处理和 CPU 参考；它不是纯 PL 核时间。历史 500 张精度与 195 张统一版本耗时见[最终结果](../results/README.md)。

## 页面打不开时

先确认网线两端已连接、板卡已进入 Ubuntu，以及电脑有线网卡实际使用 `192.168.137.1/24`。可用 `ipconfig` 查看；`Test-NetConnection` 检查的是 8080 网站端口，SSH 的 22 端口不替代网站检查。

若浏览器使用代理，应允许 `192.168.137.2` 走局域网直连。不要因网站暂时不可达而重复加载 FPGA。需要维护人员通过 SSH 或串口检查时，执行下面的只读命令：

```bash
ip -br -4 addr
sudo systemctl show opt50-demo-hardware.service opt50-demo-web.service --property=Id,ActiveState,SubState,MainPID,ExecMainStatus,Result
sudo journalctl -b -u opt50-demo-hardware.service -n 30 --no-pager -o cat
```

应用已记录启动自检与真实 JPEG 推理通过，并有重启后自动启动及真实推理的验证记录。一次已检查的重启中，网口曾需手动激活，随后出现 carrier 变化；网络稳定性和完全无人值守联网仍需单独验证。

## 结束展示

等待当前图片返回结果，再通过 Ubuntu 正常关机：

```bash
sudo poweroff
```

保留现有系统卡、模型与服务。下次上电时 Ubuntu 服务重新加载易失的 FPGA 配置并自检，随后提供网站。
