#ifndef KV260_GEMM_BACKEND_V1_H
#define KV260_GEMM_BACKEND_V1_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Stable C ABI. All pointers are borrowed only for the synchronous call.
 * No XRT ownership is transferred. close detaches the client only.
 * Explicit struct_bytes permits strict size/version rejection. */
#define KV260_B_GEMM_ABI_V1 1u
enum kv260_b_error { B_GEMM_OK=0, B_BAD_SHAPE=1, B_BAD_IDENTITY=2,
  B_BAD_INPUT=3, B_NOT_READY=4, B_TIMEOUT=5, B_OWNER_FAULT=6, B_INTERNAL=7 };
typedef struct {
  uint32_t abi_version, struct_bytes, M, K, N, layer_index;
  char request_id[192], model_run_id[96], image_id[96], layer_id[96];
  char expected_W_sha256[65];
} kv260_b_request_v1;
typedef struct {
  uint32_t abi_version, struct_bytes, status, one_start, done, kernel_error;
  uint32_t guards_ok, inputs_unchanged, tag0, tag1;
  uint64_t owner_pid, starts_before, starts_after;
  double native_us;
  char X_sha256[65], W_sha256[65], Y_sha256[65];
  char native_result_file[1024], error[512];
} kv260_b_receipt_v1;
/* actual_identity_json includes C readiness binding and actual owner identity. */
int kv260_gemm_open_v1(const char* config_json, void** handle,
                      char* actual_identity_json, size_t capacity);
int kv260_gemm_run_v1(void* handle, const kv260_b_request_v1* request,
 const int8_t* X, size_t X_bytes, const int8_t* W, size_t W_bytes,
 int32_t* Y, size_t Y_bytes, kv260_b_receipt_v1* receipt);
int kv260_gemm_info_v1(void* handle, char* json, size_t capacity);
int kv260_gemm_close_v1(void* handle);
#ifdef __cplusplus
}
#endif
#endif
