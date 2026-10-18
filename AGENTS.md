# 团队与Agent工作规则

- A维护hls，B维护model，C维护board；docs和results共同维护。
- 首读README、docs/interface.md、docs/progress.md与docs/verification.md。interface是唯一当前公共接口说明；机器契约和消费者必须一致。
- 当前交付为H576/compact12 epoch1 Dense W8A8，准确身份见docs/locks/H576_PACKAGE.lock.json和当前公开结果索引。模型、硬件、四线程及2000保留集冻结；不自动开启新实验。旧OPT38、未缩宽OPT45、A资源候选和BASELINE_LOCK保持各自用途，研究结果不转移正式资格。
- Windows使用PowerShell7。Linux/AArch64构建使用明确选定的现有SDK，不自动改PATH、环境、系统配置或安装工具。
- 使用选定Python3.12执行docs/tools/check.py；可加--cpp、--node及--research-source。普通软件结果不能标为HLS/RTL/ARM/PL验收。
- 冻结标签、资产、源码清单、独立签署和原始FAIL记录保持字节身份。新构建须记录新源/二进制/平台身份并重新验证。
- PASS/FAIL/NOT RUN及INCONCLUSIVE必须对应实际报告范围。top Interval、loop II、HLS估计、板频、kernel cycles、完整算子边界与整图时间分别报告。
- 性能值注明输入、线程、工具/时钟、测量边界和原始数据；所有预定样本保留，不跨不同身份相减或相加p50。
- 文档与注释只说明当前有效需求、设计、实现、用法及约束。就地维护已确认决策，不写Agent修改历史、临时阶段计划或方案演变。
- 实际行为与确认规范不一致时明确待核实项；未验证的行为不能变成规范。原始实验回执保留自身范围，不改签署字段。
- 不额外创建Agent过程报告；维护现有README/interface/progress/verification/workflow。原始测试数据与机器身份清单属于验证证据。
- 不读取或上传私有C留出、种子和向量，不代填独立签署。
- 板卡由当前维护者安排，仓库不转移其他会话的授权。执行前固定清单、预算、设备占用、身份、验证边界及回退。
- UNKNOWN时保留owner/BO，不重放已提交RUN、不强杀、不自动CPU fallback；证明CU/AXI静止后停止和卸载。
- boot/QSPI/reboot/reset及系统安装另获具体授权。不在KV260安装或运行Codex。
- 功能分支使用codex/<方向>-<主题>，通过PR评审；不force-push、不移动固定tag、不自动合并main，保留队友无关修改。
- 缓存、SDK、环境、大矩阵和长trace不入Git。按需Release取得运行/IP/数据资产，Git目标5MiB以内、相对main新增文件250以内。
