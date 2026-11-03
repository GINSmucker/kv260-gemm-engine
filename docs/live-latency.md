# 原始 JPEG 请求的耗时与等价优化验证

KV260 LAB 的 `Runner.run` 是完整 PS＋PL 模型计算时间。上传、图片预处理、模型包核对、CPU 参考、图后 INFO 和持久化在这一计时之外；板端 `request_work_ms` 从工作线程开始处理图片起计时，结束于结果 JSON 持久化之前，也不包含 HTTP 上传、接收阶段的图片落盘和网页查询等待。

## 测量与证据

[服务实测对照](../results/live_demo/python_service_20261101.json)记录同一张 3000×4000 原始 JPEG 的完整请求、板端处理与预处理耗时，保留每组首张和后续五次请求。[分项诊断](../results/live_demo/latency_diagnostics_20261101.json)提供 CPU 和持久化开销的定位依据。两阶段顺序测量之间有一次正常重启，不能解释为交错重复或长期性能保证。

原始吉他 JPEG 为 3000×4000。分段测量显示，完整解码、RGB 字节复制、RGB SHA 和双三次缩放都占用 CPU；不能将预处理总时间解释为空转。现有图后检查逐项同步四个 BO、验证保护区、重新计算静态权重摘要，并逐条持久化检查事件。SD 卡上的细粒度 `fsync` 存在明显长尾。

## 服务实现

[板端服务](../board/service/backend.py)使用[等价 CPU 实现](../board/service/cpu_optimizations.py)，模型、已验收快照、ORT4/1、图优化／自旋设置、会话预算和 Native 二进制保持原配置：

- 预处理复用已解码的 RGB 图像，按原始扫描行顺序分块更新 RGB SHA，避免完整 RGB 复制和大块字节拼接。完整解码、原图像素、短边248、BICUBIC、中心224裁剪、归一化和 EXIF 处理保持原规则。
- 模型包核对仍执行每次内存 SHA、文件清单、文件属性和逐级路径／符号链接检查；缓存的是固定路径字符串，不缓存文件校验结论，不跳过图后 INFO。
- [SHA 微基准](../tools/latency_diagnostics/hash_bench.cpp)比较现有软件 SHA 和板上已有 OpenSSL 实现，完整读取同一份静态权重并核对摘要。该工具不读取 BO，不替换 Native owner，也不改变计算核。

完整请求中位数为 2618.001 → 2403.094 ms，板端处理为 2083.148 → 1847.906 ms，原图预处理为 1048.193 → 913.121 ms。首张包含初始化，为 6537.586 → 7244.757 ms，未改善。优化前后十二次请求的输入张量和全部输出逐字节一致，CPU／PL 共比较12000个FP32输出，逐位不匹配为0。

细粒度日志的持久化语义、CPU 参考和 INFO 保持原样。Native SHA 微基准只提供诊断依据，Native 哈希实现没有替换。网页查询间隔为100 ms，与完整请求实测相同；只读取原请求，不重发计算。网页显示的往返时间还包括客户端摘要等开销，与板端 `request_work_ms` 分别标明。

[Python 安装工具](../board/service/install_python_update.py)只接受固定原服务身份和已通过的等价性验证，在正常关闭服务、确认 owner 已退出后更新两份 Python 文件及安装身份记录。原始文件与回执备份在板端 `/var/lib/opt50-demo-python-update-20261101`。服务禁止同一启动周期重新进入，切换通过正常重启生效；无需重新综合或生成位流。

## 复现

Python 工具在现有板端环境运行，使用 `/opt/opt50-demo-v1/private` 中的固定 `dataset` 和 `model_runtime`。读取受保护的服务代码与模型需要 root 权限；工具不调用 XRT 或 FPGA。将脚本放在独立可写目录，将原始 JPEG 放入该目录的 `images/`，不要覆盖验收快照或服务目录。

```text
sudo /home/ubuntu/fpga_project/model_b/.venv/bin/python -B validate_candidates.py
sudo /home/ubuntu/fpga_project/model_b/.venv/bin/python -B profile_preprocess.py ./images ./preprocessing.json
sudo /home/ubuntu/fpga_project/model_b/.venv/bin/python -B profile_checks.py
```

`validate_candidates.py`对原图及灰度、CMYK、渐进式 JPEG、EXIF 和非正常输入进行逐字节对照，并比较正常及篡改模型包的接受／拒绝行为。计时交替运行原实现与候选，避免将 profiler 开销当作原实现成本。`profile_checks.py`的 `cProfile` 计时仅用于定位函数开销，不能替代未插桩基准。

编译 SHA 工具时使用安装目录的原始头文件和板上已有编译器、OpenSSL，不安装或升级系统包：

```text
sudo g++-13 -O2 -Wall -Wextra -Werror -std=c++17 -I /opt/opt50-demo-v1/private/source -I /opt/opt50-demo-v1/private/source/headers hash_bench.cpp -l:libcrypto.so.3 -o hash_bench
sudo taskset -c 0 ./hash_bench /var/lib/opt50-demo-v1/model/pl_w_data.bin /var/lib/opt50-demo-v1/model/pl_w_meta.bin
```

系统调用跟踪会扰动线程调度与模型耗时；带 `strace` 的真实请求只用于定位阻塞，不纳入优化前后的速度比较。
