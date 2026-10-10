# 团队与Agent工作规则

- A维护hls/；B维护model/；C维护board/；docs/和results/共同维护。
- 首读README、docs/progress.md、docs/verification.md和docs/workflow.md。docs/interface.md是唯一当前公共接口文档；接口变更同时更新机器契约和受影响消费者。
- 主线职责骨架来自f5ead7a；已上传资料来自0b2ee9a。数值/协议不因目录迁移改变；原路径和SHA见docs/SOURCE_MAP.json及docs/MIGRATION_MAP.json。
- 正式身份docs/locks/BASELINE_LOCK.json为OPT35a/V2A；已交付开发组合docs/locks/DEVELOPMENT_LOCK.json为AB1 OPT38/v5。区别旧正式父版、当前开发组合、未采用候选与历史实验。
- 从根目录用选定Python3.12运行python -B docs/tools/check.py，按需加--cpp/--node/--model-bundle。它是本机软件检查，不是HLS/RTL/ARM/PL验收。
- Windows使用PowerShell7；Linux/AArch64运行时构建必须明确使用已有工具链。先核对可执行程序身份，不自动改PATH、安装工具、依赖或系统配置。
- 保持冻结资产锁、P1标签、签署与历史FAIL原字节。新源码/二进制/平台用新身份重新资格验证，不能借旧PASS放行。
- Gate记录PASS/FAIL/NOT RUN；比较状态可记录INCONCLUSIVE_DRIFT/PARTIAL等原实际结果，不能强改为PASS。区分top Interval、loop II、HLS估计、kernel cycles、owner wait和整网时间。
- 每个性能值注明源码/工具/时钟/shape/测量范围/证据和父版；不跨身份减旧数字，不相加p50，不从诊断或无效比较宣称加速。
- 保留独立C留出边界：不读取或上传私有种子/向量，不代填独立签署。公开图片数据仅用于固定校准、选择与回归。
- 共享KV260先由当前维护者分配，仓库不转移旧聊天授权。执行前固定清单/预算/锁/占用/Gate/回退；UNKNOWN保留owner/BO，不重发已提交请求、不强杀或自动回退CPU。
- 证明CU/AXI静止再停止/卸载；boot/QSPI/reboot/reset/系统安装须另获具体授权。不在KV260安装或运行Codex。
- 功能工作在codex/<方向>-<主题>分支，通过PR评审；不force-push、不移动旧tag、不自动合并main。
- 重要节点更新docs/progress.md、docs/verification.md和results/summary.csv；完整模型研究背景见docs/reference/，历史回执见results/evidence/。新测试不覆盖旧报告。
- 不提交工作缓存、大原始数据、SDK/环境或长日志。生成输出在build/或results/runs/；资产通过Release显式取得，当前Git内容目标5MiB以内，新增文件按实际main基线控制250以内。
- 目录迁移仅改路径的模块也应复测依赖、调用和身份，保留原SHA与旧tag来源。保留不相关队友改动；清理与恢复按主工程维护规则，不能为数量目标删除受保护材料。
