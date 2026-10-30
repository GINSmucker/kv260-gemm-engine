#define ORT_API_MANUAL_INIT
#include "onnxruntime_cxx_api.h"
#include "layout4.h"
#include "client.h"
#include "route.h"
#include "quant_neon.h"
#include "qkv_dequant.h"
#include "gelu4.h"
#include "model_catalog.hpp"
#include "picosha2.h"
#include <atomic>
#include <exception>
#include <chrono>
#include <cmath>
#include <fstream>
#include <memory>
#include <thread>
#include <vector>

namespace {
using namespace kv5;
std::string tensor_sha(const void* data,size_t bytes) {
    const auto* p=static_cast<const unsigned char*>(data);
    return picosha2::hash256_hex_string(p,p+bytes);
}
struct CustomOp;
struct QkvOp;
struct PremaxOp;
template<class T> struct View { T* pointer=nullptr; T* data() { return pointer; } };
#if !defined(OPT50_RESIDENT_BATCH) || OPT50_RESIDENT_BATCH != 1
#error This NEW plugin requires OPT50_RESIDENT_BATCH=1
#endif
struct Session {
    std::unique_ptr<Client> client;
    std::unique_ptr<CustomOp> op;
    std::unique_ptr<QkvOp> qkv_linear;
    std::unique_ptr<PremaxOp> premax;
    std::unique_ptr<kv260_gelu::Op> gelu;
    std::unique_ptr<kv260_layout::Op<3>> qkv;
    std::unique_ptr<kv260_layout::Op<1>> merge;
    std::unique_ptr<Ort::CustomOpDomain> domain;
    std::atomic<bool> active{false};
    uint64_t run=0;
    unsigned next=0;
    bool failed=false;
    std::string error;
    // One synchronous Compute at a time in this session. ORT owns FP32 outputs.
    View<int8_t> xq;
    std::vector<float> sx=std::vector<float>(197);
    View<int32_t> y;
    std::vector<int32_t> cpu_y=std::vector<int32_t>(cpu_y_capacity());
#ifdef KV5_DIAGNOSTIC
    std::array<uint64_t,4> timing{};
    std::array<uint64_t,49*4> layers{}; // quantization, C round trip, dequantization, whole Compute before dump
#endif
    ~Session();
};
using Handle=std::shared_ptr<Session>;
thread_local std::weak_ptr<Session> registering;
// Four disjoint complete-row ranges; the existing arithmetic runs unchanged.
template<class F> void rows4(OrtKernelContext* raw,const F& fn) {
    struct Work {const F& fn;std::array<std::exception_ptr,4> errors{};};
    Work work{fn,{}};
    auto block=[](void* opaque,size_t i) noexcept {
        auto& w=*static_cast<Work*>(opaque);
        constexpr size_t cuts[]={0,50,99,148,197};
        try {w.fn(cuts[i],cuts[i+1]-cuts[i]);}catch(...){w.errors[i]=std::current_exception();}
    };
    Ort::ThrowOnError(Ort::GetApi().KernelContext_ParallelFor(raw,block,4,4,&work));
    for(auto& e:work.errors)if(e)std::rethrow_exception(e);
}
struct Kernel {
    Handle session;
    Shape s;
    unsigned index;
    std::vector<float> sw, bias;
    std::vector<int64_t> output_dims;
    bool use_pl=false;
    bool qkv_output=false;
    bool has_maxima=false;
    Ort::Op cpu_op{nullptr};
    Ort::Value cpu_x{nullptr},cpu_w{nullptr},cpu_y{nullptr};
    Kernel(const OrtKernelInfo* raw,Handle bound,bool fused=false,bool maxima=false):session(std::move(bound)),qkv_output(fused),has_maxima(maxima) {
        Ort::ConstKernelInfo info(raw);
        auto layer=info.GetAttribute<int64_t>("layer_index");
        require(layer>=0 && layer<49,"B layer"); index=unsigned(layer); s=shape(index); use_pl=offloaded(index);
        const auto& identity=opt50_model::layers[index];
        require(identity.m==s.m && identity.k==s.k && identity.n==s.n && identity.pl==use_pl,
                "compiled OPT50 layer shape/route identity");
        require(info.GetAttribute<int64_t>("M")==s.m && info.GetAttribute<int64_t>("K")==s.k && info.GetAttribute<int64_t>("N")==s.n,"B fixed shape");
        output_dims=s.m==1 ? std::vector<int64_t>{1,s.n} : std::vector<int64_t>{1,s.m,s.n};
        if(qkv_output) {
            require(use_pl && index<48 && index%4==0 && s.m==197 && s.k==192 && s.n==576,"fixed QKV output");
            output_dims={1,3,197,64};
        }
        if(has_maxima)require(index<48 && index%4==3 && s.k==576,"fixed fc2 maxima");
        require(info.GetAttribute<std::string>("quant_protocol_id")=="B_W8A8_ROW_DYNAMIC_V1","B quantization protocol");
        for(unsigned i=1;i<4;++i) {
            int constant=0; auto v=info.GetTensorConstantInput(i,&constant);
            require(constant && v,"B requires fixed model initializers");
            auto dims=v.GetTensorTypeAndShapeInfo().GetShape();
            require(dims==(i==1 ? std::vector<int64_t>{s.k,s.n} : std::vector<int64_t>{s.n}),"B initializer shape");
            const auto type=v.GetTensorTypeAndShapeInfo().GetElementType();
            require(type==(i==1?ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8:ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT),
                    "OPT50 initializer dtype");
            const char* expected=i==1?identity.w_sha:i==2?identity.scale_sha:identity.bias_sha;
            const auto digest=i==1 ? tensor_sha(v.GetTensorData<int8_t>(),s.w_bytes()) :
                                    tensor_sha(v.GetTensorData<float>(),s.n*sizeof(float));
            require(digest==expected,"OPT50 W/scale/bias differs from compiled model; no fallback");
            if(i==1)require(info.GetAttribute<std::string>("W_sha256")==identity.w_sha,
                            "OPT50 graph weight identity attribute differs");
            if(i>1) {
                const auto* p=v.GetTensorData<float>();
                for(unsigned j=0;j<s.n;++j) require(std::isfinite(p[j]) && (i!=2 || p[j]>0),"B scale/bias");
                (i==2?sw:bias).assign(p,p+s.n);
            }
        }
        if(!use_pl) {
            const char* constraints[]={"T1","T2"};
            const ONNXTensorElementDataType types[]={ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8,ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8};
            cpu_op=Ort::Op::Create(raw,"MatMulInteger","",10,constraints,types,2,nullptr,0,2,1);
            auto memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
            const int64_t xd[]={s.m,s.k},wd[]={s.k,s.n},yd[]={s.m,s.n};
            int constant=0;auto w=info.GetTensorConstantInput(1,&constant);
            cpu_x=Ort::Value::CreateTensor<int8_t>(memory,session->xq.data(),s.x_bytes(),xd,2);
            cpu_w=Ort::Value::CreateTensor<int8_t>(memory,const_cast<int8_t*>(w.GetTensorData<int8_t>()),s.w_bytes(),wd,2);
            cpu_y=Ort::Value::CreateTensor<int32_t>(memory,session->cpu_y.data(),s.y_bytes()/4,yd,2);
        }
    }
    OrtStatusPtr ComputeV2(OrtKernelContext* raw) noexcept {
        try {
            require(session->active && !session->failed && session->next==index,"B inactive/failed/order");
#ifdef KV5_DIAGNOSTIC
            using Clock=std::chrono::steady_clock;
            auto entered=Clock::now();
#endif
            Ort::KernelContext context(raw); auto xv=context.GetInput(0);
#ifdef KV5_DIAGNOSTIC
            auto quant_begin=Clock::now();
#endif
            auto& xq=session->xq; auto& sx=session->sx;
            View<int32_t> y{use_pl?session->y.data():session->cpu_y.data()};
            const auto* input_data=xv.GetTensorData<float>();
            const float* maxima=has_maxima?context.GetInput(4).GetTensorData<float>():nullptr;
            if(s.m==197) rows4(raw,[&](size_t start,size_t count) {
                kv260_b3::quantize(input_data+start*s.k,count,s.k,xq.data()+start*s.k,sx.data()+start,maxima?maxima+start:nullptr);
            });
            else kv260_b3::quantize(input_data,s.m,s.k,xq.data(),sx.data());
#ifdef KV5_DIAGNOSTIC
            auto quant_end=Clock::now();
#endif
            if(use_pl) {
            session->client->sync_input(s.x_bytes());
            Header request{Op::Run,session->run,index+1,index,0,Status::Ok};
            auto result=session->client->call(request);
            require(result.status==Status::Ok && result.bytes==0,"shared owner RUN failed; no fallback");
            session->client->sync_output(s.y_bytes());
            } else {
                const OrtValue* inputs[]={cpu_x,cpu_w};OrtValue* outputs[]={cpu_y};
                cpu_op.Invoke(raw,inputs,2,outputs,1);
            }
#ifdef KV5_DIAGNOSTIC
            auto gemm_end=Clock::now();
#endif
            auto output=context.GetOutput(0,output_dims);
            auto* output_data=output.GetTensorMutableData<float>();
            std::array<float*,3> qkv_data{output_data,nullptr,nullptr};
            if(qkv_output) {
                qkv_data[1]=context.GetOutput(1,output_dims).GetTensorMutableData<float>();
                qkv_data[2]=context.GetOutput(2,output_dims).GetTensorMutableData<float>();
                rows4(raw,[&](size_t start,size_t count) {kv260_b3::dequantize_qkv(y.data(),sx.data(),sw.data(),bias.data(),qkv_data,start,count);});
            }
            else if(s.m==197) rows4(raw,[&](size_t start,size_t count) {
                kv260_b3::dequantize(y.data()+start*s.n,count,s.n,sx.data()+start,sw.data(),bias.data(),output_data+start*s.n);
            });
            else kv260_b3::dequantize(y.data(),s.m,s.n,sx.data(),sw.data(),bias.data(),output_data);
#ifdef KV5_DIAGNOSTIC
            auto end=Clock::now();
            auto ns=[](auto a,auto b) {return std::chrono::duration_cast<std::chrono::nanoseconds>(b-a).count();};
            session->timing[0]+=ns(quant_begin,quant_end); session->timing[1]+=ns(quant_end,gemm_end);
            session->timing[2]+=ns(gemm_end,end); session->timing[3]+=ns(entered,end);
            auto* layer=session->layers.data()+4*index;
            layer[0]=ns(quant_begin,quant_end);layer[1]=ns(quant_end,gemm_end);
            layer[2]=ns(gemm_end,end);layer[3]=ns(entered,end);
            const char* directory=std::getenv("KV5_DUMP");
            if(!directory && index<48 && index%4==0)directory=std::getenv("KV5_QKV_DUMP");
            if(directory && *directory) {
                auto dump=[&](const char* suffix,const void* p,size_t n) {
                    std::ofstream f(std::string(directory)+"/"+std::to_string(index)+suffix,std::ios::binary);
                    f.write(static_cast<const char*>(p),n); require(bool(f),"diagnostic dump");
                };
                dump("_X.bin",xv.GetTensorData<float>(),s.x_bytes()*4);
                std::vector<float> canonical;
                const float* dump_z=output_data;
                if(qkv_output) {
                    canonical.resize(s.m*s.n);
                    for(unsigned row=0;row<197;++row)for(unsigned part=0;part<3;++part)for(unsigned head=0;head<3;++head)
                        std::memcpy(canonical.data()+row*576+(part*3+head)*64,qkv_data[part]+(head*197+row)*64,64*sizeof(float));
                    dump_z=canonical.data();
                    for(unsigned part=0;part<3;++part)dump(part==0?"_Q.bin":part==1?"_K.bin":"_V.bin",qkv_data[part],197*192*sizeof(float));
                }
                dump("_Z.bin",dump_z,s.y_bytes());
                dump("_Xq.bin",xq.data(),s.x_bytes()); dump("_sx.bin",sx.data(),s.m*4); dump("_Y.bin",y.data(),s.y_bytes());
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
struct PremaxOp:Ort::CustomOpBase<PremaxOp,Kernel,true> {
    std::weak_ptr<Session> session;
    explicit PremaxOp(const Handle& s):session(s) {}
    OrtStatusPtr CreateKernelV2(const OrtApi&,const OrtKernelInfo* info,void** out) const noexcept {
        try { auto s=session.lock(); require(bool(s),"closed B session"); *out=new Kernel(info,std::move(s),false,true); return nullptr; }
        catch(const std::exception& e) { return Ort::GetApi().CreateStatus(ORT_FAIL,e.what()); }
    }
    const char* GetName() const { return "QuantLinearPremax"; }
    const char* GetExecutionProviderType() const { return "CPUExecutionProvider"; }
    size_t GetInputTypeCount() const { return 5; }
    ONNXTensorElementDataType GetInputType(size_t i) const { return i==1?ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8:ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT; }
    size_t GetOutputTypeCount() const { return 1; }
    ONNXTensorElementDataType GetOutputType(size_t) const { return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT; }
};
struct QkvOp:Ort::CustomOpBase<QkvOp,Kernel,true> {
    std::weak_ptr<Session> session;
    explicit QkvOp(const Handle& s):session(s) {}
    OrtStatusPtr CreateKernelV2(const OrtApi&,const OrtKernelInfo* info,void** out) const noexcept {
        try {auto s=session.lock();require(bool(s),"closed B session");*out=new Kernel(info,std::move(s),true);return nullptr;}
        catch(const std::exception& e){return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
    }
    const char* GetName() const {return "QuantQKV";}
    const char* GetExecutionProviderType() const {return "CPUExecutionProvider";}
    size_t GetInputTypeCount() const {return 4;}
    ONNXTensorElementDataType GetInputType(size_t i) const {return i==1?ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8:ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
    size_t GetOutputTypeCount() const {return 3;}
    ONNXTensorElementDataType GetOutputType(size_t) const {return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
};
// Fixed pointwise work: the original ORT Erf kernel, four disjoint aligned views.
Session::~Session() = default;
template<class F> int action(void* handle,F fn) {
    if(!handle) return 1;
    auto s=*static_cast<Handle*>(handle);
    try { fn(*s); s->error.clear(); return 0; }
    catch(const std::exception& e) { s->failed=true; s->error=e.what(); return 1; }
}
}
#define EXPORT extern "C" __attribute__((visibility("default")))
EXPORT const char* opt50_model_identity() { return opt50_model::weights_sha; }
EXPORT const char* opt50_source_model_identity() { return opt50_model::source_model_sha; }
EXPORT const char* opt50_reference_identity() { return opt50_model::reference_sha; }
EXPORT unsigned opt50_wire_version() { return kv5::wire_version; }
EXPORT const char* opt50_lifecycle_profile() { return "resident-bounded-batch-v4"; }
EXPORT int kv5_b_open(const char* socket,const unsigned char* model,void** out) {
    if(!socket || !model || !out) return 1;
    *out=nullptr;
    try {
        require(std::string(opt50_model::source_model_sha)=="ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43",
                "B24 final source model catalog identity differs");
        auto s=std::make_shared<Session>(); std::array<unsigned char,32> digest{};
        std::copy(model,model+32,digest.begin()); 
        require(picosha2::bytes_to_hex_string(digest.begin(),digest.end())==opt50_model::weights_sha,
                "OPT50 compiled model digest differs; legacy Dense handshake forbidden");
        s->client=std::make_unique<Client>(socket,digest);
        s->xq.pointer=s->client->x_data(); s->y.pointer=s->client->y_data();
        registering=s; *out=new Handle(std::move(s)); return 0;
    } catch(...) { return 1; }
}
// Call close after destroying the ORT InferenceSession. No permanent process binding.
EXPORT void kv5_b_close(void* handle) { delete static_cast<Handle*>(handle); }
EXPORT const char* kv5_b_error(void* handle) { return handle?(*static_cast<Handle*>(handle))->error.c_str():"null session"; }
EXPORT int kv5_b_begin(void* handle,uint64_t run) {
    return action(handle,[&](Session& s) {
        require(!s.failed&&run==s.run+1&&run<=50&&(!s.run||s.next==49),"resident session requires next complete run; failed sessions cannot retry");
        bool idle=false; require(s.active.compare_exchange_strong(idle,true),"session already running");
        try {
            
        require(s.client->call({Op::Begin,run,0,0,0,Status::Ok}).status==Status::Ok,"owner BEGIN failed");
            s.run=run; s.next=0; s.failed=false;
#ifdef KV5_DIAGNOSTIC
            s.timing={}; s.layers={};
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
EXPORT int boundary_layers(void* handle,uint64_t* out) {
    return action(handle,[&](Session& s) { require(out && !s.active,"layers after graph"); std::copy(s.layers.begin(),s.layers.end(),out); });
}
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
        s->qkv_linear=std::make_unique<QkvOp>(s);s->domain->Add(s->qkv_linear.get());
        s->premax=std::make_unique<PremaxOp>(s);s->domain->Add(s->premax.get());
        s->gelu=std::make_unique<kv260_gelu::Op>();s->domain->Add(s->gelu.get());
        s->qkv=std::make_unique<kv260_layout::Op<3>>();s->domain->Add(s->qkv.get());
        s->merge=std::make_unique<kv260_layout::Op<1>>();s->domain->Add(s->merge.get());
        Ort::UnownedSessionOptions(options).Add(*s->domain); registering.reset(); return nullptr;
    } catch(const std::exception& e) { return Ort::GetApi().CreateStatus(ORT_FAIL,e.what()); }
}
