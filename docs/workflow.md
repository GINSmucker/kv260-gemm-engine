# 团队协作与交付

A负责HLS数据通路，B负责模型、数值和CPU/PL路由，C负责平台、设备运行时及恢复。interface是共享契约；progress、verification与summary.csv维护当前已确认事实。

## 分支与使用

完整资料从codex/team-handoff-20261007取得；[分支说明](branches.md)列出当前分支用途。新工作建立codex/<方向>-<主题>，如a-ddr-burst、b-ps-profile、c-wire5-recovery。PR说明父版身份、行为/接口、实际验证与未运行范围；保留队友改动，不force-push或移动固定tag。

```powershell
git clone --depth 1 --single-branch --branch codex/team-handoff-20261007 https://github.com/GINSmucker/kv260-gemm-engine.git
cd kv260-gemm-engine
python -B docs/tools/check.py
```

已有修改先提交或保存，再git pull --ff-only。完整模型、研究源码/运行包及数据按资产锁显式下载，不将缓存提交。

## 验证与评审

源码与接口以独立INT64/逐位参考检查；新ARM构建产生新二进制身份；新平台使用自己的clock/DDR/MMIO与配对文件。共享板卡串行占用，公开数值、完成/排空、断开恢复和回退分别确认。整图性能保留全部预定样本并报告分位数，诊断与正式测量分别使用。

研究验证与正式独立C、PL质量、独立长期稳定性、UI就绪和发布签署分别判定。当前OPT45为研究交付，不凭已取得资产或开发PASS代签。

## 文档与证据

就地维护当前需求、设计、接口、用法与验证范围，不记录Agent修改历史或临时Phase/Step计划。确认决策更新直接覆盖过时说明；实际实现偏差作为待核实问题，不自定新规范。

原始JSON/签署和冻结manifest保持字节，测量比较必须有固定范围。Git保留小型说明和公开回执；原始性能样本、IP/运行文件按需资产化；矩阵、长trace、SDK与环境留在本机保护目录。

板卡操作许可来自当前维护者；boot/QSPI/复位/系统安装独立授权。UNKNOWN保留资源，证明静止后停止。Codex运行在开发者电脑。
