#ifndef MODEL_GEMM_SPARSE24_CONFIG_HPP
#define MODEL_GEMM_SPARSE24_CONFIG_HPP

#include <stdint.h>
#include <ap_int.h>

namespace model_gemm {
typedef ap_uint<128> wide_t;
typedef ap_int<8> data_t;
typedef ap_int<16> product_t;
typedef int32_t output_t;

static const uint32_t MODE_DENSE=0, MODE_SPARSE24=1;
enum Error { OK=0, INVALID_SHAPE=1, INVALID_MODE=2, INVALID_METADATA=3 };

// Hardware capability subset of H576_SPARSE24_SPLIT128_V1, NOT a new format.
// The original head/proj remain PS-routed. Do not copy OPT46's MLP K/N=768.
static const unsigned MAX_M=197, MAX_K=576, MAX_N=576;
static const unsigned MAX_WEIGHT_ELEMS=110592;
static const unsigned MAX_X_WORDS=7092;
static const unsigned MAX_W_DATA_WORDS=6912;
static const unsigned MAX_W_META_WORDS=864;
static const unsigned MAX_Y_WORDS=28368;
static const unsigned TM=32, MICRO_M=32, MICRO_N=16;
static const unsigned X_ROW_WORDS_PHYSICAL=64;
static const unsigned W_BANK_DEPTH=8192;
static const unsigned META_WORDS_PHYSICAL=1024;
static const unsigned OUTPUT_ROWS=16;
static const unsigned OUTPUT_GROUPS=MAX_N/MICRO_N;
static const unsigned OUTPUT_BLOCK_WORDS=OUTPUT_ROWS*OUTPUT_GROUPS;

static_assert(MAX_X_WORDS*16==MAX_M*MAX_K, "X interface depth");
static_assert(MAX_W_DATA_WORDS*16==MAX_WEIGHT_ELEMS, "W data interface depth");
static_assert(MAX_W_META_WORDS*32==MAX_WEIGHT_ELEMS/4, "Metadata interface depth");
static_assert(MAX_Y_WORDS*4==MAX_M*MAX_N, "Y interface depth");
static_assert(TM==MICRO_M && MICRO_N==16, "One shared 32x16 MAC array");
static_assert(TM==2*OUTPUT_ROWS, "Two output slabs per full M tile");
static_assert(MAX_K/16<=X_ROW_WORDS_PHYSICAL, "X local capacity");
static_assert(MAX_W_DATA_WORDS<=W_BANK_DEPTH && MAX_W_META_WORDS<=META_WORDS_PHYSICAL,
              "Resident weight capacity");

inline bool valid_shape(uint32_t M,uint32_t K,uint32_t N) {
#pragma HLS INLINE
    // Bounds before product keep the 32-bit length calculation safe.
    if(M==0 || M>MAX_M || K==0 || K>MAX_K || N==0 || N>MAX_N) return false;
    if((K&15u)!=0 || (N&15u)!=0) return false;
    return K*N<=MAX_WEIGHT_ELEMS;
}
}

// One top only, with runtime mode. A NEW control ABI, not old C runtime offsets.
uint32_t model_gemm_top(const model_gemm::wide_t* X,
                       const model_gemm::wide_t* W_data,
                       const model_gemm::wide_t* W_meta,
                       model_gemm::wide_t* Y,
                       uint32_t M,uint32_t K,uint32_t N,uint32_t mode);
#endif
