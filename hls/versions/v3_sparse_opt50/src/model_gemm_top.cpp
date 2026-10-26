#include "model_gemm_core.hpp"

uint32_t model_gemm_top(const model_gemm::wide_t* X,
                       const model_gemm::wide_t* W_data,
                       const model_gemm::wide_t* W_meta,
                       model_gemm::wide_t* Y,
                       uint32_t M,uint32_t K,uint32_t N,uint32_t mode) {
#pragma HLS INTERFACE m_axi port=X offset=slave bundle=gmem0 depth=7092 max_read_burst_length=16 num_read_outstanding=16 max_widen_bitwidth=128
#pragma HLS INTERFACE m_axi port=W_data offset=slave bundle=gmem1 depth=6912 max_read_burst_length=16 num_read_outstanding=16 max_widen_bitwidth=128
#pragma HLS INTERFACE m_axi port=W_meta offset=slave bundle=gmem1 depth=864 max_read_burst_length=16 num_read_outstanding=16 max_widen_bitwidth=128
#pragma HLS INTERFACE m_axi port=Y offset=slave bundle=gmem2 depth=28368 max_write_burst_length=64 max_widen_bitwidth=128
#pragma HLS INTERFACE s_axilite port=X bundle=control
#pragma HLS INTERFACE s_axilite port=W_data bundle=control
#pragma HLS INTERFACE s_axilite port=W_meta bundle=control
#pragma HLS INTERFACE s_axilite port=Y bundle=control
#pragma HLS INTERFACE s_axilite port=M bundle=control
#pragma HLS INTERFACE s_axilite port=K bundle=control
#pragma HLS INTERFACE s_axilite port=N bundle=control
#pragma HLS INTERFACE s_axilite port=mode bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
#pragma HLS INTERFACE ap_ctrl_hs port=return
    using namespace model_gemm;
    if(mode!=MODE_DENSE && mode!=MODE_SPARSE24) return INVALID_MODE;
    if(!valid_shape(M,K,N)) return INVALID_SHAPE;

    data_t wbank[16][W_BANK_DEPTH];
    wide_t metadata[META_WORDS_PHYSICAL];
#pragma HLS ARRAY_PARTITION variable=wbank complete dim=1
#pragma HLS BIND_STORAGE variable=wbank type=RAM_2P impl=BRAM latency=1
#pragma HLS BIND_STORAGE variable=metadata type=RAM_1P impl=BRAM latency=1
    const bool sparse=mode==MODE_SPARSE24;
    const unsigned words=sparse ? K*N/32 : K*N/16;
    load_weight_data(W_data,wbank,words);
    if(sparse) {
        const unsigned meta_words=K*N/128;
        load_metadata(W_meta,metadata,meta_words);
        if(!valid_metadata(metadata,meta_words)) return INVALID_METADATA;
    } else {
        // Dense must issue ZERO AXI reads on W_meta, including on reused calls.
        metadata[0]=0;
    }
    // Only after all metadata has been checked may the DATAFLOW writer start.
    // Otherwise an early-return producer could leave a waiting consumer stuck.
    shared_layer(X,wbank,metadata,Y,M,K,N,mode);
    return OK;
}
