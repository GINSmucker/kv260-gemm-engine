# 快速开始

本页首先支持**无硬件的源码／证据复核和精确运行包还原**。板端快照保留原环境及历史门禁，不能把clone成功或文件PASS当成新设备可以直接START。

已有下载资产时，可将下述核对和还原流程集中执行到一个新目录：

```text
python -B tools/rehearse_submission.py --assets /path/to/downloads --out .test_build/submission-rehearsal
```

资产目录须包含Sparse24、Dense36、layer02三个payload和paired500证据归档；输出父目录须存在，输出目录须尚不存在。脚本保留各检查日志和`result.json`，不会访问板卡。可通过`--compiler`指定已有、支持C++17及`<filesystem>`的编译器执行模拟后端自测；未提供时该项单列为`NOT RUN`。Vitis HLS 2020.2自带GCC 6.2不满足此要求。

最终成果页使用`pwsh -File ./start-final.ps1`启动，默认地址http://127.0.0.1:4179/；见[网页使用指南](workbench.md)。

## 1. 检查源码与统计

使用现有Python3.12，无需安装XRT或访问设备：

```text
python -B tools/check_release.py
python -B tools/check_docs.py
python -B -m unittest discover -s tests -v
```

这些检查核对源码字节、目录、文档及195张统计，不重新运行模型。

## 2. 下载匹配资产

[最终Release](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/opt50-sparse24-final-20261030)提供以下下载；先按[资产清单](../assets_manifest.json)核对SHA256：

| 下载 | 用途 |
|---|---|
| [sparse24-payload.tar.gz](https://github.com/GINSmucker/kv260-gemm-engine/releases/download/opt50-sparse24-final-20261030/sparse24-payload.tar.gz) | 24层剪枝模型及运行包缺省载荷 |
| [dense36-payload.tar.gz](https://github.com/GINSmucker/kv260-gemm-engine/releases/download/opt50-sparse24-final-20261030/dense36-payload.tar.gz) | 原始Dense对照模型及载荷 |
| [layer02-payload.tar.gz](https://github.com/GINSmucker/kv260-gemm-engine/releases/download/opt50-sparse24-final-20261030/layer02-payload.tar.gz) | 同权重单层Dense／Sparse数据 |
| [fd96539d-platform.tar.gz](https://github.com/GINSmucker/kv260-gemm-engine/releases/download/opt50-sparse24-final-20261030/fd96539d-platform.tar.gz) | 已验收平台固件、metadata及配对产物 |
| [paired500-evidence.tar.gz](https://github.com/GINSmucker/kv260-gemm-engine/releases/download/opt50-sparse24-final-20261030/paired500-evidence.tar.gz) | 500张完整原始CPU／PL证明 |
| [B原始交付ZIP](https://github.com/GINSmucker/kv260-gemm-engine/releases/download/opt50-sparse24-final-20261030/B_FINAL_H576_24L_100K_20261026_v1.zip) | 原B模型、训练配方和来源材料 |

仅浏览[C的H576网站](workbench.md)时无需下载这些资产。网站和当前Sparse24图片验收保持各自数据来源。

按 [资产清单](../assets_manifest.json) 取得匹配Release文件。Sparse和Dense分别使用`sparse24-payload.tar.gz`、`dense36-payload.tar.gz`；单层case使用`layer02-payload.tar.gz`；完整证据为`paired500-evidence.tar.gz`。资产在Git之外，不能用其他模型／随机权重替代。

先建立新的`.release`输出父目录，再还原选择的原始包，例如：

```text
python -c "from pathlib import Path; Path('.release').mkdir(exist_ok=True)"
python -B tools/restore_bundle.py --snapshot sparse24 --asset /path/to/sparse24-payload.tar.gz --out .release/sparse24
python -B .release/sparse24/verify_tools.py
```

`/path/to/...`替换为实际下载文件。还原工具先核对资产SHA及完整成员清单，只创建新的输出目录。原源码清单和模型清单保持原字节，不改pins。

可用现有g++对还原包执行两项无设备C++自测，例如Windows Vitis自带工具：

```text
python -c "from pathlib import Path; Path('.test_build').mkdir(exist_ok=True)"
python -B tools/run_runtime_selftests.py --bundle .release/sparse24 --compiler D:/Vivado/2026.1/Vitis/tps/mingw/10.0.0/win64.o/nt/bin/g++.exe --out .test_build/sparse24
```

它检查49层实际权重打包与有界owner协议，编译／运行模拟后端，不编译ARM也不打开设备。其他平台应提供已有的匹配C++17编译器；当前新目录实际执行状态见检查记录。

## 3. 独立复核已保存输出

将`paired500-evidence.tar.gz`在新的本地报告目录保存为`evidence.tar.gz`，并从归档的`report/`取得`summary.json`和`per_image_comparison.csv`。

```text
python -B tools/verify_pair_archive.py /path/to/report-directory
```

它固定检查板端成功日志的归档SHA，复核两模型500张全部保存logits、Top5及主性能统计；会在输出目录新建`local_independent_review.json`，不访问板卡。

## 4. 受检ARM环境

原环境为Ubuntu24.04.2/aarch64、kernel6.8.0-1015-xilinx、匹配zocl/XRT2.18、g++13、XRT/UUID开发头和库，以及NumPy1.26.4/ORT1.22.1。模型、固件、metadata和编译器均受身份校验。

完整还原包中的`build_arm.py`负责新建ARM编译输出和两个CPU自测，不执行owner。Sparse r1可能通过原始来源证明复用已编译二进制；该路径需要原板卡资料。Dense36使用独立的模型容量和新编译收据。

## 5. 现场硬件运行

保留独立UART日志，核对当前boot、驻留固件、驱动与持锁guardian；建立当前启动的新计划，通过layer02再进入有界完整模型验收。每图36个PL调用，每批最多50张、900秒窗口，无自动重试或续期。

**本页不提供沿用2026-10-30旧plan/token的启动命令。** 这些只属于历史证据。新环境或历史资料不全时保持NOT RUN，不去掉门禁强行执行。原始各包的README和实现见 [运行包索引](../board/runtime/README.md)。
