#ifndef KV260_GEMM_BACKEND_V2A_H
#define KV260_GEMM_BACKEND_V2A_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* OPT12-V2A interface preparation. These declarations do not supply a backend.
 * All pointers are borrowed for the single synchronous call. No XRT ownership
 * is transferred. close only detaches; it cannot ACK or stop the owner.
 * A v2a implementation must reject v1 structs/schema and use the fixed stream
 * contract, including the original total deadline and no REQUEST retry. */
#define KV260_B_GEMM_ABI_V2A 2u
enum kv260_b_error_v2a {
  B2_GEMM_OK=0, B2_BAD_SHAPE=1, B2_BAD_IDENTITY=2, B2_BAD_INPUT=3,
  B2_NOT_READY=4, B2_TIMEOUT=5, B2_OWNER_FAULT=6, B2_INTERNAL=7
};
typedef struct {
  uint32_t abi_version, struct_bytes, M, K, N, layer_index;
  char request_id[192], model_run_id[96], image_id[96], layer_id[96];
  char expected_W_sha256[65];
} kv260_b_request_v2a;
typedef struct {
  uint32_t abi_version, struct_bytes, status, one_start, done, kernel_error;
  uint32_t guards_ok, inputs_unchanged, tag0, tag1;
  uint64_t owner_pid, starts_before, starts_after;
  double native_us;
  char X_sha256[65], W_sha256[65], Y_sha256[65];
  char native_result_file[1024], error[512];
} kv260_b_receipt_v2a;
int kv260_gemm_open_v2a(const char* config_json, void** handle,
                      char* actual_identity_json, size_t capacity);
int kv260_gemm_run_v2a(void* handle, const kv260_b_request_v2a* request,
 const int8_t* X, size_t X_bytes, const int8_t* W, size_t W_bytes,
 int32_t* Y, size_t Y_bytes, kv260_b_receipt_v2a* receipt);
int kv260_gemm_info_v2a(void* handle, char* json, size_t capacity);
int kv260_gemm_close_v2a(void* handle);
#ifdef __cplusplus
}
#endif
#endif
