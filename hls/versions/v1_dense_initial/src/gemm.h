#ifndef RMP_SPARSE_GEMM_GEMM_H
#define RMP_SPARSE_GEMM_GEMM_H

#include <ap_int.h>

// Runtime matrix limits for the v0.2 tiled dense GEMM.
// The limits describe the supported host buffers; only one tile is stored
// on-chip at a time.
#define GEMM_MAX_M 1024
#define GEMM_MAX_K 1024
#define GEMM_MAX_N 1024

#define GEMM_MAX_X_SIZE (GEMM_MAX_M * GEMM_MAX_K)
#define GEMM_MAX_W_SIZE (GEMM_MAX_K * GEMM_MAX_N)
#define GEMM_MAX_Y_SIZE (GEMM_MAX_M * GEMM_MAX_N)

// Fixed hardware tile and K-lane parallelism.
#define GEMM_TM 16
#define GEMM_TK 32
#define GEMM_TN 16
#define GEMM_PAR_K 4

typedef ap_int<8> gemm_input_t;
typedef ap_int<8> gemm_weight_t;
typedef ap_int<32> gemm_output_t;

// Computes Y[M x N] = X[M x K] * W[K x N].
// All matrices are stored contiguously in row-major order.
void gemm_top(
    const gemm_input_t X[GEMM_MAX_X_SIZE],
    const gemm_weight_t W[GEMM_MAX_W_SIZE],
    gemm_output_t Y[GEMM_MAX_Y_SIZE],
    int M,
    int N,
    int K);

#endif  // RMP_SPARSE_GEMM_GEMM_H
