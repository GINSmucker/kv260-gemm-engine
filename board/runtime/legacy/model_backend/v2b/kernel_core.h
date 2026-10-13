// Isolated V2B kernel core derived from the frozen cacheable parent.
// ARM SDK compile/metadata-only passed; instantiation and PL are NOT RUN.
// Full cacheable BO sizes/sync, guard/const, pointers, shape, Done/drain and
// 120-second quarantine are preserved; X/Y transfer files are removed.
#pragma once
// One owner handles hs Done read-clear, BO lifetime, and one full Linear/start.
// A peer disconnect cannot destroy an active BO. No bitstream programming here.
#include <xrt/xrt_device.h>
#include <xrt/xrt_bo.h>
#include <experimental/xrt_xclbin.h>
#include <experimental/xrt_ip.h>
#include "model_gemm_config.hpp"
#include "model_gemm_registers.hpp"
#include "protocol/stream_v2b_storage.h"
#include "../guard_bytes.h"
#include "../y_snapshot.h"
#include "../sha256_crypto.h"
#include <array>
#include <chrono>
#include <csignal>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <thread>
#include <vector>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
using json=nlohmann::json;
namespace fs=std::filesystem;
namespace R=model_gemm_regs;
using Clock=std::chrono::steady_clock;
static void need(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
static double elapsed(Clock::time_point begin){return std::chrono::duration<double,std::micro>(Clock::now()-begin).count();}
static std::string digest(const unsigned char* p,size_t n){return kv260_crypto_sha256(p,n);}
static void metadata_only(const xrt::xclbin& b){
    const axlf* h=b.get_axlf();need(h&&h->m_header.m_numSections==6,"metadata sections mismatch");unsigned seen=0;
    for(unsigned i=0;i<h->m_header.m_numSections;++i){unsigned f=0;
        switch(h->m_sections[i].m_sectionKind){
        case EMBEDDED_METADATA:f=1;break;case IP_LAYOUT:f=2;break;case MEM_TOPOLOGY:f=4;break;
        case CONNECTIVITY:f=8;break;case ASK_GROUP_TOPOLOGY:f=16;break;case ASK_GROUP_CONNECTIVITY:f=32;break;
        default:throw std::runtime_error("bitstream or unexpected metadata section");}
        need(!(seen&f),"duplicate metadata section");seen|=f;
    }need(seen==63,"incomplete metadata");
    for(const char* name:{"model_gemm_top:{gemm_1}","axi_observer:{status_1}"}){
        auto ips=b.get_ips(name);need(ips.size()==1&&ips[0].get_size()>=0x80,"new model IP missing/ambiguous");
    }
}
static_assert(static_cast<uint32_t>(xrt::bo::flags::cacheable)==(1u<<24),"installed cacheable flag ABI mismatch");
static constexpr std::array<size_t,3> BO_SIZE={
    ((model_gemm::MAX_X+64+4095)/4096)*4096,
    ((model_gemm::MAX_W+64+4095)/4096)*4096,
    ((model_gemm::MAX_Y*4+64+4095)/4096)*4096};

class KernelCore {
    xrt::device device{0};xrt::uuid uuid;
    std::unique_ptr<xrt::ip> ip,observer;
    std::array<std::unique_ptr<xrt::bo>,3> bo;
    std::array<unsigned char*,3> map{};
    fs::path root;uint32_t tag0,tag1;
    bool active=false,done_latched=false;
    uint64_t starts=0;
    json last=json::object();
    bool drain(){return observer->read_register(0x14)==0&&observer->read_register(0x18)==0&&
        observer->read_register(0x1c)==0&&observer->read_register(0x3c)==1;}
    json traffic(){json v=json::object();for(uint32_t off=0x10;off<=0x3c;off+=4)v[std::to_string(off)]=observer->read_register(off);return v;}
public:
    bool faulted=false;
    KernelCore(const std::string& file,const fs::path& task_root,uint32_t a,uint32_t b):root(fs::canonical(task_root)),tag0(a),tag1(b){
        xrt::xclbin binary(file);metadata_only(binary);uuid=device.load_xclbin(binary);
        ip=std::make_unique<xrt::ip>(device,uuid,"model_gemm_top:{gemm_1}");
        observer=std::make_unique<xrt::ip>(device,uuid,"axi_observer:{status_1}");
        json identity={{"magic",observer->read_register(0)},{"abi",observer->read_register(4)},
            {"tag0",observer->read_register(8)},{"tag1",observer->read_register(12)},{"ctrl",ip->read_register(R::CTRL)}};
        std::cout<<"MODEL_STARTUP "<<identity.dump()<<std::endl;
        need(identity["magic"]==0x46383031u&&identity["abi"]==0x10000u,"observer ABI mismatch");
        need(identity["tag0"]==tag0&&identity["tag1"]==tag1,"model platform fingerprint mismatch");
        need(safe(),"new CU not initially idle/drained");
        ip->write_register(R::GIE,0);ip->write_register(R::IER,0);
        for(size_t i=0;i<3;++i){
            bo[i]=std::make_unique<xrt::bo>(device,BO_SIZE[i],xrt::bo::flags::cacheable,0);map[i]=bo[i]->map<unsigned char*>();
            need(bo[i]->get_flags()==xrt::bo::flags::cacheable,"requested BO flags mismatch");
            uint64_t address=bo[i]->address();
            need(address<=UINT64_MAX-BO_SIZE[i],"BO address overflow");
            bool low=address>0&&address+BO_SIZE[i]<=0x80000000ULL;
            bool high=address>=0x800000000ULL&&address+BO_SIZE[i]<=0x880000000ULL;
            need((low||high)&&address%64==0,"BO outside paired DDR/alignment");
            for(size_t j=0;j<i;++j)need(address+BO_SIZE[i]<=bo[j]->address()||bo[j]->address()+BO_SIZE[j]<=address,"BO alias");
        }
    }
    bool safe() noexcept{
        if(active)return false;
        try{uint32_t c=ip->read_register(R::CTRL);return (c&4)&&!(c&0x81)&&drain()&&observer->read_register(0x10)==0;}
        catch(...){return false;}
    }
    json info(){json addresses=json::array();for(size_t i=0;i<3;++i)addresses.push_back({{"address",bo[i]->address()},{"bytes",BO_SIZE[i]},
        {"high_address",bo[i]->address()>UINT32_MAX}});
        return {{"pid",getpid()},{"uuid",uuid.to_string()},{"tag0",tag0},{"tag1",tag1},{"starts",starts},
            {"active",active},{"done_latched",done_latched},{"safe",safe()},{"faulted",faulted},{"bo",addresses},{"last",last}};
    }
    std::pair<kv260_stream_v2b::Bytes,json> execute(const json& req,
            const kv260_stream_v2b::Bytes& x,const kv260_stream_v2b::Bytes& w,
            const std::function<void()>& before_start,const std::function<void(uint64_t)>& after_start){
        need(!faulted&&safe(),"owner not available");
        need(req.at("mode")=="dense_w8a8","request mode mismatch");
        need(req.at("layout")=="compact_row_major"&&req.at("dtype_x")=="int8"&&req.at("dtype_w")=="int8"&&req.at("dtype_y")=="int32","layout/dtype mismatch");
        for(const char* dim:{"M","K","N"})
            need(req.at(dim).is_number_unsigned()&&req.at(dim).get<uint64_t>()<=UINT32_MAX,"shape scalar out of uint32 range");
        uint32_t m=req.at("M").get<uint32_t>(),k=req.at("K").get<uint32_t>(),n=req.at("N").get<uint32_t>();
        need(model_gemm::valid_shape(m,k,n),"unsupported shape");
        size_t nx=size_t(m)*k,nw=size_t(k)*n,ny=size_t(m)*n*4;
        need(x.size()==nx&&w.size()==nw,"stream complete input lengths");
        need(digest(x.data(),nx)==req.at("X_sha256")&&digest(w.data(),nw)==req.at("W_sha256"),"stream full input SHA256 mismatch");
        need(bool(before_start)&&bool(after_start),"serial start observation adapters required");
        auto begin=Clock::now();
        for(size_t i=0;i<3;++i)std::memset(map[i],0x5a,BO_SIZE[i]);
        std::memcpy(map[0],x.data(),nx);std::memcpy(map[1],w.data(),nw);std::memset(map[2],0xa5,ny);
        double copy_us=elapsed(begin);auto t=Clock::now();
        for(size_t i=0;i<3;++i)bo[i]->sync(XCL_BO_SYNC_BO_TO_DEVICE,BO_SIZE[i],0);
        double sync_in_us=elapsed(t);need(safe(),"CU became busy before MMIO");
        observer->write_register(0x40,0x434c5245);need(observer->read_register(0x10)==0,"counter clear failed");
        for(size_t i=0;i<3;++i){uint32_t lo[]={R::X_LOW,R::W_LOW,R::Y_LOW},hi[]={R::X_HIGH,R::W_HIGH,R::Y_HIGH};
            uint64_t a=bo[i]->address();ip->write_register(lo[i],uint32_t(a));ip->write_register(hi[i],uint32_t(a>>32));
            need(ip->read_register(lo[i])==uint32_t(a)&&ip->read_register(hi[i])==uint32_t(a>>32),"64-bit pointer readback mismatch");}
        ip->write_register(R::M,m);ip->write_register(R::K,k);ip->write_register(R::N,n);
        need(ip->read_register(R::M)==m&&ip->read_register(R::K)==k&&ip->read_register(R::N)==n,"shape readback mismatch");
        need(ip->read_register(R::GIE)==0&&ip->read_register(R::IER)==0,"IRQ unexpectedly enabled");
        before_start();
        active=true;done_latched=false;++starts;t=Clock::now();
        // Ownership is set before the potentially ambiguous write transaction.
        ip->write_register(R::CTRL,1);
        after_start(starts);
        uint32_t ctrl=0;auto deadline=t+std::chrono::seconds(120);
        while(true){
            ctrl=ip->read_register(R::CTRL);done_latched=done_latched||bool(ctrl&2);
            if(done_latched&&(ctrl&4)&&!(ctrl&0x81)&&drain())break;
            if(Clock::now()>deadline){faulted=true;std::cerr<<"MODEL_QUARANTINE active=true starts="<<starts<<std::endl;
                // Retain all BOs and continue observing completion; no kill/unload.
                while(true){ctrl=ip->read_register(R::CTRL);done_latched=done_latched||bool(ctrl&2);
                    if(done_latched&&(ctrl&4)&&!(ctrl&0x81)&&drain())break;
                    std::this_thread::sleep_for(std::chrono::milliseconds(100));}
                active=false;throw std::runtime_error("deadline exceeded; late DMA stop observed");}
            std::this_thread::sleep_for(std::chrono::microseconds(50));
        }
        uint32_t err=ip->read_register(R::ERROR);active=false;double wait_us=elapsed(t);
        need(err==0,"kernel returned error");auto tr=traffic();
        need(tr["16"]==0&&tr["60"]==1&&tr["32"].get<uint32_t>()>0&&tr["40"].get<uint32_t>()>0&&
            tr["52"]==uint32_t(size_t(m)*n)&&tr["48"].get<uint32_t>()>0&&tr["56"].get<uint32_t>()==tr["48"].get<uint32_t>(),"AXI traffic/error/drain evidence mismatch");
        t=Clock::now();for(size_t i=0;i<3;++i)bo[i]->sync(XCL_BO_SYNC_BO_FROM_DEVICE,BO_SIZE[i],0);double sync_out_us=elapsed(t);
        size_t valid[]={nx,nw,ny};for(size_t i=0;i<3;++i)need(kv260_guard_bytes(map[i]+valid[i],BO_SIZE[i]-valid[i]),"BO guard modified");
        need(std::memcmp(map[0],x.data(),nx)==0&&std::memcmp(map[1],w.data(),nw)==0,"const input modified");
        need(safe(),"CU not safe after output sync");
        auto y_snapshot=kv260_y_snapshot(map[2],ny);
        last={{"status","PASS"},{"request_id",req.at("request_id")},{"M",m},{"K",k},{"N",n},{"starts",starts},{"one_start",true},
            {"done_latched",done_latched},{"ctrl_after",ctrl},{"kernel_error",err},{"traffic",tr},{"inputs_unchanged",true},{"guards_pass",true},
            {"Y_sha256",digest(y_snapshot.data(),ny)},{"Y_bytes",ny},{"copy_us",copy_us},{"sync_in_us",sync_in_us},{"start_to_idle_and_drain_us",wait_us},
            {"sync_out_us",sync_out_us},{"native_us",elapsed(begin)},{"kernel_cycles","UNAVAILABLE"},{"tag0",tag0},{"tag1",tag1}};
        std::cout<<"MODEL_COMPLETE "<<last.dump()<<std::endl;return {std::move(y_snapshot),last};
    }
};
