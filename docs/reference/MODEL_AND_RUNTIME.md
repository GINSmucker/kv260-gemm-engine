# 模型与软件调度

模型为`deit_tiny_patch16_224.fb_in1k`，ImageNet1k非distilled，224输入、12 blocks、49静态权重Linear、1000输出。PatchEmbed、24个attention MatMul和其他FP32算子在KV260 PS/ORT；49 Linear按完整层交给PL。电脑用于离线构建和UI，整网运行在板端。

冻结模型身份为B_MODEL_BUNDLE_V1，manifest SHA见[BASELINE_LOCK](../locks/BASELINE_LOCK.json)。[模型资产锁](../locks/ASSETS.lock.json)指向完整约139.55MiB归档。原989个payload必须保留；运行时[verify_bundle](../../model/b/runtime.py)逐个检查长度和SHA。不能手动删case或替换图后仍用原manifest。

包中包含FP32、W8A8语义和49 custom节点PL三张ONNX图，49组Wq/sw/bias、完整层case和20图同机参考、标签/预处理/数值协议及冻结结果。ONNX/opset和工具源也有身份。离线导出为ONNX1.17/opset18，运行ORT1.22.1；Python3.12、NumPy1.26.4、Pillow12.3.0固定。

量化W为per-output-channel、X为per-row，RNE，[-127,127]。Xq/Wq/Y按紧凑little-endian row-major；PL只做INT8×INT8→INT32归约。动态量化、FP32两乘后加bias的数值顺序在B插件；C不承担scale/bias。禁止fast-math/FMA改变已冻结语义。

默认源码来自实际使用的B0.2.0 wheel；V2A C++插件是实际修正后的平台tag版本。原wheel内旧tag C++只作为冻结构建参考，Release另有实际插件。源码路径映射和不同身份见[SOURCE_MAP](../SOURCE_MAP.json)。

默认ORT 4 intra/1 inter、sequential、graph optimization OFF。一次完整模型按49节点串行调用唯一owner；B插件和C分别处理数值/身份与设备生命周期。CPU/PL后端显式选择，缺桥接或身份不合即失败，无静默fallback。

CPU功能入口来自[原B CLI](../../model/b/cli.py)，V2A来自[versioned CLI](../../model/b/v2a/cli.py)。从仓库根目录执行CPU示例：

```powershell
python -B -m model.b.cli --help
python -B docs/tools/check.py --model-bundle build/assets/model/B_MODEL_BUNDLE_V1
```

ARM的实际V2A调用由维护者在已就绪窗口中指定全部身份文件：

```text
python3 -B -m model.b.v2a.cli --bundle <frozen-B-bundle> --tensor <public-input.npy> --backend arm_pl_v2a --plugin-v2a <matched-plugin.so> --c-ready <new-window-ready.json> --bridge <matched-bridge.so> --bridge-config <private-new-window-config.json> --output <new-result.json> --trace <new-49-layer-trace.jsonl>
```

这些窗口路径必须由本次owner/boot真实绑定产生，不能直接用Release里的historical_ready当配置。配置包含run/window和进程身份，保持板端私有；公开结果保留输入SHA、层回执、真实starts及logits绑定。

ARM构建由`board/runtime/tools/build_runtime.py`提供，无`--execute`仅准备命令。显式`--uuid-include`使用已经取得的SDK头，`--execute`仅在AArch64 Linux编译，不打开设备或加载驱动。输出新的source/binary manifest，bridge owner pin按实际重建绑定；新二进制不自动继承旧ready。

V2B研究B0.3.0在`model/research/v2b/model`，与默认B0.2.0隔离。它的`model.b`应在独立源根/环境导入，搭配ABI3/wire3的owner/bridge。B3仅保留[Neon头](../../model/research/b3/custom_op/quant_neon.h)和插件候选：AArch64逐bit成立，不能把它当Windows替代，也不能拼接旧观察时延。

最新F05 B0.6.0使用完整模型begin/commit、复用scratch、固定POD/trace与等价NEON。D3五图20429960输出、Xq/sx/Y/logits逐位一致，272 NEON/19拒绝；实际ABI4库与源码由两个F05资产取得，原B989模型不重新打包。运行候选仍未采用；构建入口显式`--variant f05`，不切换正式冻结V2A；当前开发默认AB1 v5。详见[F05_HANDOFF](F05_HANDOFF.md)。

10月9日当前开发是独立v5产品Runner(bundle, plugin, socket)和CLI；模型一次加载，输入run_id递增，完整失败不切CPU或重放。正常51RPC/图，无JSON回执/持久提交/逐层导出，诊断插件单独编译。AB1只将native UUID/tags绑到已有OPT38，其余B数值/协议不变。取v5-source、v5-runtime和opt38-platform，原B989不变；CLI和构建命令见[V5/AB](V5_AB_HANDOFF.md)。

最新AB2/3单因素生产阶段和20图逐层对照已完成；首窗口整体FAIL来自后续profile1第0层Y比对，不代表前面全部PL失败。独立profile窗1/2线程数值FAIL，4线程PASS。AB6两次20图同v5比较均VALID，候选p50=431.020901/430.067842ms，较较快父版下降1.678801%/1.860703%；logits逐位、CLI和回退PASS，各3332starts。第二窗FP32参考最大漂移5.101189%，整体INCONCLUSIVE，正式PL/CPU验收未通过；第一窗CPU speedup=0.763596，PL仍慢于FP32。AB5 exact-GELU probe有635540个逐位差异/5446656元素、max_abs4.768e-7，候选拒绝。源码delta和复建配方用于接续，已发布默认运行库保持AB1。 scratch19559204→757268B属于容量，非每图分配量。

Windows和ARM CPU的500/full3925质量已通过，跨主机量化logits历史FAIL保留。PL质量/公平性能/模型稳定性尚未通过；20图功能、500质量与正式性能使用各自协议，不用一图CPU smoke放行。

UI由Node本机工作台、SSH loopback转发和板端代理组成。浏览器入口4173，转发8741→板端8740。界面从实时capability判断可用性；当前V2A/V2B整合UI Gate未完成。历史v1/cacheable S4的通过不等于当前组合通过。启动方式见[UI说明](../../board/ui/README.md)。
