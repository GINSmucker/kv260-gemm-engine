#ifndef MODEL_GEMM_SPARSE24_CORE_HPP
#define MODEL_GEMM_SPARSE24_CORE_HPP

#include "../include/model_gemm_config.hpp"
#include <hls_streamofblocks.h>
#include <assert.h>

namespace model_gemm {
typedef ap_uint<512> output_pack_t;
typedef output_pack_t output_block_t[OUTPUT_BLOCK_WORDS];
typedef hls::stream_of_blocks<output_block_t,2> output_blocks_t;

inline data_t signed_byte(ap_uint<8> raw) {
#pragma HLS INLINE
    data_t value;
    value.range(7,0)=raw;
    return value;
}

inline void load_weight_data(const wide_t* W_data, data_t bank[16][W_BANK_DEPTH],
                             unsigned words) {
#pragma HLS INLINE off
#pragma HLS ARRAY_PARTITION variable=bank complete dim=1
    assert(words>0 && words<=MAX_W_DATA_WORDS);
    // No mode-dependent access, no early exit, no individual lane DDR reads.
    // Dense words=K*N/16; Sparse words=K*N/32. Both are exact for valid_shape.
    load_data_words: for(unsigned word=0;word<words;++word) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=6912
#pragma HLS PIPELINE II=1
        const wide_t value=W_data[word];
        load_data_bytes: for(unsigned lane=0;lane<16;++lane) {
#pragma HLS UNROLL
            bank[lane][word]=signed_byte(value.range(8*lane+7,8*lane));
        }
    }
}

inline void load_metadata(const wide_t* W_meta, wide_t cache[META_WORDS_PHYSICAL],
                          unsigned words) {
#pragma HLS INLINE off
    assert(words>0 && words<=MAX_W_META_WORDS);
    // Separate same-bundle read phase: never alternate W_data and W_meta here.
    load_metadata_words: for(unsigned word=0;word<words;++word) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=864
#pragma HLS PIPELINE II=1
        cache[word]=W_meta[word];
    }
}

inline bool valid_metadata(const wide_t cache[META_WORDS_PHYSICAL],unsigned words) {
#pragma HLS INLINE off
    // K%16=N%16=0 proves T=(K/4)*N is a multiple of 64: all loaded codes real.
    // Verify AFTER the Burst read loop. No dataflow consumer has started yet.
    bool bad=false;
    check_metadata_words: for(unsigned word=0;word<words;++word) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=864
#pragma HLS PIPELINE II=1
        const wide_t value=cache[word];
        bool word_bad=false;
        check_metadata_codes: for(unsigned r=0;r<32;++r) {
#pragma HLS UNROLL
            const ap_uint<4> code=value.range(4*r+3,4*r);
            const ap_uint<2> p0=code.range(1,0), p1=code.range(3,2);
            word_bad=word_bad || p0>=p1;
        }
        bad=bad || word_bad;
    }
    return !bad;
}

inline void compute_and_pack(const wide_t* X,
                             const data_t wbank[16][W_BANK_DEPTH],
                             const wide_t metadata[META_WORDS_PHYSICAL],
                             output_blocks_t& blocks,
                             uint32_t M,uint32_t K,uint32_t N,uint32_t mode) {
#pragma HLS INLINE off
#pragma HLS ARRAY_PARTITION variable=wbank complete dim=1
    wide_t xt[TM][X_ROW_WORDS_PHYSICAL];
#pragma HLS ARRAY_PARTITION variable=xt complete dim=1
#pragma HLS BIND_STORAGE variable=xt type=RAM_1P impl=LUTRAM latency=1
    output_t acc[MICRO_M][MICRO_N];
#pragma HLS ARRAY_PARTITION variable=acc complete dim=0
    output_pack_t pending_upper[OUTPUT_BLOCK_WORDS];
#pragma HLS BIND_STORAGE variable=pending_upper type=RAM_1P impl=BRAM

    const bool sparse=mode==MODE_SPARSE24;
    const unsigned steps=sparse ? K/2 : K;
    const unsigned row_words=K/16;
    assert(row_words>0 && row_words<=MAX_K/16 && N%16==0);

    m_tiles: for(unsigned m0=0;m0<M;m0+=TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=7
        const unsigned valid_m=M-m0<TM ? M-m0 : TM;
        // Clear invalid rows as well, preventing stale input across calls/tiles.
        clear_x_words: for(unsigned word=0;word<X_ROW_WORDS_PHYSICAL;++word) {
#pragma HLS PIPELINE II=1
            clear_x_rows: for(unsigned row=0;row<TM;++row) {
#pragma HLS UNROLL
                xt[row][word]=0;
            }
        }
        load_x_rows: for(unsigned row=0;row<valid_m;++row) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
            const unsigned base=(m0+row)*row_words;
            // Row start/length are whole beats by capability check, no row pad.
            load_x_words: for(unsigned word=0;word<row_words;++word) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=36
#pragma HLS PIPELINE II=1
                xt[row][word]=X[base+word];
            }
        }

        // One acquisition source site, reused by the rolled slab loop. This
        // avoids the HLS 200-1631 check hit by the two sequential lock sites.
        // First iteration computes once; the optional second only copies upper.
        produce_slabs: for(unsigned row0=0;row0<valid_m;row0+=OUTPUT_ROWS) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2
#pragma HLS UNROLL off=true
#pragma HLS PIPELINE off
        hls::write_lock<output_block_t> block(blocks);
        if(row0==0) {
        n_microtiles: for(unsigned n0=0;n0<N;n0+=MICRO_N) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=36
            clear_acc_rows: for(unsigned mm=0;mm<MICRO_M;++mm) {
#pragma HLS UNROLL
                clear_acc_cols: for(unsigned nn=0;nn<MICRO_N;++nn) {
#pragma HLS UNROLL
                    acc[mm][nn]=0;
                }
            }

            // ONE pipelined loop and ONE 32x16 multiplication source site.
            // Do not replace by compute<MODE>() or FUNCTION_INSTANTIATE mode.
            shared_mac_steps: for(unsigned step=0;step<steps;++step) {
#pragma HLS LOOP_TRIPCOUNT min=8 max=576
#pragma HLS PIPELINE II=1
                const unsigned g=sparse ? step>>1 : step>>2;
                const unsigned t=g*N+n0;
                const unsigned dense_address=(step*N+n0)>>4;
                const unsigned sparse_address=t>>3; // 2*t bytes / 16
                const unsigned address=sparse ? sparse_address : dense_address;
                const unsigned second=sparse ? address+1 : address;

                data_t lo[16],hi[16],weights[16];
                ap_uint<2> positions[16];
#pragma HLS ARRAY_PARTITION variable=lo complete
#pragma HLS ARRAY_PARTITION variable=hi complete
#pragma HLS ARRAY_PARTITION variable=weights complete
#pragma HLS ARRAY_PARTITION variable=positions complete
                // Explicit fixed-bank two reads: enough for 16 sparse columns
                // without replicating W RAM or dynamic bank-index arbitration.
                read_weight_banks: for(unsigned lane=0;lane<16;++lane) {
#pragma HLS UNROLL
                    lo[lane]=wbank[lane][address];
                    hi[lane]=wbank[lane][second];
                }
                const wide_t meta=metadata[sparse ? t>>5 : 0];
                const wide_t meta16=meta>>(4*(t&31u));
                const unsigned slot=step&1u;
                select_columns: for(unsigned nn=0;nn<16;++nn) {
#pragma HLS UNROLL
                    const unsigned bank_even=2*(nn&7u);
                    const data_t a=nn<8 ? lo[bank_even] : hi[bank_even];
                    const data_t b=nn<8 ? lo[bank_even+1] : hi[bank_even+1];
                    const data_t sparse_w=slot ? b : a;
                    const ap_uint<4> code=meta16.range(4*nn+3,4*nn);
                    const ap_uint<2> sparse_p=slot ? code.range(3,2) : code.range(1,0);
                    weights[nn]=sparse ? sparse_w : lo[nn];
                    positions[nn]=sparse ? sparse_p : ap_uint<2>(step&3u);
                }

                shared_mac_rows: for(unsigned mm=0;mm<MICRO_M;++mm) {
#pragma HLS UNROLL
                    // Explicitly one X-bank read PER ROW, hoisted above nn.
                    // Every column selects within the SAME quartet, not a new
                    // random RAM read and not one position broadcast to all N.
                    const wide_t xword=xt[mm][g>>2];
                    const ap_uint<32> quartet=(xword>>(32*(g&3u))).range(31,0);
                    shared_mac_columns: for(unsigned nn=0;nn<MICRO_N;++nn) {
#pragma HLS UNROLL
                        const ap_uint<8> raw=(quartet>>(8*positions[nn].to_uint())).range(7,0);
                        const data_t selected_x=signed_byte(raw);
                        const product_t product=selected_x*weights[nn];
                        acc[mm][nn]+=product.to_int();
                    }
                }
            }

            // Transpose only the CURRENT microtile into one wide BRAM write
            // per row. No LUTRAM read-modify-write in the MAC recurrence.
            pack_result_rows: for(unsigned row=0;row<valid_m;++row) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
#pragma HLS PIPELINE II=1
                output_pack_t packed=0;
                pack_result_lanes: for(unsigned nn=0;nn<16;++nn) {
#pragma HLS UNROLL
                    packed.range(32*nn+31,32*nn)=static_cast<uint32_t>(acc[row][nn]);
                }
                if(row<OUTPUT_ROWS) block[row*OUTPUT_GROUPS+n0/16]=packed;
                else pending_upper[(row-OUTPUT_ROWS)*OUTPUT_GROUPS+n0/16]=packed;
            }
        }
        } else {
            copy_upper_rows: for(unsigned row=0;row<valid_m-OUTPUT_ROWS;++row) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
                copy_upper_groups: for(unsigned group=0;group<N/16;++group) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=36
#pragma HLS PIPELINE II=1
                    const unsigned address=row*OUTPUT_GROUPS+group;
                    block[address]=pending_upper[address];
                }
            }
        }
        } // Release each slab before the next acquisition at the same site.
    }
}

inline void write_output_blocks(output_blocks_t& blocks,wide_t* Y,
                                uint32_t M,uint32_t N) {
#pragma HLS INLINE off
    const unsigned words_per_row=N/4;
    assert(words_per_row>0 && words_per_row<=MAX_N/4);
    write_m_tiles: for(unsigned m0=0;m0<M;m0+=TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=7
        const unsigned valid_m=M-m0<TM ? M-m0 : TM;
        write_slabs: for(unsigned row0=0;row0<valid_m;row0+=OUTPUT_ROWS) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=2
        hls::read_lock<output_block_t> block(blocks);
        const unsigned rows=valid_m-row0<OUTPUT_ROWS ? valid_m-row0 : OUTPUT_ROWS;
        write_rows: for(unsigned row=0;row<rows;++row) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
            const unsigned base=(m0+row0+row)*words_per_row;
            // All valid rows are whole beats. No if around Y[base+word].
            // No tile-wise DDR write gaps; every row is compact contiguous.
            write_y_words: for(unsigned word=0;word<words_per_row;++word) {
#pragma HLS LOOP_TRIPCOUNT min=4 max=144
#pragma HLS PIPELINE II=1
                const output_pack_t packed=block[row*OUTPUT_GROUPS+word/4];
                const unsigned lo=128*(word&3u);
                Y[base+word]=packed.range(lo+127,lo);
            }
        }
        }
    }
}

inline void shared_layer(const wide_t* X,const data_t wbank[16][W_BANK_DEPTH],
                         const wide_t metadata[META_WORDS_PHYSICAL],wide_t* Y,
                         uint32_t M,uint32_t K,uint32_t N,uint32_t mode) {
#pragma HLS INLINE off
#pragma HLS DATAFLOW
#pragma HLS ARRAY_PARTITION variable=wbank complete dim=1
#pragma HLS ALLOCATION function instances=compute_and_pack limit=1
    output_blocks_t blocks;
#pragma HLS BIND_STORAGE variable=blocks type=RAM_2P impl=BRAM
    compute_and_pack(X,wbank,metadata,blocks,M,K,N,mode);
    write_output_blocks(blocks,Y,M,N);
}
}
#endif
