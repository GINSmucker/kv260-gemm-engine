#pragma once
#include "onnxruntime_cxx_api.h"
#include <array>
#include <cstring>
#include <exception>

namespace kv260_layout {
// Fixed model layouts only. Four disjoint row ranges; all FP32 bits are copied.
template<size_t Outputs> struct Kernel {
    struct Work {const float* x;std::array<float*,3> y{};};
    static void block(void* opaque,size_t part) noexcept {
        auto& w=*static_cast<Work*>(opaque);
        constexpr size_t cuts[]={0,50,99,148,197};
        for(size_t row=cuts[part];row<cuts[part+1];++row) {
            for(size_t head=0;head<3;++head) {
                if constexpr(Outputs==3) {
                    for(size_t qkv=0;qkv<3;++qkv)
                        std::memcpy(w.y[qkv]+(head*197+row)*64,w.x+row*576+(qkv*3+head)*64,64*sizeof(float));
                } else {
                    std::memcpy(w.y[0]+row*192+head*64,w.x+(head*197+row)*64,64*sizeof(float));
                }
            }
        }
    }
    OrtStatusPtr ComputeV2(OrtKernelContext* raw) noexcept {
        try {
            Ort::KernelContext ctx(raw);auto x=ctx.GetInput(0);Work work{x.GetTensorData<float>(),{}};
            if constexpr(Outputs==3) {
                const int64_t dims[]={1,3,197,64};
                for(size_t i=0;i<3;++i)work.y[i]=ctx.GetOutput(i,dims,4).GetTensorMutableData<float>();
            } else {
                const int64_t dims[]={1,197,192};work.y[0]=ctx.GetOutput(0,dims,3).GetTensorMutableData<float>();
            }
            Ort::ThrowOnError(Ort::GetApi().KernelContext_ParallelFor(raw,block,4,4,&work));
            return nullptr;
        } catch(const std::exception& e) {return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
    }
};
template<size_t Outputs> struct Op:Ort::CustomOpBase<Op<Outputs>,Kernel<Outputs>,true> {
    OrtStatusPtr CreateKernelV2(const OrtApi&,const OrtKernelInfo*,void** out) const noexcept {
        try {*out=new Kernel<Outputs>();return nullptr;}
        catch(const std::exception& e){return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
    }
    const char* GetName() const {return Outputs==3?"QKVLayout4":"AttentionMerge4";}
    const char* GetExecutionProviderType() const {return "CPUExecutionProvider";}
    size_t GetInputTypeCount() const {return 1;}
    ONNXTensorElementDataType GetInputType(size_t) const {return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
    size_t GetOutputTypeCount() const {return Outputs;}
    ONNXTensorElementDataType GetOutputType(size_t) const {return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
};
}
