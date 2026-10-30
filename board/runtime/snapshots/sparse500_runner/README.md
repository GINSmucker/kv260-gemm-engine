# 新启动下继续累计500张验收

此包仅处理这次已核清的重新启动：已提交索引的前305张保留，旧8e候选是在Owner启动前拒绝，START为0、未提交图片，不能伪造为已完成或复用旧计划。固定只读审查还验证旧c4b的141→155条零图正常关闭证明；不创建恢复状态、不开新Native事务。

固定旧来源为h1的155文件，清单24,547字节，SHA256 `8f256e8eca931b80ea9513c62f0921ec0700558c5604476ea68902833411bd1e`。新软件158文件，清单25,141字节，SHA256 `38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d`，板端独立目录 `b24_accuracy_resident_20261030_reboot_r1/software`。

静态安装严格只接受4个变更文件（README.md、release_compat.py、run_model.py、verify_tools.py）、3个新增文件（reboot_review.py、predecessor_h1_tools_manifest.json、tests/test_reboot_review_r1.py）和新清单。全部95个模型、数据、C++/头文件及CPU测试源码相同；目标存在或部分安装均拒绝覆盖。运行状态、索引、build和ELF不复制。

新入口显式要求当前 `--guardian-plan` 和 `--layer-plan`，旧启动证明不能默认沿用。已确认的新guardian为e04a80c307c7；layer计划必须取这次板端实际新结果，不能猜测token。新目录没有build时只调用不变stage_arm复用固定fix1四ELF、执行两个CPU自测；完整收据只复核，部分输出保留，不重新编译Native。

验证新的5图后，每批最多50张推进，严格止于500。当前305到500还需195张（5+50+50+50+40），共7020次PL调用；每图保留全部1000个CPU/PL logits逐位比较、完整36次调用和INFO/guard/static检查。失败不自动重试、旧计划不续期、已提交图片不重放。

报告独立重算前500张原始输出和标签，时延按版本分组；306～500的新版本单独列组。新报告在公开可读 `b24_acceptance500_reboot_exports_20261030_v1/action_TOKEN`，原私有证据不chmod、不删除。

证据tar含CPU/PL原始输出NPY、标签/JPEG、Native/关闭/commit证明及去重清单。大float输入仍在板端，索引保存路径/大小/SHA；权重和ELF不重复打包，不能声称小归档可重跑完整模型。目标是500张快速子集验收，不是完整1800张或全ImageNet验证。

Windows上传仅传新工具归档和checksum；下载仅读取新导出到新的Windows目录并核SHA。实际精确哈希、后台安装和运行命令在交付目录BOARD_STEPS.md/NEXT_STEPS.md。安装、CPU准备与后续195张放在同一新后台服务，SSH断开只查询，不再次提交。
