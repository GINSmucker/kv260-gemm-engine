#ifndef MODEL_GEMM_CONFIG_HPP
#define MODEL_GEMM_CONFIG_HPP

#include <ap_int.h>

// OPT40 external memory beat: 128 bits.
// X/W contain 16 signed INT8 values per beat.
// Y contains 4 signed INT32 values per beat.
typedef ap_uint<128> axi_word_t;
typedef ap_int<8>    data_t;
typedef ap_int<32>   acc_t;

static const int AXI_BITS       = 128;
static const int INT8_PER_AXI   = 16;
static const int INT32_PER_AXI  = 4;

// Supported model dimensions.
static const int MAX_M = 197;
static const int MAX_K = 768;
static const int MAX_N = 1000;

// OPT40 tiling and compute-array dimensions.
// One pipelined K iteration performs 16 x 16 = 256 MACs.
static const int TM      = 16;
static const int TN      = 16;
static const int TK      = 32;
static const int MICRO_M = 16;
static const int MICRO_N = 16;

static const int MAX_X_ELEMS = MAX_M * MAX_K;
static const int MAX_W_ELEMS = 192 * 1000; // largest legal K*N
static const int MAX_Y_ELEMS = MAX_M * 768;

static const int MAX_X_WORDS = (MAX_X_ELEMS + INT8_PER_AXI - 1) / INT8_PER_AXI;
static const int MAX_W_WORDS = (MAX_W_ELEMS + INT8_PER_AXI - 1) / INT8_PER_AXI;
static const int MAX_Y_WORDS = (MAX_Y_ELEMS + INT32_PER_AXI - 1) / INT32_PER_AXI;

static const int X_WORDS_PER_ROW_MAX = (MAX_K + INT8_PER_AXI - 1) / INT8_PER_AXI;

enum GemmStatus {
    GEMM_OK = 0,
    GEMM_ERR_UNSUPPORTED_SHAPE = 1,
    GEMM_ERR_ALIGNMENT = 2
};

static bool is_supported_shape(int M, int K, int N) {
#pragma HLS INLINE
    return (M == 1   && K == 192 && N == 1000) ||
           (M == 197 && K == 192 && N == 192)  ||
           (M == 197 && K == 192 && N == 576)  ||
           (M == 197 && K == 192 && N == 768)  ||
           (M == 197 && K == 768 && N == 192);
}

extern "C" int model_gemm_top(
    const axi_word_t *X,
    const axi_word_t *W,
    axi_word_t       *Y,
    int M,
    int K,
    int N);

#endif
