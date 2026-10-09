# GitHub 分支与版本导航

**继续当前完整模型开发、让新 Codex 接手或同步进度，从 [codex/team-handoff-20261007](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/team-handoff-20261007) 的 `system/` 开始。** 日期是入口建立时的标识，10月8日和9日的更新都追加在同一分支。

本说明于2026-10-09（Asia/Shanghai）核对GitHub全部4个分支、3个开放PR和5个Release。中文用途名用于阅读，实际Git分支名和既有拉取地址保持一致；未推送的个人工作不在本说明内。

## 按任务选择分支

|实际分支|中文用途名|适合的工作与内容|验证边界 / PR|
|---|---|---|---|
|`main`|通用GEMM主线|原作者v0.2 Dense引擎，根`src/`/`tb/`；动态M/N/K、4路MAC。适合通用GEMM IP开发|当前完整模型交接尚在协作分支，main不等于最新整网版本；[README](https://github.com/GINSmucker/kv260-gemm-engine/blob/main/README.md)|
|`codex/team-handoff-20261007`|当前完整模型协作入口|`system/`覆盖算法、软件调度、模型/板卡、公开Gate/失败、身份锁与按需资产；新成员、A/B/C/UI接手选此分支|正式父版与开发候选分开；[PR #2，draft](https://github.com/GINSmucker/kv260-gemm-engine/pull/2)|
|`codex/f8-xrt-board-integration-20260928`|历史F8实板闭环|基于v0.1阶段，固定8×8 PS/PL/XRT、DDR BO和断开后恢复；用于审阅早期硬件闭环|9次/576输出、高DDR、恢复与starter回退PASS；该分支正式封存/性能benchmark未运行，非完整模型结果；[PR #1，open](https://github.com/GINSmucker/kv260-gemm-engine/pull/1)|
|`codex/runtime-g0-g1-20261007`|早期G0/G1运行时软件候选|基于10月7日交接，不变Frame/Request/Result、单次解析、B诊断计时和本机测试；用于独立评审机制|G1 PARTIAL、生产关闭；本机部分测试及同机数值通过，归档W8A8跨主机FAIL；Linux完整适配/ARM/真实PL未运行；[PR #3，draft](https://github.com/GINSmucker/kv260-gemm-engine/pull/3)|

历史分支的首读文件：

- F8：[交付报告](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/f8-xrt-board-integration-20260928/docs/F8_DELIVERY_20260928.md)、[接口](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/f8-xrt-board-integration-20260928/docs/F8_RUNTIME.md)、[复现](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/f8-xrt-board-integration-20260928/docs/F8_REPRODUCE.md)。
- G0/G1：[本机执行记录](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/runtime-g0-g1-20261007/system/docs/G0_G1_LOCAL_20261008.md)，包含PARTIAL、跨主机FAIL和NOT RUN项。

“主线”“当前入口”“历史闭环”“研究候选”描述用途。软件、实板功能、性能可比性、质量、正式发布各有自己的Gate；draft状态和名字中的日期不替代Gate。

## 当前完整模型接手顺序

1. [AGENTS](AGENTS.md)：团队工作规则。
2. [START_HERE](system/docs/START_HERE.md)：环境、零上下文接手与检查。
3. [STATUS](system/docs/STATUS.md)、[V5_AB_HANDOFF](system/docs/V5_AB_HANDOFF.md)：实际进度、失败、未运行项。
4. [BASELINE_LOCK](system/BASELINE_LOCK.json)：正式冻结父版`system_a3_20261005_02`，OPT35a/V2A。
5. [DEVELOPMENT_LOCK](system/research/DEVELOPMENT_LOCK.json)、[ASSETS.lock](system/ASSETS.lock.json)：默认已交付AB1 OPT38+v5与配对资产。
6. [TEAM_WORKFLOW](system/docs/TEAM_WORKFLOW.md)：A/B/C/UI分工、验证和整合。

截至10月9日已回收的交接：AB1同软件20图core p50=438.385ms，较较快35a父版下降50.632%；AB2/3两次AB6同v5比较有效，431.021/430.068ms、下降1.679%/1.861%。默认运行包固定AB1，后续候选delta/复建配方随源码资产提供。第二AB6的FP32参考漂移5.101%，正式PL/CPU加速验收未通过；线程1/2数值FAIL、GELU拒绝和历史失败保留。后续结果以STATUS和证据为准。

### 新接手者

```powershell
git clone --depth 1 --single-branch --branch codex/team-handoff-20261007 https://github.com/GINSmucker/kv260-gemm-engine.git
cd kv260-gemm-engine
```

在已有项目Python3.12依赖和GitHub权限的环境，从`system/`运行`python -B tools/check.py`。v5源码、运行文件、OPT38平台及旧模型按ASSETS.lock显式下载；克隆Git不代表取得了全部运行资产。

### 已有本地协作分支

先提交或保存自己的工作，再切换和快进；Git拒绝覆盖时保留现场。

```powershell
git switch codex/team-handoff-20261007
git pull --ff-only origin codex/team-handoff-20261007
```

此前只克隆main、尚无本地协作分支时，显式取得ref再建立跟踪：

```powershell
git fetch origin codex/team-handoff-20261007:refs/remotes/origin/codex/team-handoff-20261007
git switch --track -c codex/team-handoff-20261007 origin/codex/team-handoff-20261007
```

新工作从最新协作入口建立自己的`codex/`分支。旧runtime机制需要单独评审移植、重新绑定当前源码/接口与验证，不能只因分支日期较近就继承旧STATUS或资格。

## 分支依赖与整合

- PR #2：`codex/team-handoff-20261007 → main`，完整模型协作资料与后续整合入口，仍为draft。
- PR #1：`codex/f8-xrt-board-integration-20260928 → main`，独立历史F8交付。盘点时与main分叉，F8独有1提交、main后续2提交；保留原配对平台和v0.1接口。
- PR #3：`codex/runtime-g0-g1-20261007 → codex/team-handoff-20261007`，依赖PR #2、基于`eba1a78`。在盘点基准`a10b17b`/`062d2d9`之间，runtime独有2提交、缺6次后续交接；本文新提交会继续增加文档差距。它不是当前协作入口的更新分支。

关系来自GitHub Compare，实时差距需重新比较；ahead不代表性能更好。开放PR由维护者评审，本次只整理导航和用途，不合并或关闭PR。F8实板包按其自身接口复现，不能直接混入main的v0.2或模型OPT38平台。

## 固定标签和Release

|固定标签|中文说明|提交|使用方式|
|---|---|---|---|
|[v0.1.0](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/v0.1.0)|原Naive GEMM IP发布|`206b8ce`|复现原v0.1 IP；F8系统产物另行配对|
|[v0.2.0](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/v0.2.0)|原通用Dense第二周发布|`cd0870d`|复现main当前通用IP|
|[team-handoff-20261007](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-handoff-20261007)|首次完整模型接手快照|`eba1a78`|旧模型、IP、板端资产固定来源|
|[team-handoff-20261008](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-handoff-20261008)|F05运行时增量|`839e3b4`|F05源码/运行候选及当日Gate|
|[team-handoff-20261009](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-handoff-20261009)|v5/OPT38与AB进度|`a10b17b`|三个约971KiB新资产、AB2/3源码接续|

team-handoff三次Release均是协作预发布，资料/资产可取得不等于最终比赛发布通过。分支用于持续协作，固定tag复现当时状态。本文追加在协作分支，旧tag和资产保持固定。

## 后续命名

现有名称保持，按上表中文用途阅读。新分支采用`codex/<方向>-<具体改动>`，任务内容排在日期前：

|方向|命名示例|说明|
|---|---|---|
|A硬件|`codex/a-ddr-burst`|限定DDR burst机制|
|B模型|`codex/b-ps-profile`|定位PS/ORT实际热点|
|C运行时|`codex/c-wire5-recovery`|限定wire5错误/恢复契约|
|UI|`codex/ui-model-status`|真实模型状态与交互|
|资料|`codex/docs-branch-guide`|单独文档任务|

例子不表示已创建分支或获准实验。长期协作入口保持稳定名字；日期主要放在run_id、tag和Release，独立实验确有需要再加日期。PR标题用中文说明用途，正文写父版、改动、真实验证、未运行项和整合目标。

新增分支、改变用途/PR base或固定快照时维护本文；实验结果仍写STATUS和证据，避免复制长日志。
