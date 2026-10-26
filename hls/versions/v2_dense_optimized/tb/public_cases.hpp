#ifndef MODEL_GEMM_PUBLIC_CASES_HPP
#define MODEL_GEMM_PUBLIC_CASES_HPP
#include "../include/model_gemm_config.hpp"
#include <vector>
#include <string>
#include <stdexcept>
#include <limits>

namespace public_cases {
// Public development PRNG, fixed by the protocol. Never a holdout seed.
inline uint32_t next(uint32_t& state) {
    state^=state<<13; state^=state>>17; state^=state<<5; return state;
}
inline int8_t random_value(uint32_t& state, unsigned distribution) {
    uint32_t r=next(state);
    if (distribution==0) return static_cast<int8_t>(static_cast<int>(r%17)-8);
    if (distribution==2 && r%10<8) return 0;
    return static_cast<int8_t>(static_cast<int>((r>>8)%256)-128);
}
inline void fill(const model_gemm::Shape& s, unsigned test,
                 int8_t* X, int8_t* W) {
    const unsigned nx=s.M*s.K, nw=s.K*s.N;
    for(unsigned i=0;i<nx;++i) X[i]=0;
    for(unsigned i=0;i<nw;++i) W[i]=0;
    if(test>=12) {
        uint32_t state=0x29A10001u ^ (s.M<<16) ^ (s.K<<4) ^ s.N ^ (test*0x9e3779b9u);
        const unsigned distribution=(test-12)%3;
        for(unsigned i=0;i<nx;++i) X[i]=random_value(state,distribution);
        for(unsigned i=0;i<nw;++i) W[i]=random_value(state,distribution);
        return;
    }
    if(test<=6) {
        const int8_t xv[7]={0,3,1,-1,127,-128,-128};
        const int8_t wv[7]={7,0,1,1,127,-128,127};
        for(unsigned i=0;i<nx;++i) X[i]=xv[test];
        for(unsigned i=0;i<nw;++i) W[i]=wv[test];
    } else if(test==7 || test==8) {
        const unsigned k=test==7 ? 0 : s.K-1;
        for(unsigned m=0;m<s.M;++m) X[m*s.K+k]=static_cast<int8_t>(static_cast<int>(m%11)-5);
        for(unsigned n=0;n<s.N;++n) W[k*s.N+n]=static_cast<int8_t>(static_cast<int>(n%17)-8);
    } else if(test==9) {
        X[(s.M-1)*s.K+s.K-1]=-128;
        W[(s.K-1)*s.N+s.N-1]=127;
    } else if(test==10) {
        for(unsigned m=0;m<s.M;++m)
            for(unsigned k=0;k<s.K;++k) X[m*s.K+k]=static_cast<int8_t>(static_cast<int>((m*13+k*7+3)%31)-15);
        for(unsigned k=0;k<s.K;++k)
            for(unsigned n=0;n<s.N;++n) W[k*s.N+n]=static_cast<int8_t>(static_cast<int>((k*5+n*11+1)%37)-18);
    } else {
        // Each adjacent K-tile pair cancels exactly; signs vary within tiles.
        for(unsigned m=0;m<s.M;++m)
            for(unsigned k=0;k<s.K;++k) {
                const int sign=(k/32)%2 ? -1 : 1;
                X[m*s.K+k]=static_cast<int8_t>(sign*(static_cast<int>((m*3+k%32)%15)-7));
            }
        for(unsigned k=0;k<s.K;++k)
            for(unsigned n=0;n<s.N;++n) W[k*s.N+n]=static_cast<int8_t>(static_cast<int>((k%32+n*2)%17)-8);
    }
}

inline std::vector<int32_t> golden(const model_gemm::Shape& s,
                                 const int8_t* X, const int8_t* W) {
    // Independent complete matrix oracle: no tile function, buffer or DUT helper.
    std::vector<int32_t> result(s.M*s.N);
    for(unsigned m=0;m<s.M;++m)
        for(unsigned n=0;n<s.N;++n) {
            int64_t sum=0;
            for(unsigned k=0;k<s.K;++k)
                sum+=static_cast<int64_t>(X[m*s.K+k])*static_cast<int64_t>(W[k*s.N+n]);
            if(sum<std::numeric_limits<int32_t>::min() || sum>std::numeric_limits<int32_t>::max())
                throw std::runtime_error("Reference INT32 range");
            result[m*s.N+n]=static_cast<int32_t>(sum);
        }
    return result;
}

struct Transaction { unsigned shape, test; };
static const Transaction RTL_TRANSACTIONS[8] = {
    {0,10},{1,10},{2,10},{3,10},{4,10},{4,5},{0,15},{1,16}
};
}
#endif
