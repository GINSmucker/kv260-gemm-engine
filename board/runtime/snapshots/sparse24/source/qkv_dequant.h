#pragma once
#include "quant_neon.h"
#include <array>
namespace kv260_b3 {
// Complete rows own disjoint Q/K/V slices; arithmetic order matches dequantize.
inline void dequantize_qkv(const int32_t* y,const float* sx,const float* sw,const float* bias,
                          const std::array<float*,3>& out,size_t start,size_t count) {
    nearest();
    for(size_t row=start;row<start+count;++row) {
        const auto scale=vdupq_n_f32(sx[row]);
        for(size_t part=0;part<3;++part)for(size_t head=0;head<3;++head) {
            const size_t channel=(part*3+head)*64;
            auto* target=out[part]+(head*197+row)*64;
            for(size_t d=0;d<64;d+=4) {
                const auto f=vcvtq_f32_s32(vld1q_s32(y+row*576+channel+d));
                const auto t1=vmulq_f32(f,scale);
                const auto t2=vmulq_f32(t1,vld1q_f32(sw+channel+d));
                vst1q_f32(target+d,vaddq_f32(t2,vld1q_f32(bias+channel+d)));
            }
        }
    }
}
}
