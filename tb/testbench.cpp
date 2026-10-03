#include "../src/gemm.h"

#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

namespace {

void compute_golden(
    const std::vector<gemm_input_t> &X,
    const std::vector<gemm_weight_t> &W,
    std::vector<std::int32_t> &Y_golden,
    int M,
    int N,
    int K) {
    for (int i = 0; i < M; ++i) {
        for (int j = 0; j < N; ++j) {
            std::int32_t accumulator = 0;
            for (int k = 0; k < K; ++k) {
                const std::int32_t x_value =
                    static_cast<std::int8_t>(X[i * K + k]);
                const std::int32_t w_value =
                    static_cast<std::int8_t>(W[k * N + j]);
                accumulator += x_value * w_value;
            }
            Y_golden[i * N + j] = accumulator;
        }
    }
}

bool run_case(
    const char *case_name,
    const std::vector<gemm_input_t> &X,
    const std::vector<gemm_weight_t> &W,
    int M,
    int N,
    int K) {
    // C/RTL co-simulation marshals pointer arguments according to the
    // interface depth declared on gemm_top.  Keep full-depth interface
    // buffers here, while checking only the active M/N/K regions.
    std::vector<gemm_input_t> X_buffer(GEMM_MAX_X_SIZE, 0);
    std::vector<gemm_weight_t> W_buffer(GEMM_MAX_W_SIZE, 0);
    std::vector<gemm_output_t> Y_hw(GEMM_MAX_Y_SIZE, 0);
    std::vector<std::int32_t> Y_golden(M * N, 0);

    for (std::size_t index = 0; index < X.size(); ++index) {
        X_buffer[index] = X[index];
    }
    for (std::size_t index = 0; index < W.size(); ++index) {
        W_buffer[index] = W[index];
    }

    gemm_top(
        X_buffer.data(), W_buffer.data(), Y_hw.data(), M, N, K);
    compute_golden(X, W, Y_golden, M, N, K);

    bool passed = true;
    for (int i = 0; i < M; ++i) {
        for (int j = 0; j < N; ++j) {
            const int index = i * N + j;
            const std::int32_t actual =
                static_cast<std::int32_t>(Y_hw[index]);
            const std::int32_t expected = Y_golden[index];

            if (actual != expected) {
                std::printf(
                    "ERROR [%s] Y[%d][%d]: HW=%d, expected=%d\n",
                    case_name,
                    i,
                    j,
                    static_cast<int>(actual),
                    static_cast<int>(expected));
                passed = false;
            }
        }
    }

    std::printf(
        "[%s] M=%d N=%d K=%d: %s\n",
        case_name,
        M,
        N,
        K,
        passed ? "PASS" : "FAIL");
    return passed;
}

void fill_random(
    std::vector<gemm_input_t> &X,
    std::vector<gemm_weight_t> &W,
    std::uint32_t seed) {
    std::mt19937 generator(seed);
    std::uniform_int_distribution<int> distribution(-128, 127);

    for (std::size_t index = 0; index < X.size(); ++index) {
        X[index] = distribution(generator);
    }
    for (std::size_t index = 0; index < W.size(); ++index) {
        W[index] = distribution(generator);
    }
}

bool run_random_case(
    const char *case_name,
    int M,
    int N,
    int K,
    std::uint32_t seed) {
    std::vector<gemm_input_t> X(M * K);
    std::vector<gemm_weight_t> W(K * N);
    fill_random(X, W, seed);
    return run_case(case_name, X, W, M, N, K);
}

bool run_zero_case() {
    const int M = 3;
    const int N = 5;
    const int K = 9;
    const std::vector<gemm_input_t> X(M * K, 0);
    const std::vector<gemm_weight_t> W(K * N, 0);
    return run_case("zero-boundary", X, W, M, N, K);
}

bool run_identity_case() {
    const int M = 5;
    const int N = 7;
    const int K = 7;
    std::vector<gemm_input_t> X(M * K);
    std::vector<gemm_weight_t> W(K * N, 0);

    for (int i = 0; i < M; ++i) {
        for (int k = 0; k < K; ++k) {
            X[i * K + k] = ((i * K + k) % 17) - 8;
        }
    }
    for (int k = 0; k < K; ++k) {
        W[k * N + k] = 1;
    }

    return run_case("identity-boundary", X, W, M, N, K);
}

}  // namespace

int main() {
    bool all_passed = true;

    std::printf("--- Runtime Tiled INT8 GEMM: Y = XW ---\n");
    std::printf(
        "Tile=%dx%dx%d, K-parallel=%d\n",
        GEMM_TM,
        GEMM_TN,
        GEMM_TK,
        GEMM_PAR_K);

    all_passed = run_zero_case() && all_passed;
    all_passed = run_identity_case() && all_passed;
    all_passed = run_random_case("single-element", 1, 1, 1, 1U) && all_passed;
    all_passed = run_random_case("week1-8x8", 8, 8, 8, 2U) && all_passed;
    all_passed =
        run_random_case("exact-tile", 16, 16, 32, 3U) && all_passed;
    all_passed =
        run_random_case("partial-tile", 13, 17, 10, 4U) && all_passed;
    all_passed =
        run_random_case("multi-tile", 30, 20, 50, 5U) && all_passed;

    std::printf("OVERALL RESULT: %s\n", all_passed ? "PASS" : "FAIL");
    return all_passed ? 0 : 1;
}
