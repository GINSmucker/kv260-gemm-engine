// New OPT50 adapter. One user-managed physical CU, four persistent guarded BOs,
// exactly one graph (36 Dense OR 24 Dense + 12 Sparse), SAME logical weights,
// SAME compiled binary/equal BO capacities; no loading/recovery/fallback.
#include "kernel.h"
#include "native_gate.hpp"
#include "performance_trace.hpp"
#if !defined(OPT50_PERFORMANCE_LIGHT) || OPT50_PERFORMANCE_LIGHT != 1
#error This NEW release requires the explicit OPT50_PERFORMANCE_LIGHT=1 build profile
#endif
#include <xrt/xrt_device.h>
#include <xrt/xrt_bo.h>
#include <experimental/xrt_xclbin.h>
#include <experimental/xrt_ip.h>
#include <atomic>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <thread>
#include <unistd.h>

namespace {
using namespace opt50_core;
using Clock=std::chrono::steady_clock;
std::string quoted(const std::string& text){std::ostringstream s;s<<'"';for(unsigned char c:text){if(c=='"'||c=='\\')s<<'\\'<<char(c);else if(c<32)s<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(c)<<std::dec;else s<<char(c);}s<<'"';return s.str();}
class Journal {
    int fd_=-1,root_fd_=-1;unsigned sequence_=0;
public:
    explicit Journal(const std::string& root) {
        opt50_gate::private_dir(root);const auto folder=root+"/native_events";
        need(::mkdir(folder.c_str(),0700)==0,"new native event directory required; never reuse/retry");
        const int parent=::open(root.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);need(parent>=0,"native journal parent open");const bool synced=::fsync(parent)==0;::close(parent);need(synced,"native directory parent fsync");
        fd_=::open(folder.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);need(fd_>=0,"native journal directory open");
        root_fd_=::open(root.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);need(root_fd_>=0,"performance receipt parent open");
    }
    ~Journal(){if(fd_>=0){::close(fd_);}if(root_fd_>=0){::close(root_fd_);}}
    void publish(const std::string& raw) {
        const int out=::openat(root_fd_,"performance_native.json",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        need(out>=0,"new compact performance receipt required; never overwrite");size_t at=0;
        while(at<raw.size()){const auto n=::write(out,raw.data()+at,raw.size()-at);if(n<0&&errno==EINTR)continue;if(n<=0){::close(out);throw std::runtime_error("short compact performance write");}at+=size_t(n);}
        const bool synced=::fsync(out)==0,closed=::close(out)==0;need(synced&&closed&&::fsync(root_fd_)==0,"compact performance file/directory fsync");
    }
    void event(const std::string& phase,const std::string& detail) {
        need(sequence_<100000,"native journal bounded inventory");std::ostringstream name;name<<"event_"<<std::setw(6)<<std::setfill('0')<<++sequence_<<".json";
        const auto raw="{\"schema\":\"opt50_model_native_event_v1\",\"sequence\":"+std::to_string(sequence_)+",\"phase\":"+quoted(phase)+",\"details\":"+quoted(detail)+",\"monotonic_ns\":"+std::to_string(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count())+"}\n";
        const int out=::openat(fd_,name.str().c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);need(out>=0,"exclusive native event");size_t at=0;
        while(at<raw.size()){const auto n=::write(out,raw.data()+at,raw.size()-at);if(n<0&&errno==EINTR)continue;if(n<=0){::close(out);throw std::runtime_error("native event short write");}at+=size_t(n);}
        const bool sync=::fsync(out)==0,closed=::close(out)==0;need(sync&&closed&&::fsync(fd_)==0,"native event file/directory fsync");
    }
};
uint32_t physical_read(Journal& journal,uint64_t address) {
    need(address==0xfd615000ULL||address==0xfd380000ULL||address==0xfd380014ULL||address==0xa0000000ULL||(address>=0xa0010000ULL&&address<=0xa001003cULL&&address%4==0),"raw physical read allowlist");
    journal.event("RAW_MMIO_READ_INTENT",std::to_string(address)+(address==0xa0000000ULL?" AP_CTRL_COR_DONE_READY":""));
    const int fd=::open("/dev/mem",O_RDONLY|O_SYNC|O_NOFOLLOW|O_CLOEXEC);need(fd>=0,"readonly /dev/mem open");struct stat s{};
    if(::fstat(fd,&s)!=0||!S_ISCHR(s.st_mode)||major(s.st_rdev)!=1||minor(s.st_rdev)!=1){::close(fd);throw std::runtime_error("/dev/mem identity");}
    const auto page=::sysconf(_SC_PAGESIZE);if(page<4096||(page&(page-1))){::close(fd);throw std::runtime_error("MMIO page size");}
    const auto base=address&~uint64_t(page-1);void* mapped=::mmap(nullptr,size_t(page),PROT_READ,MAP_SHARED,fd,off_t(base));
    if(mapped==MAP_FAILED){::close(fd);throw std::runtime_error("readonly physical mmap");}
    std::atomic_thread_fence(std::memory_order_seq_cst);const auto value=*reinterpret_cast<volatile uint32_t*>(static_cast<unsigned char*>(mapped)+(address-base));std::atomic_thread_fence(std::memory_order_seq_cst);
    const bool unmapped=::munmap(mapped,size_t(page))==0,closed=::close(fd)==0;need(unmapped&&closed,"physical mapping close");journal.event("RAW_MMIO_READ_RETURNED",std::to_string(address)+" value="+std::to_string(value));return value;
}
void raw_preflight(Journal& j) {
    for(unsigned repeat=0;repeat<2;++repeat){need((physical_read(j,0xfd615000ULL)&0x300)==0x200,"PS HPM0 width");need((physical_read(j,0xfd380000ULL)&0xb)==0&&(physical_read(j,0xfd380014ULL)&0xb)==0,"PS HP0 width/pause");}
    std::array<uint32_t,16> previous{};
    for(unsigned repeat=0;repeat<2;++repeat){std::array<uint32_t,16> sample{};for(size_t i=0;i<16;++i)sample[i]=physical_read(j,0xa0010000ULL+4*i);need(sample[0]==0x46383031&&sample[1]==0x10000&&sample[2]==4254487453u&&sample[3]==1287452466u&&sample[4]==0&&sample[5]==0&&sample[6]==0&&sample[7]==0&&sample[15]==1,"raw observer identity/error/drain");if(repeat)need(sample==previous,"raw observer counters changed");previous=sample;}
    const auto ctrl=physical_read(j,0xa0000000ULL);need((ctrl&4)&&!(ctrl&0x81),"raw GEMM initial idle/start/auto");
    j.event("RAW_PREFLIGHT_PASS","PS fields, observer identity/stability and initial COR control only; no START; no reset/clock write");
}
void metadata_only(const xrt::xclbin& binary,size_t bytes) {
    const auto* h=binary.get_axlf();need(h&&h->m_header.m_numSections==6,"six metadata-only sections");unsigned seen=0;
    for(unsigned i=0;i<6;++i){const auto& s=h->m_sections[i];need(s.m_sectionOffset<=bytes&&s.m_sectionSize<=bytes-s.m_sectionOffset,"xclbin section bounds");unsigned bit=0;switch(s.m_sectionKind){case EMBEDDED_METADATA:bit=1;break;case IP_LAYOUT:bit=2;break;case MEM_TOPOLOGY:bit=4;break;case CONNECTIVITY:bit=8;break;case ASK_GROUP_TOPOLOGY:bit=16;break;case ASK_GROUP_CONNECTIVITY:bit=32;break;default:throw std::runtime_error("programming/unexpected xclbin section");}need(!(seen&bit),"duplicate metadata section");seen|=bit;}
    need(seen==63&&binary.get_uuid().to_string()==uuid,"fixed OPT50 platform UUID");
}
// DETAILS is deliberately not evaluated in the measured graph: no string
// formatting/allocation or disk write per register, observer read or sync.
#define PERF_TRACE(PHASE,DETAILS) do {if(graph_hot_){note(PHASE);}else{event(PHASE,DETAILS);}}while(false)
class Native final:public kv5::Kernel {
    State state_;
    std::unique_ptr<Journal> journal_;
    xrt::device* device_=nullptr;xrt::ip *gemm_=nullptr,*observer_=nullptr;
    std::array<xrt::bo*,4> bo_{};std::array<unsigned char*,4> map_{};std::array<size_t,4> allocated_{};
    int lock_=-1;bool closed_=false,shared_=false;
    Clock::time_point deadline_{};
    const std::array<size_t,4> caps_=payload_capacities();
    opt50_performance::Trace trace_;
    opt50_performance::CheckAudit checks_;
    bool graph_hot_=false,receipt_written_=false;
    std::string active_data_sha_,active_meta_sha_;
    unsigned current_layer_=49;
    void event(const std::string& phase,const std::string& detail){need(bool(journal_),"native journal unavailable");journal_->event(phase,detail);}
    void note(const char* phase){trace_.note(phase,current_layer_,state_.commands,uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count()));}
    void sync(size_t i,bool to){checks_.sync(graph_hot_,allocated_[i],!state_.begun&&to&&(i==1||i==2));PERF_TRACE(to?"BO_TO_DEVICE_INTENT":"BO_FROM_DEVICE_INTENT","slot="+std::to_string(i)+" whole allocation bytes="+std::to_string(allocated_[i]));bo_[i]->sync(to?XCL_BO_SYNC_BO_TO_DEVICE:XCL_BO_SYNC_BO_FROM_DEVICE,allocated_[i],0);PERF_TRACE(to?"BO_TO_DEVICE_RETURNED":"BO_FROM_DEVICE_RETURNED",std::to_string(i));}
    Bytes payload(size_t i)const{return Bytes(map_[i]+guard,map_[i]+guard+caps_[i]);}
    void all_guards(){checks_.guards(graph_hot_,4);for(size_t i=0;i<4;++i)check_guards(map_[i],caps_[i],allocated_[i]);}
    void final_checks(){for(size_t i=0;i<4;++i)sync(i,false);all_guards();checks_.static_hashes(graph_hot_,2);const auto data_sha=sha(payload(1)),meta_sha=sha(payload(2));need(data_sha==active_data_sha_&&meta_sha==active_meta_sha_,"static selected-mode padded Wdata/Wmeta changed");}
    void close_safe() {
        if (closed_) {
            return;
        }
        need(!state_.active, "never free active DMA resources");
        graph_hot_=false; // STOP/close is after the timed Runner.run, never inside END.
        if(gemm_&&observer_)idle(*this);
        // This is outside Runner.run. Do not silently free completed-graph BOs
        // if a fresh final guard/static-weight inspection cannot be proven.
        if(state_.begun){final_checks();}
        const auto count=std::count_if(bo_.begin(),bo_.end(),[](xrt::bo* p){return p!=nullptr;});event("RESOURCE_CLOSE_INTENT","allocated_BO_count="+std::to_string(count)+"; no unload/reset/recovery");
        for(auto& b:bo_){delete b;b=nullptr;}delete gemm_;gemm_=nullptr;delete observer_;observer_=nullptr;delete device_;device_=nullptr;
        closed_=true;event("RESOURCES_CLOSED","released_BO_count="+std::to_string(count)+"; exported client handles must already be closed; resident firmware unchanged");
        if(lock_>=0){::close(lock_);lock_=-1;}
    }
    [[noreturn]] void retain(const std::string& reason) noexcept {
        graph_hot_=false; // Failure-only durable evidence, no successful timing result.
        try{event("RETAINED_UNKNOWN_NATIVE_STATE",reason);}catch(...){}
        std::cerr<<"OPT50_RETAINED "<<reason<<"; keep owner/CU/ALL four BOs/exported mappings/new lock and old guardian; no retry, kill, reset, unload or reboot\n"<<std::flush;
        for(;;)std::this_thread::sleep_for(std::chrono::seconds(30));
    }
public:
    Native(const char* metadata,const std::vector<int8_t>& canonical,const char* root):state_(opt50_gate::checked().execution),trace_(opt50_gate::checked().execution) {
        const auto& gate=opt50_gate::checked();need(metadata&&root&&gate.metadata==metadata&&gate.root==root,"factory arguments differ from validated native job");
        const auto weights=opt50_gate::private_file(gate.weights),data=opt50_gate::private_file(gate.data),meta=opt50_gate::private_file(gate.meta),binary_bytes=opt50_gate::private_file(gate.metadata);
        need(canonical.size()==weights.size()&&std::equal(weights.begin(),weights.end(),canonical.begin(),[](unsigned char a,int8_t b){return a==static_cast<unsigned char>(b);}),"owner canonical weights changed after native gate");
        const auto active=active_data(gate.execution,weights,data,meta);
        validate_active_data(gate.execution,weights,active,meta);
        active_data_sha_=sha(active);active_meta_sha_=sha(meta);
        need(binary_bytes.size()==8560&&sha(binary_bytes)==metadata_sha,"paired metadata SHA");
        lock_=::fcntl(gate.lock_fd,F_DUPFD_CLOEXEC,3);need(lock_>=0,"retain inherited new companion lock description");
        journal_=std::make_unique<Journal>(gate.task_root);event("NATIVE_ENTERED","one graph run_id=1; exactly 36 PL calls; execution_mode="+std::string(execution_name(state_.execution()))+" active_data_sha256="+active_data_sha_+" active_data_capacity_bytes="+std::to_string(fair_data_bytes)+"; SAME canonical weights; no accuracy/steady-state acceptance");
        try {
            need(::sysconf(_SC_PAGESIZE)==4096,"fixed 4096-byte ZOCL shared BO page contract required before MMIO/XRT");
            raw_preflight(*journal_);xrt::xclbin binary(metadata);metadata_only(binary,binary_bytes.size());
            event("XRT_DEVICE_OPEN_INTENT","device=0 after raw observer preflight");device_=new xrt::device(0);
            event("XRT_METADATA_REGISTRATION_INTENT","MUTATION: metadata only, not firmware programming");const auto id=device_->load_xclbin(binary);need(id.to_string()==uuid,"registered UUID");
            event("XRT_OBSERVER_OPEN_INTENT","axi_observer:{status_1}");observer_=new xrt::ip(*device_,id,"axi_observer:{status_1}");
            need(obs(0)==0x46383031&&obs(4)==0x10000&&obs(8)==4254487453u&&obs(12)==1287452466u,"XRT observer fingerprint");
            event("XRT_GEMM_OPEN_INTENT","model_gemm_top:{gemm_1}");gemm_=new xrt::ip(*device_,id,"model_gemm_top:{gemm_1}");idle(*this);write(4,0);write(8,0);need(read(4)==0&&read(8)==0,"interrupt disable readback");
            for(size_t i=0;i<4;++i){allocated_[i]=allocation(caps_[i]);event("BO_ALLOC_INTENT","slot="+std::to_string(i)+" cacheable group0 bytes="+std::to_string(allocated_[i]));bo_[i]=new xrt::bo(*device_,allocated_[i],xrt::bo::flags::cacheable,0);const uint64_t a=bo_[i]->address();const size_t actual=bo_[i]->size();std::ostringstream d;d<<"slot="<<i<<" address_hex=0x"<<std::hex<<std::setw(16)<<std::setfill('0')<<a<<std::dec<<" address_dec="<<a<<" bytes="<<allocated_[i]<<" actual_BO_bytes="<<actual<<" alignment_remainder="<<(a%64);event("BO_ALLOC_RETURNED",d.str());need(actual==allocated_[i],"BO created size differs from page-aligned shared contract: "+d.str());need(ddr_range(a,allocated_[i]),"BO low/high DDR bounds or 64-byte alignment: "+d.str());event("BO_MAP_INTENT",std::to_string(i));map_[i]=bo_[i]->map<unsigned char*>();need(map_[i]!=nullptr,"BO map null");event("BO_MAP_RETURNED",d.str());set_guards(map_[i],caps_[i],allocated_[i]);}
            for(size_t i=0;i<4;++i)for(size_t j=i+1;j<4;++j)need(address(i)+allocated_[i]<=address(j)||address(j)+allocated_[j]<=address(i),"overlapping guarded BOs");
            std::copy(active.begin(),active.end(),map_[1]+guard);std::copy(meta.begin(),meta.end(),map_[2]+guard);for(size_t i=0;i<4;++i)sync(i,true);all_guards();need(checks_.initial_weight_to_device_sync_calls==2,"two static weight BOs synchronized once");event("NATIVE_READY","single CU/four global-guarded BOs; equal allocation capacities in both modes; weights synchronized once; shared payload offset=64; H576-aligned client-only per-layer X/Y cache synchronization");
        }catch(const std::exception& e){state_.fail();try{close_safe();}catch(...){retain("constructor cleanup failed before first START");}throw std::runtime_error(e.what());}
    }
    ~Native() override{if(state_.active)retain("destruction attempted during unknown DMA");try{close_safe();}catch(...){retain("resource close state uncertain");}}
    std::array<int,2> share(kv5::SharedInfo& info) override {
        need(!shared_&&!state_.begun&&!closed_,"share X/Y once before graph");shared_=true;std::array<int,2> fd{{-1,-1}};
        // export_buffer() is borrowed from its BO; only our owned duplicates
        // enter fd[], so both the owner and this catch may close them safely.
        try{for(size_t i=0;i<2;++i){const size_t n=i?3:0;info.size[i]=allocated_[n];info.physical[i]=address(n);info.flags[i]=uint32_t(bo_[n]->get_flags());event("BO_EXPORT_DUP_INTENT","slot="+std::to_string(n)+"; XRT export handle remains borrowed; owner receives an owned duplicate");const int borrowed=bo_[n]->export_buffer();need(borrowed>=0,"borrowed export X/Y FD");fd[i]=::fcntl(borrowed,F_DUPFD_CLOEXEC,3);need(fd[i]>=0,"owned duplicate X/Y FD");event("BO_EXPORT_DUP_RETURNED","slot="+std::to_string(n)+" borrowed_XRT_fd="+std::to_string(borrowed)+" owned_owner_fd="+std::to_string(fd[i]));}return fd;}catch(...){for(int f:fd)if(f>=0)::close(f);throw;}
    }
    void begin(uint64_t run) override {graph_hot_=true;current_layer_=49;try{state_.begin(run);idle(*this);PERF_TRACE("MODEL_BEGIN","run_id=1; 36 START maximum; client owns bounded X/Y sync; no second graph permitted");}catch(...){state_.fail();throw;}}
    void end(uint64_t run) override {current_layer_=49;try{state_.end(run);idle(*this);PERF_TRACE("MODEL_END_COMPLETED","run_id=1 calls=36 execution_mode="+std::string(execution_name(state_.execution()))+"; completion/error/drain only; guards/static weights checked by later INFO outside Runner.run");}catch(...){state_.fail();throw;}}
    void abort(uint64_t run) override {need(!state_.active,"ABORT cannot cancel DMA");state_.abort(run);idle(*this);PERF_TRACE("MODEL_ABORT_TERMINAL","no second graph or replay is permitted");}
    bool flush_performance_receipt() override {
        if(!state_.finished||state_.failed||state_.active){return false;}
        trace_.validate(state_);
        if(!receipt_written_){
            graph_hot_=false;
            checks_.begin_postrun(uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count()));
            idle(*this);final_checks();
            checks_.finish_postrun(uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count()));
            const auto raw=trace_.json(state_,checks_,active_data_sha_,active_meta_sha_);journal_->publish(raw);receipt_written_=true;
            event("PERFORMANCE_COMPACT_PUBLISHED","performance_native.json; INFO after timed Runner.run; fresh four-BO guards and static W SHA checked here, never per layer/END");
        }
        return true;
    }
    void execute(unsigned layer) override {current_layer_=layer;try{execute_layer(*this,state_,layer);}catch(const std::exception& e){if(state_.active)retain(e.what());throw;}}
    uint64_t address(size_t i)const{return bo_[i]->address();}
    uint32_t read(uint32_t offset){need(gemm_&&(offset==0||offset==4||offset==8||offset==0x10||offset==0x18||offset==0x1c||offset==0x24||offset==0x28||offset==0x30||offset==0x34||offset==0x3c||offset==0x40||offset==0x48||offset==0x50||offset==0x58||offset==0x60),"GEMM read allowlist");PERF_TRACE("GEMM_READ_INTENT",std::to_string(offset)+(offset==0?" COR_DONE_READY":""));const auto v=gemm_->read_register(offset);PERF_TRACE("GEMM_READ_RETURNED",std::to_string(offset)+" value="+std::to_string(v));return v;}
    uint32_t obs(uint32_t offset){need(observer_&&offset<=0x3c&&offset%4==0,"observer read allowlist");PERF_TRACE("OBSERVER_READ_INTENT",std::to_string(offset));const auto v=observer_->read_register(offset);PERF_TRACE("OBSERVER_READ_RETURNED",std::to_string(offset)+" value="+std::to_string(v));return v;}
    void write(uint32_t offset,uint32_t value){need(gemm_&&(offset==0||offset==4||offset==8||offset==0x18||offset==0x1c||offset==0x24||offset==0x28||offset==0x30||offset==0x34||offset==0x3c||offset==0x40||offset==0x48||offset==0x50||offset==0x58||offset==0x60),"GEMM write allowlist");if(offset==0)need(state_.active&&value==1&&state_.commands<=36,"START needs active lifetime/budget");else need(!state_.active,"parameter writes forbidden while active");PERF_TRACE(offset==0?"START_WRITE_INTENT":"GEMM_WRITE_INTENT",std::to_string(offset)+" value="+std::to_string(value));gemm_->write_register(offset,value);PERF_TRACE(offset==0?"START_WRITE_RETURNED":"GEMM_WRITE_RETURNED",std::to_string(offset));}
    // The imported client already TO_DEVICE-synchronized the exact valid X
    // range before RUN, and FROM_DEVICE-synchronizes valid Y after the response.
    // Never touch shared X/Y payloads here: even the final CPU head reuses X.
    void prepare(unsigned layer){need(!state_.active&&!state_.failed&&layer==current_layer_,"prepare inactive graph only; client owns payload and bounded cache synchronization");}
    void clear_observer(){idle(*this);PERF_TRACE("OBSERVER_CLEAR_INTENT","idle/drained statistics only");observer_->write_register(0x40,0x434c5245);PERF_TRACE("OBSERVER_CLEAR_RETURNED","no reset or clock write");for(uint32_t off=0x10;off<=0x38;off+=4)need(obs(off)==0,"observer clear readback");need(obs(0x3c)==1,"observer quiet after clear");}
    void start_intent(unsigned layer){(void)layer;deadline_=Clock::now()+std::chrono::seconds(120);PERF_TRACE("DMA_ACTIVE_BEFORE_START","run=1; no retry");}
    void completion_receipt(unsigned layer){(void)layer;PERF_TRACE("DMA_COMPLETION_PROVEN","seen_done+idle+start/auto-clear+drained");}
    void pause(){need(Clock::now()<deadline_,"120-second deadline; keep ALL resources without retry");std::this_thread::sleep_for(std::chrono::microseconds(50));}
    void verify(unsigned layer){need(!state_.active&&!state_.failed&&layer==current_layer_,"only completed inactive layer may commit");PERF_TRACE("LAYER_COMPLETION_VERIFIED",std::to_string(layer)+"; return/AXI errors checked; client consumes Y after RUN; no per-layer owner payload or guard scan");}
};
#undef PERF_TRACE
} // namespace
namespace kv5 {
std::unique_ptr<Kernel> make_kernel(const char* metadata,const std::vector<int8_t>& weights,const char* model_root){return std::make_unique<Native>(metadata,weights,model_root);}
}
