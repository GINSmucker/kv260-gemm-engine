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
    uint32_t xt[TM][256];
    input_t wbank[16][12000];
    output_t acc[TM][MICRO_N][TN/MICRO_N];
    // Eight X rows and sixteen W columns feed one outer product.
    // Each independent accumulator bank is updated once per K step.
#pragma HLS ARRAY_PARTITION variable=xt complete dim=1
#pragma HLS ARRAY_PARTITION variable=wbank complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc complete dim=2
#pragma HLS BIND_STORAGE variable=acc type=RAM_2P impl=LUTRAM latency=1

    // Entire current W is reloaded on EVERY call. No cross-call cache semantics.
    // Compact flat byte index: bank=flat%16, address=flat/16. No row padding.
    const uint32_t w_words=K*N/4;
    full_w_words: for (uint32_t word=0;word<w_words;++word) {
#pragma HLS LOOP_TRIPCOUNT min=9216 max=48000
#pragma HLS PIPELINE II=1
        const uint32_t value=W[word];
        const uint32_t addr=word/4;
        if ((word&3u)==0) {
            wbank[0][addr]=signed_lane(value,0);
            wbank[1][addr]=signed_lane(value,1);
            wbank[2][addr]=signed_lane(value,2);
            wbank[3][addr]=signed_lane(value,3);
        } else if ((word&3u)==1) {
            wbank[4][addr]=signed_lane(value,0);
            wbank[5][addr]=signed_lane(value,1);
            wbank[6][addr]=signed_lane(value,2);
            wbank[7][addr]=signed_lane(value,3);
        } else if ((word&3u)==2) {
            wbank[8][addr]=signed_lane(value,0);
            wbank[9][addr]=signed_lane(value,1);
            wbank[10][addr]=signed_lane(value,2);
            wbank[11][addr]=signed_lane(value,3);
        } else {
            wbank[12][addr]=signed_lane(value,0);
            wbank[13][addr]=signed_lane(value,1);
            wbank[14][addr]=signed_lane(value,2);
            wbank[15][addr]=signed_lane(value,3);
        }
    }
    m_tiles: for (uint32_t m0=0; m0<M; m0+=TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=25
        // Initialize every physical address, including padding and invalid rows.
        // Power-of-two physical depth and direct addresses avoid the failed
        // variable-bound/clamp-helper exit mux used in OPT32.
        x_init_words: for (unsigned word=0; word<256; ++word) {
#pragma HLS PIPELINE II=1
            x_init_rows: for (unsigned row=0; row<TM; ++row) {
#pragma HLS UNROLL
                xt[row][word&255u]=0;
            }
        }
        x_rows: for (unsigned mm=0; mm<TM; ++mm) {
            if (m0+mm<M) {
                const uint32_t base=(m0+mm)*K/4;
                x_words: for (unsigned kw=0; kw<K/4; ++kw) {
#pragma HLS LOOP_TRIPCOUNT min=48 max=192
#pragma HLS PIPELINE II=1
                    xt[mm][kw&255u]=X[base+kw];
                }
            }
        }
        n_tiles: for (uint32_t n0=0; n0<N; n0+=TN) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=16
            const unsigned valid_n=N-n0<TN ? N-n0 : TN;
            // One write per accumulator bank, four addresses per bank.
            clear_groups: for (unsigned group=0; group<TN/MICRO_N; ++group) {
#pragma HLS PIPELINE II=1
                clear_rows: for (unsigned mm=0; mm<TM; ++mm) {
#pragma HLS UNROLL
                    clear_cols: for (unsigned nn=0; nn<MICRO_N; ++nn) {
#pragma HLS UNROLL
                        acc[mm][nn][group]=0;
                    }
                }
            }
            // X and W are resident: accumulate complete K without short restarts.
            // Head N=1000 has an eight-column tail in a sixteen-column group.
            n_micro: for (unsigned ns=0; ns<valid_n; ns+=MICRO_N) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=4
                outer_k: for (unsigned kk=0; kk<K; ++kk) {
#pragma HLS LOOP_TRIPCOUNT min=192 max=768
#pragma HLS PIPELINE II=1
                    input_t w16[16];
#pragma HLS ARRAY_PARTITION variable=w16 complete dim=1
                    const uint32_t flat=kk*N+n0+ns;
                    const bool rotate=(flat&8u)!=0;
                    const uint32_t a0=flat/16, a1=(flat+8)/16;
                    const uint32_t safe0=a0<w_words/4 ? a0 : 0;
                    const uint32_t safe1=a1<w_words/4 ? a1 : 0;
                    // For odd head rows, banks 8..15 supply the first half;
                    // banks 0..7 at the next address supply the second half.
                    // The final invalid half may read only clamped addresses.
                    supply_w: for (unsigned lane=0;lane<8;++lane) {
#pragma HLS UNROLL
                        const input_t lo=wbank[lane][rotate ? safe1 : safe0];
                        const input_t hi=wbank[lane+8][safe0];
                        w16[lane]=rotate ? hi : lo;
                        w16[lane+8]=ns+8<valid_n ? (rotate ? lo : hi) : 0;
                    }
                    micro_m: for (unsigned mm=0; mm<MICRO_M; ++mm) {
#pragma HLS UNROLL
                        micro_n: for (unsigned nn=0; nn<MICRO_N; ++nn) {
#pragma HLS UNROLL
                            // int8 inputs explicitly sign-extend before multiplication.
                            const product_t product=static_cast<product_t>(
                                static_cast<int32_t>(signed_lane(xt[mm][(kk/4)&255u],kk%4)) *
                                static_cast<int32_t>(w16[nn]));
                            acc[mm][nn][ns/MICRO_N]+=static_cast<output_t>(product);
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
                        Y[base+nn]=acc[mm][nn%MICRO_N][nn/MICRO_N];
                    }
                }
            }
        }
    }
}
}
#endif
