#pragma once
#include <array>
#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <string>
#ifdef _WIN32
#include <windows.h>
#else
#include <time.h>
#endif

namespace kv260_runtime_v3 {
struct Stamp { std::uint64_t wall_ns,thread_cpu_ns; };
inline Stamp stamp() {
    auto wall=std::chrono::steady_clock::now().time_since_epoch();
    std::uint64_t cpu=0;
#ifdef _WIN32
    FILETIME birth,exit,kernel,user;
    if(!GetThreadTimes(GetCurrentThread(),&birth,&exit,&kernel,&user))throw std::runtime_error("thread CPU clock unavailable");
    auto ticks=[](FILETIME t){return (std::uint64_t(t.dwHighDateTime)<<32)|t.dwLowDateTime;};
    cpu=(ticks(kernel)+ticks(user))*100;
#else
    timespec t{};
    if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))throw std::runtime_error("thread CPU clock unavailable");
    cpu=std::uint64_t(t.tv_sec)*1000000000+std::uint64_t(t.tv_nsec);
#endif
    return {static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(wall).count()),cpu};
}
// Adjacent stamps form a partition. A failed/unfinished measurement is never
// exported as a complete row. Session total must include terminal trace export.
class LayerTiming {
public:
    enum Phase { Input,Quantize,Prepare,Compute,Receipt,Dequantize,Trace,Dump,Count };
    struct Delta {std::uint64_t wall_ns=0,thread_cpu_ns=0;};
private:
    std::array<Delta,Count> values_{};
    Stamp start_,last_;
    unsigned next_=0;
public:
    explicit LayerTiming(Stamp begin):start_(begin),last_(begin) {}
    void mark(Phase p,Stamp now) {
        if(unsigned(p)!=next_ || now.wall_ns<last_.wall_ns || now.thread_cpu_ns<last_.thread_cpu_ns)
            throw std::runtime_error("timing phase/order/clock mismatch");
        values_[next_++]={now.wall_ns-last_.wall_ns,now.thread_cpu_ns-last_.thread_cpu_ns};last_=now;
    }
    const std::array<Delta,Count>& values() const {
        if(next_!=Count)throw std::runtime_error("unfinished timing partition");
        return values_;
    }
    Delta total() const {values();return {last_.wall_ns-start_.wall_ns,last_.thread_cpu_ns-start_.thread_cpu_ns};}
};
} // namespace kv260_runtime_v3
