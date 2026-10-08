# 多端协作与整合

各成员在独立`codex/`分支工作，共同基线为[BASELINE_LOCK](../BASELINE_LOCK.json)。通过Git/PR同步源码、状态和接口；共享KV260由当前维护者串行安排，不能多个Codex同时占板。

|方向|可独立推进|给集成方的交付|
|---|---|---|
|A FPGA|公开TB、HLS/RTL、bank/访存/微核研究|新source/contract/IP manifest、数值/非法请求/AXI/时序/资源和未运行项|
|B模型|CPU等价、PS/ORT定位、量化/反量化及scratch/trace组织|图/数值身份、CPU/INT64/错误路径、依赖与精确计时schema|
|C运行时|G0/G1不可变对象与真实C++软件链，之后驻留/记录适配|状态/恢复契约、source/binary/ABI、完整软件异常、板窗与回退结果|
|UI|capability/代理/前端/导出/取消，现有Node/Python测试|明确真实CPU/PL状态、输入和回执绑定、错误及可观测进度|

目前最小v5和AB1已完成，开发硬件采用38；先定位AB2/3首组合PL诊断失败，再评估常量图/会话scratch/量化精简。AB4新A内核才需要新HLS/RTL/配对平台；AB5需真正热点和等价证据。UI未发布v5 ready，不能沿用ABI4。正式质量/稳定性/PL对CPU与发布仍需独立Gate；不得代填A/C签署或读取C私有留出。

每个PR应说明：基于哪个baseline SHA；单一机制与源码范围；对shape/寄存器/ABI/wire/数值/恢复的影响；实际执行的验证及NOT RUN；新source/binary/asset SHA；失败/不确定与回退方案。给出可以从此仓库和显式资产重建的命令，不依赖个人完整results树。

整合顺序为软件/公开数值→实际ARM身份→安全窄恢复→完整单图→预声明有效成本筛选→20图/正式S5→质量/稳定性/UI/发布。可明确复用逐字节不变模块的既有证明，新增或未测内容不能省略。

公共证据更新写短STATUS和优化表，历史长日志保存到独立审计归档。新实验必须有client日期、run_id、boot、source/binary/asset身份；HLS估计、loop II、top Interval、native wait、cycles和模型延迟分开。

当前协作Release仅是接手快照。main与正式发布由队友审核整合，本PR不自动合并；已有v0.1/v0.2、F8 PR和P1冻结身份保留。

轻量取得本分支：

```powershell
git clone --depth 1 --single-branch --branch codex/team-handoff-20261007 https://github.com/GINSmucker/kv260-gemm-engine.git
```

合并后使用main。需要历史审计时再显式fetch相应ref；大型模型与运行产物仍按ASSETS.lock显式下载。
