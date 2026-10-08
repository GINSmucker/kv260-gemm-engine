# v5 与 A/B 当前开发接续

10月9日当前开发基线是AB1 OPT38+最小v5，正式父版system_a3_20261005_02和历史Gate保留。已完成v5 R1～R3、两次R4及AB1；AB2/3软件通过，但首个组合PL诊断FAIL，不能将这些delta作为默认已采用版本。

## 实测和取舍

v5/35a生产20图core p50=888.360855ms、范围887.289～889.632ms。独立三图诊断软件扣wait241.448960、C扣owner31.579796、B记录/回执0ms，达到600/100/30目标。对F05父版的两次R4仍INCONCLUSIVE_DRIFT；第二窗父COMMIT整区间约497.849ms导致34.270%逐图漂移，不把其全称fsync或删除慢样本。

AB1使用同一v5/B数值链，仅绑已有38 UUID/tags。20图35a before→38→35a after core中位887.999602→438.384590→888.105542ms；父最大漂移3.791476%、CPU3.610684%，logits逐位一致，较较快父版下降50.632344%。三图诊断wait646.406→197.531ms，非wait仍约241.7ms。三窗3823starts，其中候选148starts/五shape/8285456全INT64和实际客户端退出/QUERY/拒绝重复RUN通过，root静止/starter/UI回退PASS。开发硬件采用38，未签正式PL/CPU加速比、p95/质量/长稳/UI/发布。

## 当前代码与资产

正式父版见BASELINE_LOCK；当前开发组合见[DEVELOPMENT_LOCK](../research/DEVELOPMENT_LOCK.json)。旧B989、driver cba…及历史资产不复制；新增OPT38配对BIN/DTBO/metadata之前未随基础35a运行包发布，故本次提供必要38平台小包。

```powershell
python -B tools/assets.py v5-source --download --extract
python -B tools/assets.py v5-runtime --download --extract
python -B tools/assets.py opt38-platform --download --extract
python -B tools/build_runtime.py --variant v5-ab1 --uuid-include <existing-uuid-SDK>
```

最后一条仅预览/准备；`--execute`限AArch64 Linux已有g++13/XRT/uuid/libcrypto环境，只编译，不加载或启动owner。源码包根目录有owner/kernel/client/protocol/B/NEON、runtime/CLI、固定ORT头和BUILD_RECIPE。当前production与diagnostic运行文件分目录，生产不导出计时/数组。软件初始模型SHA一次、请求定长/编号和真正硬件完成保留；新二进制重新资格验证，不能继承本次旧签署。

有维护者已验证owner时，使用源码包产品入口：`python cli.py run --bundle <B989> --plugin <production-libquant_v5.so> --socket <owner.sock> --input <public-image.npy> --run-id <increasing-id>`。Runner加载一次，可连续run并close；模型权重文件和metadata与开发锁由维护者固定，当前UI仍CPU模式。下载不是PL加载授权。

v5契约为32B小端header、HELLO一次、BEGIN+49RUN+END；无ACK/热SHA/逐包proc/持久账本/native JSON，一计算连接加一管理连接。完整下一RUN消费前一Y，只保留最近Y；重复RUN拒绝，丢回复仅QUERY最近Y，不重放未知执行。半帧不阻塞管理；STOP和信号延至DMA完成，不强杀未知owner/BO。没有F05持久COMMIT重开保证，不混用ABI2/3/4。

## AB2/3和正在推进的边界

AB2首版多折叠48权重Transpose，image00误差3.814697e-6，逐位Gate FAIL，原结果保留。修正版排除Transpose，PL696→323、语义1382→1009、FP32792→419，373常量输出及FP32/语义各20图逐位通过。Session工作区19559204→757268B是容量节省，不是每图分配量或已测时延收益。

AB3行内NEON汇总非有限标记，行尾归约一次，保留absmax/除法/RNE/两乘加bias；去不可达tail、缓存固定shape。fegetround保留，不造线程状态框架。AB2 166/AB3 173文件ARM软件回收PASS；源码包candidates只有对应delta，默认不编译它们。

首组合板窗v5_ab23_screen_20261009_01已回收41文件、status FAIL：benchmark第0层Y逐位比较失败；rollback_errors为空、recovery PASS。仅凭该断言不能确认根因是fold、数据传播或硬件，不将软件PASS扩大为PL采用；不发布新折叠ONNX或AB2/3二进制为默认资产。进一步定位属于新工作包，本上传不重跑板卡。

AB4需真实预算后新HLS/RTL/配对，AB5 probe尚无本机完整回执，AB6组合两公平窗未运行。新任务不因目录存在而记PASS；本次以已回收receipt为截止，后续运行需新的同步。原A/B静态审查、旧profile和循环/内存估算只用于排序，不拼成新性能。

最新证据见[进度汇总](../evidence/latest/runtime_progress_20261009.json)。原实验日期保留；本轮上传没有板卡、HLS、RTL或ARM新实验。
