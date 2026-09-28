// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2026.1 (64-bit)
// Tool Version Limit: 2026.06
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef __linux__

#include "xstatus.h"
#ifdef SDT
#include "xparameters.h"
#endif
#include "xgemm_top.h"

extern XGemm_top_Config XGemm_top_ConfigTable[];

#ifdef SDT
XGemm_top_Config *XGemm_top_LookupConfig(UINTPTR BaseAddress) {
	XGemm_top_Config *ConfigPtr = NULL;

	int Index;

	for (Index = (u32)0x0; XGemm_top_ConfigTable[Index].Name != NULL; Index++) {
		if (!BaseAddress || XGemm_top_ConfigTable[Index].Control_BaseAddress == BaseAddress) {
			ConfigPtr = &XGemm_top_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XGemm_top_Initialize(XGemm_top *InstancePtr, UINTPTR BaseAddress) {
	XGemm_top_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XGemm_top_LookupConfig(BaseAddress);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XGemm_top_CfgInitialize(InstancePtr, ConfigPtr);
}
#else
XGemm_top_Config *XGemm_top_LookupConfig(u16 DeviceId) {
	XGemm_top_Config *ConfigPtr = NULL;

	int Index;

	for (Index = 0; Index < XPAR_XGEMM_TOP_NUM_INSTANCES; Index++) {
		if (XGemm_top_ConfigTable[Index].DeviceId == DeviceId) {
			ConfigPtr = &XGemm_top_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XGemm_top_Initialize(XGemm_top *InstancePtr, u16 DeviceId) {
	XGemm_top_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XGemm_top_LookupConfig(DeviceId);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XGemm_top_CfgInitialize(InstancePtr, ConfigPtr);
}
#endif

#endif

