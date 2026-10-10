# A：OPT38计算核

当前主入口是src/model_gemm_top.cpp、src/model_gemm_core.hpp、src/model_gemm_config.hpp及tb/testbench.cpp。include/中的兼容头只转发，配置唯一源在src/；旧OPT35a参考保存在candidates/。

|项目|定义|
|---|---|
|目标|KV260 commercial K26，xck26-sfvc784-2LV-c|
|顶层/控制|model_gemm_top，ap_ctrl_hs，一start完成完整层|
|输入/输出|X/W INT8行优先，AXI输入packed32；Y INT32；MMIO为64bit字节地址|
|五shape M,K,N|head 1,192,1000；proj 197,192,192；qkv 197,192,576；fc1 197,192,768；fc2 197,768,192|
|微结构|8×16外积、128 MAC；完整K，X跨N复用，W每start重装|
|时钟|100MHz/10ns，legacy2020.2.2 uncertainty1ns|

矩阵定义和寄存器唯一入口为[docs/interface](../docs/interface.md)。不支持任意shape，不把TK32兼容常量当K归约分段，不添加FPGA scale/bias。

## 运行

从根目录：
```powershell
python -B docs/tools/check.py --cpp --cxx <existing-cxx>
./hls/tools/hls.ps1 -Candidate opt38 -Stage csim -HlsTool '<existing-vitis_hls-2020.2.2>'
```

普通C++公开RTL profile检查8有效/8非法shape，HLS pragma被忽略，不能称RTL。旧2020.2.2脚本按hls/config冻结工作副本；2026.1工作使用hls/hls_config.cfg，不能把两套工具结果合并。本轮目录验证中没有新HLS/RTL执行。

[综合摘要](reports/synthesis.md)保留主线2026.1尚未重新综合的状态，历史2020.2.2结果另在[参考算法](../docs/reference/ALGORITHM.md)与[验证汇总](../docs/verification.md)。scripts/generate_contract.py为历史INT8声明工具，必须显式--output到忽略的build/，不生成当前packed32配置。新HLS导出/monitor/资源/时序仍须独立Gate，不从本机软件PASS继承A2。
