#pragma once
#include "protocol.h"
#include <memory>
#include <vector>

namespace kv5 {
// The implementation owns the DMA lifetime, including exceptions after START.
class Kernel {
public:
#ifdef KV5_DIAGNOSTIC
    std::array<uint64_t,6> timing{}; // copy, input sync, MMIO, wait, output sync, output copy
#endif
    virtual ~Kernel() = default;
    virtual void execute(Shape s, const int8_t* x, const int8_t* w, int32_t* y)=0;
};
std::unique_ptr<Kernel> make_kernel(const char* metadata, const char* root);
}
