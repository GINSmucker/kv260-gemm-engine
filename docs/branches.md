# GitHub 分支与版本导航

核对日期2026-10-10。当前完整模型协作入口是 [codex/team-handoff-20261007](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/team-handoff-20261007)，直接从仓库根目录开始。日期标识入口建立时间，后续进度继续增量追加。结构迁移与队友已合并PR #4的main整合，等待[PR #2](https://github.com/GINSmucker/kv260-gemm-engine/pull/2)评审。

## 按任务选择

|实际分支|中文用途名|内容与边界|
|---|---|---|
|`main`|已合并OPT38职责骨架|f5ead7a / PR #4，hls/model/board/docs/results根结构；OPT38 Vitis2026.1 C-sim为队友报告PASS，2026.1新综合/RTL/板端NOT RUN|
|`codex/team-handoff-20261007`|当前完整模型协作入口|在main骨架中整合完整B模型、真实AB1 v5运行时、数据入口、共享接口/状态/公开证据；[PR #2 draft](https://github.com/GINSmucker/kv260-gemm-engine/pull/2)，新成员从此接手|
|`chore/repository-layout`|已合并的目录整理分支|943d25f，队友[PR #4 merged](https://github.com/GINSmucker/kv260-gemm-engine/pull/4)；保留其用途，持续开发从当前协作入口建立新分支|
|`codex/f8-xrt-board-integration-20260928`|历史F8实板闭环|[PR #1 open](https://github.com/GINSmucker/kv260-gemm-engine/pull/1)；固定8×8 PS/PL/XRT、9次/576输出及恢复；原正式封存/性能benchmark NOT RUN，非完整模型|
|`codex/runtime-g0-g1-20261007`|早期G0/G1软件研究|[PR #3 draft](https://github.com/GINSmucker/kv260-gemm-engine/pull/3)，base为协作分支；不可变对象/单次解析/B计时，G1 PARTIAL、生产关闭；本机通过部分与跨主机FAIL并存，ARM/PL NOT RUN|

旧分支代码与接口有自己的身份，不能把旧日期、ahead数或名称视为当前Gate。原F8首读[交付报告](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/f8-xrt-board-integration-20260928/docs/F8_DELIVERY_20260928.md)。旧G0/G1首读[本机记录](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/runtime-g0-g1-20261007/system/docs/G0_G1_LOCAL_20261008.md)，旧路径仍属于那个分支。

## 零上下文接手

1. [AGENTS](../AGENTS.md)与[README](../README.md)说明职责和边界。
2. [接口](interface.md)、[进度](progress.md)、[验证](verification.md)是共享入口。
3. A读[hls](../hls/README.md)，B读[model](../model/README.md)，C读[board](../board/README.md)。
4. [正式父版锁](locks/BASELINE_LOCK.json)、[开发锁](locks/DEVELOPMENT_LOCK.json)与[资产锁](locks/ASSETS.lock.json)分别固定资格与文件身份。
5. [工作流](workflow.md)、[接手细节](reference/START_HERE.md)、[迁移映射](MIGRATION.md)提供命令及旧路径定位。

```powershell
git clone --depth 1 --single-branch --branch codex/team-handoff-20261007 https://github.com/GINSmucker/kv260-gemm-engine.git
cd kv260-gemm-engine
python -B docs/tools/check.py
python -B model/golden.py --check model/cases/tensors.npz
```

使用已确认项目Python3.12与固定依赖；完整模型/数据/IP/运行包在Release按需取得。已有工作先保存自己的修改，再`git pull --ff-only origin codex/team-handoff-20261007`。当前软件检查不打开设备，也不证明新的ARM/PL/HLS资格。

开发默认AB1 OPT38+v5；同软件20图core p50=438.385ms，较较快35a减少50.632%。后续AB6两窗431.021/430.068ms、同v5减少1.679%/1.861%，第二CPU参考漂移5.101%，整体INCONCLUSIVE。正式冻结父版仍OPT35a/V2A，正式PL/CPU加速验收未签。最新状态以progress和原公开证据为准。

## 固定快照

|标签|用途|
|---|---|
|[v0.1.0](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/v0.1.0)|原Naive GEMM；不移动|
|[v0.2.0](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/v0.2.0)|原通用Dense第二周发布，cd0870d；对应旧源码结构|
|[team-handoff-20261007](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-handoff-20261007)|完整模型首次快照，旧模型/IP/运行包|
|[team-handoff-20261008](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-handoff-20261008)|F05 ABI4研究源码/运行包，未采用|
|[team-handoff-20261009](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-handoff-20261009)|AB1 v5与OPT38平台；后续AB2/3源码接续|
|[team-data-20261010](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-data-20261010)|856JPEG固定校准/测试子集及20图预处理引用|

协作预发布不代表正式比赛发布Gate。标签仍保留当时system结构，按当时说明复现；当前分支按新根结构。九个既有资产URL/摘要/字节固定，目录迁移不重新上传大模型和数据。

## 新分支与整合

新分支采用`codex/<方向>-<具体改动>`，如`codex/a-ddr-burst`、`codex/b-ps-profile`、`codex/c-wire5-recovery`、`codex/ui-model-status`。例子不代表已创建或已获准实验。PR写清parent SHA、接口/数值影响、真实验证、未运行项、资产和回退。

PR #2目标main；PR #1保留历史F8评审；PR #3目标协作分支、是早期研究候选，移植前重新核对接口与最新parent。目录更新没有合并或关闭这些PR。共享板卡串行协调，旧聊天授权不向新开发者转移。
