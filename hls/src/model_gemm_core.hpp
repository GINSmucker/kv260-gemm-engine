#ifndef MODEL_GEMM_CORE_HPP
#define MODEL_GEMM_CORE_HPP

#include "../include/model_gemm_config.hpp"

static data_t get_int8_lane(const axi_word_t &word, int lane) {
#pragma HLS INLINE
    ap_uint<8> bits = word.range(lane * 8 + 7, lane * 8);
    return data_t(bits);
}

// Read 16 consecutive INT8 weights from a byte-contiguous packed buffer.
// All legal N values are multiples of 8. Therefore a 16-value vector is
// either 128-bit aligned or split evenly across two adjacent AXI words.
static axi_word_t read_w_vector(
    const axi_word_t w_buffer[MAX_W_WORDS + 1],
    int element_index) {
#pragma HLS INLINE
    const int word_index = element_index >> 4;
    const int lane       = element_index & 15;

    const axi_word_t lo = w_buffer[word_index];
    if (lane == 0) {
        return lo;
    }

    const axi_word_t hi = w_buffer[word_index + 1];
    ap_uint<256> joined = (ap_uint<256>(hi) << 128) | ap_uint<256>(lo);
    return axi_word_t(joined >> (lane * 8));
}

static void dense_layer_opt40(
    const axi_word_t *X,
    const axi_word_t w_buffer[MAX_W_WORDS + 1],
    axi_word_t       *Y,
    int M,
    int K,
    int N) {

    // Sixteen independent row banks. Each bank stores 16 INT8 values in one
    // 128-bit word, so one word from every row can be read in the same cycle.
    axi_word_t x_tile[TM][X_WORDS_PER_ROW_MAX];
#pragma HLS ARRAY_PARTITION variable=x_tile complete dim=1

    // 16 x 16 INT32 partial sums, fully partitioned to support
    // 256 parallel read-modify-writes per pipelined K iteration.
    acc_t acc[TM][TN];
#pragma HLS ARRAY_PARTITION variable=acc complete dim=1
#pragma HLS ARRAY_PARTITION variable=acc complete dim=2

M_TILE_LOOP:
    for (int m0 = 0; m0 < M; m0 += TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=13

        // X rows are contiguous and every legal K is divisible by 16.
        // The external interface therefore transfers one full 128-bit word
        // on every successful memory beat.
    LOAD_X_ROW:
        for (int mi = 0; mi < TM; ++mi) {
            const int row = m0 + mi;
        LOAD_X_WORD:
            for (int kw = 0; kw < K / INT8_PER_AXI; ++kw) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=12 max=48
                if (row < M) {
                    x_tile[mi][kw] = X[row * (K / INT8_PER_AXI) + kw];
                } else {
                    x_tile[mi][kw] = 0;
                }
            }
        }

    N_TILE_LOOP:
        for (int n0 = 0; n0 < N; n0 += TN) {
#pragma HLS LOOP_TRIPCOUNT min=12 max=63

        INIT_ACC_N:
            for (int ni = 0; ni < TN; ++ni) {
#pragma HLS PIPELINE II=1
            INIT_ACC_M:
                for (int mi = 0; mi < TM; ++mi) {
#pragma HLS UNROLL
                    acc[mi][ni] = 0;
                }
            }

        N_MICRO_LOOP:
            for (int ns = 0; ns < TN; ns += MICRO_N) {
#pragma HLS UNROLL off

                // The final N tile of the 1x192x1000 layer contains only
                // 8 valid columns. Remaining lanes are not written to Y.
                if ((n0 + ns) < N) {
                    int w_element = n0 + ns;

            K_TILE_LOOP:
                for (int k0 = 0; k0 < K; k0 += TK) {
#pragma HLS LOOP_TRIPCOUNT min=6 max=24

                K_PIPELINE_LOOP:
                    for (int kk = 0; kk < TK; ++kk) {
#pragma HLS PIPELINE II=1
                        const int k = k0 + kk;
                        const int x_word_index = k >> 4;
                        const int x_lane       = k & 15;

                        data_t x_vec[MICRO_M];
#pragma HLS ARRAY_PARTITION variable=x_vec complete
                    READ_X_VECTOR:
                        for (int mi = 0; mi < MICRO_M; ++mi) {
#pragma HLS UNROLL
                            x_vec[mi] = get_int8_lane(x_tile[mi][x_word_index], x_lane);
                        }

                        // n0 and ns are multiples of 16. For N=1000 the row
                        // start alternates between lane 0 and lane 8; the helper
                        // assembles the latter case from two dual-port reads.
                        const axi_word_t w_word = read_w_vector(w_buffer, w_element);

                        data_t w_vec[MICRO_N];
#pragma HLS ARRAY_PARTITION variable=w_vec complete
                    READ_W_VECTOR:
                        for (int nj = 0; nj < MICRO_N; ++nj) {
#pragma HLS UNROLL
                            w_vec[nj] = get_int8_lane(w_word, nj);
                        }

                    MAC_M_LOOP:
                        for (int mi = 0; mi < MICRO_M; ++mi) {
#pragma HLS UNROLL
                        MAC_N_LOOP:
                            for (int nj = 0; nj < MICRO_N; ++nj) {
#pragma HLS UNROLL
                                if ((m0 + mi) < M && (n0 + ns + nj) < N) {
                                    ap_int<16> product = x_vec[mi] * w_vec[nj];
#pragma HLS BIND_OP variable=product op=mul impl=DSP
                                    acc[mi][ns + nj] += product;
                                }
                            }
                        }
                        // Address recurrence avoids a runtime k*N multiplier
                        // in the II=1 compute loop.
                        w_element += N;
                    }
                }
                }
            }

            // Store 4 INT32 results per 128-bit beat. A full 16-column tile
            // contains 4 output words for each valid row.
            const int valid_rows = (M - m0 < TM) ? (M - m0) : TM;
            const int valid_cols = (N - n0 < TN) ? (N - n0) : TN;
            const int valid_words = valid_cols / INT32_PER_AXI;
        STORE_Y_ROW:
            for (int mi = 0; mi < valid_rows; ++mi) {
                const int row = m0 + mi;
                const int y_base = (row * N + n0) / INT32_PER_AXI;
            STORE_Y_WORD:
                for (int nw = 0; nw < valid_words; ++nw) {
#pragma HLS PIPELINE II=1
                    axi_word_t out_word = 0;
                    PACK_Y:
                    for (int lane = 0; lane < INT32_PER_AXI; ++lane) {
#pragma HLS UNROLL
                        const acc_t value = acc[mi][nw * INT32_PER_AXI + lane];
                        out_word.range(lane * 32 + 31, lane * 32) = value.range(31, 0);
                    }
                    Y[y_base + nw] = out_word;
                }
            }
        }
    }
}

#endif
