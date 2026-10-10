#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// ABI5. Open, then register this library on ORT options on the same thread.
// Destroy the ORT session before close. One caller brackets the entire graph.
// A nonzero return is failure; error() exposes the most recent session error.
int kv5_b_open(const char* socket, const unsigned char model[32], void** session);
int kv5_b_begin(void* session, uint64_t run);
int kv5_b_end(void* session);
int kv5_b_abort(void* session);
const char* kv5_b_error(void* session);
void kv5_b_close(void* session);
#ifdef __cplusplus
}
#endif
