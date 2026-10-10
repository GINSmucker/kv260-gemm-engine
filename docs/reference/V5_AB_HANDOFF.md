# v5 与 A/B 当前开发接续

默认开发运行包为AB1 OPT38+最小v5，正式父版system_a3_20261005_02和历史Gate保留。最新回执覆盖AB2/3单因素、线程profile、AB5和AB6两次组合；源码候选已进一步完成4线程整图资格，不以首FAIL覆盖后续结果。

## 实测和取舍

v5/35a生产20图core p50=888.360855ms、范围887.289～889.632ms。独立三图诊断软件扣wait241.448960、C扣owner31.579796、B记录/回执0ms，达到600/100/30目标。对F05父版的两次R4仍INCONCLUSIVE_DRIFT；第二窗父COMMIT整区间约497.849ms导致34.270%逐图漂移，不把其全称fsync或删除慢样本。

AB1使用同一v5/B数值链，仅绑已有38 UUID/tags。20图35a before→38→35a after core中位887.999602→438.384590→888.105542ms；父最大漂移3.791476%、CPU3.610684%，logits逐位一致，较较快父版下降50.632344%。三图诊断wait646.406→197.531ms，非wait仍约241.7ms。三窗3823starts，其中候选148starts/五shape/8285456全INT64和实际客户端退出/QUERY/拒绝重复RUN通过，root静止/starter/UI回退PASS。开发硬件采用38，未签正式PL/CPU加速比、p95/质量/长稳/UI/发布。

## 当前代码与资产

正式父版见BASELINE_LOCK；当前开发组合见[DEVELOPMENT_LOCK](../locks/DEVELOPMENT_LOCK.json)。旧B989、driver cba…及历史资产不复制；新增OPT38配对BIN/DTBO/metadata之前未随基础35a运行包发布，故本次提供必要38平台小包。

```powershell
python -B docs/tools/assets.py v5-source --download --extract
python -B docs/tools/assets.py v5-runtime --download --extract
python -B docs/tools/assets.py opt38-platform --download --extract
python -B board/runtime/tools/build_runtime.py --variant v5-ab1 --uuid-include <existing-uuid-SDK>
```

最后一条仅预览/准备；`--execute`限AArch64 Linux已有g++13/XRT/uuid/libcrypto环境，只编译，不加载或启动owner。源码包根目录有owner/kernel/client/protocol/B/NEON、runtime/CLI、固定ORT头和BUILD_RECIPE。当前production与diagnostic运行文件分目录，生产不导出计时/数组。软件初始模型SHA一次、请求定长/编号和真正硬件完成保留；新二进制重新资格验证，不能继承本次旧签署。

有维护者已验证owner时，使用源码包产品入口：`python cli.py run --bundle <B989> --plugin <production-libquant_v5.so> --socket <owner.sock> --input <public-image.npy> --run-id <increasing-id>`。Runner加载一次，可连续run并close；模型权重文件和metadata与开发锁由维护者固定，当前UI仍CPU模式。下载不是PL加载授权。

v5契约为32B小端header、HELLO一次、BEGIN+49RUN+END；无ACK/热SHA/逐包proc/持久账本/native JSON，一计算连接加一管理连接。完整下一RUN消费前一Y，只保留最近Y；重复RUN拒绝，丢回复仅QUERY最近Y，不重放未知执行。半帧不阻塞管理；STOP和信号延至DMA完成，不强杀未知owner/BO。没有F05持久COMMIT重开保证，不混用ABI2/3/4。

## AB2/3和正在推进的边界

AB2首版多折叠48权重Transpose，image00误差3.814697e-6，逐位Gate FAIL，原结果保留。修正版排除Transpose，PL696→323、语义1382→1009、FP32792→419，373常量输出及FP32/语义各20图逐位通过。Session工作区19559204→757268B是容量节省，不是每图分配量或已测时延收益。

AB3行内NEON汇总非有限标记，行尾归约一次，保留absmax/除法/RNE/两乘加bias；去不可达tail、缓存固定shape。fegetround保留，不造线程状态框架。AB2 166/AB3 173文件ARM软件回收PASS；源码包candidates只有对应delta，默认不编译它们。

首组合窗v5_ab23_screen_20261009_01回收41文件、整体FAIL、恢复PASS。完整原B/fold/scratch/combined/AB3/原Bafter生产比较已完成且VALID；20图逐层等价PASS，core p50为438.365659/437.570146/438.131487/437.442855/430.013316/439.060602ms。失败发生于随后profile1第0层Y检查。独立profile02外层PASS_R2_BOARD、回退PASS，内部1/2线程PROFILE_NUMERIC_MISMATCH，三正式输入max_abs0.077726/0.157673/0.209252；4线程logits逐位PASS。保持ORT intra=4/inter=1、顺序执行、禁图优化/禁spinning；线程1/2计时不能作为可用收益。

最新AB2/3单因素生产阶段和20图逐层对照已完成；首窗口整体FAIL来自后续profile1第0层Y比对，不代表前面全部PL失败。独立profile窗1/2线程数值FAIL，4线程PASS。AB6两次20图同v5比较均VALID，候选p50=431.020901/430.067842ms，较较快父版下降1.678801%/1.860703%；logits逐位、CLI和回退PASS，各3332starts。第二窗FP32参考最大漂移5.101189%，整体INCONCLUSIVE，正式PL/CPU验收未通过；第一窗CPU speedup=0.763596，PL仍慢于FP32。AB5 exact-GELU probe有635540个逐位差异/5446656元素、max_abs4.768e-7，候选拒绝。源码delta和复建配方用于接续，已发布默认运行库保持AB1。

复建AB2/3时，在新源码工作目录覆盖candidates/ab3内b_plugin.cpp、quant_neon.h、runtime.py三项；其余共享输入SHA不变，实际ARM命令和折叠图SHA见源码包CANDIDATE_REBUILD.json。fold_graph.py --bundle/--out在新目录生成图，不覆盖B989；AB3 Runner支持graph参数。生产/诊断插件分别编译；候选库不替换默认v5-runtime。AB4尚无新内核实测，不凭旧预算推定收益。

最新证据见[进度汇总](../../results/evidence/latest/runtime_progress_20261009.json)。原实验日期保留；本轮上传没有板卡、HLS、RTL或ARM新实验。
