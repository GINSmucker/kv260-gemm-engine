# 多端协作与整合

各成员在独立`codex/`分支工作，共同基线为[BASELINE_LOCK](../BASELINE_LOCK.json)。通过Git/PR同步源码、状态和接口；共享KV260由当前维护者串行安排，不能多个Codex同时占板。

|方向|可独立推进|给集成方的交付|
|---|---|---|
|A FPGA|公开TB、HLS/RTL、bank/访存/微核研究|新source/contract/IP manifest、数值/非法请求/AXI/时序/资源和未运行项|
|B模型|CPU等价、PS/ORT定位、量化/反量化及scratch/trace组织|图/数值身份、CPU/INT64/错误路径、依赖与精确计时schema|
|C运行时|G0/G1不可变对象与真实C++软件链，之后驻留/记录适配|状态/恢复契约、source/binary/ABI、完整软件异常、板窗与回退结果|
|UI|capability/代理/前端/导出/取消，现有Node/Python测试|明确真实CPU/PL状态、输入和回执绑定、错误及可观测进度|

目前优先C G0/G1；A可离线为新软件下OPT38组合准备，B可以定位真实计时边界与等价 scratch/trace，UI跟踪新契约。硬件新DUT或数值变化需重新履行独立Gate；不得代填A/C签署或读取C私有留出。

每个PR应说明：基于哪个baseline SHA；单一机制与源码范围；对shape/寄存器/ABI/wire/数值/恢复的影响；实际执行的验证及NOT RUN；新source/binary/asset SHA；失败/不确定与回退方案。给出可以从此仓库和显式资产重建的命令，不依赖个人完整results树。

整合顺序为软件/公开数值→实际ARM身份→安全窄恢复→完整单图→预声明有效成本筛选→20图/正式S5→质量/稳定性/UI/发布。可明确复用逐字节不变模块的既有证明，新增或未测内容不能省略。

公共证据更新写短STATUS和优化表，历史长日志保存到独立审计归档。新实验必须有client日期、run_id、boot、source/binary/asset身份；HLS估计、loop II、top Interval、native wait、cycles和模型延迟分开。

原PACKAGE_MANIFEST/SOURCE_MAP仅描述冻结交付，不因开发修改而反写。当前分支用独立DEVELOPMENT_SOURCE.lock.json覆盖明确改动，绑定原manifest SHA；`tools/source_manifest.py`只记录Git非忽略源，`tools/check.py`严格核对对应字节。v3目前仅本机核心/B诊断候选，生产接线与独立验收仍是[后续依赖](G0_G1_LOCAL_20261008.md)。

当前协作Release仅是接手快照。main与正式发布由队友审核整合，本PR不自动合并；已有v0.1/v0.2、F8 PR和P1冻结身份保留。

轻量取得本分支：

```powershell
git clone --depth 1 --single-branch --branch codex/team-handoff-20261007 https://github.com/GINSmucker/kv260-gemm-engine.git
```

合并后使用main。需要历史审计时再显式fetch相应ref；大型模型与运行产物仍按ASSETS.lock显式下载。
