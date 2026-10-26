# v3 外部资产

本分支仅保存源码、许可证和小型证据。模型/权重/图片、ARM二进制、位流、DTBO/XSA/xclbin、IP导出包和完整AMD仿真源码不在Git。哈希是身份约束，不代表资产已经公开可下载。

| 输入 | 身份或来源 |
|---|---|
| 固定完整模型 | manifest `ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb`，含外部 `model_catalog.hpp` |
| 数学权重/参考图 | [model/README.md](../model/README.md) 列SHA，58文件模型目录必须完整 |
| 十二层AXI包 | manifest `6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf`，129载荷 |
| Linux固件 | fd96539d bit.bin SHA `7de86da2363e92453f9de350dcf81a2b1d4d2e4fe4a64ad1e66db8da9563c662`；不可与未字节交换的原始导出文件混用 |
| metadata | 8560B，SHA `715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551`；XML/JSON源见 [deployment_sources](../board/deployment_sources/metadata_sources/metadata_manifest.json) |
| HLS IP/preset/C参考包 | 来源索引见 [platform/external_inputs](../board/platform/external_inputs/board_files_SOURCE.md)，原完整平台清单在 [evidence](../board/evidence/platform_project_manifest.json) |
| PS VIP | 从已安装Vivado2026.1匹配源本地生成；自研补丁脚本不等于官方补丁，不分发完整vendor模型 |
| ARM环境 | Ubuntu24.04.2/aarch64、kernel6.8.0-1015-xilinx、XRT2.18、匹配zocl、g++13及UUID/XRT开发头库；编译器SHA仍被运行包固定 |
| Python环境 | 模型执行固定NumPy1.26.4/ORT1.22.1；模型工具Windows锁文件不能直接用于ARM安装 |

原Dense H576交付可从 [h576-summary-20261019](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/h576-summary-20261019) 与 [C资产锁](https://github.com/GINSmucker/kv260-gemm-engine/blob/codex/team-handoff-20261007/docs/locks/ASSETS.lock.json) 追溯；其模型/位流/metadata不自动适配OPT50八参数ABI。

开发阶段100张、523张图片曾用于候选比较，不能自动充当独立质量验收集。后续真实图片测试应固定未用于候选选择/训练的输入、标签、预处理和哈希。未取得的输入明确标缺失，不生成占位权重来绕过校验。
