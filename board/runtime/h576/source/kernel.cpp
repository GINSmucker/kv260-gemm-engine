// Fixed OPT45 platform; immutable PL weights are synchronized at owner creation.
#include "kernel.h"
#include "route.h"
#include "platform_id.h"
#include <xrt/xrt_device.h>
#include <xrt/xrt_bo.h>
#include <experimental/xrt_xclbin.h>
#include <experimental/xrt_ip.h>
#include <chrono>
#include <cstring>
#include <iostream>
#include <thread>

namespace kv5 {
namespace {
using Clock=std::chrono::steady_clock;
constexpr std::array<size_t,3> sizes={151552,3981312,606208};
constexpr std::array<uint32_t,3> pointers={0x18,0x24,0x30};
void metadata_only(const xrt::xclbin& binary) {
    const axlf* h=binary.get_axlf();
    require(h && h->m_header.m_numSections==6,"metadata-only xclbin required");
    unsigned seen=0;
    for(unsigned i=0;i<h->m_header.m_numSections;++i) {
        unsigned flag=0;
        switch(h->m_sections[i].m_sectionKind) {
        case EMBEDDED_METADATA: flag=1; break;
        case IP_LAYOUT: flag=2; break;
        case MEM_TOPOLOGY: flag=4; break;
        case CONNECTIVITY: flag=8; break;
        case ASK_GROUP_TOPOLOGY: flag=16; break;
        case ASK_GROUP_CONNECTIVITY: flag=32; break;
        default: throw std::runtime_error("unexpected programming section");
        }
        require(!(seen&flag),"duplicate metadata section"); seen|=flag;
    }
    require(seen==63 && binary.get_uuid().to_string()==EXPECTED_UUID,"paired platform metadata");
}
class Native final : public Kernel {
    xrt::device device{0};
    std::unique_ptr<xrt::ip> ip, observer;
    std::array<std::unique_ptr<xrt::bo>,3> bo;
    std::array<unsigned char*,3> map{};
    std::array<uint64_t,49> weight_address{};
    bool active=false, done=false;
    bool drained() {
        return observer->read_register(0x14)==0 && observer->read_register(0x18)==0 &&
            observer->read_register(0x1c)==0 && observer->read_register(0x3c)==1;
    }
    bool completed() {
        uint32_t ctrl=ip->read_register(0);
        done=done || bool(ctrl&2);
        return done && (ctrl&4) && !(ctrl&0x81) && drained();
    }
    // Called after any ambiguous START/observer error. Never free an active BO.
    void retain_until_complete() noexcept {
        while(active) {
            try { if(completed()) { active=false; break; } } catch(...) {}
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }
public:
    explicit Native(const char* file,const std::vector<int8_t>& weights) {
        xrt::xclbin binary(file); metadata_only(binary);
        auto uuid=device.load_xclbin(binary);
        ip=std::make_unique<xrt::ip>(device,uuid,"model_gemm_top:{gemm_1}");
        observer=std::make_unique<xrt::ip>(device,uuid,"axi_observer:{status_1}");
        require(observer->read_register(0)==0x46383031 && observer->read_register(4)==0x10000 &&
            observer->read_register(8)==3652287023u && observer->read_register(12)==1996644805u,"platform fingerprint");
        uint32_t ctrl=ip->read_register(0);
        require((ctrl&4) && !(ctrl&0x81) && drained(),"CU initially busy");
        ip->write_register(4,0); ip->write_register(8,0);
        for(unsigned i=0;i<3;++i) {
            bo[i]=std::make_unique<xrt::bo>(device,sizes[i],xrt::bo::flags::cacheable,0);
            map[i]=bo[i]->map<unsigned char*>();
            uint64_t address=bo[i]->address();
            require(address%64==0 && ((address>0 && address<0x80000000ULL-sizes[i]) ||
                (address>=0x800000000ULL && address<0x880000000ULL-sizes[i])),"DDR BO range");
            ip->write_register(pointers[i],uint32_t(address));
            ip->write_register(pointers[i]+4,uint32_t(address>>32));
        }
        size_t source=0,destination=0;
        for(unsigned layer=0;layer<49;++layer) {
            auto bytes=shape(layer).w_bytes();
            require(source+bytes<=weights.size(),"fixed weight source range");
            if(offloaded(layer)) {
                require(destination%64==0 && destination+bytes<=sizes[1],"fixed weight BO range");
                std::memcpy(map[1]+destination,weights.data()+source,bytes);
                weight_address[layer]=bo[1]->address()+destination;
                destination+=bytes;
            }
            source+=bytes;
        }
        require(source==weights.size() && destination==sizes[1],"fixed weight layout");
        bo[1]->sync(XCL_BO_SYNC_BO_TO_DEVICE,sizes[1],0);
    }
    std::array<int,2> share(SharedInfo& info) override {
        for(unsigned i=0;i<2;++i) {
            auto& buffer=*bo[i?2:0];info.size[i]=buffer.size();info.physical[i]=buffer.address();info.flags[i]=uint32_t(buffer.get_flags());
        }
        return {bo[0]->export_buffer(),bo[2]->export_buffer()};
    }
    ~Native() override { retain_until_complete(); }
    void execute(unsigned layer) override {
        const auto s=shape(layer);
#ifdef KV5_DIAGNOSTIC
        auto tick=Clock::now();
        auto mark=[&](unsigned i) { auto now=Clock::now(); timing[i]=std::chrono::duration_cast<std::chrono::nanoseconds>(now-tick).count(); tick=now; };
#endif
#ifdef KV5_DIAGNOSTIC
        timing[0]=timing[1]=0;
#endif
        const uint64_t address=weight_address[layer];
        ip->write_register(0x24,uint32_t(address));
        ip->write_register(0x28,uint32_t(address>>32));
        // Previous execution completed before returning; this single owner is
        // the only writer of shape/address/START registers.
        observer->write_register(0x40,0x434c5245);
        ip->write_register(0x3c,s.m); ip->write_register(0x44,s.k); ip->write_register(0x4c,s.n);
        active=true; done=false;
#ifdef KV5_DIAGNOSTIC
        mark(2);
#endif
        try {
            ip->write_register(0,1);
            auto deadline=Clock::now()+std::chrono::seconds(120);
            while(!completed()) {
                if(Clock::now()>deadline) throw std::runtime_error("kernel deadline; retaining DMA until completion");
                std::this_thread::sleep_for(std::chrono::microseconds(50));
            }
            active=false;
#ifdef KV5_DIAGNOSTIC
            mark(3);
#endif
            require(ip->read_register(0x10)==0 && observer->read_register(0x10)==0,"kernel/AXI error");
#ifdef KV5_DIAGNOSTIC
            mark(4);
#endif
#ifdef KV5_DIAGNOSTIC
            mark(5);
#endif
        } catch(...) { retain_until_complete(); throw; }
    }
};
}
std::unique_ptr<Kernel> make_kernel(const char* metadata,const std::vector<int8_t>& weights) { return std::make_unique<Native>(metadata,weights); }
}
