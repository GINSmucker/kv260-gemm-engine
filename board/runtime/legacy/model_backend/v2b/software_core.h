// Mathematical software adapter for the actual owner/bridge integration tests.
// This translation unit contains no XRT, MMIO, device memory or hardware start.
#pragma once
#if !defined(KV260_V2B_SOFTWARE_TEST)
#error software_core must never be linked into a production owner
#endif
#include "protocol/stream_v2b_storage.h"
#include "../sha256_crypto.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <thread>
class KernelCore {
    using Json=kv260_stream_v2b::Json;using Bytes=kv260_stream_v2b::Bytes;
    std::filesystem::path root_;std::uint32_t tag0_,tag1_;std::uint64_t starts_=0;
    bool active_=false,done_=false;Json last_=Json::object();
public:
    bool faulted=false;
    KernelCore(const std::string&,const std::string& root,std::uint32_t a,std::uint32_t b):root_(root),tag0_(a),tag1_(b){}
    bool safe() const{return !active_;}
    Json info() const{
        return {{"pid",::getpid()},{"uuid","763f3efb-0df3-6ae4-1cc4-974db7b092ca"},{"tag0",tag0_},{"tag1",tag1_},
            {"starts",starts_},{"active",active_},{"done_latched",done_},{"safe",safe()},{"faulted",faulted},
            {"bo",Json::array({{{"address",0x10000000u},{"bytes",151552u},{"high_address",false}},
                {{"address",0x11000000u},{"bytes",192512u},{"high_address",false}},
                {{"address",0x12000000u},{"bytes",606208u},{"high_address",false}}})},{"last",last_}};
    }
    std::pair<Bytes,Json> execute(const Json& r,const Bytes& x,const Bytes& w,
            const std::function<void()>& before,const std::function<void(std::uint64_t)>& after){
        namespace V=kv260_stream_v2b;
        const auto m=V::uint_value(r.at("M"),UINT32_MAX),k=V::uint_value(r.at("K"),UINT32_MAX),n=V::uint_value(r.at("N"),UINT32_MAX);
        const auto size=V::matrix_bytes(m,k,n);V::require(safe() && !faulted && x.size()==size.x && w.size()==size.w,"software complete shape/input");
        V::require(kv260_crypto_sha256(x.data(),x.size())==r.at("X_sha256") && kv260_crypto_sha256(w.data(),w.size())==r.at("W_sha256"),"software input SHA");
        Json control={{"mode","none"},{"delay_ms",0}};
        const auto path=root_/"software_control.json";if(std::filesystem::exists(path)){
            std::ifstream f(path);f>>control;V::exact_keys(control,{"mode","delay_ms"});
        }
        const auto mode=V::ascii_string(control.at("mode"),1,32);const auto delay=V::uint_value(control.at("delay_ms"),2000);
        V::require(mode=="none" || mode=="throw_before" || mode=="bad_y_digest" || mode=="throw_intent","explicit software fault mode");
        if(mode=="throw_before")throw std::runtime_error("software before-start injected failure");
        const auto began=std::chrono::steady_clock::now();before();
        if(mode=="throw_intent")throw std::runtime_error("software uncertain start-intent failure");
        active_=true;done_=false;++starts_;after(starts_);
        if(std::filesystem::exists(path)){
            std::ofstream f(root_/"software_started.json");f<<Json{{"starts",starts_},{"request_id",r.at("request_id")}}.dump();f.close();
        }
        if(delay)std::this_thread::sleep_for(std::chrono::milliseconds(delay));
        Bytes y(size.y);for(std::size_t row=0;row<m;++row)for(std::size_t col=0;col<n;++col){
            std::int64_t sum=0;for(std::size_t kk=0;kk<k;++kk)
                sum+=static_cast<std::int8_t>(x[row*k+kk])*static_cast<std::int8_t>(w[kk*n+col]);
            V::require(sum>=INT32_MIN && sum<=INT32_MAX,"software INT32 range");const auto narrow=static_cast<std::int32_t>(sum);
            std::memcpy(y.data()+4*(row*n+col),&narrow,4);
        }
        active_=false;done_=true;const auto us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-began).count();
        const auto yh=kv260_crypto_sha256(y.data(),y.size());
        Json traffic=Json::object();for(unsigned off=0x10;off<=0x3c;off+=4)traffic[std::to_string(off)]=0;
        // CPU test observations follow the frozen OPT35a traffic contract.
        // These values are fixtures, never hardware observations.
        traffic["32"]=m*(k/32)*((n+63)/64);traffic["36"]=8*m*(k/32)*((n+63)/64);
        traffic["40"]=k*n/64;traffic["44"]=16*k*n/64;
        traffic["48"]=m*((n+15)/16);traffic["52"]=m*n;traffic["56"]=m*((n+15)/16);traffic["60"]=1;
        last_={{"status","PASS"},{"request_id",r.at("request_id")},{"M",m},{"K",k},{"N",n},{"starts",starts_},
            {"one_start",true},{"done_latched",true},{"ctrl_after",4},{"kernel_error",0},{"traffic",traffic},
            {"inputs_unchanged",true},{"guards_pass",true},{"Y_sha256",mode=="bad_y_digest"?std::string(64,'0'):yh},{"Y_bytes",size.y},
            {"copy_us",0.0},{"sync_in_us",0.0},{"start_to_idle_and_drain_us",us},{"sync_out_us",0.0},{"native_us",us},
            {"kernel_cycles","UNAVAILABLE"},{"tag0",tag0_},{"tag1",tag1_}};
        return {std::move(y),last_};
    }
};
