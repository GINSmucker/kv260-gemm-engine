# v3 来源与活动路径

本独立分支从三阶段归档 commit `00efbf17ec540610f3b1a3f33d05fdcb1ed508ae` 建立，没有改写归档目录：

| 原归档 | 本分支活动位置 |
|---|---|
| `hls/versions/v3_sparse_opt50/{src,include,tb,docs,tools}` | 同内容提升至 `hls/` |
| `hls/versions/v3_sparse_opt50/hls_config_portable.cfg` | `hls/hls_config.cfg`，full/rtl用例集，未在新位置执行HLS |
| `board/versions/v3_sparse_opt50/runtime_snapshot/` | 原47文件整体提升到 `board/runtime/`，pins不变 |
| `board/versions/v3_sparse_opt50/` 的其他源码/证据 | `board/` 保持对应子目录，runner只调整包搜索目录 |
| `model/versions/v3_sparse12/` | 工具及import依赖提升到 `model/` |

原默认OPT38 HLS、旧board/runtime/vivado、旧模型工具、旧首页/接口/部分工作台文档与旧summary已保存至 [pre_v3_default/](pre_v3_default/README.md)。[机械移动记录](default_paths_moved.json) 记录主要旧路径，避免在活动路径同时出现旧核/旧ABI。原归档COPY_INDEX保留在 [board/evidence/archive_copy_index.json](../../board/evidence/archive_copy_index.json)，其相对路径是原归档路径，不是活动目录校验器。

实际硬件来源是 `D:/GEMM/opt50_system_simfix_20261024_v4`，不是先前失败的resetfix工程。详细来源与外部依赖见 [board/SNAPSHOT_STATUS.md](../../board/SNAPSHOT_STATUS.md)，C++/导出RTL来源对应见 [hls_source_alignment.json](../../board/evidence/hls_source_alignment.json)。文件对齐不是本次重新编译。

历史文件的README、NOT_RUN、日期、绝对路径和pins保留原字节；它们只陈述创建时的范围。本分支的新入口README与 [VERSION.json](../../VERSION.json) 定义活动版本。固定boot/guardian/编译器路径限制没有被目录整理消除。
