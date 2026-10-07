# model_gemm_v1 生成寄存器记录

三方核验日期：2026-10-04，build `model_gemm_opt35a_clear_v1_full_20261004_205715_369`。导出驱动头、component.xml、Verilog 三方地址一致，status PASS。

| 寄存器 | 实际 offset | 语义 |
|---|---:|---|
| CTRL | 0x00 | Read/COR，Done由唯一 owner读清；start COH，auto_restart关闭 |
| GIE | 0x04 | 未启用中断 |
| IER | 0x08 | 未启用中断 |
| ISR | 0x0c | 未启用中断 |
| ERROR | 0x10 | 只读，0 OK /1 INVALID_SHAPE |
| X_LOW | 0x18 | 32-bit参数/地址字，运行中保持稳定 |
| X_HIGH | 0x1c | 32-bit参数/地址字，运行中保持稳定 |
| W_LOW | 0x24 | 32-bit参数/地址字，运行中保持稳定 |
| W_HIGH | 0x28 | 32-bit参数/地址字，运行中保持稳定 |
| Y_LOW | 0x30 | 32-bit参数/地址字，运行中保持稳定 |
| Y_HIGH | 0x34 | 32-bit参数/地址字，运行中保持稳定 |
| M | 0x3c | 32-bit参数/地址字，运行中保持稳定 |
| K | 0x44 | 32-bit参数/地址字，运行中保持稳定 |
| N | 0x4c | 32-bit参数/地址字，运行中保持稳定 |

三路 m_axi 地址64-bit、数据32-bit。X/W/Y各两个32-bit字；顶层M/K/N为uint32。CTRL bit0=start、bit1=done、bit2=idle、bit3=ready、bit7=auto_restart；没有 Continue。初版没有 kernel cycles。

完整头/RTL/component 与 hash 在运行目录 `hls_export_inspection.json` 和交付包。地址资料核对不等于新平台或实板通过。
