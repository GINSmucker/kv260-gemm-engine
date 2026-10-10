#include "../include/model_gemm_config.hpp"
#include "model_gemm_core.hpp"

extern "C" int model_gemm_top(
    const axi_word_t *X,
    const axi_word_t *W,
    axi_word_t       *Y,
    int M,
    int K,
    int N) {

#pragma HLS INTERFACE m_axi port=X offset=slave bundle=gmem0 depth=MAX_X_WORDS \
    max_read_burst_length=64 num_read_outstanding=16 latency=64
#pragma HLS INTERFACE m_axi port=W offset=slave bundle=gmem1 depth=MAX_W_WORDS \
    max_read_burst_length=64 num_read_outstanding=16 latency=64
#pragma HLS INTERFACE m_axi port=Y offset=slave bundle=gmem2 depth=MAX_Y_WORDS \
    max_write_burst_length=64 num_write_outstanding=16 latency=64

#pragma HLS INTERFACE s_axilite port=X bundle=control
#pragma HLS INTERFACE s_axilite port=W bundle=control
#pragma HLS INTERFACE s_axilite port=Y bundle=control
#pragma HLS INTERFACE s_axilite port=M bundle=control
#pragma HLS INTERFACE s_axilite port=K bundle=control
#pragma HLS INTERFACE s_axilite port=N bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control

    if (!is_supported_shape(M, K, N)) {
        return GEMM_ERR_UNSUPPORTED_SHAPE;
    }

    // Explicit 128-bit packing requires K to be a multiple of 16 and N to be
    // a multiple of 4. Every currently supported model shape satisfies this.
    if ((K & 15) != 0 || (N & 3) != 0) {
        return GEMM_ERR_ALIGNMENT;
    }

    // Cache W once. It is reused by every M tile. The sentinel word makes the
    // final split-vector read safe without changing the external allocation.
    static axi_word_t w_buffer[MAX_W_WORDS + 1];
#pragma HLS BIND_STORAGE variable=w_buffer type=RAM_2P impl=BRAM

    const int w_words = (K * N + INT8_PER_AXI - 1) / INT8_PER_AXI;
LOAD_W:
    for (int i = 0; i < w_words; ++i) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=2304 max=12000
        w_buffer[i] = W[i];
    }
    w_buffer[w_words] = 0;

    dense_layer_opt40(X, w_buffer, Y, M, K, N);
    return GEMM_OK;
}
