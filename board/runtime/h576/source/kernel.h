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
    virtual std::array<int,2> share(SharedInfo& info)=0;
    virtual ~Kernel() = default;
    virtual void execute(unsigned layer)=0;
};
std::unique_ptr<Kernel> make_kernel(const char* metadata, const std::vector<int8_t>& weights);
}
