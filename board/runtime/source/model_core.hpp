#pragma once
// Device-free OPT50 full-model contract and shared single-CU execution sequence.
#include "protocol.h"
#include "route.h"
#include "picosha2.h"
#include "model_catalog.hpp"
#include <algorithm>
#include <array>
#include <cstdint>
#include <fstream>
#include <filesystem>
#include <limits>
#include <map>
#include <regex>
#include <string>
#include <vector>

namespace opt50_core {
using Bytes=std::vector<unsigned char>;
inline void need(bool ok,const std::string& why){if(!ok)throw std::runtime_error(why);}
enum class Execution { Mixed, AllDense };
inline const char* execution_name(Execution execution) {
    switch(execution){case Execution::Mixed:return "mixed";case Execution::AllDense:return "all_dense";}
    throw std::runtime_error("unknown execution mode");
}
inline Execution execution_from_name(const std::string& name) {
    if(name=="mixed")return Execution::Mixed;
    if(name=="all_dense")return Execution::AllDense;
    throw std::runtime_error("execution_mode must be exactly all_dense or mixed");
}
inline unsigned expected_sparse(Execution execution){(void)execution_name(execution);return execution==Execution::Mixed?12u:0u;}
inline unsigned expected_dense(Execution execution){return 36u-expected_sparse(execution);}
// Identical allocated Wdata capacity in BOTH arms; the mixed representation
// occupies its original verified prefix and zero-fills the unused suffix.
inline constexpr size_t fair_data_bytes=3981312;
inline constexpr auto dense_data_offsets=[] {
    std::array<size_t,49> offsets{};size_t at=0;
    for(unsigned i=0;i<49;++i){offsets[i]=at;if(opt50_model::layers[i].pl)at+=size_t(opt50_model::layers[i].k)*opt50_model::layers[i].n;}
    return offsets;
}();
inline opt50_model::Layer execution_layer(Execution execution,unsigned layer) {
    (void)execution_name(execution);need(layer<49,"execution layer outside model");
    auto descriptor=opt50_model::layers[layer];
    if(descriptor.pl&&execution==Execution::AllDense) {
        descriptor.mode=0;descriptor.data_offset=dense_data_offsets[layer];
        descriptor.data_bytes=size_t(descriptor.k)*descriptor.n;
        descriptor.meta_offset=0;descriptor.meta_bytes=16;
    }
    return descriptor;
}
inline constexpr size_t guard=64;
inline constexpr const char* uuid="fd96539d-4cbc-f732-7694-0f0236ca1cf3";
inline constexpr const char* metadata_sha="715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551";
inline constexpr const char* boot="f0f2eed8-5820-4df5-ba19-d05527720f97";
inline constexpr std::array<uint32_t,4> pointer_regs={0x18,0x24,0x30,0x3c};
inline constexpr std::array<uint32_t,4> scalar_regs={0x48,0x50,0x58,0x60};
inline constexpr std::array<unsigned,12> sparse_layers={26,27,30,31,34,35,38,39,42,43,46,47};
inline bool sparse(unsigned layer){return std::find(sparse_layers.begin(),sparse_layers.end(),layer)!=sparse_layers.end();}
inline std::string sha(const Bytes& raw){return picosha2::hash256_hex_string(raw);}
inline bool is_sha(const char* text){return text && std::regex_match(text,std::regex("[0-9a-f]{64}"));}
// ZOCL exports/imports the GEM page size, not the original unaligned request.
// Guard the complete page tail so imported X/Y sizes match the shared contract.
inline size_t allocation(size_t payload){need(payload && payload<=SIZE_MAX-4223,"page-guarded allocation overflow");return ((payload+128+4095)/4096)*4096;}
inline bool ddr_range(uint64_t address,size_t bytes) {
    if(!bytes || address%64)return false;
    const auto fits=[&](uint64_t low,uint64_t high){return address>=low && address<high && bytes<=high-address;};
    return fits(64,0x80000000ULL)||fits(0x800000000ULL,0x880000000ULL);
}
inline Bytes read_file(const std::string& path,size_t limit=64*1024*1024) {
    std::ifstream in(std::filesystem::u8path(path),std::ios::binary|std::ios::ate);need(bool(in),"file open: "+path);
    const auto end=in.tellg();need(end>=0 && uint64_t(end)<=limit,"bounded file length: "+path);
    Bytes raw(static_cast<size_t>(end));in.seekg(0);
    if(!raw.empty())in.read(reinterpret_cast<char*>(raw.data()),std::streamsize(raw.size()));
    need(bool(in),"file short read: "+path);return raw;
}
inline std::array<size_t,4> payload_capacities(){return {opt50_model::max_x_bytes,fair_data_bytes,opt50_model::meta_bytes,opt50_model::max_y_bytes};}
inline void validate_catalog() {
    need(opt50_model::layers.size()==49 && opt50_model::weights_bytes==4615680 &&
         opt50_model::data_bytes==3317760 && opt50_model::meta_bytes==165952 &&
         opt50_model::max_x_bytes==113472 && opt50_model::max_y_bytes==453888,"fixed H576 OPT50 model capacities");
    need(is_sha(opt50_model::weights_sha)&&is_sha(opt50_model::data_sha)&&is_sha(opt50_model::meta_sha)&&is_sha(opt50_model::reference_sha),"compiled model SHA syntax");
    size_t canonical=0,data=0,meta=64,max_x=0,max_y=0;unsigned pl=0,dense=0,sparse_count=0;
    for(unsigned i=0;i<49;++i) {
        const auto& l=opt50_model::layers[i];const auto s=kv5::shape(i);
        need(l.m==s.m && l.k==s.k && l.n==s.n && l.pl==kv5::offloaded(i) && l.mode==unsigned(sparse(i)),"fixed model layer shape/route/mode");
        need(l.canonical_offset==canonical && is_sha(l.w_sha)&&is_sha(l.scale_sha)&&is_sha(l.bias_sha),"layer canonical offset/hash identity");
        canonical+=s.w_bytes();
        if(l.pl) {
            ++pl;l.mode?++sparse_count:++dense;
            need(l.data_offset==data && l.data_offset%64==0 && l.data_bytes==s.w_bytes()/(l.mode?2:1),"layer packed data layout");
            data+=l.data_bytes;
            need(l.meta_offset==(l.mode?meta:0) && l.meta_offset%64==0 && l.meta_bytes==(l.mode?s.w_bytes()/8:16),"layer packed metadata layout");
            if(l.mode)meta+=l.meta_bytes;
            max_x=std::max(max_x,s.x_bytes());max_y=std::max(max_y,s.y_bytes());
        } else need(l.mode==0 && l.data_bytes==0 && l.meta_bytes==0,"CPU layers must never have a PL payload");
    }
    need(canonical==opt50_model::weights_bytes && data==opt50_model::data_bytes && meta==opt50_model::meta_bytes &&
         pl==36 && dense==24 && sparse_count==12 && max_x==opt50_model::max_x_bytes && max_y==opt50_model::max_y_bytes,"whole model layout/36-call route totals");
}
inline void validate_pack(const Bytes& canonical,const Bytes& data,const Bytes& meta) {
    validate_catalog();
    need(canonical.size()==opt50_model::weights_bytes && sha(canonical)==opt50_model::weights_sha &&
         data.size()==opt50_model::data_bytes && sha(data)==opt50_model::data_sha &&
         meta.size()==opt50_model::meta_bytes && sha(meta)==opt50_model::meta_sha,"whole model packed artifact identity");
    need(std::all_of(meta.begin(),meta.begin()+64,[](unsigned char c){return c==0;}),"shared Dense metadata 64-byte zero prefix");
    for(const auto& l:opt50_model::layers) {
        const size_t count=size_t(l.k)*l.n;
        Bytes expected(canonical.begin()+l.canonical_offset,canonical.begin()+l.canonical_offset+count);
        need(sha(expected)==l.w_sha,"canonical layer Wq SHA");
        if(!l.pl)continue;
        Bytes expanded(count,0);
        if(!l.mode)std::copy_n(data.data()+l.data_offset,count,expanded.data());
        else for(size_t t=0;t<count/4;++t) {
            const unsigned code=(meta[l.meta_offset+t/2]>>(4*(t%2)))&15;
            const unsigned p0=code&3,p1=code>>2;
            need(p0<p1,"sparse metadata position code");
            expanded[(4*(t/l.n)+p0)*l.n+t%l.n]=data[l.data_offset+2*t];
            expanded[(4*(t/l.n)+p1)*l.n+t%l.n]=data[l.data_offset+2*t+1];
        }
        need(expanded==expected,"Dense/Sparse PL pack must equal the SAME canonical pruned Wq");
    }
}
inline Bytes active_data(Execution execution,const Bytes& canonical,const Bytes& data,const Bytes& meta) {
    validate_pack(canonical,data,meta); // Both arms use the SAME pinned logical Wq.
    Bytes active(fair_data_bytes,0);
    if(execution==Execution::Mixed)std::copy(data.begin(),data.end(),active.begin());
    else {
        (void)execution_name(execution);size_t total=0;
        for(unsigned i=0;i<49;++i)if(opt50_model::layers[i].pl) {
            const auto descriptor=execution_layer(execution,i);
            need(descriptor.data_offset==total && descriptor.data_offset%64==0 &&
                 descriptor.data_bytes<=active.size()-total,"all-Dense active payload bounds");
            std::copy_n(canonical.data()+descriptor.canonical_offset,descriptor.data_bytes,active.data()+total);
            total+=descriptor.data_bytes;
        }
        need(total==active.size(),"all-Dense active capacity must equal sum of the 36 PL weights");
    }
    return active;
}
inline void validate_active_data(Execution execution,const Bytes& canonical,const Bytes& active,const Bytes& meta) {
    need(canonical.size()==opt50_model::weights_bytes&&sha(canonical)==opt50_model::weights_sha&&
         active.size()==fair_data_bytes&&meta.size()==opt50_model::meta_bytes&&sha(meta)==opt50_model::meta_sha,
         "active pack fixed canonical/meta/capacity identity");
    for(unsigned i=0;i<49;++i)if(opt50_model::layers[i].pl) {
        const auto l=execution_layer(execution,i);const size_t count=size_t(l.k)*l.n;
        need(l.data_offset<=active.size()&&l.data_bytes<=active.size()-l.data_offset&&
             l.meta_offset<=meta.size()&&l.meta_bytes<=meta.size()-l.meta_offset,"active descriptor bounds");
        Bytes expanded(count,0);
        if(!l.mode)std::copy_n(active.data()+l.data_offset,count,expanded.data());
        else for(size_t t=0;t<count/4;++t) {
            const unsigned code=(meta[l.meta_offset+t/2]>>(4*(t%2)))&15;
            const unsigned p0=code&3,p1=code>>2;need(p0<p1,"active sparse metadata position code");
            expanded[(4*(t/l.n)+p0)*l.n+t%l.n]=active[l.data_offset+2*t];
            expanded[(4*(t/l.n)+p1)*l.n+t%l.n]=active[l.data_offset+2*t+1];
        }
        need(std::equal(expanded.begin(),expanded.end(),canonical.begin()+l.canonical_offset),
             "active Dense/Sparse must equal SAME canonical pruned Wq for all 36 PL layers");
    }
    if(execution==Execution::Mixed)need(std::all_of(active.begin()+opt50_model::data_bytes,active.end(),[](unsigned char c){return c==0;}),
                                      "mixed fair-capacity suffix must remain zero");
}
inline void set_guards(unsigned char* map,size_t payload,size_t allocated) {
    need(allocated==allocation(payload),"whole BO guarded allocation size");
    std::fill_n(map,allocated,0xa5);std::fill_n(map+guard,payload,0);
}
inline void check_guards(const unsigned char* map,size_t payload,size_t allocated) {
    need(allocated==allocation(payload) && std::all_of(map,map+guard,[](unsigned char c){return c==0xa5;}) &&
         std::all_of(map+guard+payload,map+allocated,[](unsigned char c){return c==0xa5;}),"global BO prefix/suffix guard changed");
}
struct State {
private:
    Execution execution_;
public:
    explicit State(Execution execution=Execution::Mixed):execution_(execution){(void)execution_name(execution);}
    Execution execution()const{return execution_;}
    bool begun=false,finished=false,failed=false,active=false,done=false;
    uint64_t run=0;unsigned calls=0,commands=0,next=kv5::next_pl(0);
    void begin(uint64_t id){need(!begun&&!failed&&!active&&id==1,"only ONE graph run_id=1 may begin in this owner lifetime");begun=true;run=id;}
    void admit(unsigned layer){need(begun&&!finished&&!failed&&!active && layer==next && layer<49 && opt50_model::layers[layer].pl,"PL layer order/route/session");}
    void start(){need(!active&&commands==calls&&commands<36,"START lifetime/budget");active=true;done=false;++commands;}
    bool observe(uint32_t ctrl,bool drained){done=done||bool(ctrl&2);return done&&(ctrl&4)&&!(ctrl&0x81)&&drained;}
    void complete(){need(active,"no active DMA completion");active=false;}
    void commit(unsigned layer){need(!active&&commands==calls+1&&layer==next,"commit only after proven closed DMA and checks");++calls;next=kv5::next_pl(layer+1);}
    void fail(){failed=true;}
    void end(uint64_t id){need(begun&&!finished&&!failed&&!active&&run==id&&calls==36&&commands==36&&next==49,"full graph needs exactly 36 ordered proven PL calls");finished=true;}
    void abort(uint64_t id){need(begun&&!finished&&!active&&run==id,"ABORT is not DMA cancellation");failed=true;finished=true;}
};
template<class B> bool drained(B& b){return b.obs(0x14)==0 && b.obs(0x18)==0 && b.obs(0x1c)==0 && b.obs(0x3c)==1;}
template<class B> void idle(B& b){const auto c=b.read(0);need((c&4)&&!(c&0x81)&&drained(b)&&b.obs(0x10)==0,"GEMM/AXI not idle/drained/error-free");}
template<class B> void execute_layer(B& b,State& state,unsigned layer) {
    try {
        state.admit(layer);idle(b);b.prepare(layer);
        const auto l=execution_layer(state.execution(),layer);
        const std::array<uint64_t,4> address={b.address(0)+guard,b.address(1)+guard+l.data_offset,b.address(2)+guard+l.meta_offset,b.address(3)+guard};
        for(size_t i=0;i<4;++i){b.write(pointer_regs[i],uint32_t(address[i]));b.write(pointer_regs[i]+4,uint32_t(address[i]>>32));need(b.read(pointer_regs[i])==uint32_t(address[i])&&b.read(pointer_regs[i]+4)==uint32_t(address[i]>>32),"64-bit pointer readback");}
        const std::array<uint32_t,4> values={l.m,l.k,l.n,l.mode};
        for(size_t i=0;i<4;++i){b.write(scalar_regs[i],values[i]);need(b.read(scalar_regs[i])==values[i],"shape/mode readback");}
        idle(b);b.clear_observer();idle(b); // consume stale COR done before this START.
        state.start();b.start_intent(layer);b.write(0,1);
        // One COR control observation per iteration. The backend's monotonic
        // deadline, not an iteration-count approximation, bounds the wait.
        for(;;){const auto c=b.read(0);if(state.observe(c,drained(b)))break;b.pause();}
        state.complete();b.completion_receipt(layer);
        need(b.read(0x10)==0 && b.obs(0x10)==0,"GEMM return/AXI errors");b.verify(layer);state.commit(layer);
    } catch(...){state.fail();throw;}
}
class Fake {
    std::array<Bytes,4> memory_;std::array<uint64_t,4> addresses_{};std::map<uint32_t,uint32_t> registers_;
    State& state_;unsigned layer_=0,fake_polls_=0;bool deferred_checks_=false;
public:
    bool bad_guard=false,ambiguous_start=false,never_done=false,bad_high=false;
    unsigned starts=0,dense=0,sparse_count=0,guard_scans=0;
    explicit Fake(State& s,bool high=true,bool deferred_checks=false):state_(s),deferred_checks_(deferred_checks) {
        auto caps=payload_capacities();uint64_t cursor=high?0x800100000ULL:0x100000ULL;
        for(size_t i=0;i<4;++i){memory_[i].resize(allocation(caps[i]));addresses_[i]=cursor;need(ddr_range(cursor,memory_[i].size()),"fake low/high DDR");set_guards(memory_[i].data(),caps[i],memory_[i].size());cursor+=((memory_[i].size()+4095)/4096)*4096;}
    }
    uint64_t address(size_t i)const{return addresses_[i];}
    uint32_t obs(uint32_t offset){return offset==0x3c?1:0;}
    uint32_t read(uint32_t offset){if(offset==0)return state_.active?(never_done?1:6):4;for(auto p:pointer_regs)if(bad_high&&offset==p+4)return registers_[offset]^1u;return registers_[offset];}
    void write(uint32_t offset,uint32_t value){if(offset==0){need(state_.active&&value==1,"active must precede START");++starts;if(ambiguous_start)throw std::runtime_error("fake ambiguous START");execution_layer(state_.execution(),layer_).mode?++sparse_count:++dense;if(bad_guard)memory_[3][0]=0;}else registers_[offset]=value;}
    void check_buffers(){auto caps=payload_capacities();for(size_t i=0;i<4;++i){++guard_scans;check_guards(memory_[i].data(),caps[i],memory_[i].size());}}
    void prepare(unsigned layer){layer_=layer;if(!deferred_checks_)check_buffers();}
    void clear_observer(){}void start_intent(unsigned){fake_polls_=0;}void completion_receipt(unsigned){}
    void pause(){need(++fake_polls_<=12000,"fake monotonic deadline; no hardware access");}
    void verify(unsigned){if(!deferred_checks_)check_buffers();}
};
} // namespace opt50_core
