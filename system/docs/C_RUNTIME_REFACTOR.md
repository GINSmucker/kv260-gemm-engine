# C运行时下一阶段 G0至G5

本方案来自2026年10月7日CF1准确编译源码审阅和CHK05/CHK06结果。10月8日已有[独立v3本机G0/G1候选与冻结合同](G0_G1_LOCAL_20261008.md)：公开核心及B计时验证通过，**真实Linux生产适配未接线，G1整体PARTIAL**。默认父版不变，旧CF1/PS1/RW05/RW06的INCONCLUSIVE和SAX未准入不改签。

秒级成本来自逐层事务组织：B、bridge、owner、文件记录和可变协议对象之间反复解析、摘要、复制和读回；每层两次持久提交在串行关键路径中引入存储抖动。可信边界各有作用，不能通过删除guard、真实peer/文件/CU/AXI检查提速。

|阶段|具体工作|准入与产物|
|---|---|---|
|G0|冻结外部边界、对象所有权、状态/持久化表、正常时序和准确计时schema|逐入站header严格解析1次；内部私有不可变对象不重做序列化/SHA；各边界核对仍完整|
|G1|不可变Request/Result/验证凭据、单解析器、精确B准备/C ABI/B回执/trace计时|别名修改、跨任务/ID/generation、重入、坏帧/重复key/NUL/类型混淆、未知状态拒绝；真实C++软件fake-kernel成本不称PL收益|
|G2|驻留会话、任务级W私有快照、CommittedRecord、B独占scratch和有界trace|中断只QUERY/ACK原ID；重连完整认证；一个未ACK结果；首版98热fsync及完整读回/失败封存保留|
|G3|新ARM源/ABI/metadata/binary绑定、完整软件异常、三启动恢复、五shape/49层图|INT64全输出零差异、同机logits、49实际start、无fallback、完整guard/const/traffic及回退|
|G4|预声明一次父→新→父完整成本筛选|固定00/07/19、相同驻留/CPU控制/漂移门槛；冷启动、暖态、必要证据完成的端到端成本都计入|
|G5|在新软件自身身份下接入已独立A2的OPT38，再关闭整网预算|新平台tags/UUID/流量重新绑定；实际全图/恢复/公平性能，不能拼接旧观察|

具体接口：外部RawFrame/字节严格校验后转为私有不可变对象；ledger传受控引用和显式状态，不把可变JSON退回下游靠“validated”标记跳过检查。TrustedContext已经deep-copy/shared-const，不重复实施旧成果。admission用一个解析器与回调；正常REQUEST/ACK保持，先不加并发队列或去ACK。

W快照需要新的模型注册/文件变化检测契约，约49层5500416B加元数据预算明确。C任务缓存省PS文件处理，不省PL每start重装W。X接收一次拥有，Y保留到ACK；首轮不改共享内存/零拷贝BO映射或驱动。

B保持RNE、[-127,127]、两次FP32乘后加bias。预分配scratch按Kernel/会话独占，不跨并发请求共享；49条有界trace保留全部字段，必要导出计入完整端到端时间，失败不能静默丢弃。ORT无空转策略需新的匹配CPU控制，旧PS1结论不改签；首版不改系统频率/调度。

完整异常矩阵：SUB同步失败零start、COMP失败不交Y、start前后断连、RESULT/ACK丢失、短写/ENOSPC/日志损坏、W/metadata替换、假peer/PID复用、pending结果覆盖、Done未drain、deadline保留BO、重复/错ACK、STOP真实安全才成功。

新工作放在独立`v3`候选及对应B适配，不覆盖CF1或冒用ready。collector、恢复工具、UI capability跟随新ABI/wire契约。所有板窗由维护者固定清单、预算、锁及回退，不扩大到boot/复位。

首轮结构预算：同输入完整模型减host wait减实测记录同步的剩余软件量≤600ms；其中C ABI非core/非持久等待≤100ms/图，B回执/trace≤30ms/图。子项包含在600ms内，不重复相加。当前均NOT MEASURED；不是加速承诺，不能凭该预算放行正式性能。

若98屏障仍占≥500ms/图，单独设计持久化语义分支，不把旧RW05/06默认采用。改变持久粒度需独立恢复证明及公平比较。父漂移超限停止同一比较，不无限补样。最终p50/p95、质量、模型稳定性与回退保持原合同。

源码定位：[CF1 owner](../board/model_backend/v2b/owner.cpp)、[backend](../board/model_backend/v2b/backend.cpp)、[session](../board/model_backend/v2b/session.h)、[frame](../board/model_backend/v2b/protocol/stream_v2b_frame.h)、[ledger](../board/model_backend/v2b/protocol/stream_v2b_lifecycle.h)、[B插件](../research/v2b/model/b/custom_op/quant_linear_v2b.cpp)。它们是未改的研究祖先。新代码为[v3 Frame/Request/Result](../board/model_backend/v3/frame.h)、[ledger](../board/model_backend/v3/ledger.h)、[强制适配接口](../board/model_backend/v3/session.h)、[精确计时](../board/model_backend/v3/timing.h)及[新B诊断插件](../research/v3/custom_op/quant_linear_v3.cpp)，尚未形成生产owner或ready。
