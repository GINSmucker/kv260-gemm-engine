# OPT50 重启后重新加载入口 v3

仅用于已经实现、已上板验证过的 `fd96539d-4cbc-f732-7694-0f0236ca1cf3` 电路。它不是新计算核，也不是同权重 Dense/Sparse 性能测试程序。生产代码不再写死某次启动 UUID：check 在单板锁下读取两次完整 starter 快照，要求规范、非零 boot UUID 与系统基线一致，然后把实际 boot 冻结进新计划并打印供人工核对。重启后必须重新 check，旧计划永远不能跨启动执行。

## 功能和改动

- 保留旧 HLS/RTL/Vivado/位流、原模型、旧工具与日志，不修改启动配置。
- 新记录保存到 `/var/lib/opt50-loadonly-fd96539d-v3`，服务名称为 `opt50-loadonly-v3-TOKEN`；不复用旧计划、PID 或 guardian。
- tools、plan、status 的加载器 schema 升为 v3；固定硬件/固件/数据校验 schema 不变。PACKAGE/CASES 仍只读引用既有审计包，不把 B24 Golden 混进加载器的硬件来源校验。
- run 在单次 execution 目录认领前、准备执行副本前、固件暂存前及每个管理命令前检查当前 boot 与 900 秒有效期；在持久化 INTENT 后、Popen 前再检查 boot/有效期。已加载成功后的驻留 guardian 不因计划过期而被驱逐。
- 修正旧的 `/drivers/zocl` 假设：加载后核对 `renderD128` 的字符设备号、sysfs 反向绑定、`zocl-drm`、zocl 模块、srcversion 和 OF 节点及属性。
- 固件目录已存在时，仅当 root 所有、不可被组/其他用户写、无链接、恰好三文件且大小/SHA256 与审计源完全一致，才允许只读复用。不覆盖、不修补、不 chmod、不删除旧文件。
- check 固定“复用”或“新建”分支；run 开始及 LOAD_OPT50 前再核对。目录或文件被替换、缺失、损坏或新增内容均拒绝，不自动换分支。
- 继续使用已验证的文件和目录 fsync、一次性计划、设备占用与严重内核错误检查、单板锁及 systemd 防自动重试/强杀属性。

## 操作顺序与风险

上传和解压：仅新建用户目录内的软件文件；没有加载动作。归档不含新的硬件、驱动或模型载荷，继续严格审计板卡上的冻结准备包。

先运行文件/持久化检查：

```bash
# 本地测试文件写入并清理；不访问硬件。
/usr/bin/python3 -B -m unittest -v \
  test_durable.DurableTests.test_native_posix_real_directory_fsync_smoke

# 只读文件审计；不接触设备或加载程序。
/usr/bin/python3 -B ./window.py audit
```

确认 PuTTY 串口日志实际记录后，创建新计划：

```bash
# 修改：新建 root 私有计划和工具副本；不加载，不执行 CSR/START。
sudo /usr/bin/python3 -B ./window.py check --uart-log-confirmed
```

将 check 的完整输出交回审查，另行明确授权后才能执行其返回的 run 命令。不要自己拼接旧计划。check 有效期 900 秒，单次使用；过期不续期、不复用。

真正的 run 会修改板卡临时运行状态：移除 starter、加载候选 zocl、加载 OPT50 overlay/位流、等待 udev；固件目录不存在时会新建三份固件文件。**不刷写 SD/QSPI/BOOT.BIN，不访问 PL CSR，不发 START，不运行 XRT 设备程序，不改变启动配置。**

成功后 guardian 持锁驻留，状态 `LOAD_ONLY_OBSERVED_AWAITING_REVIEW`。随后仍需核对新 guardian，再准备有界的公平对照窗口。不得根据旧状态强停、卸载、重试或恢复。

本工具没有 CSR、START、自动恢复/卸载/复位/重启入口。超时或未知状态保留进程、日志和锁，不自动进入下一步。`clk_summary` 只证明 Linux 时钟框架观察，不等于物理时钟/复位或整数计算证明。

## 文件

- `window.py`：audit / check / 单独授权 load-only run / status。
- `board.py`：系统基线、实际 DRM 绑定、时钟框架与占用观察。
- `firmware.py`：旧固件完整核验、不可变新建/复用分支。
- `artifacts.py`、`board_snapshot.py`、`durable.py`：沿用已冻结的文件审计、OS 快照及持久化实现；不放宽原硬件身份。
- `test_*.py`：合成故障测试；不能代替真实板端加载。
- `tools_manifest.json`：本次软件身份。打包和本机测试完成不表示已通过 ARM 板端加载或性能测试。

板卡没有执行过本次 v3 加载前，状态只能是 NOT_RUN。主机软件测试不是 ARM 加载、整数正确性或性能证明；本工具发出的 START 命令数量始终为 0。
