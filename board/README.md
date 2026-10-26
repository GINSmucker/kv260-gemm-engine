# C · OPT50 平台与 PS 运行软件

活动运行包位于 [runtime/](runtime/verify_tools.py)，是已发布 `fair_compare_v1_fix1` 的原46载荷与原 tools_manifest，共47文件。`source/`、`support/`、expected 清单及包内 README 保留原字节；没有改写其 pins 来配合目录迁移。原 `runtime_snapshot` 只是归档目录名，包根整体迁移不改变内部相对路径。

| 路径 | 作用 |
|---|---|
| [runtime/verify_tools.py](runtime/verify_tools.py) | 原46文件只读身份检查 |
| [run_cpu_tests.py](run_cpu_tests.py) 与 [runtime_tests/](runtime_tests/test_compare.py) | 合成 proof/worker/输入/对照/权限模拟测试，需 NumPy |
| [runtime/build_arm.py](runtime/build_arm.py) | 已固定工具链下 ARM 编译与 CPU 自测，不执行 owner |
| [platform/](platform/README.md) | simfix v4 平台脚本、配置、测试、自研 F8 RTL |
| [deployment_sources/](deployment_sources/firmware/kv260_opt50_fd96539d_4cbcf732.dts) | 固件 DTS、shell、metadata XML/JSON 源描述 |
| [evidence/](evidence/system_verified_fd96539d.json) | 系统来源、实现报告与 HLS 对齐证据 |
| [loadonly_snapshot/](loadonly_snapshot/README.md) | 历史 load-only v2 工具；加载属于硬件修改 |
| [licenses/](licenses/README.md) | ORT MIT 与原头文件版权来源 |

从仓库根目录运行 `python -X utf8 -B board/runtime/verify_tools.py` 只读检查文件。`python -X utf8 -B board/run_cpu_tests.py` 运行无设备合成测试，会在自有临时目录写入并清理数据；模型/历史环境缺失项明确 SKIP，不把它们算作板端通过。

ARM 构建需要完整外部模型目录（含 `model_catalog.hpp`）、匹配 Ubuntu/aarch64/XRT/UUID SDK 和被固定的 g++13。构建会新建编译输出与收据；不在 Windows 执行，不手工运行 owner。包内 `prepare_source.py` 依赖原兄弟 `performance_v2`，修复安装器/旧板端命令也属于历史流程，不能当成当前 clone 的通用入口。

原运行门禁仍绑定 boot `f0f2eed8-5820-4df5-ba19-d05527720f97`、guardian plan `74abb1e54663`、root 私有目录、编译器 SHA，以及 `/home/ubuntu/fpga_project/opt50_integration/fair_compare_fd96539d_v1_fix1` 等固定路径。新设备/启动必须另建经审查流程；本分支不删除安全检查、不自动加载固件、不授权 START。

平台完整来源是 `D:/GEMM/opt50_system_simfix_20261024_v4`，其 `build_20261024_132157_775_c2e60ae4` 经 `finish_20261024_v1` 导出、`verify_system_v2.py` 核查。原始 IP、board preset、C 参考包和匹配 PS VIP 输入未打包；直接运行 `platform/scripts/run_vivado.ps1` 将因缺少新准备清单而拒绝。平台需 ASCII 新目录，工具调用会生成文件且可能占用大量资源；本次没有执行。

[SNAPSHOT_STATUS.md](SNAPSHOT_STATUS.md) 与 [原归档来源索引](evidence/archive_copy_index.json) 保留详细历史位置；其中 `runtime_snapshot` 对应本活动目录 `runtime`，索引属于原快照，不是本活动目录的 manifest。完整接口见 [docs/interface.md](../docs/interface.md)，结果见 [results/README.md](../results/README.md)。
