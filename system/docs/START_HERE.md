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

给新Codex的首条任务建议：

> 请先读AGENTS.md、system/docs/START_HERE.md、STATUS.md和BASELINE_LOCK.json，说明实际采用父版、最新证据与我的工作边界。我的方向是A FPGA、B模型、C运行时或UI中的一个。先完成本机检查，提出具体单因素修改并在独立分支实施；未经当前维护者安排不使用共享板卡，保留所有未运行和失败状态。

当前G0～G3首版和F05 D0～D5已经完成，不再把“开始G0/G1”当下一步。下一边界是[F05 REQUEST非native约306.895ms](F05_HANDOFF.md)的真实细分，以及完整验证/ACK/B回执剩余工作；三预算未达标，G5仍关闭。其他成员按[TEAM_WORKFLOW](TEAM_WORKFLOW.md)独立推进公开离线工作。

需要最新研究实现时，显式下载两个小资产；原模型、IP和采用父版运行资产继续复用10月7日锁：

```powershell
python -B tools/assets.py f05-source --download --extract
python -B tools/assets.py f05-runtime --download --extract
```

源码在`assets/f05-source/f05-source`，正常ARM编译配方和公开测试均随包，编译入口见F05_HANDOFF。运行包为已资格验证但未采用的ABI4候选，不能与ABI2/3或旧ready混装。
