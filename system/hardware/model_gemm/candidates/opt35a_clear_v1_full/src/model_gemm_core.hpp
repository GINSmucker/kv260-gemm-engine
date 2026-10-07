#ifndef MODEL_GEMM_CORE_HPP
#define MODEL_GEMM_CORE_HPP
#include "../include/model_gemm_config.hpp"

namespace model_gemm {
inline input_t signed_lane(uint32_t word, unsigned lane) {
#pragma HLS INLINE
    const int32_t byte=static_cast<int32_t>((word>>(lane*8))&255u);
    return static_cast<input_t>(byte>=128 ? byte-256 : byte);
}
inline void dense_layer(const uint32_t* X, const uint32_t* W, output_t* Y,
                        uint32_t M, uint32_t K, uint32_t N) {
    uint32_t xt[TM][TK/4];
    input_t wbank[8][24000];
    output_t acc[TM][TN];
    // Exactly eight X rows and eight W columns feed one outer product.
    // Each independent accumulator bank is updated once per K step.
#pragma HLS ARRAY_PARTITION variable=xt complete dim=1
#pragma HLS ARRAY_PARTITION variable=wbank complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc cyclic factor=8 dim=2

    // Entire current W is reloaded on EVERY call. No cross-call cache semantics.
    // Flat byte index maps to bank=flat%8 and address=flat/8.
    const uint32_t w_words=K*N/4;
    full_w_words: for (uint32_t word=0;word<w_words;++word) {
#pragma HLS LOOP_TRIPCOUNT min=9216 max=48000
#pragma HLS PIPELINE II=1
        const uint32_t value=W[word];
        const uint32_t addr=word/2;
        if ((word&1u)==0) {
            wbank[0][addr]=signed_lane(value,0);
            wbank[1][addr]=signed_lane(value,1);
            wbank[2][addr]=signed_lane(value,2);
            wbank[3][addr]=signed_lane(value,3);
        } else {
            wbank[4][addr]=signed_lane(value,0);
            wbank[5][addr]=signed_lane(value,1);
            wbank[6][addr]=signed_lane(value,2);
            wbank[7][addr]=signed_lane(value,3);
        }
    }
    const uint32_t w_stride=N/8;
    m_tiles: for (uint32_t m0=0; m0<M; m0+=TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=25
        n_tiles: for (uint32_t n0=0; n0<N; n0+=TN) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=16
            const unsigned valid_n=N-n0<TN ? N-n0 : TN;
            // One write to each of the existing 64 banks per cycle, eight
            // addresses per bank. No first-K branch or dependence override.
            clear_groups: for (unsigned group=0; group<TN/MICRO_N; ++group) {
#pragma HLS PIPELINE II=1
                clear_rows: for (unsigned mm=0; mm<TM; ++mm) {
#pragma HLS UNROLL
                    clear_cols: for (unsigned nn=0; nn<MICRO_N; ++nn) {
#pragma HLS UNROLL
                        acc[mm][group*MICRO_N+nn]=0;
                    }
                }
            }
            k_tiles: for (uint32_t k0=0; k0<K; k0+=TK) {
#pragma HLS LOOP_TRIPCOUNT min=6 max=24
                x_rows: for (unsigned mm=0; mm<TM; ++mm) {
                    // All whitelisted K are multiples of TK. Guard outside the
                    // contiguous loop enables burst inference without extra reads.
                    if (m0+mm<M) {
                        const uint32_t base=((m0+mm)*K+k0)/4;
                        x_words: for (unsigned kw=0; kw<TK/4; ++kw) {
#pragma HLS PIPELINE II=1
                            xt[mm][kw]=X[base+kw];
                        }
                    } else {
                        x_zero: for (unsigned kw=0; kw<TK/4; ++kw) {
#pragma HLS PIPELINE II=1
                            xt[mm][kw]=0;
                        }
                    }
                }
                // Every whitelisted N is divisible by 8. Bound the outer
                // microtile loop itself: HLS can speculate a masked RAM read.
                n_micro: for (unsigned ns=0; ns<valid_n; ns+=MICRO_N) {
#pragma HLS LOOP_TRIPCOUNT min=5 max=8
                    outer_k: for (unsigned kk=0; kk<TK; ++kk) {
#pragma HLS PIPELINE II=1
                        input_t w8[8];
#pragma HLS ARRAY_PARTITION variable=w8 complete dim=1
                        const uint32_t addr=(k0+kk)*w_stride+(n0+ns)/8;
                        // Keep even a speculative pipeline-exit read in bounds.
                        // Valid arithmetic iterations always use the original addr.
                        const uint32_t safe_addr=addr<w_words/2 ? addr : 0;
                        supply_w: for (unsigned lane=0;lane<8;++lane) {
#pragma HLS UNROLL
                            // All eight lanes are valid for an entered microtile.
                            w8[lane]=wbank[lane][safe_addr];
                        }
                        micro_m: for (unsigned mm=0; mm<MICRO_M; ++mm) {
#pragma HLS UNROLL
                            micro_n: for (unsigned nn=0; nn<MICRO_N; ++nn) {
#pragma HLS UNROLL
                                // int8 inputs explicitly sign-extend before multiplication.
                                const product_t product=static_cast<product_t>(
                                    static_cast<int32_t>(signed_lane(xt[mm][kk/4],kk%4)) *
                                    static_cast<int32_t>(w8[nn]));
                                acc[mm][ns+nn]+=static_cast<output_t>(product);
                            }
                        }
                    }
                }
            }
            y_rows: for (unsigned mm=0; mm<TM; ++mm) {
                if (m0+mm<M) {
                    const uint32_t base=(m0+mm)*N+n0;
                    y_cols: for (unsigned nn=0; nn<valid_n; ++nn) {
#pragma HLS LOOP_TRIPCOUNT min=40 max=64
#pragma HLS PIPELINE II=1
                        Y[base+nn]=acc[mm][nn];
                    }
                }
            }
        }
    }
}
}
#endif
