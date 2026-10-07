# 优化结果与剩余问题

采用父版仍为OPT35a/V2A。下面保留实验的实际资格，目标是让后续工作解决已知最大项，避免重复无效比较。

|结果|已知事实|决定|
|---|---|---|
|OPT35a生产短筛选|p50 6570.318084ms；CPU前/后约314.4/314.9ms，漂移有效|当前开发父版，最终性能不达标|
|OPT38旧V2A|中位6834.289→5800.598→6418.816ms；wait观察646.719→197.777ms|父漂移超限，INCONCLUSIVE，保持父版|
|CF1|完整功能图约3277ms；短比较候选p50 3237.564ms|父中位漂移18.862%，输入07漂移31.603%，不签收益、不采用、不扩样|
|CHK06生产同步|一条COMPLETED fsync 285.199832ms；三图扣同步余项2115.408～2121.748ms|仅解释本窗慢尾，不追认旧1秒慢层|
|RW05|文件机制32项通过，成本父漂移20.884%，CPU增加约54.900ms|生产关闭|
|SAX|7388项差分通过；441次解析代理省24.970ms/18.2%|有效但未达预声明准入门槛，关闭|
|RW06|34项通过；观察wall691.066→310.100→646.086ms|父均值漂移6.509%，部分组43～51%；INCONCLUSIVE，不接生产|
|CHK05 native|52start/4238288完整INT64零差异，诊断图3280.125216ms|诊断专用，净观察开销未量化，不签性能|

最新CHK05原互斥账本精确闭合：API内native外2250.468097、host wait646.051318、copy/sync18.216809、native其他75.544514、量化/反量化61.404203、PS其他228.440275ms。API标签过宽，含B准备和回执摘要；应重分精确B/C边界。

native新观测共5385 poll、5336次50µs sleep；poll/drain wall645.897957ms、当前线程CPU102.138738ms。两者之差不是纯sleep或纯硬件；cycles仍不可用。49层MMIO仅1.444793ms，Y摘要30.368161ms、guards/const17.916151ms，优先重写MMIO没有秒级预算依据。

CF1正常路径静态审阅确认：owner admission和FrameReceiver重复解析，同一私有请求/结果多次校验/序列化/摘要，每层重复读取固定W，正常2RPC/98连接与98热fsync；开启trace时Y静态8次全量SHA。这些是准确快照的代码推导，不冒充实测调用数。未发现C每层创建大量业务线程或成功路径固定等待数秒。

下一步按[C重构G0/G1](C_RUNTIME_REFACTOR.md)建立私有不可变对象、单次严格解析、分层wall/线程CPU和端到端计时。首版两次持久提交与原恢复检查保留；改变持久化粒度需要独立契约。之后才做驻留连接、W任务快照、B scratch/trace适配及新软件下OPT38组合。

当前约646ms host wait已超过旧公平FP32约313ms，单独C优化也不保证达标。不能把旧OPT38 wait、旧B3量化和新C成本相加，形成虚拟模型速度。

仍开放：模型BO高地址、cycles、属性生命周期、永久AXI卡死硬件恢复、掉电/owner崩溃、并发热拔插、长期切换、新组合PL质量/稳定性/UI/正式发布。原系统驱动保持；已通过的有限窗口回退只支持对应身份与范围。

正式目标保持同板同窗PL完整模型p50小于公平ARM FP32、p95不高于ARM，再进入PL质量和模型稳定性。三图筛选不能签p95，诊断构建不能进入性能窗口，父漂移超限保留INCONCLUSIVE。

原始入口：[native账本](../evidence/latest/native_accounting.json)、[RW06](../evidence/latest/RW06_decision.json)、[CF1](../evidence/cf1/comparison.json)、[同步账本](../evidence/diagnostic/sync_accounting.json)、[源码审阅](../evidence/latest/C_runtime_audit.json)。
