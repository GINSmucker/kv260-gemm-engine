#include "gemm.h"

void gemm_top(
    const gemm_input_t X[GEMM_X_SIZE],
    const gemm_weight_t W[GEMM_W_SIZE],
    gemm_output_t Y[GEMM_Y_SIZE]) {
    // Matrix data are accessed directly in external memory through three
    // independent AXI4 master interfaces. Base addresses and start/done
    // control are exposed through the AXI4-Lite control interface.
#pragma HLS INTERFACE m_axi port=X offset=slave bundle=gmem0 depth=GEMM_X_SIZE
#pragma HLS INTERFACE m_axi port=W offset=slave bundle=gmem1 depth=GEMM_W_SIZE
#pragma HLS INTERFACE m_axi port=Y offset=slave bundle=gmem2 depth=GEMM_Y_SIZE

#pragma HLS INTERFACE s_axilite port=X bundle=control
#pragma HLS INTERFACE s_axilite port=W bundle=control
#pragma HLS INTERFACE s_axilite port=Y bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control

    /* Naive reference architecture: no manual PIPELINE, UNROLL, DATAFLOW,
     ARRAY_PARTITION, or local-buffer optimization is applied here.*/
    //Gin：一维数组便于ARRAY_PARTITION吗？   
    for (int i = 0; i < GEMM_M; ++i) {
        for (int j = 0; j < GEMM_N; ++j) {
            gemm_output_t accumulator = 0;

            for (int k = 0; k < GEMM_K; ++k) {
                const gemm_input_t x_value = X[i * GEMM_K + k];
                const gemm_weight_t w_value = W[k * GEMM_N + j];
                const ap_int<16> product = x_value * w_value;
                accumulator += product;
            }

            Y[i * GEMM_N + j] = accumulator;
        }
    }
}
