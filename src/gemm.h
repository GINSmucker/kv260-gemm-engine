#ifndef RMP_SPARSE_GEMM_GEMM_H
#define RMP_SPARSE_GEMM_GEMM_H

#include <ap_int.h>

// Week-1 baseline dimensions. The kernel computes Y = XW where:
//   X is GEMM_M x GEMM_K, W is GEMM_K x GEMM_N,
//   Y is GEMM_M x GEMM_N. All matrices use row-major storage.
#define GEMM_M 8
#define GEMM_K 8
#define GEMM_N 8

#define GEMM_X_SIZE (GEMM_M * GEMM_K)
#define GEMM_W_SIZE (GEMM_K * GEMM_N)
#define GEMM_Y_SIZE (GEMM_M * GEMM_N)

typedef ap_int<8> gemm_input_t;
typedef ap_int<8> gemm_weight_t;
typedef ap_int<32> gemm_output_t;

void gemm_top(
    const gemm_input_t X[GEMM_X_SIZE],
    const gemm_weight_t W[GEMM_W_SIZE],
    gemm_output_t Y[GEMM_Y_SIZE]);

#endif  // RMP_SPARSE_GEMM_GEMM_H
