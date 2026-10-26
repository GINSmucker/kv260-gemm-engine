#include "model_gemm_core.hpp"

uint32_t model_gemm_top(const model_gemm::wide_t* X,
                       const model_gemm::wide_t* W,
                       model_gemm::wide_t* Y,
                       uint32_t M, uint32_t K, uint32_t N) {
    // Literal capacities are verified against the one shared capability table.
    // 2020.2.2 rejects macro names in depth=, so these are explicit constants.
#pragma HLS INTERFACE m_axi port=X offset=slave bundle=gmem0 depth=9456 max_read_burst_length=16 num_read_outstanding=16
#pragma HLS INTERFACE m_axi port=W offset=slave bundle=gmem1 depth=12000 max_read_burst_length=16 num_read_outstanding=16
#pragma HLS INTERFACE m_axi port=Y offset=slave bundle=gmem2 depth=37824 max_write_burst_length=64
#pragma HLS INTERFACE s_axilite port=X bundle=control
#pragma HLS INTERFACE s_axilite port=W bundle=control
#pragma HLS INTERFACE s_axilite port=Y bundle=control
#pragma HLS INTERFACE s_axilite port=M bundle=control
#pragma HLS INTERFACE s_axilite port=K bundle=control
#pragma HLS INTERFACE s_axilite port=N bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
#pragma HLS INTERFACE ap_ctrl_hs port=return
    if (!model_gemm::valid_shape(M,K,N)) return model_gemm::INVALID_SHAPE;
    model_gemm::dense_layer(X,W,Y,M,K,N);
    return model_gemm::OK;
}
