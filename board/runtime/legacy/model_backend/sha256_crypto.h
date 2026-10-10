#pragma once
#include <cstddef>
#include <stdexcept>
#include <string>

// Public one-shot C ABI from OpenSSL 3.0 SHA256_Init(3).
// The target already has libcrypto.so.3; do not install system development files.
extern "C" unsigned char* SHA256(const unsigned char*, size_t, unsigned char*);

inline std::string kv260_crypto_sha256(const void* data, size_t bytes) {
    unsigned char digest[32];
    if (SHA256(static_cast<const unsigned char*>(data), bytes, digest) != digest)
        throw std::runtime_error("SHA256 implementation failed");
    static constexpr char hex[] = "0123456789abcdef";
    std::string result(64, '0');
    for (size_t i = 0; i < 32; ++i) {
        result[i * 2] = hex[digest[i] >> 4];
        result[i * 2 + 1] = hex[digest[i] & 15];
    }
    return result;
}
