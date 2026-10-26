#pragma once
#include "onnxruntime_cxx_api.h"
#include <arm_neon.h>
#include <array>
#include <memory>
#include <vector>
namespace kv260_gelu {
constexpr size_t count=197*576;
struct Kernel {
    std::shared_ptr<std::vector<float>> scratch;
    Ort::Op erf{nullptr};
    Ort::MemoryInfo memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
    Kernel(const OrtKernelInfo* info,std::shared_ptr<std::vector<float>> buffer):scratch(std::move(buffer)) {
        const char* constraint[]={"T"};const ONNXTensorElementDataType types[]={ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT};
        erf=Ort::Op::Create(info,"Erf","",13,constraint,types,1,nullptr,0,1,1);
    }
    struct Work {Kernel* self;OrtKernelContext* context;const float* x;float* y;float* maxima;std::array<OrtStatus*,4> errors{};};
    static void block(void* opaque,size_t part) noexcept {
        auto& w=*static_cast<Work*>(opaque);
        try {
            constexpr size_t cuts[]={0,50,99,148,197};
            const size_t start=cuts[part],rows=cuts[part+1]-start,chunk=rows*576,offset=start*576;
            const float* x=w.x+offset;float* y=w.y+offset;float* d=w.self->scratch->data()+offset;
            const auto divisor=vdupq_n_f32(1.4142135381698608f);
            for(size_t i=0;i<chunk;i+=4)vst1q_f32(d+i,vdivq_f32(vld1q_f32(x+i),divisor));
            const int64_t dims[]={int64_t(chunk)};
            auto input=Ort::Value::CreateTensor<float>(w.self->memory,d,chunk,dims,1);
            auto output=Ort::Value::CreateTensor<float>(w.self->memory,y,chunk,dims,1);
            const OrtValue* inputs[]={input};OrtValue* outputs[]={output};
            w.self->erf.Invoke(w.context,inputs,1,outputs,1);
            const auto one=vdupq_n_f32(1.0f),half=vdupq_n_f32(0.5f);
            for(size_t row=0;row<rows;++row) {
                auto maximum=vdupq_n_f32(0.0f);
                for(size_t col=0;col<576;col+=4) {
                    const size_t i=row*576+col;
                    const auto a=vaddq_f32(vld1q_f32(y+i),one);
                    const auto b=vmulq_f32(vld1q_f32(x+i),a);
                    const auto result=vmulq_f32(b,half);
                    vst1q_f32(y+i,result);maximum=vmaxq_f32(maximum,vabsq_f32(result));
                }
                w.maxima[start+row]=vmaxvq_f32(maximum);
            }
        } catch(const std::exception& e){w.errors[part]=Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
    }
    OrtStatusPtr ComputeV2(OrtKernelContext* raw) noexcept {
        try {
            Ort::KernelContext context(raw);auto input=context.GetInput(0);const int64_t dims[]={1,197,576};
            auto output=context.GetOutput(0,dims,3);
            const int64_t md[]={197};auto maxima=context.GetOutput(1,md,1);
            Work work{this,raw,input.GetTensorData<float>(),output.GetTensorMutableData<float>(),maxima.GetTensorMutableData<float>(),{}};
            Ort::ThrowOnError(Ort::GetApi().KernelContext_ParallelFor(raw,block,4,4,&work));
            OrtStatus* first=nullptr;
            for(auto* error:work.errors)if(error){if(!first)first=error;else Ort::GetApi().ReleaseStatus(error);}
            return first;
        } catch(const std::exception& e){return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
    }
};
struct Op:Ort::CustomOpBase<Op,Kernel,true> {
    // The fixed sequential graph reuses one buffer across all twelve GELUs.
    std::shared_ptr<std::vector<float>> scratch=std::make_shared<std::vector<float>>(count);
    OrtStatusPtr CreateKernelV2(const OrtApi&,const OrtKernelInfo* info,void** out) const noexcept {
        try {*out=new Kernel(info,scratch);return nullptr;}
        catch(const std::exception& e){return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
    }
    const char* GetName() const{return "Gelu4";}
    const char* GetExecutionProviderType() const{return "CPUExecutionProvider";}
    size_t GetInputTypeCount() const{return 1;}
    ONNXTensorElementDataType GetInputType(size_t) const{return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
    size_t GetOutputTypeCount() const{return 2;}
    ONNXTensorElementDataType GetOutputType(size_t) const{return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
};
}
