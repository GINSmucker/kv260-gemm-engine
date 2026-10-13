// Isolated B3 numerical candidate. No model, bridge, trace or global state.
// Caller keeps the original shape/weight/scale/bias checks and owned buffers.
#pragma once
#if !defined(__aarch64__)
#error This candidate requires AArch64 Neon; it is not a Windows replacement.
#endif
#include <arm_neon.h>
#include <algorithm>
#include <cfenv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
namespace kv260_b3 {
inline void nearest() {
    if(std::fegetround()!=FE_TONEAREST)throw std::runtime_error("BAD_INPUT: rounding mode is not nearest even");
}
inline void quantize(const float* x,std::size_t M,std::size_t K,int8_t* xq,float* sx) {
    nearest();
    const auto exponent=vdupq_n_u32(0x7f800000U);
    const auto zero=vdupq_n_f32(0.0f);
    const auto low=vdupq_n_f32(-127.0f),high=vdupq_n_f32(127.0f);
    for(std::size_t m=0;m<M;++m) {
        const auto row=x+m*K;auto a=zero;std::size_t k=0;
        for(;k+4<=K;k+=4) {
            const auto v=vld1q_f32(row+k);const auto bad=vceqq_u32(vandq_u32(vreinterpretq_u32_f32(v),exponent),exponent);
            if(vmaxvq_u32(bad))throw std::runtime_error("BAD_INPUT: nonfinite activation");
            a=vmaxq_f32(a,vabsq_f32(v));
        }
        float maximum=vmaxvq_f32(a);
        for(;k<K;++k){const auto v=row[k];if(!std::isfinite(v))throw std::runtime_error("BAD_INPUT: nonfinite activation");maximum=std::max(maximum,std::fabs(v));}
        sx[m]=maximum==0?1.0f:std::max(maximum/127.0f,std::numeric_limits<float>::min());
        const auto scale=vdupq_n_f32(sx[m]);k=0;
        auto rounded=[&](std::size_t offset) {
            const auto q=vrndnq_f32(vdivq_f32(vld1q_f32(row+offset),scale));
            return vcvtq_s32_f32(vmaxq_f32(low,vminq_f32(high,q)));
        };
        for(;k+16<=K;k+=16) {
            const auto a0=vmovn_s32(rounded(k)),a1=vmovn_s32(rounded(k+4));
            const auto a2=vmovn_s32(rounded(k+8)),a3=vmovn_s32(rounded(k+12));
            const auto b0=vmovn_s16(vcombine_s16(a0,a1)),b1=vmovn_s16(vcombine_s16(a2,a3));
            vst1q_s8(xq+m*K+k,vcombine_s8(b0,b1));
        }
        for(;k<K;++k){const float q=std::nearbyintf(row[k]/sx[m]);xq[m*K+k]=static_cast<int8_t>(std::max(-127.0f,std::min(127.0f,q)));}
    }
}
inline void dequantize(const int32_t* y,std::size_t M,std::size_t N,const float* sx,const float* sw,const float* bias,float* out) {
    nearest();
    for(std::size_t m=0;m<M;++m) {
        const auto scale=vdupq_n_f32(sx[m]);std::size_t n=0;
        for(;n+4<=N;n+=4) {
            const auto f=vcvtq_f32_s32(vld1q_s32(y+m*N+n));
            const auto t1=vmulq_f32(f,scale);
            const auto t2=vmulq_f32(t1,vld1q_f32(sw+n));
            vst1q_f32(out+m*N+n,vaddq_f32(t2,vld1q_f32(bias+n)));
        }
        for(;n<N;++n){float t1=static_cast<float>(y[m*N+n])*sx[m];float t2=t1*sw[n];out[m*N+n]=t2+bias[n];}
    }
}
} // namespace kv260_b3
