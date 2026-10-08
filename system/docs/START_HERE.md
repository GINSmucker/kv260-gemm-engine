# 零历史上下文接手步骤

先辨认两个项目：仓库根`src/`是原作者v0.2/4MAC通用GEMM；`system/`是后来完整模型系统。默认接手后者，保持原作者源码和报告。

1. 读根AGENTS、[STATUS](STATUS.md)、[BASELINE_LOCK](../BASELINE_LOCK.json)。复述当前父版、候选、未通过Gate及自己的工作方向。
2. 阅读对应领域的算法、接口、模型或优化文档。用[SOURCE_MAP](../SOURCE_MAP.json)定位实际编译源码，不从旧注释猜测最新状态。
3. 选定Python3.12环境。最小软件依赖为numpy1.26.4、onnxruntime1.22.1、Pillow12.3.0，见[ARM运行依赖](../model/b/requirements-arm.txt)。本机离线导出另有requirements-cpu/model文件。确认解释器身份后再建立项目环境；不使用未确认的Conda base。
4. 从`system/`运行下面的检查，再选择A/B/C/UI工作。输出在忽略的`build/`目录，失败保留日志。

```powershell
python -B tools/check.py
python -B tools/check.py --cpp --node
```

`--cpp`需要C++11编译器，可用`--cxx`指定。它运行8个公开完整层和8个非法shape，分别检查OPT35a/38；HLS pragma被普通编译器忽略，不能称RTL cosim。`--node`需要Node>=24，UI无npm依赖。默认检查需要已选定最小Python依赖。

按任务获取资产，不默认下载全部内容。私有仓库的下载使用已认证的GitHub CLI：

```powershell
python -B tools/assets.py board-runtime
python -B tools/assets.py ip-rebuild --download --extract
python -B tools/assets.py model --download --extract
```

第一个命令只显示身份和大小。`--download`显式获取，`--extract`拒绝覆盖已有目标和不安全成员。已有本地归档可用`--archive <file> --extract`。模型解包在`assets/model/B_MODEL_BUNDLE_V1`；完整989个payload保留原SHA校验。

模型成员可以先运行一个冻结输入的CPU接手回归：

```powershell
python -B tools/check.py --model-bundle assets/model/B_MODEL_BUNDLE_V1
```

这个检查仅覆盖一个同机输入的FP32/量化语义图，0 PL，不代替500/full质量或公平性能。模型运行支持已验证的Windows和AArch64 ARM环境；其他主机需单独建立数值资格。

注意：归档参考来自原电脑，换电脑后的相同Windows标签也不是同机。2026-10-08新电脑量化参考回归FAIL保留；同机CF1/v3候选等价另行通过，见[本机结果](G0_G1_LOCAL_20261008.md)。新开发分支的`check.py`默认验证独立`DEVELOPMENT_SOURCE.lock.json`覆盖，原发布锁保持不变；编辑后用`tools/dev.ps1 -RefreshSourceLock`显式更新。

项目环境就绪后可从原生PowerShell7运行`./tools/dev.ps1 -Cpp -Node -RuntimeV3 -BV3`；`-Model`单独检查原归档数值资格，不放宽失败阈值。所有工具、二进制、模型和日志留在ignored目录。详见[本次重复入口](G0_G1_LOCAL_20261008.md)。

给新Codex的首条任务建议：

> 请先读AGENTS.md、system/docs/START_HERE.md、STATUS.md和BASELINE_LOCK.json，核验实际远端基线和最新证据。按系统依赖与实际瓶颈统筹FPGA、模型、运行时与UI；先完成本机检查，在独立分支实施可验证候选。未经当前维护者安排不使用共享板卡，保留所有未运行和失败状态。

当前最高优先级是[C运行时G0/G1](C_RUNTIME_REFACTOR.md)：不可变请求/结果、一次严格解析和准确计时。不是重跑CF1/RW06旧比较，也不是先增加MAC。其他成员可按[TEAM_WORKFLOW](TEAM_WORKFLOW.md)独立推进公开离线工作。
