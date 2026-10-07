# 固定第三方来源与许可

此协作快照不为团队代码重新声明全仓许可证。原作者项目资料和签署保留；第三方材料保持各自许可及摘要。

|材料|来源与许可|
|---|---|
|nlohmann JSON编译头|冻结`board/model_backend/vendor/json.hpp`，MIT；同目录JSON_LICENSE.MIT，头内原版权保留|
|PicoSHA2|固定编译头中的原MIT许可/版权保留；插件和旧工具使用的版本各有SOURCE_MAP摘要|
|ORT 1.22.1 C/C++ API头|Microsoft ONNX Runtime v1.22.1，MIT，头内声明保留；[官方固定源码](https://github.com/microsoft/onnxruntime/tree/v1.22.1/include/onnxruntime/core/session)|
|KV260 board文件|XilinxBoardStore固定提交，原LICENSE随IP Release保留，来源见BOARD_AND_TOOLS|
|ZOCL补丁|XRT固定上游及其GPL许可；两份差分保留上游上下文，不在本包安装系统模块|
|DeiT模型和B冻结包|`deit_tiny_patch16_224.fb_in1k`来源、revision、数据来源与构建依赖保留在原B包model_identity/dataset_source/manifest中；归档保持原字节|

ARM runtime链接目标系统现有XRT、uuid与libcrypto，不复制系统库。B完整归档中的安装材料保留原发行包元数据；它只通过私有协作Release显式下载。
