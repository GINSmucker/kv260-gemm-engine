// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2026.1 (64-bit)
// Tool Version Limit: 2026.06
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef XGEMM_TOP_H
#define XGEMM_TOP_H

#ifdef __cplusplus
extern "C" {
#endif

/***************************** Include Files *********************************/
#ifndef __linux__
#include "xil_types.h"
#include "xil_assert.h"
#include "xstatus.h"
#include "xil_io.h"
#else
#include <stdint.h>
#include <assert.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stddef.h>
#endif
#include "xgemm_top_hw.h"

/**************************** Type Definitions ******************************/
#ifdef __linux__
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#else
typedef struct {
#ifdef SDT
    char *Name;
#else
    u16 DeviceId;
#endif
    u64 Control_BaseAddress;
} XGemm_top_Config;
#endif

typedef struct {
    u64 Control_BaseAddress;
    u32 IsReady;
} XGemm_top;

typedef u32 word_type;

/***************** Macros (Inline Functions) Definitions *********************/
#ifndef __linux__
#define XGemm_top_WriteReg(BaseAddress, RegOffset, Data) \
    Xil_Out32((BaseAddress) + (RegOffset), (u32)(Data))
#define XGemm_top_ReadReg(BaseAddress, RegOffset) \
    Xil_In32((BaseAddress) + (RegOffset))
#else
#define XGemm_top_WriteReg(BaseAddress, RegOffset, Data) \
    *(volatile u32*)((BaseAddress) + (RegOffset)) = (u32)(Data)
#define XGemm_top_ReadReg(BaseAddress, RegOffset) \
    *(volatile u32*)((BaseAddress) + (RegOffset))

#define Xil_AssertVoid(expr)    assert(expr)
#define Xil_AssertNonvoid(expr) assert(expr)

#define XST_SUCCESS             0
#define XST_DEVICE_NOT_FOUND    2
#define XST_OPEN_DEVICE_FAILED  3
#define XIL_COMPONENT_IS_READY  1
#endif

/************************** Function Prototypes *****************************/
#ifndef __linux__
#ifdef SDT
int XGemm_top_Initialize(XGemm_top *InstancePtr, UINTPTR BaseAddress);
XGemm_top_Config* XGemm_top_LookupConfig(UINTPTR BaseAddress);
#else
int XGemm_top_Initialize(XGemm_top *InstancePtr, u16 DeviceId);
XGemm_top_Config* XGemm_top_LookupConfig(u16 DeviceId);
#endif
int XGemm_top_CfgInitialize(XGemm_top *InstancePtr, XGemm_top_Config *ConfigPtr);
#else
int XGemm_top_Initialize(XGemm_top *InstancePtr, const char* InstanceName);
int XGemm_top_Release(XGemm_top *InstancePtr);
#endif

void XGemm_top_Start(XGemm_top *InstancePtr);
u32 XGemm_top_IsDone(XGemm_top *InstancePtr);
u32 XGemm_top_IsIdle(XGemm_top *InstancePtr);
u32 XGemm_top_IsReady(XGemm_top *InstancePtr);
void XGemm_top_Continue(XGemm_top *InstancePtr);
void XGemm_top_EnableAutoRestart(XGemm_top *InstancePtr);
void XGemm_top_DisableAutoRestart(XGemm_top *InstancePtr);

void XGemm_top_Set_X(XGemm_top *InstancePtr, u64 Data);
u64 XGemm_top_Get_X(XGemm_top *InstancePtr);
void XGemm_top_Set_W(XGemm_top *InstancePtr, u64 Data);
u64 XGemm_top_Get_W(XGemm_top *InstancePtr);
void XGemm_top_Set_Y(XGemm_top *InstancePtr, u64 Data);
u64 XGemm_top_Get_Y(XGemm_top *InstancePtr);

void XGemm_top_InterruptGlobalEnable(XGemm_top *InstancePtr);
void XGemm_top_InterruptGlobalDisable(XGemm_top *InstancePtr);
void XGemm_top_InterruptEnable(XGemm_top *InstancePtr, u32 Mask);
void XGemm_top_InterruptDisable(XGemm_top *InstancePtr, u32 Mask);
void XGemm_top_InterruptClear(XGemm_top *InstancePtr, u32 Mask);
u32 XGemm_top_InterruptGetEnabled(XGemm_top *InstancePtr);
u32 XGemm_top_InterruptGetStatus(XGemm_top *InstancePtr);

#ifdef __cplusplus
}
#endif

#endif
