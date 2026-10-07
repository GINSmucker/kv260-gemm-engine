# 当前开发状态 2026年10月7日

客户端日期为Asia/Shanghai。实验日期来自原报告；板端旧日历和本次上传时间不替代实验日期。当前正式开发父版为`system_a3_20261005_02`，OPT35a/V2A/修正B标签插件。详见[BASELINE_LOCK](../BASELINE_LOCK.json)。

|对象|已证范围|当前限制|
|---|---|---|
|OPT35a引擎|独立1357层/94880648完整INT64输出零差异、精确流量、真实A/C A2、回退PASS|不是模型性能或发布PASS|
|父版生产路径|152例S2、断开恢复；完整单图49start/4085992输出、logits0/fallback0；有效短筛选|短测中位6570.318084ms，对FP32前/后314.414075/314.884949ms；正式S5未通过|
|OPT38|128MAC；完整本机/平台/独立A2/生产单图通过|旧V2A比较父漂移6.079240%，INCONCLUSIVE，未采用|
|V2B CF1|实际ARM构建、软件协议、三启动恢复、完整单图通过|唯一性能比较父中位漂移18.861686%、输入07漂移31.602520%；未采用|
|B3 Neon|逐bitCPU、正常ARM、恢复、完整PL单图通过|只有组合资格，未签整网收益或采用|
|RW06页容器|34项ARM文件机制/59文件PASS，0 PL|成本父均值漂移6.508776%、部分组超过43%；生产关闭，不扩样|
|CHK05 native分账|52start/4238288完整INT64输出零差异；构建、恢复、49层图与回退PASS|诊断专用，净观察开销未量化，无公平性能或生产采用|
|C重构|10月7日源码审阅和G0～G5计划完成|新结构NOT_IMPLEMENTED；下一步G0/G1|

最新CHK05诊断图`session.run=3280.125216ms`，互斥账本为原API内native外2250.468097、host wait646.051318、copy/sync18.216809、native其他75.544514、量化/反量化61.404203、PS其他228.440275ms。纯kernel cycles仍UNAVAILABLE。

该API余项包含B请求包装和回执摘要，不能全称纯C耗时。49层MMIO设置/回读仅1.444793ms；Y摘要30.368161ms、guard/const17.916151ms。下一步重构重复解析、摘要和对象传递，首版保留两次持久提交与完整安全检查，见[新计划](C_RUNTIME_REFACTOR.md)。

CPU模型Windows/ARM的500和full3925质量已通过；跨主机量化logits仍有历史FAIL。完整PL质量、模型稳定性、当前V2A/V2B的完整UI与两次公平S5/正式发布仍未通过。历史v1 S3/S4、F8稳定性不自动放行当前组合。

最近实板状态只引用CHK05原最终root：同boot`676c365c-d044-43c4-bb22-7ff71dd5cf85`，starter、无owner/设备用户/ZOCL/render、XRT0，CPU UI空闲；两窗OF0/严重错误0，各4条属性告警。本次交付不操作板卡，未重新连接核验。

当前模型BO高地址NOT_EXERCISED，kernel cycles不可用；永久AXI卡死硬件恢复、掉电、并发热拔插及长期切换未证明。原系统驱动保留；临时ZOCL v2须核对SHA和kernel后由维护者安排。

结果入口：[最新分账JSON](../evidence/latest/native_accounting.json)、[RW06决策](../evidence/latest/RW06_decision.json)、[父版短测](../evidence/parent/short_screen.json)、[CF1比较](../evidence/cf1/comparison.json)、[OPT38 A2](../evidence/opt38/A2_USABLE.json)。证据范围说明见[EVIDENCE](EVIDENCE.md)。
