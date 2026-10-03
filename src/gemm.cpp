#include "gemm.h"

void gemm_top(
    const gemm_input_t X[GEMM_MAX_X_SIZE],
    const gemm_weight_t W[GEMM_MAX_W_SIZE],
    gemm_output_t Y[GEMM_MAX_Y_SIZE],
    int M,
    int N,
    int K) {
#pragma HLS INTERFACE m_axi port=X offset=slave bundle=gmem0 depth=GEMM_MAX_X_SIZE
#pragma HLS INTERFACE m_axi port=W offset=slave bundle=gmem1 depth=GEMM_MAX_W_SIZE
#pragma HLS INTERFACE m_axi port=Y offset=slave bundle=gmem2 depth=GEMM_MAX_Y_SIZE

#pragma HLS INTERFACE s_axilite port=X bundle=control
#pragma HLS INTERFACE s_axilite port=W bundle=control
#pragma HLS INTERFACE s_axilite port=Y bundle=control
#pragma HLS INTERFACE s_axilite port=M bundle=control
#pragma HLS INTERFACE s_axilite port=N bundle=control
#pragma HLS INTERFACE s_axilite port=K bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control

    // Reject invalid dimensions before any external-memory access.
    if (M <= 0 || N <= 0 || K <= 0 ||
        M > GEMM_MAX_M || N > GEMM_MAX_N || K > GEMM_MAX_K) {
        return;
    }

    gemm_input_t x_tile[GEMM_TM][GEMM_TK];
    gemm_weight_t w_tile[GEMM_TK][GEMM_TN];
    gemm_output_t y_tile[GEMM_TM][GEMM_TN];

    // Four banks provide four X values and four W values per compute cycle.
#pragma HLS ARRAY_PARTITION variable=x_tile cyclic factor=4 dim=2
#pragma HLS ARRAY_PARTITION variable=w_tile cyclic factor=4 dim=1

M_TILE_LOOP:
    for (int m0 = 0; m0 < M; m0 += GEMM_TM) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=64
        const int remaining_m = M - m0;
        const int valid_m =
            (remaining_m < GEMM_TM) ? remaining_m : GEMM_TM;

    N_TILE_LOOP:
        for (int n0 = 0; n0 < N; n0 += GEMM_TN) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=64
            const int remaining_n = N - n0;
            const int valid_n =
                (remaining_n < GEMM_TN) ? remaining_n : GEMM_TN;

        CLEAR_I_LOOP:
            for (int i = 0; i < GEMM_TM; ++i) {
            CLEAR_J_LOOP:
                for (int j = 0; j < GEMM_TN; ++j) {
#pragma HLS PIPELINE II=1
                    y_tile[i][j] = 0;
                }
            }

        K_TILE_LOOP:
            for (int k0 = 0; k0 < K; k0 += GEMM_TK) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
                const int remaining_k = K - k0;
                const int valid_k =
                    (remaining_k < GEMM_TK) ? remaining_k : GEMM_TK;

            CLEAR_X_I_LOOP:
                for (int i = 0; i < GEMM_TM; ++i) {
                CLEAR_X_K_LOOP:
                    for (int k = 0; k < GEMM_TK; ++k) {
#pragma HLS PIPELINE II=1
                        x_tile[i][k] = 0;
                    }
                }

            CLEAR_W_K_LOOP:
                for (int k = 0; k < GEMM_TK; ++k) {
                CLEAR_W_J_LOOP:
                    for (int j = 0; j < GEMM_TN; ++j) {
#pragma HLS PIPELINE II=1
                        w_tile[k][j] = 0;
                    }
                }

                // External-memory accesses are unconditional inside these
                // loops, allowing HLS to infer one contiguous burst per row.
            LOAD_X_VALID_I_LOOP:
                for (int i = 0; i < valid_m; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
                    const int x_row_base = (m0 + i) * K + k0;

                LOAD_X_VALID_K_LOOP:
                    for (int k = 0; k < valid_k; ++k) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
#pragma HLS PIPELINE II=1
                        x_tile[i][k] = X[x_row_base + k];
                    }
                }

            LOAD_W_VALID_K_LOOP:
                for (int k = 0; k < valid_k; ++k) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
                    const int w_row_base = (k0 + k) * N + n0;

                LOAD_W_VALID_J_LOOP:
                    for (int j = 0; j < valid_n; ++j) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
#pragma HLS PIPELINE II=1
                        w_tile[k][j] = W[w_row_base + j];
                    }
                }

            COMPUTE_I_LOOP:
                for (int i = 0; i < GEMM_TM; ++i) {
                COMPUTE_J_LOOP:
                    for (int j = 0; j < GEMM_TN; ++j) {
                        gemm_output_t partial[GEMM_PAR_K];
#pragma HLS ARRAY_PARTITION variable=partial complete dim=1

                    INIT_PARTIAL_LOOP:
                        for (int lane = 0; lane < GEMM_PAR_K; ++lane) {
#pragma HLS UNROLL
                            partial[lane] = 0;
                        }

                    COMPUTE_K_GROUP_LOOP:
                        for (int k_base = 0; k_base < GEMM_TK;
                             k_base += GEMM_PAR_K) {
#pragma HLS PIPELINE II=1
                        COMPUTE_LANE_LOOP:
                            for (int lane = 0; lane < GEMM_PAR_K; ++lane) {
#pragma HLS UNROLL
                                const int k = k_base + lane;
                                const ap_int<16> product =
                                    x_tile[i][k] * w_tile[k][j];
                                partial[lane] += product;
                            }
                        }

                        const gemm_output_t tile_sum =
                            partial[0] + partial[1] +
                            partial[2] + partial[3];
                        y_tile[i][j] += tile_sum;
                    }
                }
            }

        STORE_VALID_I_LOOP:
            for (int i = 0; i < valid_m; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
                const int y_row_base = (m0 + i) * N + n0;

            STORE_VALID_J_LOOP:
                for (int j = 0; j < valid_n; ++j) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=16
#pragma HLS PIPELINE II=1
                    Y[y_row_base + j] = y_tile[i][j];
                }
            }
        }
    }
}
