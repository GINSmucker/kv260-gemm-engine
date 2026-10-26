# OPT50 同权重对照 v1：加载日志读取修复 fix1

fix1 仅修复复核器对加载服务日志权限的假设：仅对固定 guardian 执行目录中的8份 command_01..04.stdout/stderr 日志，允许 root 所有、单链接、无链接且稳定的 0600 或 0644 文件，并核对已记录大小和 SHA。它们的父目录仍必须为 root 私有目录。计划、工具、模型输入等普通 private 读取仍严格拒绝0644。

不 chmod、不覆盖、不删除旧日志，不停止 guardian，不重载电路。C++、ARM编译源清单和数据格式没有改变，修复安装器只把已验证的4个ARM二进制和原编译收据复制到新目录，保留原argv、源文件与旧包。无需重新编译。

修复后的软件目录是 `/home/ubuntu/fpga_project/opt50_integration/fair_compare_fd96539d_v1_fix1`。先在修复包执行 `install_fix.py`，再到此新目录运行 `verify_tools.py` 和 `run_model.py check --execution-mode all_dense --uart-log-confirmed`。不要在已复制编译产物的目录重新执行下面的编译命令；该部分仅描述原始构建流程。

以下对照、计时和安全范围保持不变：

## OPT50 同权重 Dense/Sparse 完整模型对照 v1

本包是新 ARM 软件，不修改 HLS/RTL/Vivado/位流、原模型或昨天通过的 performance_v2。
当前驻留电路 UUID fd96539d-4cbc-f732-7694-0f0236ca1cf3，绑定本次启动 f0f2eed8-5820-4df5-ba19-d05527720f97 以及新加载计划 /var/lib/opt50-loadonly-fd96539d-v2/plan_74abb1e54663/plan.json。guardian PID/start_ticks 从真实服务与 /proc 获取，不沿用昨天PID。

## 对照范围

- all_dense：同一剪枝模型的36个PL层全部按Dense表示和mode=0计算，13个PS层不变。
- mixed：同样36个PL层中24个Dense、12个Sparse；13个PS层不变。
- 两组使用完全相同的 owner/plugin 二进制、原始 canonical 剪枝权重、图、量化/反量化、bias、输入、ORT1.22.1和NumPy1.26.4。不是拿原始未剪枝模型冒充Dense对照。
- intra4/inter1、顺序执行、图优化OFF、spinning0，主线程CPU0/工作线程CPU1/2/3，owner四核可调度，与v2一致。
- 两组四BO容量相同：Wdata有效容量3981312B；mixed压缩数据保留原3317760B前缀并零填尾部；Wmeta同为165952B。这不改变模型文件，初始化时在RAM中生成活动表示并逐层验证等价。
- 必要client X/Y有效长度同步保留，owner图内零payload同步、零全量hash/guard扫描、零磁盘事件。结束后INFO新鲜检查全部BO和活动权重，再正常STOP/退出0。
- 每份计划只允许一个完整模型、36次START；无预热/重复/自动重试/CPU回退，不改变固件/时钟/启动配置。两组分别单独创建新计划和授权。

这里首先取得一个受控单次样本对。它仍有首次运行/执行顺序/调度波动，不能宣称稳定均值、P95、持续吞吐或真实图片准确率；后续重复统计需要独立有界流程。

## 先编译，不运行owner

上传新归档并核对SHA，解压到新目录 /home/ubuntu/fpga_project/opt50_integration/fair_compare_fd96539d_v1，然后执行：

```bash
# 只读软件身份检查，无设备访问
/usr/bin/python3 -B ./verify_tools.py

# 修改：新建ARM二进制、编译收据和CPU自测输出；不运行owner、不打开PL
/usr/bin/python3 -B ./build_arm.py \
  --model /home/ubuntu/fpga_project/opt50_integration/full_model_fd96539d_v1_fix1/model \
  --out ./build_arm_fair_v1
```

编译含两个CPU自测：原始49层权重身份与两种活动载荷解码等价；模式、预算、完成/保护区/计时审计反例。编译通过不是新板端计算通过。不要手动运行 opt50_model_owner。

## 创建Dense计划

保持PuTTY串口日志、新load-only guardian运行。以下只创建root私有计划、复制文件和只读OS核验，不发CSR/START：

```bash
sudo /usr/bin/python3 -B ./run_model.py check \
  --execution-mode all_dense --uart-log-confirmed
```

核对check输出后另行授权它返回的run命令。它会访问驻留CU寄存器/XRT、分配同步BO并发36次START，属于硬件运行修改，不是只读。计划900秒、一次性，失败不自动杀进程或重试。

Dense成功且正常关闭后，再创建mixed计划：

```bash
sudo /usr/bin/python3 -B ./run_model.py check \
  --execution-mode mixed --uart-log-confirmed
```

同样需新计划和明确授权。两个计划不能并发；共用原global companion flock，load guardian继续保有板卡锁，没有转让。

两组完成后，compare.py --dense-plan 路径 --mixed-plan 路径 只读复核并输出结果；不会发START或停止任何进程。不要将旧141.95ms和今天Dense单次样本直接组成受控对照。

## 计时和证明

Runner.run计时包含完整PS/PL模型、BEGIN/END完成证明和client缓存同步；排除Runner/Session初始化、数据核验/序列化、INFO全BO检查及关闭。同步/IPC/轮询窗口不叫纯PL周期；未实现独立硬件周期测量仍标缺测。

两组均必须1000个logits与同一CPU参考逐位相同，记录实际模式、36次有序调用、active载荷SHA和关闭证据。此处“同权重”是稀疏化后的同一组数学权重；剪枝相对原始模型的精度变化另做真实图片评估。

## 已知边界

当前仍是W8A8和固定H576路由，不是W4A8或完整Transformer硬件化。旧启动的板测日志只作历史，既不改写也不作为当前进程仍安全的许可。新namespace为 /var/lib/opt50-fair-fd96539d-v1。重启、guardian/固件改变时应拒绝，不能绕过或手动启动旧程序。

本机CPU/合成测试和打包不证明ARM/XRT编译或新的板端对照已经执行。具体执行证据见新计划的completion.json，不能只看文件名或历史PASS。
