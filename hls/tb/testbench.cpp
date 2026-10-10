#include "../include/model_gemm_config.hpp"

#include <cstdio>
#include <vector>

static int make_value(int index, int salt) {
    return ((index * 17 + salt * 29) % 15) - 7;
}

static void pack_int8(std::vector<axi_word_t> &dst,
                      std::vector<int> &plain,
                      int count,
                      int salt) {
    dst.assign((count + INT8_PER_AXI - 1) / INT8_PER_AXI, axi_word_t(0));
    plain.resize(count);
    for (int i = 0; i < count; ++i) {
        ap_int<8> value = make_value(i, salt);
        plain[i] = int(value);
        dst[i >> 4].range((i & 15) * 8 + 7, (i & 15) * 8) = value.range(7, 0);
    }
}

static int unpack_int32(const std::vector<axi_word_t> &src, int index) {
    const int word = index >> 2;
    const int lane = index & 3;
    ap_uint<32> bits = src[word].range(lane * 32 + 31, lane * 32);
    return int(ap_int<32>(bits));
}

static bool run_case(int M, int K, int N, int salt) {
    std::vector<axi_word_t> x_words;
    std::vector<axi_word_t> w_words;
    // C/RTL co-simulation generates a wrapper from the fixed m_axi depth
    // declarations. The wrapper may dump the complete declared range rather
    // than only the runtime-valid range, so every C test buffer must be at
    // least as large as its interface depth. CSim alone does not expose this.
    std::vector<axi_word_t> y_words(MAX_Y_WORDS, 0);
    std::vector<int> x;
    std::vector<int> w;

    pack_int8(x_words, x, M * K, salt);
    pack_int8(w_words, w, K * N, salt + 3);
    x_words.resize(MAX_X_WORDS, axi_word_t(0));
    w_words.resize(MAX_W_WORDS, axi_word_t(0));

    const int status = model_gemm_top(
        x_words.data(), w_words.data(), y_words.data(), M, K, N);
    if (status != GEMM_OK) {
        std::printf("FAIL shape %dx%dx%d: status=%d\n", M, K, N, status);
        return false;
    }

    int errors = 0;
    for (int i = 0; i < M; ++i) {
        for (int j = 0; j < N; ++j) {
            int golden = 0;
            for (int k = 0; k < K; ++k) {
                golden += x[i * K + k] * w[k * N + j];
            }
            const int actual = unpack_int32(y_words, i * N + j);
            if (actual != golden) {
                if (errors < 8) {
                    std::printf("Mismatch Y[%d][%d]: HW=%d SW=%d\n",
                                i, j, actual, golden);
                }
                ++errors;
            }
        }
    }

    std::printf("%s shape M=%d K=%d N=%d, errors=%d\n",
                errors == 0 ? "PASS" : "FAIL", M, K, N, errors);
    return errors == 0;
}

int main() {
    bool ok = true;
    ok &= run_case(1,   192, 1000, 1);
    ok &= run_case(197, 192, 192,  2);
    ok &= run_case(197, 192, 576,  3);
    ok &= run_case(197, 192, 768,  4);
    ok &= run_case(197, 768, 192,  5);

    // Invalid-shape calls must satisfy the wrapper's fixed pointer depths too,
    // even though the kernel returns before accessing the buffers.
    std::vector<axi_word_t> dummy_x(MAX_X_WORDS, 0);
    std::vector<axi_word_t> dummy_w(MAX_W_WORDS, 0);
    std::vector<axi_word_t> dummy_y(MAX_Y_WORDS, 0);
    const int invalid_status = model_gemm_top(
        dummy_x.data(), dummy_w.data(), dummy_y.data(), 8, 8, 8);
    ok &= (invalid_status == GEMM_ERR_UNSUPPORTED_SHAPE);

    std::printf("OPT40 C simulation: %s\n", ok ? "PASSED" : "FAILED");
    return ok ? 0 : 1;
}
