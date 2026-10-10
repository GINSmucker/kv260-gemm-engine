#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

// Compare every byte, with memcpy avoiding aliasing and alignment assumptions.
// No read extends beyond [bytes, bytes + count); a short tail stays bytewise.
inline bool kv260_guard_bytes(const unsigned char* bytes, size_t count) {
    size_t pos = 0;
    for (; count - pos >= sizeof(uint64_t); pos += sizeof(uint64_t)) {
        uint64_t word;
        std::memcpy(&word, bytes + pos, sizeof(word));
        if (word != UINT64_C(0x5a5a5a5a5a5a5a5a)) return false;
    }
    for (; pos < count; ++pos) if (bytes[pos] != 0x5a) return false;
    return true;
}
