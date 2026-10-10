# A：完整层GEMM计算核

默认开发源码位于src与tb，配置唯一源为src/model_gemm_config.hpp；include提供兼容转发。默认OPT38采用128MAC、100MHz、32bit输入数据master。

当前研究源码由opt45-source资产提供：hardware/model_gemm/candidates/opt45_y768_v1。OPT45为512MAC、TM32/TN768、完整K，X/W/Y三路128bit；X为32行×64个128bit字LUTRAM，acc32×16×48个INT32、96KiB。W每start完整装载，X按M块初始化/加载，阶段无DATAFLOW重叠。Y直接按有效行/N范围打包，无额外复制。

五shape和14寄存器共同遵守[唯一接口](../docs/interface.md)。整型结果INT32、地址64bit字节，不支持任意shape或FPGA scale/bias。

## 软件检查

```powershell
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/check.py --cpp --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
```

OPT45的ap_uint128要求显式提供含ap_int.h的Vitis include目录，不分发SDK。普通C++公开TB采用完整INT64参考，8有效+8拒绝、418064输出；不验证RTL背压。默认两个核可用同命令不传research-source检查。

## HLS与平台

源码资产根为build/assets/opt45-source/opt45-source。run_hls.ps1使用已有Windows Vitis_HLS2020.2.2、target8ns/uncertainty1ns；HLS工程各命令保持同一ASCII盘符。2026.1默认配置位于hls_config.cfg，资格由自己的工具报告确定。

从源码资产根运行：

```powershell
./hardware/model_gemm/candidates/opt45_y768_v1/run_hls.ps1 -Stage csim
```

已有55文件HLS/IP交付可按manifest核对；平台脚本必须显式指定OPT45 DeliveryRoot、ManifestName及ExpectedManifestSHA256=68e5aebce7b2ccce672b6a8944e2c0392ad0d750786faef70d92a0a1a54ca217。新平台fingerprint/metadata/owner需要新身份验证，构建不自动继承A2。

资源和验证分别见[综合摘要](reports/synthesis.md)、[verification](../docs/verification.md)。scripts/generate_contract.py只提供显式build输出的INT8声明工具，不写当前packed配置。
