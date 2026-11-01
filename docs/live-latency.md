# 原始 JPEG 请求的耗时与等价优化验证

KV260 LAB 的 `Runner.run` 是完整 PS＋PL 模型计算时间。上传、图片预处理、模型包核对、CPU 参考、图后 INFO 和持久化在这一计时之外；板端 `request_work_ms` 从工作线程开始处理图片起计时，结束于结果 JSON 持久化之前，也不包含 HTTP 上传、接收阶段的图片落盘和网页查询等待。

## 测量边界与证据

[模型对照](../results/final_optimization/20261101/model_review.json)记录同板、同模型和第306–500张输入的原设置、运行后停止自旋、动态分块三臂验证。[完整请求对照](../results/final_optimization/20261101/complete_request_review.json)单独记录3000×4000原始JPEG的两轮原版／候选请求，每组包含一个新PL会话首请求及随后五次请求。查询间隔100ms；客户端边界为POST开始至查询到PASS，不包含选择文件和浏览器渲染。CPU参考已由启动自检初始化，首请求不能称为全系统冷启动。

原始吉他 JPEG 为 3000×4000。分段测量显示，完整解码、RGB 字节复制、RGB SHA 和双三次缩放都占用 CPU；不能将预处理总时间解释为空转。现有图后检查逐项同步四个 BO、验证保护区、重新计算静态权重摘要，并逐条持久化检查事件。SD 卡上的细粒度 `fsync` 存在明显长尾。

## 服务实现

[板端服务](../board/service/backend.py)使用[等价CPU实现](../board/service/cpu_optimizations.py)及原运行包的会话配置：

[当前服务身份](../results/service_rollback/20261101/status.json)绑定原配对安装回执、新boot及READY启动自检；展示入口为`http://127.0.0.1:4179/#overview`。

- 预处理复用已解码的 RGB 图像，按原始扫描行顺序分块更新 RGB SHA，避免完整 RGB 复制和大块字节拼接。完整解码、原图像素、短边248、BICUBIC、中心224裁剪、归一化和 EXIF 处理保持原规则。
- 模型包核对仍执行每次内存 SHA、文件清单、文件属性和逐级路径／符号链接检查；缓存的是固定路径字符串，不缓存文件校验结论，不跳过图后 INFO。
- CPU参考与PL会话均保留原4/1配置，关闭自旋。PL会话顺序执行、关闭图优化，主线程在CPU0，三个ORT工作线程在CPU1–3；不设置Run结束停止自旋或动态分块候选选项。
- 原Native owner、插件、模型、固件、36次PL调用、50图／900秒预算及不重放规则保持有效。每图仍运行CPU参考、完整INFO、保护区及静态权重检查，保留原文件与目录fsync。
- 原服务的`request_work_ms`结束于最终结果JSON持久化之前；客户端完整耗时还包含上传、接收落盘、结果发布及查询等待。候选中的新增细分字段不属于当前原服务回执。查询与板端处理重叠，不能相加。

自旋策略仅作为实验候选保留。相同195张的Runner中位数为133.741→122.591ms，均值134.039→123.261ms；P95为137.405→125.578ms。该记录每图每臂测一次，分五个39图组，初始化在Runner之外。进程CPU时间均值306.017→457.169ms，增加约49%；CPU时间不能与墙钟相加，也不是功耗测量。另一轮相同线程配置曾出现233.873ms调度长尾，触发进程尚未确定，不能承诺长期尾延迟上界。默认服务使用原设置；模型局部收益不足以证明完整请求收益。

OpenSSL静态权重摘要及并行RGB摘要不在默认路径启用。两轮完整请求的暖中位数分别为2509.035→2223.533ms和4357.898→2117.185ms，但原版重复中位波动1848.863ms，大于较小的一轮收益285.502ms；第二轮首请求8719.869→13015.827ms退化。必要持久化仍有秒级长尾，证据不足以将完整请求改善归因于这些候选。[原始CPU优化归档](../results/live_demo/python_service_20261101.json)、[分项归档](../results/live_demo/latency_diagnostics_20261101.json)与新对照分别保留，不替代当前配置成绩。

自旋候选的[六次吉他原图请求](../results/final_optimization/20261101/final_request_review.json)保留新PL会话首请求6600.362ms、随后五次中位2688.376ms、最大4810.402ms；全部6000个输出逐位差异0。这是该候选的描述性验证，不是当前原配置成绩或完整请求加速对照，也不用于抵消前述波动和冷请求回退。

[配对切换工具](../board/runtime/candidates/final_opt/install_trial.py)的`model-only`配置只改变Python会话策略及计时，保留原Native二进制和预处理。它要求正常停服务、设备用户为零、两个独占锁及完整配对备份；备份位于`/var/lib/opt50-final-20261101/original_service`。`original`模式恢复原配对。服务禁止同一boot重新进入，切换经正常重启生效。新现场不能照搬归档boot、job或token。

## 复现

以下CPU诊断工具在现有板端环境运行，使用 `/opt/opt50-demo-v1/private` 中的固定 `dataset` 和 `model_runtime`。读取受保护的服务代码与模型需要root；这些诊断不调用XRT或FPGA。将脚本放在独立目录，原始JPEG放在`images/`，不覆盖验收快照或服务目录。板端运行和配对切换的完整约束见[候选工具说明](../board/runtime/candidates/final_opt/README.md)。

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
