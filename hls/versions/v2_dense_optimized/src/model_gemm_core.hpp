#ifndef MODEL_GEMM_CORE_HPP
#define MODEL_GEMM_CORE_HPP
#include "../include/model_gemm_config.hpp"
#include <hls_streamofblocks.h>

namespace model_gemm {
// OPT46 changes only result hand-off. The external AXI word remains 128 bits.
// Each block holds at most 16 rows x 768 INT32 results = 48 KiB.
// Two BRAM blocks decouple the single compute engine from the AXI writer.
static const unsigned OUTPUT_ROWS=16;
static const unsigned OUTPUT_GROUPS=TN/MICRO_N;
typedef ap_uint<512> output_pack_t;
typedef output_pack_t output_block_t[OUTPUT_ROWS*OUTPUT_GROUPS];
typedef hls::stream_of_blocks<output_block_t,2> output_blocks_t;
static_assert(TM%OUTPUT_ROWS==0 && MICRO_N==16 && TN%MICRO_N==0,
              "OPT46 output packing requires 16-column groups and whole slabs");

inline input_t signed_lane(uint32_t word, unsigned lane) {
#pragma HLS INLINE
    const int32_t byte=static_cast<int32_t>((word>>(lane*8))&255u);
    return static_cast<input_t>(byte>=128 ? byte-256 : byte);
}
inline void compute_and_pack(const wide_t* X, const wide_t* W,
                             output_blocks_t& output_blocks,
                             uint32_t M, uint32_t K, uint32_t N) {
#pragma HLS INLINE off
    wide_t xt[TM][64];
    input_t wbank[16][12000];
    output_t acc[TM][MICRO_N][TN/MICRO_N];
    // Thirty-two X rows and sixteen W columns feed one outer product.
    // Each independent accumulator bank is updated once per K step.
#pragma HLS ARRAY_PARTITION variable=xt complete dim=1
#pragma HLS BIND_STORAGE variable=xt type=RAM_1P impl=LUTRAM latency=1
#pragma HLS ARRAY_PARTITION variable=wbank complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc complete dim=2
#pragma HLS BIND_STORAGE variable=acc type=RAM_2P impl=LUTRAM latency=1

    // Entire current W is reloaded on EVERY call. No cross-call cache semantics.
    // Compact flat byte index: bank=flat%16, address=flat/16. No row padding.
    const uint32_t w_words=K*N/16;
    full_w_words: for (uint32_t word=0;word<w_words;++word) {
#pragma HLS LOOP_TRIPCOUNT min=2304 max=12000
#pragma HLS PIPELINE II=1
        const wide_t value=W[word];
        load_lanes: for(unsigned lane=0;lane<16;++lane) {
#pragma HLS UNROLL
            const unsigned byte=value.range(8*lane+7,8*lane).to_uint();
            wbank[lane][word]=static_cast<input_t>(byte>=128 ? int(byte)-256 : int(byte));
        }
    }
    m_tiles: for (uint32_t m0=0; m0<M; m0+=TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=7
        // Initialize every physical address, including padding and invalid rows.
        // Power-of-two physical depth and direct addresses avoid the failed
        // variable-bound/clamp-helper exit mux used in OPT32.
        x_init_words: for (unsigned word=0; word<64; ++word) {
#pragma HLS PIPELINE II=1
            x_init_rows: for (unsigned row=0; row<TM; ++row) {
#pragma HLS UNROLL
                xt[row][word&63u]=0;
            }
        }
        x_rows: for (unsigned mm=0; mm<TM; ++mm) {
            if (m0+mm<M) {
                const uint32_t base=(m0+mm)*K/16;
                x_words: for (unsigned kw=0; kw<K/16; ++kw) {
#pragma HLS LOOP_TRIPCOUNT min=12 max=48
#pragma HLS PIPELINE II=1
                    xt[mm][kw&63u]=X[base+kw];
                }
            }
        }
        n_tiles: for (uint32_t n0=0; n0<N; n0+=TN) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2
            const unsigned valid_n=N-n0<TN ? N-n0 : TN;
            // One write per accumulator bank, forty-eight addresses per bank.
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
#pragma HLS LOOP_TRIPCOUNT min=4 max=48
                outer_k: for (unsigned kk=0; kk<K; ++kk) {
#pragma HLS LOOP_TRIPCOUNT min=192 max=768
#pragma HLS PIPELINE II=1
                    input_t w16[16];
#pragma HLS ARRAY_PARTITION variable=w16 complete dim=1
                    const uint32_t flat=kk*N+n0+ns;
                    const bool rotate=(flat&8u)!=0;
                    const uint32_t a0=flat/16, a1=(flat+8)/16;
                    const uint32_t safe0=a0<w_words ? a0 : 0;
                    const uint32_t safe1=a1<w_words ? a1 : 0;
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
                                static_cast<int32_t>(signed_lane((xt[mm][(kk/16)&63u] >> (32*((kk/4)%4))).to_uint(),kk%4)) *
                                static_cast<int32_t>(w16[nn]));
                            acc[mm][nn][ns/MICRO_N]+=static_cast<output_t>(product);
                        }
                    }
                }
            }
            const unsigned valid_m=M-m0<TM ? M-m0 : TM;
            const unsigned groups=(valid_n+MICRO_N-1)/MICRO_N;
            output_slabs: for (unsigned row0=0; row0<valid_m; row0+=OUTPUT_ROWS) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2
                // Acquiring a block waits for a FREE buffer. Leaving this scope
                // publishes it, even though the producer function is not done.
                hls::write_lock<output_block_t> block(output_blocks);
                const unsigned rows=valid_m-row0<OUTPUT_ROWS ? valid_m-row0 : OUTPUT_ROWS;
                pack_rows: for (unsigned row=0; row<rows; ++row) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
                    pack_groups: for (unsigned group=0; group<groups; ++group) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=48
#pragma HLS PIPELINE II=1
                        output_pack_t value=0;
                        pack_lanes: for(unsigned lane=0;lane<MICRO_N;++lane) {
#pragma HLS UNROLL
                            value.range(32*lane+31,32*lane)=static_cast<uint32_t>(
                                acc[row0+row][lane][group]);
                        }
                        // One BRAM word contains 16 INT32 results. Packing four
                        // AXI beats at once avoids a one-beat/cycle hand-off
                        // that would serialize compute almost as before.
                        block[row*OUTPUT_GROUPS+group]=value;
                    }
                }
            }
        }
    }
}

inline void write_output_blocks(output_blocks_t& output_blocks, wide_t* Y,
                                uint32_t M, uint32_t N) {
#pragma HLS INLINE off
    // This traversal MUST match compute_and_pack: m tile, n tile, row slab.
    // Dimensions determine each block's valid extent; no metadata FIFO is needed.
    write_m_tiles: for (uint32_t m0=0; m0<M; m0+=TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=7
        const unsigned valid_m=M-m0<TM ? M-m0 : TM;
        write_n_tiles: for (uint32_t n0=0; n0<N; n0+=TN) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2
            const unsigned valid_n=N-n0<TN ? N-n0 : TN;
            write_slabs: for (unsigned row0=0; row0<valid_m; row0+=OUTPUT_ROWS) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2
                // A READY block stays owned by this writer until every valid
                // row has been consumed, then its storage becomes FREE again.
                hls::read_lock<output_block_t> block(output_blocks);
                const unsigned rows=valid_m-row0<OUTPUT_ROWS ? valid_m-row0 : OUTPUT_ROWS;
                y_rows: for (unsigned row=0; row<rows; ++row) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
                    const uint32_t base=((m0+row0+row)*N+n0)/4;
                    // Keep the original contiguous full-row AXI write loop.
                    // A short final row/group never writes beyond valid_n.
                    y_cols: for (unsigned word=0; word<valid_n/4; ++word) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=192
#pragma HLS PIPELINE II=1
                        const output_pack_t packed=block[row*OUTPUT_GROUPS+word/4];
                        const unsigned lo=128*(word%4);
                        const wide_t value=packed.range(lo+127,lo);
                        Y[base+word]=value;
                    }
                }
            }
        }
    }
}

inline void dense_layer(const wide_t* X, const wide_t* W, wide_t* Y,
                        uint32_t M, uint32_t K, uint32_t N) {
#pragma HLS INLINE off
#pragma HLS DATAFLOW
    output_blocks_t output_blocks;
#pragma HLS BIND_STORAGE variable=output_blocks type=RAM_2P impl=bram
    compute_and_pack(X,W,output_blocks,M,K,N);
    write_output_blocks(output_blocks,Y,M,N);
}
}
#endif
