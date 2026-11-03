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
    virtual void begin(uint64_t run)=0;
    virtual void end(uint64_t run)=0;
    virtual void abort(uint64_t run)=0;
    virtual void execute(unsigned layer)=0;
    // Called by management INFO only, outside Runner.run's measured region.
    virtual bool flush_performance_receipt()=0;
    virtual bool can_stop()const=0;
    virtual void fail_lifetime(const char* reason)noexcept=0;
};
std::unique_ptr<Kernel> make_kernel(const char* metadata, const std::vector<int8_t>& weights,
                                    const char* model_root);
}
