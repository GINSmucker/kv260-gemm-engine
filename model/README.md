# B：模型、量化与CPU/PL调度

模型为DeiT Tiny、224输入、12 blocks、49个Linear和1000输出。冻结B模型的989payload由model资产及BASELINE_LOCK固定，研究交付使用相同Wq/sw/bias数值身份。

quantize.py与golden.py提供共享数学入口；b包含完整模型工具，v5为默认载荷型适配。研究共享适配在opt45-source的runtime中，匹配运行图/插件在opt45-runtime。CPU/PL配置必须显式选择。

## 数值与线程

RNE、[-127,127]/zp0；X逐行scale、W逐输出通道scale，完整INT8×INT8→INT32。反量化两次FP32乘再加bias，禁止fast-math/FMA改变已确认顺序。logits要求逐位一致。

研究ORT为intra4/inter1、顺序执行、spinning0、图优化关闭。Layout4直接写入三个[1,3,197,64]目标及反向输出布局；Erf/量化/反量化复用ORT线程池。当前图251节点，布局不增加浮点计算。

固定qkv/fc1/fc2在PL、proj/head在CPU，36次PL/图。B导入X/Y BO，直接量化/反量化；CPU整数结果使用151296B普通工作区。必要cache sync保留，无失败后fallback或自动调优。

## 公共测试与资产

cases/tensors.npz约194KiB，冻结公开head为M1/K192/N1000，含Xq/Wq/sx/sw/bias/Y与后处理参考；不含私有C留出。

```powershell
python -B model/golden.py --check model/cases/tensors.npz
python -B docs/tools/assets.py model --download --extract
python -B docs/tools/assets.py opt45-runtime --download --extract
python -B docs/tools/assets.py opt45-evidence --download --extract
```

模型根为build/assets/model/B_MODEL_BUNDLE_V1；研究包根为build/assets/opt45-runtime/opt45-runtime/AB_SHARED_OPT45_X128_Y768_LAYOUT4_FIXED_20261010。研究包含匹配251节点图、权重连续文件、库、Runner/CLI和metadata，33成员manifest固定。

单CPU输入的软件回归入口为docs/tools/check.py --model-bundle；Windows和ARM后端名前缀不同。正式PL500/full质量与独立长稳NOT RUN；20图逐位验证不代替这些范围。研究使用与设备前置条件见[board](../board/README.md)和[接口](../docs/interface.md)。
