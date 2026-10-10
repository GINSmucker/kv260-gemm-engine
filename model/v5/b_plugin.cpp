#define ORT_API_MANUAL_INIT
#include "onnxruntime_cxx_api.h"
#include "client.h"
#include "quant_neon.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <fstream>
#include <memory>
#include <thread>
#include <vector>

namespace {
using namespace kv5;
struct CustomOp;
struct Session {
    std::unique_ptr<Client> client;
    std::unique_ptr<CustomOp> op;
    std::unique_ptr<Ort::CustomOpDomain> domain;
    std::atomic<bool> active{false};
    uint64_t run=0;
    unsigned next=0;
    bool failed=false;
    std::string error;
#ifdef KV5_DIAGNOSTIC
    std::array<uint64_t,4> timing{}; // quantization, C round trip, dequantization, whole Compute before dump
#endif
    ~Session();
};
using Handle=std::shared_ptr<Session>;
thread_local std::weak_ptr<Session> registering;
struct Kernel {
    Handle session;
    Shape s;
    unsigned index;
    std::vector<int8_t> xq;
    std::vector<float> sx, sw, bias;
    std::vector<int32_t> y;
    Kernel(const OrtKernelInfo* raw,Handle bound):session(std::move(bound)) {
        Ort::ConstKernelInfo info(raw);
        auto layer=info.GetAttribute<int64_t>("layer_index");
        require(layer>=0 && layer<49,"B layer"); index=unsigned(layer); s=shape(index);
        require(info.GetAttribute<int64_t>("M")==s.m && info.GetAttribute<int64_t>("K")==s.k && info.GetAttribute<int64_t>("N")==s.n,"B fixed shape");
        require(info.GetAttribute<std::string>("quant_protocol_id")=="B_W8A8_ROW_DYNAMIC_V1","B quantization protocol");
        for(unsigned i=1;i<4;++i) {
            int constant=0; auto v=info.GetTensorConstantInput(i,&constant);
            require(constant && v,"B requires fixed model initializers");
            auto dims=v.GetTensorTypeAndShapeInfo().GetShape();
            require(dims==(i==1 ? std::vector<int64_t>{s.k,s.n} : std::vector<int64_t>{s.n}),"B initializer shape");
            if(i>1) {
                const auto* p=v.GetTensorData<float>();
                for(unsigned j=0;j<s.n;++j) require(std::isfinite(p[j]) && (i!=2 || p[j]>0),"B scale/bias");
                (i==2?sw:bias).assign(p,p+s.n);
            }
        }
        xq.resize(s.x_bytes()); sx.resize(s.m); y.resize(size_t(s.m)*s.n);
    }
    OrtStatusPtr ComputeV2(OrtKernelContext* raw) noexcept {
        try {
            require(session->active && !session->failed && session->next==index,"B inactive/failed/order");
#ifdef KV5_DIAGNOSTIC
            using Clock=std::chrono::steady_clock;
            auto entered=Clock::now();
#endif
            Ort::KernelContext context(raw); auto xv=context.GetInput(0);
            auto dimensions=xv.GetTensorTypeAndShapeInfo().GetShape();
            require(dimensions==(s.m==1?std::vector<int64_t>{1,s.k}:std::vector<int64_t>{1,s.m,s.k}),"B activation shape");
#ifdef KV5_DIAGNOSTIC
            auto quant_begin=Clock::now();
#endif
            kv260_b3::quantize(xv.GetTensorData<float>(),s.m,s.k,xq.data(),sx.data());
#ifdef KV5_DIAGNOSTIC
            auto quant_end=Clock::now();
#endif
            Header request{Op::Run,session->run,index+1,index,uint32_t(xq.size()),Status::Ok};
            auto result=session->client->call(request,xq.data(),y.data(),s.y_bytes());
            require(result.status==Status::Ok && result.bytes==s.y_bytes(),"owner RUN failed; no fallback");
#ifdef KV5_DIAGNOSTIC
            auto gemm_end=Clock::now();
#endif
            dimensions.back()=s.n;
            auto output=context.GetOutput(0,dimensions);
            kv260_b3::dequantize(y.data(),s.m,s.n,sx.data(),sw.data(),bias.data(),output.GetTensorMutableData<float>());
#ifdef KV5_DIAGNOSTIC
            auto end=Clock::now();
            auto ns=[](auto a,auto b) {return std::chrono::duration_cast<std::chrono::nanoseconds>(b-a).count();};
            session->timing[0]+=ns(quant_begin,quant_end); session->timing[1]+=ns(quant_end,gemm_end);
            session->timing[2]+=ns(gemm_end,end); session->timing[3]+=ns(entered,end);
            const char* directory=std::getenv("KV5_DUMP");
            if(directory && *directory) {
                auto dump=[&](const char* suffix,const void* p,size_t n) {
                    std::ofstream f(std::string(directory)+"/"+std::to_string(index)+suffix,std::ios::binary);
                    f.write(static_cast<const char*>(p),n); require(bool(f),"diagnostic dump");
                };
                dump("_Xq.bin",xq.data(),xq.size()); dump("_sx.bin",sx.data(),sx.size()*4); dump("_Y.bin",y.data(),y.size()*4);
            }
#endif
            ++session->next; return nullptr;
        } catch(const std::exception& e) {
            session->failed=true; session->error=e.what();
            return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());
        }
    }
};
struct CustomOp:Ort::CustomOpBase<CustomOp,Kernel,true> {
    std::weak_ptr<Session> session;
    explicit CustomOp(const Handle& s):session(s) {}
    OrtStatusPtr CreateKernelV2(const OrtApi&,const OrtKernelInfo* info,void** out) const noexcept {
        try { auto s=session.lock(); require(bool(s),"closed B session"); *out=new Kernel(info,std::move(s)); return nullptr; }
        catch(const std::exception& e) { return Ort::GetApi().CreateStatus(ORT_FAIL,e.what()); }
    }
    const char* GetName() const { return "QuantLinear"; }
    const char* GetExecutionProviderType() const { return "CPUExecutionProvider"; }
    size_t GetInputTypeCount() const { return 4; }
    ONNXTensorElementDataType GetInputType(size_t i) const { return i==1?ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8:ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT; }
    size_t GetOutputTypeCount() const { return 1; }
    ONNXTensorElementDataType GetOutputType(size_t) const { return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT; }
};
Session::~Session() = default;
template<class F> int action(void* handle,F fn) {
    if(!handle) return 1;
    auto s=*static_cast<Handle*>(handle);
    try { fn(*s); s->error.clear(); return 0; }
    catch(const std::exception& e) { s->error=e.what(); return 1; }
}
}
#define EXPORT extern "C" __attribute__((visibility("default")))
EXPORT int kv5_b_open(const char* socket,const unsigned char* model,void** out) {
    if(!socket || !model || !out) return 1;
    *out=nullptr;
    try {
        auto s=std::make_shared<Session>(); std::array<unsigned char,32> digest{};
        std::copy(model,model+32,digest.begin()); s->client=std::make_unique<Client>(socket,digest);
        registering=s; *out=new Handle(std::move(s)); return 0;
    } catch(...) { return 1; }
}
// Call close after destroying the ORT InferenceSession. No permanent process binding.
EXPORT void kv5_b_close(void* handle) { delete static_cast<Handle*>(handle); }
EXPORT const char* kv5_b_error(void* handle) { return handle?(*static_cast<Handle*>(handle))->error.c_str():"null session"; }
EXPORT int kv5_b_begin(void* handle,uint64_t run) {
    return action(handle,[&](Session& s) {
        bool idle=false; require(s.active.compare_exchange_strong(idle,true),"session already running");
        try {
            require(s.client->call({Op::Begin,run,0,0,0,Status::Ok}).status==Status::Ok,"owner BEGIN failed");
            s.run=run; s.next=0; s.failed=false;
#ifdef KV5_DIAGNOSTIC
            s.timing={};
#endif
        } catch(...) { s.active=false; throw; }
    });
}
EXPORT int kv5_b_end(void* handle) {
    return action(handle,[](Session& s) {
        require(s.active && !s.failed && s.next==49,"incomplete graph");
        require(s.client->call({Op::End,s.run,50,49,0,Status::Ok}).status==Status::Ok,"owner END failed");
        s.active=false;
    });
}
EXPORT int kv5_b_abort(void* handle) {
    return action(handle,[](Session& s) {
        require(s.active,"inactive graph");
        require(s.client->call({Op::Abort,s.run,0,0,0,Status::Ok}).status==Status::Ok,"owner ABORT failed");
        s.active=false;
    });
}
#ifdef KV5_DIAGNOSTIC
EXPORT int kv5_b_timing(void* handle,uint64_t* out) {
    return action(handle,[&](Session& s) { require(out && !s.active,"timing after graph"); std::copy(s.timing.begin(),s.timing.end(),out); });
}
#endif
EXPORT OrtStatus* ORT_API_CALL RegisterCustomOps(OrtSessionOptions* options,const OrtApiBase* base) {
    Ort::InitApi(base->GetApi(ORT_API_VERSION));
    try {
        auto s=registering.lock(); require(s && !s->domain,"open B session before registering ops on this thread");
        s->op=std::make_unique<CustomOp>(s);
        s->domain=std::make_unique<Ort::CustomOpDomain>("com.kv260"); s->domain->Add(s->op.get());
        Ort::UnownedSessionOptions(options).Add(*s->domain); registering.reset(); return nullptr;
    } catch(const std::exception& e) { return Ort::GetApi().CreateStatus(ORT_FAIL,e.what()); }
}
