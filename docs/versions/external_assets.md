# 外部资产与来源边界

Git 保存源码与小型证据；本次不上传模型、图片、IP大包、完整厂商仿真模型或板卡二进制。
以下列表是已知身份，不意味着每项都已发布为可下载 Release 资产。
没有发布的资产不能仅凭哈希从 Git 重建，也不能创建全零占位来通过运行检查。

## 已有 Dense H576 交付入口

- [已有 H576 Release](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/h576-summary-20261019)。
- [C 的交付分支及资产锁](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/team-handoff-20261007/docs/locks/ASSETS.lock.json)。
- 本机 `h576-runtime-public-ui.tar.gz`：43423483B，SHA256
  `c29a006fe472fd2bfb545d7bbf77f34a5090ff9315043cdd7e5830e518d61f7c`。
  成员前缀 `v5_h576_ui_20261017_01/`。必须按实际资产锁核对下载项，不能假定 Release 内所有包都同一版本。
- 包内旧Dense模型、位流、metadata 与 PS源码只用于原包；不与当前Sparse模型/新ABI混用。

## 当前 OPT50 固件与模型

- 驻留硬件 UUID：`fd96539d-4cbc-f732-7694-0f0236ca1cf3`；observer tags：4254487453、1287452466。
- 125MHz 目标；本次保存的板端 OS clock 观察为124999999Hz。
- `kv260_opt50_fd96539d_4cbcf732.bit.bin` SHA256：
  `7de86da2363e92453f9de350dcf81a2b1d4d2e4fe4a64ad1e66db8da9563c662`。
- 对应 metadata 8560B，SHA256：
  `715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551`。
- 十二层Sparse模型 manifest SHA256：
  `ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb`。
- 模型及AXI测试包身份见 `model/versions/v3_sparse12/README.md`。

本次源码归档不宣称上述OPT50大资产已上传Release；完整加载需要另行取得与pinned manifest匹配的资产。
平台输入IP、板卡preset和仿真模型的缺失/来源详见 `board/versions/v3_sparse_opt50/SNAPSHOT_STATUS.md`。
第三方板卡preset保留其许可证；AMD VIP模型补丁在已安装工具的本地副本生成，不能以归档名义
把整份厂商派生源码默认公开上传。

## 分类输入

100张及523张图片是开发阶段样本，不能自动升级为独立验收集；本分支不提交这些图片或标签。
新测试集需记录来源、样本/标签哈希、预处理和是否参与过候选选择。新模型微调另建身份。
