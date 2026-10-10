# F05 当前研究实现与接续

2026年10月8日F05 D0～D5完成。D1～D4功能/恢复通过，D5为有效三图开发比较；全部软件成本预算FAIL，因此G5关闭，正式父版仍system_a3_20261005_02。它是当前有实际收益证据的研究候选，不是正式发布或UI ready。

|完整结果|父前|F05|父后|
|---|---:|---:|---:|
|三图中位/ms|6445.621097|1465.884971|6663.105521|
|session中位/ms|6409.031800|1400.893781|6626.383154|

每窗固定预热0、正式0/7/19；三图均快于两侧父版，较两侧父中位均值减少77.634975%，约4.47倍。父最大漂移3.374142%、CPU最大0.498919%。完整结果包含BEGIN、49层、COMMIT与必要结果/trace/profile导出，不能与只含session的时间混用。588starts/49031904输出摘要锚定、1481文件验证和三窗回退PASS；三图不签正式S5/p95或长稳。

成本下界软件798.998ms、C422.725ms、B35.973ms，超过600/100/30ms。精确fsync持续时间NOT REPORTED，BEGIN/COMMIT括号只用于保守边界。下一工作优先REQUEST非native306.895ms的内部细分；其余验证46.458ms、ACK47.001ms、prepare17.102ms、copy/POD5.268ms仍在C边界内。尚未证明owner/摘要/复制/传输/调度哪项主导，不归成纯网络，不对同一D5追加样本。

## 身份与取得

候选SHA及owner/bridge/plugin/wheel、平台/A2/driver/metadata全绑定见[F05_LOCK](../locks/F05_LOCK.json)。默认采用父版锁不变；F05仍用已验OPT35a，原guard/const/AXI和冻结KernelCore逐字节复用。B原模型资产继续使用10月7日Release。

```powershell
python -B docs/tools/assets.py f05-source --download --extract
python -B docs/tools/assets.py f05-runtime --download --extract
python -B board/runtime/tools/build_runtime.py --variant f05 --uuid-include <existing-uuid-SDK-include>
```

最后一条仅准备/预览。加`--execute`只允许AArch64 Linux已有g++13/XRT/uuid/libcrypto环境，编译输出到新的build目录，不启动owner或加载PL。新二进制有新SHA，须重新资格验证。原始精确编译参数在源码包BUILD_RECIPE.json；SDK路径参数化，冻结代码原字节保持。

源码包在`build/assets/f05-source/f05-source`：src为ABI4/log/transaction/wire/server/native adapter；b为B与NEON；vendor为实际编译所需旧受控KernelCore/协议；headers为固定ORT API；tests是公开日志、事务、socket、ABI和B边界探针；SOURCE_INDEX与asset_manifest逐成员记录SHA。测试B probe保留原实验路径和输出约束，仅作来源；移植到新环境时显式绑定B资产/短socket目录/新输出，不直接运行旧全局板卡控制器。

运行包包含实际owner、bridge、NEON B共享库、0.6.0 wheel、ABI头/注册表及candidate。既有基础板测/IP/驱动/metadata从10月7日资产复用，F05不复制BIN/KO/完整模型。配置4 generation、真实boot/PID/birth/peer以及新窗口必须由维护者固定；旧ready不能接ABI4。

## 合同与恢复

MODEL_TRANSACTION_V1：唯一owner掌握W私有快照、日志writer和串行dispatcher；最多4驻留连接、1执行线程、一个活动epoch和一个未ACK Y。wire固定128B小端header/完整SHA/保留位为零；模型最多256 epoch，49层逐序号。BEGIN成功持久/读回后才允许首start；49层全部ACK且真实safe后才COMMIT。COMMIT保留49完整native、7448B trace与4000B logits，成功晚于提交完成。正常每图两次热fsync，层间Y易失。

同步不确定后writer永久poison，不成功、不复用ID；完整重开验证日志链/语义、恢复fsync和实际文件身份后只读查询完整已有COMMIT，不执行或补写模型。旧未提交epoch为UNKNOWN。RESULT/ACK丢失按原QUERY/ACK恢复、不重发REQUEST；实际调用者死亡在safe和完整pending Y封存后ABORT。owner突然死亡、断电、永久AXI卡死与长期切换仍未证明。

D3正常XRT/NEON、完整软件五图20429960输出/Xq/sx/Y/logits逐位一致；D4 52starts/4426408完整INT64输出、调用者死亡/COMMIT/回退PASS。四PL窗共640starts，OF0/严重0/属性各4；最后独立root同boot6f08c7ac…494e、starter/无ZOCL/render/设备用户/锁空闲、UI恢复。以上来自原实验，本次上传不操作板卡。

旧socket路径超108B、未编号ABI断言、B缩进-Werror、收集压缩超时及其后修复均保留；旧V3比较仍INCONCLUSIVE。公共小回执见[进度汇总](../../results/evidence/latest/runtime_progress_20261008.json)。正式性能、PL质量、205模型/10045starts稳定性、ABI4 UI与发布均未放行。
