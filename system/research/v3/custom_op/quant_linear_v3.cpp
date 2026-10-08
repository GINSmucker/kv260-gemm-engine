#define ORT_API_MANUAL_INIT
#if defined(__MINGW32__) && !defined(_stdcall)
// ORT's Windows C API uses the MSVC spelling. MinGW 8 exposes __stdcall.
#define _stdcall __stdcall
#endif
#include "onnxruntime_cxx_api.h"
#include "sha256_crypto.h"
#include "gemm_backend_v2b.h"
#include "../../../board/model_backend/v3/timing.h"
#include <algorithm>
#include <cfenv>
#include <chrono>
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <fstream>
#include <cstdio>
#include <iomanip>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#ifndef _WIN32
#include <dlfcn.h>
#else
#include <windows.h>
#endif

namespace {
std::mutex mu;
uint64_t calls=0, starts=0;
std::string env(const char* key, const char* fallback="") {
#ifdef _WIN32
  std::wstring wkey(key,key+std::strlen(key));
  DWORD count=GetEnvironmentVariableW(wkey.c_str(),nullptr,0);
  if(!count) return fallback;
  std::vector<wchar_t> value(count);
  GetEnvironmentVariableW(wkey.c_str(),value.data(),count);
  int bytes=WideCharToMultiByte(CP_UTF8,0,value.data(),-1,nullptr,0,nullptr,nullptr);
  std::string result(bytes,'\0');
  WideCharToMultiByte(CP_UTF8,0,value.data(),-1,result.data(),bytes,nullptr,nullptr);
  result.resize(bytes-1); return result;
#else
  const char* value=std::getenv(key); return value?value:fallback;
#endif
}
std::string digest(const void* pointer, size_t bytes) {
  return kv260_crypto_sha256(pointer,bytes);
}
void checked(bool value,const char* error) { if(!value) throw std::runtime_error(error); }
void text_copy(char* dst,size_t n,const std::string& s) {
  checked(s.size()<n,"BAD_INPUT: identifier too long"); std::memcpy(dst,s.c_str(),s.size()+1);
}
std::string bounded_text(const char* p,size_t n) {
  checked(std::memchr(p,0,n)!=nullptr,"BAD_IDENTITY: unterminated C receipt string");
  return std::string(p);
}
std::string json_quote(const std::string& s) {
  std::ostringstream out; out<<'"';
  for(unsigned char c:s) {
    if(c=='"'||c=='\\') out<<'\\'<<c;
    else if(c<32) out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<static_cast<unsigned>(c)<<std::dec;
    else out<<c;
  }
  out<<'"'; return out.str();
}
void write_payload(const std::string& path,const void* ptr,size_t bytes,bool append=false) {
#ifdef _WIN32
  int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path.c_str(),-1,nullptr,0);
  checked(n>0,"BAD_INPUT: UTF8 path"); std::vector<wchar_t> wide(n);
  MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,path.c_str(),-1,wide.data(),n);
  FILE* f=_wfopen(wide.data(),append?L"ab":L"wb");
#else
  FILE* f=std::fopen(path.c_str(),append?"ab":"wb");
#endif
  checked(f!=nullptr,"BAD_INPUT: evidence file open");
  size_t written=std::fwrite(ptr,1,bytes,f); int status=std::fclose(f);
  checked(written==bytes&&status==0,"BAD_INPUT: evidence file write");
}
struct Bridge {
  void* library=nullptr; void* handle=nullptr;
  std::string actual_identity_json;
  decltype(&kv260_gemm_open_v2b) open=nullptr;
  decltype(&kv260_gemm_run_v2b) run=nullptr;
  decltype(&kv260_gemm_info_v2b) info=nullptr;
  decltype(&kv260_gemm_close_v2b) close=nullptr;
  Bridge() {
#ifdef _WIN32
    throw std::runtime_error("NOT_READY: PL bridge requires ARM Linux C backend");
#else
    auto path=env("KV260_GEMM_BRIDGE"); checked(!path.empty(),"NOT_READY: missing C bridge");
    library=dlopen(path.c_str(),RTLD_NOW|RTLD_LOCAL); checked(library!=nullptr,"NOT_READY: dlopen C bridge");
    open=reinterpret_cast<decltype(open)>(dlsym(library,"kv260_gemm_open_v2b"));
    run=reinterpret_cast<decltype(run)>(dlsym(library,"kv260_gemm_run_v2b"));
    info=reinterpret_cast<decltype(info)>(dlsym(library,"kv260_gemm_info_v2b"));
    close=reinterpret_cast<decltype(close)>(dlsym(library,"kv260_gemm_close_v2b"));
    checked(open&&run&&info&&close,"BAD_IDENTITY: C bridge ABI exports missing");
    char identity[8192]{};
    checked(open(env("KV260_GEMM_CONFIG").c_str(),&handle,identity,sizeof(identity))==0,"NOT_READY: C open failed");
    checked(handle!=nullptr,"NOT_READY: C open returned no handle");
    actual_identity_json=bounded_text(identity,sizeof(identity));
    checked(!actual_identity_json.empty(),"BAD_IDENTITY: missing actual C owner identity");
#endif
  }
  ~Bridge() { if(handle&&close) close(handle); /* No driver/owner unload. */ }
};
std::unique_ptr<Bridge> bridge;

struct Kernel {
  int64_t M,K,N,index; std::string id,whash;
  explicit Kernel(const OrtKernelInfo* info) {
    Ort::ConstKernelInfo i(info);
    M=i.GetAttribute<int64_t>("M"); K=i.GetAttribute<int64_t>("K"); N=i.GetAttribute<int64_t>("N");
    index=i.GetAttribute<int64_t>("layer_index");
    id=i.GetAttribute<std::string>("layer_id"); whash=i.GetAttribute<std::string>("W_sha256");
    checked(i.GetAttribute<std::string>("quant_protocol_id")=="B_W8A8_ROW_DYNAMIC_V1","BAD_IDENTITY: quant protocol");
    bool valid=(M==1&&K==192&&N==1000)||(M==197&&K==192&&(N==192||N==576||N==768))||(M==197&&K==768&&N==192);
    checked(valid&&index>=0&&index<49,"BAD_SHAPE: outside full-model whitelist");
    checked(whash.size()==64,"BAD_IDENTITY: invalid W SHA");
  }
  OrtStatusPtr ComputeV2(OrtKernelContext* raw) noexcept {
    try {
      kv260_runtime_v3::LayerTiming timing(kv260_runtime_v3::stamp());
      std::lock_guard<std::mutex> lock(mu);
      checked(std::fegetround()==FE_TONEAREST,"BAD_INPUT: rounding mode is not nearest even");
      Ort::KernelContext context(raw);
      auto xv=context.GetInput(0),wv=context.GetInput(1),sv=context.GetInput(2),bv=context.GetInput(3);
      auto shape=xv.GetTensorTypeAndShapeInfo().GetShape();
      checked((shape==std::vector<int64_t>{1,M,K}&&M==197)||(shape==std::vector<int64_t>{1,K}&&M==1),"BAD_SHAPE: activation shape");
      checked(wv.GetTensorTypeAndShapeInfo().GetShape()==std::vector<int64_t>{K,N},"BAD_SHAPE: W transpose");
      checked(sv.GetTensorTypeAndShapeInfo().GetShape()==std::vector<int64_t>{N}&&bv.GetTensorTypeAndShapeInfo().GetShape()==std::vector<int64_t>{N},"BAD_SHAPE: scale/bias axes");
      const float* x=xv.GetTensorData<float>(); const int8_t* w=wv.GetTensorData<int8_t>();
      const float* sw=sv.GetTensorData<float>(); const float* bias=bv.GetTensorData<float>();
      checked(digest(w,K*N)==whash,"BAD_IDENTITY: W SHA mismatch");
      for(int64_t n=0;n<N;n++) checked(std::isfinite(sw[n])&&sw[n]>0&&std::isfinite(bias[n]),"BAD_INPUT: scale/bias finite-positive");
      std::vector<int8_t> xq(M*K); std::vector<float> sx(M); std::vector<int32_t> y(M*N,0);
      timing.mark(kv260_runtime_v3::LayerTiming::Input,kv260_runtime_v3::stamp());
      auto begin=std::chrono::steady_clock::now();
      for(int64_t m=0;m<M;m++) {
        float a=0;
        for(int64_t k=0;k<K;k++) { float v=x[m*K+k]; checked(std::isfinite(v),"BAD_INPUT: nonfinite activation"); a=std::max(a,std::fabs(v)); }
        sx[m]=a==0?1.0f:std::max(a/127.0f,std::numeric_limits<float>::min());
        for(int64_t k=0;k<K;k++) { float v=std::nearbyintf(x[m*K+k]/sx[m]); xq[m*K+k]=static_cast<int8_t>(std::max(-127.0f,std::min(127.0f,v))); }
      }
      auto quant_end=std::chrono::steady_clock::now();
      timing.mark(kv260_runtime_v3::LayerTiming::Quantize,kv260_runtime_v3::stamp());
      std::string backend=env("KV260_B_BACKEND","cpu_reference"); kv260_b_receipt_v2b receipt{};
      std::string xhash,yhash;
      std::string run=env("KV260_B_RUN_ID"), image=env("KV260_B_IMAGE_ID");
      std::string request_id=run+"_"+image+"_l"+std::to_string(index);
      if(backend=="pl_v2b") {
        // G1 source candidate has no production binding/ready. ABI3 declarations
        // below only freeze the future C call boundary; they do not grant PL use.
        throw std::runtime_error("NOT_READY: V3 G1 has no qualified ARM bridge/owner binding");
        if(!bridge) bridge=std::make_unique<Bridge>();
        kv260_b_request_v2b request{};
        request.abi_version=KV260_B_GEMM_ABI_V2B; request.struct_bytes=sizeof(request);
        request.M=M; request.K=K; request.N=N; request.layer_index=index;
        checked(!run.empty()&&!image.empty(),"BAD_INPUT: missing model/image identity");
        text_copy(request.model_run_id,sizeof(request.model_run_id),run);
        text_copy(request.image_id,sizeof(request.image_id),image);
        text_copy(request.layer_id,sizeof(request.layer_id),id);
        text_copy(request.request_id,sizeof(request.request_id),request_id);
        text_copy(request.expected_W_sha256,sizeof(request.expected_W_sha256),whash);
        receipt.abi_version=KV260_B_GEMM_ABI_V2B; receipt.struct_bytes=sizeof(receipt);
        timing.mark(kv260_runtime_v3::LayerTiming::Prepare,kv260_runtime_v3::stamp());
        int error=bridge->run(bridge->handle,&request,xq.data(),xq.size(),w,K*N,y.data(),y.size()*sizeof(int32_t),&receipt);
        timing.mark(kv260_runtime_v3::LayerTiming::Compute,kv260_runtime_v3::stamp());
        checked(error==0&&receipt.status==0,"OWNER_FAULT: C run failed; no automatic retry/fallback");
        checked(receipt.abi_version==3&&receipt.struct_bytes==sizeof(receipt),"BAD_IDENTITY: C receipt ABI");
        checked(receipt.one_start==1&&receipt.done==1&&receipt.kernel_error==0&&receipt.guards_ok==1&&receipt.inputs_unchanged==1&&receipt.starts_after==receipt.starts_before+1,"OWNER_FAULT: invalid start/completion evidence");
        checked(receipt.tag0==0xf04207fb&&receipt.tag1==0xe4ebd6cd,"BAD_IDENTITY: hardware tags");
        xhash=digest(xq.data(),xq.size());yhash=digest(y.data(),y.size()*4);
        checked(bounded_text(receipt.X_sha256,sizeof(receipt.X_sha256))==xhash&&bounded_text(receipt.W_sha256,sizeof(receipt.W_sha256))==whash&&bounded_text(receipt.Y_sha256,sizeof(receipt.Y_sha256))==yhash,"BAD_IDENTITY: C payload SHA");
        checked(receipt.owner_pid>0&&std::isfinite(receipt.native_us)&&receipt.native_us>=0,"BAD_IDENTITY: owner/timing receipt");
        checked(!bounded_text(receipt.native_result_file,sizeof(receipt.native_result_file)).empty(),"BAD_IDENTITY: native evidence path missing");
        ++starts;
      } else {
        checked(backend=="cpu_reference"||backend=="cpu_fault_test","BAD_INPUT: unknown backend");
        timing.mark(kv260_runtime_v3::LayerTiming::Prepare,kv260_runtime_v3::stamp());
        // Exact integer implementation, separate from float semantic graph and INT64 Golden.
        for(int64_t m=0;m<M;m++) for(int64_t k=0;k<K;k++) {
          int32_t q=xq[m*K+k]; const int8_t* wk=w+k*N; int32_t* ym=y.data()+m*N;
          for(int64_t n=0;n<N;n++) ym[n]+=q*static_cast<int32_t>(wk[n]);
        }
        if(backend=="cpu_fault_test"&&index==48) y[0]+=65536;
        timing.mark(kv260_runtime_v3::LayerTiming::Compute,kv260_runtime_v3::stamp());
        xhash=digest(xq.data(),xq.size());yhash=digest(y.data(),y.size()*4);
      }
      timing.mark(kv260_runtime_v3::LayerTiming::Receipt,kv260_runtime_v3::stamp());
      auto gemm_end=std::chrono::steady_clock::now();
      auto out_shape=shape; out_shape.back()=N;
      auto output=context.GetOutput(0,out_shape); float* result=output.GetTensorMutableData<float>();
      for(int64_t m=0;m<M;m++) for(int64_t n=0;n<N;n++) {
        float t1=static_cast<float>(y[m*N+n])*sx[m]; float t2=t1*sw[n]; result[m*N+n]=t2+bias[n];
      }
      auto end=std::chrono::steady_clock::now();
      ++calls;
      timing.mark(kv260_runtime_v3::LayerTiming::Dequantize,kv260_runtime_v3::stamp());
      auto trace=env("KV260_B_TRACE_FILE");
      if(!trace.empty()) {
        std::ostringstream f;
        auto us=[](auto a,auto b){return std::chrono::duration<double,std::micro>(b-a).count();};
        f<<std::setprecision(17)<<"{\"schema\":\"b_layer_trace_v3_g1\",\"layer_id\":\""<<id<<"\",\"layer_index\":"<<index
         <<",\"M\":"<<M<<",\"K\":"<<K<<",\"N\":"<<N<<",\"backend\":\""<<backend<<"\",\"calls\":"<<calls<<",\"starts_delta\":"<<(backend=="pl_v2b"?1:0)
         <<",\"fallback_count\":0,\"X_sha256\":\""<<xhash<<"\",\"W_sha256\":\""<<whash<<"\",\"Y_sha256\":\""<<yhash
         <<"\",\"quantize_us\":"<<us(begin,quant_end)<<",\"gemm_api_us\":"<<us(quant_end,gemm_end)<<",\"dequantize_us\":"<<us(gemm_end,end)
         <<",\"owner_pid\":"<<receipt.owner_pid<<",\"starts_before\":"<<receipt.starts_before<<",\"starts_after\":"<<receipt.starts_after<<",\"native_us\":"<<receipt.native_us
         <<",\"model_run_id\":"<<json_quote(run)<<",\"image_id\":"<<json_quote(image)<<",\"request_id\":"<<json_quote(request_id)
         <<",\"one_start\":"<<receipt.one_start<<",\"done\":"<<receipt.done<<",\"kernel_error\":"<<receipt.kernel_error
         <<",\"guards_ok\":"<<receipt.guards_ok<<",\"inputs_unchanged\":"<<receipt.inputs_unchanged
         <<",\"actual_tags\":";
        if(backend=="pl_v2b") f<<'['<<receipt.tag0<<','<<receipt.tag1<<']'; else f<<"null";
        f<<",\"actual_identity_json\":"<<(backend=="pl_v2b"?json_quote(bridge->actual_identity_json):"null")
         <<",\"native_result_file\":"<<(backend=="pl_v2b"?json_quote(bounded_text(receipt.native_result_file,sizeof(receipt.native_result_file))):"null")<<"}\n";
        auto row=f.str(); write_payload(trace,row.data(),row.size(),true);
      }
      timing.mark(kv260_runtime_v3::LayerTiming::Trace,kv260_runtime_v3::stamp());
      auto dump=env("KV260_B_DUMP_DIR");
      if(!dump.empty()) {
        auto save=[&](const char* suffix,const void* ptr,size_t bytes) { write_payload(dump+"/"+std::to_string(index)+suffix,ptr,bytes); };
        save("_Xq.bin",xq.data(),xq.size()); save("_sx.bin",sx.data(),sx.size()*4); save("_Y.bin",y.data(),y.size()*4);
      }
      timing.mark(kv260_runtime_v3::LayerTiming::Dump,kv260_runtime_v3::stamp());
      auto timing_file=env("KV260_B_TIMING_FILE");
      if(!timing_file.empty()) {
        auto total=timing.total();const auto& values=timing.values();
        const char* phases[]={"B_input","B_quantize","B_prepare",backend=="pl_v2b"?"C_ABI":"B_CPU_REFERENCE",
            "B_receipt","B_dequantize","B_trace_export","B_dump_export"};
        std::ostringstream row;row<<"{\"schema\":\"b_layer_timing_v3_g1\",\"layer_index\":"<<index
            <<",\"request_id\":"<<json_quote(request_id)<<",\"backend\":"<<json_quote(backend)
            <<",\"pre_terminal_export_wall_ns\":"<<total.wall_ns<<",\"thread_cpu_ns\":"<<total.thread_cpu_ns
            <<",\"terminal_export_cost\":\"ENCLOSING_SESSION_RUN\",\"phases\":{";
        for(unsigned p=0;p<kv260_runtime_v3::LayerTiming::Count;++p) {
            if(p)row<<',';
            row<<json_quote(phases[p])<<":{\"wall_ns\":"<<values[p].wall_ns<<",\"thread_cpu_ns\":"<<values[p].thread_cpu_ns<<'}';
        }
        row<<"}}\n";auto text=row.str();write_payload(timing_file,text.data(),text.size(),true);
      }
      return nullptr;
    } catch(const std::exception& e) { return Ort::GetApi().CreateStatus(ORT_FAIL,e.what()); }
  }
};
struct CustomOp: Ort::CustomOpBase<CustomOp,Kernel,true> {
  OrtStatusPtr CreateKernelV2(const OrtApi&,const OrtKernelInfo* info,void** out) const noexcept {
    try { *out=new Kernel(info); return nullptr; } catch(const std::exception& e){return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
  }
  const char* GetName() const {return "QuantLinear";}
  const char* GetExecutionProviderType() const {return "CPUExecutionProvider";}
  size_t GetInputTypeCount() const {return 4;}
  ONNXTensorElementDataType GetInputType(size_t i) const {return i==1?ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8:ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
  size_t GetOutputTypeCount() const {return 1;}
  ONNXTensorElementDataType GetOutputType(size_t) const {return ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;}
};
CustomOp op;
std::vector<std::unique_ptr<Ort::CustomOpDomain>> domains;
}
extern "C" {
#ifdef _WIN32
__declspec(dllexport)
#else
__attribute__((visibility("default")))
#endif
OrtStatus* ORT_API_CALL RegisterCustomOps(OrtSessionOptions* options,const OrtApiBase* base) {
  Ort::InitApi(base->GetApi(ORT_API_VERSION));
  try {
    auto domain=std::make_unique<Ort::CustomOpDomain>("com.kv260"); domain->Add(&op);
    Ort::UnownedSessionOptions so(options); so.Add(*domain); domains.push_back(std::move(domain)); return nullptr;
  } catch(const std::exception& e){return Ort::GetApi().CreateStatus(ORT_FAIL,e.what());}
}
}
