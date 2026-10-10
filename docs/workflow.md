# 团队工作流：分支、提交与Release

A负责hls，B负责model，C负责board；docs/interface.md为唯一公共接口，progress/verification/results由三人共同维护。完整系统从当前codex/team-handoff-20261007根目录接手，日期是入口建立标识；main已合并PR #4职责骨架，等待本PR完整资料整合。

[分支导航](branches.md)解释main、历史F8、早期G0/G1与当前协作入口。新功能分支用codex/<方向>-<主题>，如a-ddr-burst、b-ps-profile、c-wire5-recovery。每个PR写明parent SHA、源码/数值/ABI影响、实际验证/未运行项、资产身份及回退。

先做本机软件/公开case，再新ARM身份、窄恢复、完整单图与预声明性能；质量/长期稳定性/UI/正式发布各自判Gate。新的源码、平台和boot不借旧PASS，INCONCLUSIVE/PARTIAL按原测量语义记录。

## 拉取

```powershell
git clone --depth 1 --single-branch --branch codex/team-handoff-20261007 https://github.com/GINSmucker/kv260-gemm-engine.git
cd kv260-gemm-engine
python -B docs/tools/check.py
```

已有协作分支提交或保存自己的工作后git pull --ff-only。路径迁移与旧tag对照见[MIGRATION](MIGRATION.md)，本地已有system缓存不要盲目删除。

## 资产与证据

IP、平台/运行二进制、模型和固定JPEG通过九个已发布资产按需取得，锁位于docs/locks，缓存build/assets忽略。现有Release/tag字节固定；新增文档或路径不重复上传权重/数据。小公开case可进入model/cases，长日志和工作目录在build或results/runs。

重要结果更新progress、verification、summary.csv；小型公开签署/原结论放results/evidence，原字段/换行不改。不能读取C私有留出或代签验证，也不能仅以Code合并宣称当前板卡可用。

共享板卡由维护者串行安排，旧聊天授权不由仓库转移；加载/运行前固定owner/驱动/metadata/平台/boot/window/预算/锁/回退，证明静止后停止，UNKNOWN保留BO。boot/QSPI/复位及系统安装另行授权。Codex留在开发者电脑。
