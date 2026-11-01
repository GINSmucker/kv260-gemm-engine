#pragma once
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <sstream>

namespace opt50_diagnostic {
inline uint64_t now() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}
// All stamps are main-thread steady-clock wall times; worker times are not summed.
struct Linear {
    std::array<uint64_t,49*7> stamps{};
    void reset() { stamps={}; }
    void record(unsigned layer,const std::array<uint64_t,7>& values) {
        if(layer>=49 || stamps[7*layer])throw std::runtime_error("duplicate diagnostic Linear");
        for(size_t i=0;i<values.size();++i)
            if(!values[i] || (i && values[i]<values[i-1]))throw std::runtime_error("Linear stamp order");
        std::copy(values.begin(),values.end(),stamps.begin()+7*layer);
    }
};
struct Control {
    // enter, active-before-START, START-returned, completion-proven, leave, pauses.
    std::array<std::array<uint64_t,6>,49> layers{};
    void reset() { layers={}; }
    void mark(unsigned layer,size_t point) {
        if(layer>=49 || point>=5 || layers[layer][point])throw std::runtime_error("control stamp index/reuse");
        layers[layer][point]=now();
    }
    void pause(unsigned layer) {
        if(layer>=49)throw std::runtime_error("control pause layer");
        ++layers[layer][5];
    }
    std::string json() const {
        std::ostringstream out;out<<'[';
        bool comma=false;
        for(unsigned layer=0;layer<49;++layer)if(layers[layer][0]) {
            const auto& v=layers[layer];
            for(size_t i=0;i<5;++i)
                if(!v[i] || (i && v[i]<v[i-1]))throw std::runtime_error("incomplete control stamps");
            if(comma)out<<',';
            comma=true;out<<"{\"layer\":"<<layer<<",\"stamps\":[";
            for(size_t i=0;i<6;++i){if(i)out<<',';out<<v[i];}
            out<<"]}";
        }
        out<<']';return out.str();
    }
};
} // namespace opt50_diagnostic
