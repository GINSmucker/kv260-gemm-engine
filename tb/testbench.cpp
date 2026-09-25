#include "../src/gemm.h"

#include <cstdint>
#include <cstdio>
#include <random>

namespace {

void compute_golden(
    const gemm_input_t X[GEMM_X_SIZE],
    const gemm_weight_t W[GEMM_W_SIZE],
    std::int32_t Y_golden[GEMM_Y_SIZE]) {
    for (int i = 0; i < GEMM_M; ++i) {
        for (int j = 0; j < GEMM_N; ++j) {
            std::int32_t accumulator = 0;

            for (int k = 0; k < GEMM_K; ++k) {
                const std::int32_t x_value =
                    static_cast<std::int8_t>(X[i * GEMM_K + k]);
                const std::int32_t w_value =
                    static_cast<std::int8_t>(W[k * GEMM_N + j]);
                accumulator += x_value * w_value;
            }

            Y_golden[i * GEMM_N + j] = accumulator;
        }
    }
}

bool run_case(
    const char *case_name,
    const gemm_input_t X[GEMM_X_SIZE],
    const gemm_weight_t W[GEMM_W_SIZE]) {
    gemm_output_t Y_hw[GEMM_Y_SIZE] = {};
    std::int32_t Y_golden[GEMM_Y_SIZE] = {};

    gemm_top(X, W, Y_hw);
    compute_golden(X, W, Y_golden);

    bool passed = true;
    for (int i = 0; i < GEMM_M; ++i) {
        for (int j = 0; j < GEMM_N; ++j) {
            const int index = i * GEMM_N + j;
            const std::int32_t actual = static_cast<std::int32_t>(Y_hw[index]);
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

    std::printf("[%s] %s\n", case_name, passed ? "PASS" : "FAIL");
    return passed;
}

void fill_zero_case(
    gemm_input_t X[GEMM_X_SIZE],
    gemm_weight_t W[GEMM_W_SIZE]) {
    for (int index = 0; index < GEMM_X_SIZE; ++index) {
        X[index] = 0;
    }
    for (int index = 0; index < GEMM_W_SIZE; ++index) {
        W[index] = 0;
    }
}

void fill_identity_case(
    gemm_input_t X[GEMM_X_SIZE],
    gemm_weight_t W[GEMM_W_SIZE]) {
    for (int i = 0; i < GEMM_M; ++i) {
        for (int k = 0; k < GEMM_K; ++k) {
            X[i * GEMM_K + k] = ((i * GEMM_K + k) % 17) - 8;
        }
    }

    for (int k = 0; k < GEMM_K; ++k) {
        for (int j = 0; j < GEMM_N; ++j) {
            W[k * GEMM_N + j] = (k == j) ? 1 : 0;
        }
    }
}

void fill_extreme_case(
    gemm_input_t X[GEMM_X_SIZE],
    gemm_weight_t W[GEMM_W_SIZE]) {
    for (int index = 0; index < GEMM_X_SIZE; ++index) {
        X[index] = (index % 2 == 0) ? -128 : 127;
    }
    for (int index = 0; index < GEMM_W_SIZE; ++index) {
        W[index] = (index % 3 == 0) ? -128 : 127;
    }
}

void fill_random_case(
    gemm_input_t X[GEMM_X_SIZE],
    gemm_weight_t W[GEMM_W_SIZE]) {
    std::mt19937 generator(20260925U);
    std::uniform_int_distribution<int> distribution(-128, 127);

    for (int index = 0; index < GEMM_X_SIZE; ++index) {
        X[index] = distribution(generator);
    }
    for (int index = 0; index < GEMM_W_SIZE; ++index) {
        W[index] = distribution(generator);
    }
}

}  // namespace

int main() {
    gemm_input_t X[GEMM_X_SIZE] = {};
    gemm_weight_t W[GEMM_W_SIZE] = {};
    bool all_passed = true;

    std::printf("--- 8x8 INT8 Naive GEMM: Y = XW ---\n");

    fill_zero_case(X, W);
    all_passed = run_case("zero", X, W) && all_passed;

    fill_identity_case(X, W);
    all_passed = run_case("identity-weight", X, W) && all_passed;

    fill_extreme_case(X, W);
    all_passed = run_case("signed-extreme", X, W) && all_passed;

    fill_random_case(X, W);
    all_passed = run_case("deterministic-random", X, W) && all_passed;

    // Re-run the same data to catch accidental state retention between calls.
    all_passed = run_case("repeat-call", X, W) && all_passed;

    std::printf("OVERALL RESULT: %s\n", all_passed ? "PASS" : "FAIL");
    return all_passed ? 0 : 1;
}
