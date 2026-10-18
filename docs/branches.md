# GitHub分支与交付用途

H576 当前成果入口为 [研究交付总结](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/h576-summary-20261019)，通过独立功能分支评审，未自动合并。

未缩宽配置及既有协作资料入口为[codex/team-handoff-20261007](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/team-handoff-20261007)。该入口提供默认OPT38/v5与未缩宽OPT45共享研究资产，状态见[progress](progress.md)。

|分支|用途|资格与评审|
|---|---|---|
|main|OPT38职责骨架，hls/model/board/docs/results|f5ead7a，2026.1 C-sim队友报告PASS；2026.1综合/RTL/板端NOT RUN|
|codex/team-handoff-20261007|OPT45 Dense性能优化基准、完整接口及已发布研究资产|[PR #2 draft](https://github.com/GINSmucker/kv260-gemm-engine/pull/2)，目标main；A从hls/README接手，正式独立资格不由资料更新产生|
|hls/opt40-axi128-burst|A_OPT40_AXI128_M16N16资源取舍候选|68a9e643；A摘要记录2026.1 CSim/综合/Cosim PASS，平台及同条件实板性能NOT RUN|
|chore/repository-layout|main目录骨架对应分支|[PR #4 merged](https://github.com/GINSmucker/kv260-gemm-engine/pull/4)，943d25f|
|codex/f8-xrt-board-integration-20260928|固定8×8 F8闭环独立资料|[PR #1 open](https://github.com/GINSmucker/kv260-gemm-engine/pull/1)，与main存在冲突；按F8接口评审|
|codex/runtime-g0-g1-20261007|不可变对象/解析/B计时的软件研究|[PR #3 draft](https://github.com/GINSmucker/kv260-gemm-engine/pull/3)，base协作分支，存在冲突；G1 PARTIAL、生产关闭|

新工作建立codex/<方向>-<主题>，保留自身改动再快进；不自动合并、force-push或移动tag。运行共享板卡由维护者串行安排，分支名称、日期或ahead数不是性能与资格证明。

固定资产入口见[ASSETS](locks/ASSETS.lock.json)：team-opt45-20261010为OPT45测量研究；model/dataset及默认OPT38资产各有固定tag。v0.1/v0.2、F8、ABI2/3/4与wire5配置使用自己的契约。版本资产的可取得状态不代表当前boot可运行。

[README](../README.md)、[interface](interface.md)、[workflow](workflow.md)和[接手说明](reference/START_HERE.md)提供当前用法。
