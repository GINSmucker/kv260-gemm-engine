// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2026.1 (64-bit)
// Tool Version Limit: 2026.06
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
// control
// 0x00 : Control signals
//        bit 0  - ap_start (Read/Write/COH)
//        bit 1  - ap_done (Read)
//        bit 2  - ap_idle (Read)
//        bit 3  - ap_ready (Read/COR)
//        bit 4  - ap_continue (Read/Write/SC)
//        bit 7  - auto_restart (Read/Write)
//        bit 9  - interrupt (Read)
//        others - reserved
// 0x04 : Global Interrupt Enable Register
//        bit 0  - Global Interrupt Enable (Read/Write)
//        others - reserved
// 0x08 : IP Interrupt Enable Register (Read/Write)
//        bit 0 - enable ap_done interrupt (Read/Write)
//        bit 1 - enable ap_ready interrupt (Read/Write)
//        others - reserved
// 0x0c : IP Interrupt Status Register (Read/TOW)
//        bit 0 - ap_done (Read/TOW)
//        bit 1 - ap_ready (Read/TOW)
//        others - reserved
// 0x10 : Data signal of X
//        bit 31~0 - X[31:0] (Read/Write)
// 0x14 : Data signal of X
//        bit 31~0 - X[63:32] (Read/Write)
// 0x18 : reserved
// 0x1c : Data signal of W
//        bit 31~0 - W[31:0] (Read/Write)
// 0x20 : Data signal of W
//        bit 31~0 - W[63:32] (Read/Write)
// 0x24 : reserved
// 0x28 : Data signal of Y
//        bit 31~0 - Y[31:0] (Read/Write)
// 0x2c : Data signal of Y
//        bit 31~0 - Y[63:32] (Read/Write)
// 0x30 : reserved
// (SC = Self Clear, COR = Clear on Read, TOW = Toggle on Write, COH = Clear on Handshake)

#define XGEMM_TOP_CONTROL_ADDR_AP_CTRL 0x00
#define XGEMM_TOP_CONTROL_ADDR_GIE     0x04
#define XGEMM_TOP_CONTROL_ADDR_IER     0x08
#define XGEMM_TOP_CONTROL_ADDR_ISR     0x0c
#define XGEMM_TOP_CONTROL_ADDR_X_DATA  0x10
#define XGEMM_TOP_CONTROL_BITS_X_DATA  64
#define XGEMM_TOP_CONTROL_ADDR_W_DATA  0x1c
#define XGEMM_TOP_CONTROL_BITS_W_DATA  64
#define XGEMM_TOP_CONTROL_ADDR_Y_DATA  0x28
#define XGEMM_TOP_CONTROL_BITS_Y_DATA  64

