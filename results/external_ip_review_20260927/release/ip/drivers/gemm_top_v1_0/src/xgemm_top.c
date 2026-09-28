// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2026.1 (64-bit)
// Tool Version Limit: 2026.06
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
/***************************** Include Files *********************************/
#include "xgemm_top.h"

/************************** Function Implementation *************************/
#ifndef __linux__
int XGemm_top_CfgInitialize(XGemm_top *InstancePtr, XGemm_top_Config *ConfigPtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(ConfigPtr != NULL);

    InstancePtr->Control_BaseAddress = ConfigPtr->Control_BaseAddress;
    InstancePtr->IsReady = XIL_COMPONENT_IS_READY;

    return XST_SUCCESS;
}
#endif

void XGemm_top_Start(XGemm_top *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL) & 0x80;
    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL, Data | 0x01);
}

u32 XGemm_top_IsDone(XGemm_top *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL);
    return (Data >> 1) & 0x1;
}

u32 XGemm_top_IsIdle(XGemm_top *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL);
    return (Data >> 2) & 0x1;
}

u32 XGemm_top_IsReady(XGemm_top *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL);
    // check ap_start to see if the pcore is ready for next input
    return !(Data & 0x1);
}

void XGemm_top_Continue(XGemm_top *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL) & 0x80;
    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL, Data | 0x10);
}

void XGemm_top_EnableAutoRestart(XGemm_top *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL, 0x80);
}

void XGemm_top_DisableAutoRestart(XGemm_top *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_AP_CTRL, 0);
}

void XGemm_top_Set_X(XGemm_top *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_X_DATA, (u32)(Data));
    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_X_DATA + 4, (u32)(Data >> 32));
}

u64 XGemm_top_Get_X(XGemm_top *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_X_DATA);
    Data += (u64)XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_X_DATA + 4) << 32;
    return Data;
}

void XGemm_top_Set_W(XGemm_top *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_W_DATA, (u32)(Data));
    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_W_DATA + 4, (u32)(Data >> 32));
}

u64 XGemm_top_Get_W(XGemm_top *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_W_DATA);
    Data += (u64)XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_W_DATA + 4) << 32;
    return Data;
}

void XGemm_top_Set_Y(XGemm_top *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_Y_DATA, (u32)(Data));
    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_Y_DATA + 4, (u32)(Data >> 32));
}

u64 XGemm_top_Get_Y(XGemm_top *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_Y_DATA);
    Data += (u64)XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_Y_DATA + 4) << 32;
    return Data;
}

void XGemm_top_InterruptGlobalEnable(XGemm_top *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_GIE, 1);
}

void XGemm_top_InterruptGlobalDisable(XGemm_top *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_GIE, 0);
}

void XGemm_top_InterruptEnable(XGemm_top *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_IER);
    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_IER, Register | Mask);
}

void XGemm_top_InterruptDisable(XGemm_top *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_IER);
    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_IER, Register & (~Mask));
}

void XGemm_top_InterruptClear(XGemm_top *InstancePtr, u32 Mask) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XGemm_top_WriteReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_ISR, Mask);
}

u32 XGemm_top_InterruptGetEnabled(XGemm_top *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_IER);
}

u32 XGemm_top_InterruptGetStatus(XGemm_top *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XGemm_top_ReadReg(InstancePtr->Control_BaseAddress, XGEMM_TOP_CONTROL_ADDR_ISR);
}

