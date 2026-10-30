# B24 常驻验收：本次重启恢复 r1

本版从固定 h1 的155个静态文件复制到新目录。模型、权重、所有C++/头文件及两份C++自测共95个文件保持原字节；仅增加本次重启的Python历史审查与旧h1结果兼容。ORT仍为4/1线程，每图36次PL调用（Dense12/Sparse24），不重新运行Vivado、不更换固件算法。

当前恢复仅绑定已观察的新启动 `cbbb80ca-8805-4971-9271-f59afbcaef81`；旧启动为 `2b0eaebd-2dd9-45c5-b135-26315bfdbb7f`。

- c4b零图事件：必须存在原 `CLOSED_ZERO_GRAPHS` 独立收据，重验原固定审计源码、check/intent/receipt、141条初始化前缀及155条完整关闭事件、全部原文件pin和精确STOP0日志。仅做历史静态审查，不查询旧PID/旧guardian当前状态，不联系owner，不把0张加入精度索引。
- 8e事件：只承认已观察到的 `REFUSED_BATCH_BEFORE_OWNER`、固定 `EXECUTION_PREFLIGHT_BEFORE_NATIVE_POPEN` 及guardian服务拒绝原因；验证原h1全部文件、封存旧plan、启动拒绝收据与空parent journal，并要求无Native/worker/样本/commit/关闭证据。该尝试验收0张，不声称设备正常关闭；所有原记录与索引保持原样。
- 历史已提交305张仍由严格索引和原闭合证明维护。任何commit、completion或与上述事实不符的材料均拒绝，需单独对账，不自动重放。

上板前使用本次启动的新guardian和layer02计划；外部有限500runner显式提供这两个新路径，不沿用旧启动token。旧6efa完整模型证明作为历史来源继续使用。新的同启动5图pilot闭合且逐位一致后，再逐批最多50张继续至累计500；每批新计划仍有原900秒预算，失败或不确定立即停止，不自动续期、kill或卸载。

ARM仍使用固定fix1来源的源码相同复用与纯CPU自测，收据绑定本版新清单，不能直接把旧h1收据冒充本版编译。构建与历史审查均不访问PL。

`reboot_review.py` 全部只读，不写任何epoch、closed或验收状态。`tests/test_reboot_review_r1.py` 以实际h1源文件及真实Native事件格式验证两条严格分支；Windows权限与进程观察明确模拟，不能记作板测。
