#pragma once
#include <array>
#include <cstdint>
#include <cstddef>
#include <stdexcept>

namespace kv5 {
// New wire identity: never connect this adapter to the old six-argument owner.
inline constexpr uint32_t wire_magic = 0x3035504f;
inline constexpr uint16_t wire_version = 50;
inline constexpr size_t shared_offset = 64;
inline constexpr size_t shared_x_bytes = 197 * 576;
inline constexpr size_t shared_y_bytes = 197 * 576 * 4;
// ZOCL rounds GEM sizes to PAGE_SIZE; imported xrt::bo reports that size.
// This fixed ARM platform contract is 4096-byte pages, checked before XRT.
inline constexpr size_t shared_x_allocation = ((shared_x_bytes+128+4095)/4096)*4096;
inline constexpr size_t shared_y_allocation = ((shared_y_bytes+128+4095)/4096)*4096;
inline void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
struct Shape {
    uint32_t m, k, n;
    size_t x_bytes() const { return size_t(m) * k; }
    size_t w_bytes() const { return size_t(k) * n; }
    size_t y_bytes() const { return size_t(m) * n * 4; }
};
inline Shape shape(unsigned layer) {
    require(layer < 49, "layer outside model");
    if (layer == 48) return {1, 192, 1000};
    switch (layer % 4) {
    case 0: return {197, 192, 576};
    case 1: return {197, 192, 192};
    case 2: return {197, 192, 576};
    default: return {197, 576, 192};
    }
}
enum class Op : uint16_t { Hello=1, Begin, Run, End, Abort, Query, Stop, Info, Map };
enum class Status : uint32_t { Ok, Invalid, Busy, Missing, Fault };
struct Header {
    Op op = Op::Hello;
    uint64_t run = 0;
    uint32_t sequence = 0, layer = 0, bytes = 0;
    Status status = Status::Ok;
};
struct SharedInfo { uint64_t size[2], physical[2]; uint32_t flags[2]; };
using Wire = std::array<unsigned char, 32>;
inline void put(Wire& w, size_t p, uint64_t value, size_t n) {
    for (size_t i=0; i<n; ++i) w[p+i] = static_cast<unsigned char>(value >> (8*i));
}
inline uint64_t get(const Wire& w, size_t p, size_t n) {
    uint64_t value=0;
    for (size_t i=0; i<n; ++i) value |= uint64_t(w[p+i]) << (8*i);
    return value;
}
inline Wire encode(const Header& h) {
    Wire w{};
    put(w,0,wire_magic,4); put(w,4,wire_version,2); put(w,6,uint16_t(h.op),2);
    put(w,8,h.run,8); put(w,16,h.sequence,4); put(w,20,h.layer,4);
    put(w,24,h.bytes,4); put(w,28,uint32_t(h.status),4);
    return w;
}
inline Header decode(const Wire& w) {
    require(get(w,0,4)==wire_magic && get(w,4,2)==wire_version, "OPT50 wire version; legacy owner/client forbidden");
    auto op=get(w,6,2);
    require(op>=1 && op<=9, "wire operation");
    return {Op(op),get(w,8,8),uint32_t(get(w,16,4)),uint32_t(get(w,20,4)),
            uint32_t(get(w,24,4)),Status(get(w,28,4))};
}
}
