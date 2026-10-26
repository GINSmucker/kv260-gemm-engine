# 三个版本分支

三个独立分支采用相同顶层职责和文档入口，以各自 `VERSION.json` 声明当前实现。

| 版本 | 分支 | 当前实现 |
| --- | --- | --- |
| v1 初级Dense | [codex/v1-dense-initial](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v1-dense-initial) | 历史tiled v0.2，INT8→INT32；尚无H576整图平台 |
| v2 优化Dense | [codex/v2-h576-dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense) | 直接复制真实H576交付分支，内核保持原样，原512MAC/128bit及PS调度 |
| v3 当前Sparse | [codex/v3-opt50-sparse](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v3-opt50-sparse) | OPT50一个共享阵列，调用时选择Dense/Sparse，固定十二个Sparse层 |

共同组织：

```text
README.md                  版本、目录、使用、验证、限制、导航
VERSION.json               本分支身份、来源、活跃路径和源码SHA
hls/README.md              A：当前核、配置、testbench
model/README.md            B：数值参考、模型格式、外部资产
board/README.md            C：匹配运行时、平台及部署限制
docs/README.md             接口、验证和来源文档入口
docs/interface.md          本版本ABI与数据合同
docs/verification.md       实际通过范围和未验证项
results/README.md          本版本证据和计时范围
tools/check_version_layout.py  只读布局、SHA和配置路径检查
tools/run_active_native.py     本机C++合成用例，不是HLS/RTL/板测
```

结构一致不要求顶层函数、控制ABI和成熟度一致。v1参数顺序是M,N,K；H576为三指针Dense；
OPT50增加W_meta和mode。v1缺少匹配板端功能时明确标NOT RUN，不放旧驱动冒充。
H576没有OPT46的输出双缓冲；OPT46不再作为v2。H576原公开图片和稳态证据不能转签给Sparse。

原始归档分支保留来源和旧快照历史；当前工作请先查看所在分支的VERSION，不依据同名旧文件选核。
各分支的 `hls/hls_config.cfg` 引用自己的 `hls/src`、`include`、`tb`，不指向D盘历史工作目录。
H576新增的8ns相对路径配置尚未重新运行HLS，不冒充原冻结配置。

完整模型性能比较还需统一模型/权重、线程、频率、预热和计时范围；不能把v2历史中位数
与v3零输入单次结果直接作排名。原始文件、锁和回执的PASS范围保持不变。
