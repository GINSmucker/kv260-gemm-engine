#pragma once
// Shared, device-free logic. The fake backend exercises the SAME two-mode
// sequence as the XRT backend; it is never evidence of a physical PL run.
#include "support/opt50_common.hpp"
#include <functional>
#include <iomanip>
#include <iostream>
#include <memory>

namespace b24layer02 {
using namespace opt50;
inline constexpr const char* uuid = "fd96539d-4cbc-f732-7694-0f0236ca1cf3";
inline constexpr const char* case_id = "B24_dev0000_layer02";
inline constexpr const char* source_model_sha = "ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43";
inline constexpr const char* archive_sha = "7ada4de5cf99d09d3d6a5b9d92108bcbce85dc636e0c1669d8b14d5171139fc4";
// The plan binds the live boot. A prior experiment's boot is never compiled in.
inline bool boot_uuid(const std::string& value) {
    return std::regex_match(value,std::regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"));
}
inline constexpr const char* metadata_sha = "715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551";
inline constexpr const char* companion_lock = "/var/lib/opt50-first-access-fd96539d-v1/companion.lock";
inline constexpr uint64_t observer_base=0xa0010000ULL, gemm_base=0xa0000000ULL;
inline constexpr std::array<uint32_t,4> argument_regs={reg::x,reg::w_data,reg::w_meta,reg::y};
inline constexpr std::array<const char*,6> payload_names={"X","W_dense_data","W_dense_meta","W_sparse_data","W_sparse_meta","Y_golden"};
inline constexpr std::array<size_t,6> payload_bytes={37824,110592,16,55296,13824,453888};
inline constexpr std::array<const char*,6> payload_sha={
    "67563398b91ee2a852c21b098bad353e5db10e2d13652574512bb88149b2dfe9",
    "953f919af13d4b9a19ecb60e9bcaa5a59eb4e19204cd8bdbe63cce2c1a239e14",
    "374708fff7719dd5979ec875d56cd2286f6d3cf7ec317a3b25632aab28ec37bb",
    "6a424a993a7f6b0d991663b7619f14fffd7d83d6c3f09c7702af3c6a1bf4ae9b",
    "94a777971b6bc52b7180251d2c474c5ae4a79fba7e509917e171451ae7e06f78",
    "7e5b8c893a3477702c2566097a1c12d82e5d710d5b9ede11d42ecd410803d292"};

// These labels describe which reviewed address window contains the START
// address. The authoritative full-allocation check remains the unchanged
// opt50::ddr_range() policy supplied by C's accepted low/high DDR contract.
inline const char* ddr_window(uint64_t address) {
    if(address>=64 && address<0x80000000ULL)return "HP0_LOW";
    if(address>=0x800000000ULL && address<0x880000000ULL)return "HP0_HIGH";
    return "OUTSIDE_REVIEWED_DDR";
}
inline std::string bo_description(size_t slot,uint64_t address,size_t bytes) {
    std::ostringstream out;
    out<<"slot="<<slot<<" bytes="<<bytes
       <<" address_hex=0x"<<std::hex<<std::setw(16)<<std::setfill('0')<<address<<std::dec
       <<" address_dec="<<address<<" required_alignment_bytes=64"
       <<" alignment_remainder="<<(address%64)
       <<" ddr_window="<<ddr_window(address)
       <<" full_allocation_valid="<<(opt50::ddr_range(address,bytes)?"true":"false");
    return out.str();
}

struct Layer02Job {
    Fields fields;
    Shape dimensions{197,192,576,0};
    std::array<Bytes,6> payload;
    Job mode(unsigned mode_number) const {
        require(mode_number<2,"real-layer mode must be Dense 0 or Sparse 1");
        return {{},{dimensions.m,dimensions.k,dimensions.n,mode_number},
                {payload[0],payload[mode_number?3:1],payload[mode_number?4:2],payload[5]}};
    }
    std::array<size_t,4> maximum_capacities() const {
        const auto dense=mode(0).dimensions.sizes(),sparse=mode(1).dimensions.sizes();
        std::array<size_t,4> caps{};
        for(size_t i=0;i<4;++i)caps[i]=std::max(dense[i],sparse[i]);
        return caps;
    }
};
inline void validate_same_pruned_math(const Layer02Job& j) {
    for(unsigned mode:{0u,1u}) {
        const auto one=j.mode(mode);
        validate_payload(one.dimensions,one.data);
        require(cpu_reference(one)==j.payload[5],"independent INT64 reference differs from real-layer golden");
    }
    // Dense is the same pruned matrix expanded to K*N, NOT an unpruned baseline.
    const auto s=j.dimensions;
    Bytes expanded(size_t(s.k)*s.n,0);
    for(size_t t=0;t<size_t(s.k/4)*s.n;++t) {
        const unsigned code=(j.payload[4][t/2]>>(4*(t%2)))&15;
        expanded[(4*(t/s.n)+(code&3))*s.n+t%s.n]=j.payload[3][2*t];
        expanded[(4*(t/s.n)+(code>>2))*s.n+t%s.n]=j.payload[3][2*t+1];
    }
    require(expanded==j.payload[1],"Dense and Sparse encoded weights differ");
}
inline Layer02Job load_layer02_job(const std::string& path) {
    Layer02Job j; j.fields=read_fields(path);
    const auto& f=j.fields;
    require(f.size()==26 && field(f,"schema")=="opt50_b24_layer02_job_v1","exact real-layer KV schema/26 fields required; tiny/other production jobs are not accepted");
    require(field(f,"case_id")=="B24_dev0000_layer02" && number(field(f,"M"))==197 &&
            number(field(f,"K"))==192 && number(field(f,"N"))==576,"only B24_dev0000_layer02 M197K192N576 is authorized");
    require(field(f,"package_manifest_sha256")==package_sha &&
            sha256(read_file(field(f,"package_manifest")))==package_sha,"fixed production case package manifest identity");
    require(std::regex_match(field(f,"run_id"),std::regex("[0-9a-f]{12}")),"real-layer run token");
    require(boot_uuid(field(f,"boot_id")),"plan boot must be a canonical UUID; hardware mode also checks the current boot");
    require(field(f,"source_model_sha256")==source_model_sha,"fixed B24 final W8A8 model identity");
    require(number(field(f,"expected_parent_pid"))>1,"parent PID required");
    require(field(f,"companion_lock")==companion_lock,"new global companion lock path differs");
    require(field(f,"metadata_sha256")==metadata_sha,"fixed metadata SHA identity");
    require(!field(f,"metadata").empty(),"metadata path required");
    for(size_t i=0;i<6;++i) {
        const std::string key=payload_names[i];
        require(field(f,key+"_sha256")==payload_sha[i],"fixed B24_dev0000_layer02 payload SHA: "+key);
        j.payload[i]=read_file(field(f,key),payload_bytes[i]);
        require(j.payload[i].size()==payload_bytes[i] && sha256(j.payload[i])==payload_sha[i],"real-layer payload length/SHA: "+key);
    }
    validate_same_pruned_math(j);
    return j;
}
inline bool ps_fields_match(const std::array<uint32_t,3>& raw) {
    return (raw[0]&0x300)==0x200 && (raw[1]&0xb)==0 && (raw[2]&0xb)==0;
}
struct CompletionLatch {
    bool seen_done=false;
    bool update(uint32_t control,bool quiet) {
        seen_done=seen_done || bool(control&2); // ap_done is COR: never lose a prior sample.
        return completion(seen_done,control,quiet);
    }
};
struct GuardBuffer {
    size_t active=0;
    Bytes expected;
    void prepare(unsigned char* map,size_t allocation,const Bytes& payload,bool output=false) {
        active=payload.size();
        require(allocation>=active+128,"guard allocation too short");
        std::fill(map,map+allocation,0xa5);
        if(output) std::fill(map+64,map+64+active,0xcd);
        else std::copy(payload.begin(),payload.end(),map+64);
        expected=payload;
    }
    void verify(const unsigned char* map,size_t allocation,bool output=false) const {
        require(std::all_of(map,map+64,[](unsigned char x){return x==0xa5;}) &&
                std::all_of(map+64+active,map+allocation,[](unsigned char x){return x==0xa5;}),"BO redzone or mode-dependent inactive tail overwritten");
        if(!output) require(std::equal(expected.begin(),expected.end(),map+64),"DMA changed input buffer");
    }
};
template<class B> std::array<uint32_t,16> observer_sample(B& b) {
    std::array<uint32_t,16> s{};
    for(uint32_t i=0;i<16;++i) s[i]=b.read_observer(4*i);
    require(s[0]==0x46383031 && s[1]==0x10000 && s[2]==4254487453u && s[3]==1287452466u,"observer magic/version/build tags differ");
    require(s[4]==0 && drained(s[5],s[6],s[7],s[15]),"observer AXI error/pending/not quiet");
    return s;
}
template<class B> void stable_observer(B& b) {
    const auto first=observer_sample(b);
    const auto second=observer_sample(b);
    require(first==second,"observer traffic counters changed before START");
}
template<class B> bool bus_drained(B& b) {
    const auto r=b.read_observer(0x14),w=b.read_observer(0x18),y=b.read_observer(0x1c),quiet=b.read_observer(0x3c);
    return drained(r,w,y,quiet);
}
template<class B> void idle(B& b) {
    const auto ctrl=b.read_gemm(0);
    require((ctrl&4) && !(ctrl&0x81) && bus_drained(b),"GEMM is not idle/start-clear/auto-restart-clear/drained");
    require(b.read_observer(0x10)==0,"observer AXI error");
}
template<class B> std::array<Bytes,2> two_modes(B& b,const Layer02Job& job) {
    stable_observer(b);
    idle(b);
    b.write_gemm(reg::gie,0);b.write_gemm(reg::ier,0);
    require(b.read_gemm(reg::gie)==0 && b.read_gemm(reg::ier)==0,"GIE/IER disable readback");
    b.allocate(job.maximum_capacities()); // Exactly once: reuse these four BOs and one CU.
    std::array<Bytes,2> outputs;
    for(unsigned mode:{0u,1u}) {
        idle(b);
        const auto one=job.mode(mode);
        std::array<GuardBuffer,4> guards;
        for(size_t i=0;i<4;++i) {
            guards[i].prepare(b.map(i),b.allocation(i),one.data[i],i==3);
            b.sync_to(i);
            const auto address=b.address(i)+64;
            b.write_gemm(argument_regs[i],uint32_t(address));
            b.write_gemm(argument_regs[i]+4,uint32_t(address>>32));
            require(b.read_gemm(argument_regs[i])==uint32_t(address) &&
                    b.read_gemm(argument_regs[i]+4)==uint32_t(address>>32),"64-bit pointer readback differs");
        }
        const auto s=job.dimensions;
        const std::array<uint32_t,4> off={reg::m,reg::k,reg::n,reg::mode},values={s.m,s.k,s.n,mode};
        for(size_t i=0;i<4;++i) {
            b.write_gemm(off[i],values[i]);
            require(b.read_gemm(off[i])==values[i],"scalar readback differs");
        }
        idle(b);stable_observer(b);
        b.write_observer(0x40,0x434c5245); // Only after idle+drain; no clock/reset writes.
        const auto cleared=observer_sample(b);
        for(size_t i=4;i<15;++i) require(cleared[i]==0,"observer counters did not clear");
        idle(b); // Deliberately consume any stale COR ap_done BEFORE this launch.
        CompletionLatch latch;
        b.before_start(mode); // Sets lifetime active BEFORE possibly ambiguous write.
        b.write_gemm(0,1);
        bool finished=false;
        for(unsigned poll=0;poll<12000;++poll) {
            const auto ctrl=b.read_gemm(0);
            if(latch.update(ctrl,bus_drained(b))) {finished=true;break;}
            b.pause_poll();
        }
        require(finished,"bounded completion deadline; resources must be retained");
        b.completed(); // Only after seen_done + idle + start/auto-clear + DMA drained.
        require(b.read_gemm(reg::result)==0 && b.read_observer(0x10)==0,"GEMM return/observer AXI error; output rejected");
        for(size_t i=0;i<4;++i) {
            b.sync_from(i); // All four, not just Y: input/guard verification needs cache invalidate.
            guards[i].verify(b.map(i),b.allocation(i),i==3);
        }
        outputs[mode]=Bytes(b.map(3)+64,b.map(3)+64+one.data[3].size());
        require(outputs[mode]==job.payload[5],"real-layer exact INT32 golden mismatch");
    }
    require(outputs[0]==outputs[1],"Dense/Sparse hardware output mismatch");
    return outputs;
}
class FakeBackend {
    std::array<Bytes,4> bytes_;
    std::map<uint32_t,uint32_t> regs_;
    unsigned mode_=0;
    uint64_t base_=0x10000;
    std::array<uint64_t,4> addresses_{};
public:
    bool active=false,corruption=false,never_done=false,throw_start=false,bad_high_readback=false;
    bool corrupt_output_tail=false,corrupt_sparse_weight_tail=false;
    unsigned starts=0,allocations=0,done_reads=0,high_word_readbacks=0;
    std::array<std::array<uint64_t,4>,2> pointers_at_start{};
    const Layer02Job& job;
    explicit FakeBackend(const Layer02Job& j,bool high_ddr=false):base_(high_ddr?0x800010000ULL:0x10000ULL),job(j) {}
    uint32_t read_observer(uint32_t off) {
        if(off==0) return 0x46383031;
        if(off==4) return 0x10000;
        if(off==8) return 4254487453u;
        if(off==12) return 1287452466u;
        return off==0x3c?1:0;
    }
    uint32_t read_gemm(uint32_t off) {
        if(off==0) {
            if(active) {++done_reads;return never_done?1:6;}
            return 4;
        }
        for(const auto low:argument_regs)if(off==low+4) {
            ++high_word_readbacks;
            return bad_high_readback?(regs_[off]^1u):regs_[off];
        }
        return regs_[off];
    }
    void write_observer(uint32_t off,uint32_t value) {require(off==0x40 && value==0x434c5245,"unexpected fake observer write");}
    void write_gemm(uint32_t off,uint32_t value) {
        if(off==reg::mode) mode_=value;
        if(off==0) {
            require(active && value==1,"fake START lifetime ordering");
            for(size_t i=0;i<4;++i) {
                const auto low=argument_regs[i];
                const uint64_t pointer=uint64_t(regs_[low])|(uint64_t(regs_[low+4])<<32);
                require(pointer==address(i)+64,"fake START pointer high/low words differ from same guarded BO");
                pointers_at_start[mode_][i]=pointer;
            }
            ++starts;if(throw_start)throw std::runtime_error("synthetic ambiguous START");
            const auto y=cpu_reference(job.mode(mode_));
            std::copy(y.begin(),y.end(),bytes_[3].begin()+64);
            if(corruption)bytes_[0][0]=0;
            if(corrupt_output_tail)bytes_[3][64+y.size()]=0;
            if(corrupt_sparse_weight_tail && mode_==1)bytes_[1][64+job.payload[3].size()]=0;
        } else regs_[off]=value;
    }
    void allocate(const std::array<size_t,4>& caps) {
        ++allocations;require(allocations==1,"buffers reallocated across modes");
        uint64_t cursor=base_;
        for(size_t i=0;i<4;++i) {
            bytes_[i].resize(((caps[i]+63)/64)*64+128);
            addresses_[i]=cursor;
            require(opt50::ddr_range(address(i),bytes_[i].size()),"fake BO outside unchanged low/high DDR contract");
            cursor+=((bytes_[i].size()+4095)/4096)*4096;
        }
    }
    unsigned char* map(size_t i){return bytes_[i].data();}
    size_t allocation(size_t i)const{return bytes_[i].size();}
    uint64_t address(size_t i)const{return addresses_[i];}
    void sync_to(size_t){}void sync_from(size_t){}
    void before_start(unsigned){require(!active,"fake active twice");active=true;}
    void completed(){require(active,"fake completion without START");active=false;}
    void pause_poll(){}
};
inline Layer02Job synthetic(uint32_t m=2,uint32_t k=16,uint32_t n=16) {
    require(m>=1 && m<=197 && k>=16 && k<=576 && n>=16 && n<=576 &&
            k%16==0 && n%16==0 && size_t(k)*n<=110592,"synthetic shape within OPT50 capability");
    Layer02Job j;j.dimensions={m,k,n,0};
    auto& p=j.payload;
    const std::array<int,16> x={-128,-127,-65,-1,0,1,2,63,64,126,127,-7,11,-23,42,-99};
    const std::array<int,8> a={-128,-127,-1,0,1,2,63,127},b={127,0,-128,1,-1,64,-64,-127};
    const std::array<unsigned,6> codes={4,8,12,9,13,14};
    p[0].resize(size_t(m)*k);p[1].resize(size_t(k)*n);p[2].resize(16);
    p[3].resize(size_t(k)*n/2);p[4].resize(size_t(k)*n/8);p[5].resize(size_t(m)*n*4);
    for(size_t r=0;r<m;++r)for(size_t col=0;col<k;++col)p[0][r*k+col]=static_cast<unsigned char>(x[(col+5*r)%16]);
    for(size_t t=0;t<size_t(k/4)*n;++t) {
        const auto code=codes[t%6];
        p[3][2*t]=static_cast<unsigned char>(a[t%8]);p[3][2*t+1]=static_cast<unsigned char>(b[t%8]);
        p[4][t/2]|=static_cast<unsigned char>(code<<(4*(t%2)));
        p[1][(4*(t/n)+(code&3))*n+t%n]=p[3][2*t];
        p[1][(4*(t/n)+(code>>2))*n+t%n]=p[3][2*t+1];
    }
    p[5]=cpu_reference(j.mode(0));
    require(cpu_reference(j.mode(1))==p[5],"synthetic sparse reference");
    return j;
}
inline void pure_self_test() {
    opt50::self_test();
    require(boot_uuid("00000000-0000-0000-0000-000000000000") &&
            boot_uuid("ffffffff-ffff-ffff-ffff-ffffffffffff") &&
            !boot_uuid("not-a-boot") && !boot_uuid("00000000000000000000000000000000"),
            "boot identity syntax only; current boot is checked at hardware entry");
    require(ddr_range(64,192) && ddr_range(0x10000ULL,192) &&
            ddr_range(0x7fffff40ULL,192) && ddr_range(0x7fffffc0ULL,64),"valid 192-byte and end-inclusive low DDR allocations");
    require(ddr_range(0x800000000ULL,192) && ddr_range(0x800010000ULL,192) &&
            ddr_range(0x87fffff40ULL,192) && ddr_range(0x87fffffc0ULL,64),"valid >4GiB high DDR lower/upper boundaries");
    require(!ddr_range(0,192) && !ddr_range(64,0) && !ddr_range(65,192) &&
            !ddr_range(0x800000001ULL,192),"zero-size, null and non-64-byte-aligned BO rejection");
    require(!ddr_range(0x7fffffc0ULL,128) && !ddr_range(0x87fffffc0ULL,128) &&
            !ddr_range(0x80000000ULL,64) && !ddr_range(0x880000000ULL,64) &&
            !ddr_range(0x7ffffffc0ULL,64) && !ddr_range(0x100000000ULL,192),"DDR end crossing, exclusive limits and unmapped gap rejection");
    require(!ddr_range(UINT64_MAX-63,128) &&
            !ddr_range(0x800000000ULL,std::numeric_limits<size_t>::max()) &&
            !ddr_range(0x7fffffc0ULL,std::numeric_limits<size_t>::max()),"overflow-safe full-allocation bounds");
    const auto high_description=bo_description(0,0x800010000ULL,192);
    require(high_description.find("address_hex=0x0000000800010000")!=std::string::npos &&
            high_description.find("address_dec="+std::to_string(0x800010000ULL))!=std::string::npos &&
            high_description.find("alignment_remainder=0")!=std::string::npos &&
            high_description.find("ddr_window=HP0_HIGH")!=std::string::npos &&
            high_description.find("full_allocation_valid=true")!=std::string::npos,"complete 64-bit hexadecimal/decimal high-DDR BO diagnostics");
    require(ps_fields_match({0xa00,0x3b0,0x3b0}) && !ps_fields_match({0x900,0x3b0,0x3b0}) && !ps_fields_match({0xa00,0x3b8,0x3b0}),"PS relevant-field masks");
    CompletionLatch cor;require(!cor.update(2,false) && cor.update(4,true),"COR done must remain latched");
    CompletionLatch missing;require(!missing.update(4,true),"idle without done must not complete");
    auto j=synthetic();FakeBackend ok(j);const auto result=two_modes(ok,j);
    require(ok.starts==2 && ok.allocations==1 && !ok.active && result[0]==j.payload[5] && result[1]==j.payload[5],"same sequence/CU/BO two-mode fake test");
    require(ok.high_word_readbacks==8 && ok.pointers_at_start[0]==ok.pointers_at_start[1],"low-DDR pointer high-word readback and same BO reuse");
    FakeBackend high(j,true);const auto high_result=two_modes(high,j);
    require(high.starts==2 && high.allocations==1 && !high.active && high.high_word_readbacks==8 &&
            high_result[0]==j.payload[5] && high_result[1]==j.payload[5] &&
            high.pointers_at_start[0]==high.pointers_at_start[1],"same CU/four high-DDR BOs across Dense and Sparse");
    for(size_t i=0;i<4;++i)require(high.pointers_at_start[0][i]==high.address(i)+64 &&
            high.pointers_at_start[0][i]>UINT32_MAX && (high.pointers_at_start[0][i]>>32)==8,
            ">4GiB high/low pointer programming is not truncated");
    FakeBackend high_bad(j,true);high_bad.bad_high_readback=true;bool high_rejected=false;
    try{two_modes(high_bad,j);}catch(const std::runtime_error&){high_rejected=true;}
    require(high_rejected && high_bad.starts==0 && !high_bad.active,"bad high pointer readback rejected BEFORE START");
    for(const auto& dimensions:std::array<std::array<uint32_t,3>,3>{{{{1,16,32}},{{17,32,48}},{{65,16,32}}}}) {
        auto dynamic=synthetic(dimensions[0],dimensions[1],dimensions[2]);
        validate_same_pruned_math(dynamic);
        FakeBackend dynamic_high(dynamic,true);const auto dynamic_output=two_modes(dynamic_high,dynamic);
        const auto caps=dynamic.maximum_capacities();
        require(dynamic_output[0].size()==size_t(dimensions[0])*dimensions[2]*4 &&
                dynamic_output[0]==dynamic.payload[5] && dynamic_output[1]==dynamic.payload[5] &&
                dynamic_high.starts==2 && dynamic_high.allocations==1 && !dynamic_high.active &&
                dynamic_high.high_word_readbacks==8 &&
                dynamic_high.pointers_at_start[0]==dynamic_high.pointers_at_start[1],
                "dynamic M/K/N/output size and high-DDR same-four-BO two-mode sequence");
        require(caps[0]==size_t(dimensions[0])*dimensions[1] &&
                caps[1]==size_t(dimensions[1])*dimensions[2] &&
                caps[2]==size_t(dimensions[1])*dimensions[2]/8 &&
                caps[3]==dynamic.payload[5].size(),"dynamic maximum Dense/Sparse BO capacities");
        for(size_t i=0;i<4;++i)for(size_t k=i+1;k<4;++k)
            require(dynamic_high.address(i)+dynamic_high.allocation(i)<=dynamic_high.address(k),
                    "dynamic fake allocations must not overlap above 4GiB");
    }
    auto dynamic=synthetic(17,32,48);bool guard_rejected=false;
    FakeBackend dynamic_tail(dynamic,true);dynamic_tail.corrupt_output_tail=true;
    try{two_modes(dynamic_tail,dynamic);}catch(const std::runtime_error&){guard_rejected=true;}
    require(guard_rejected && !dynamic_tail.active && dynamic_tail.starts==1,
            "dynamic Y exact active-size redzone corruption rejected after safe completion");
    FakeBackend dynamic_sparse_tail(dynamic,true);dynamic_sparse_tail.corrupt_sparse_weight_tail=true;guard_rejected=false;
    try{two_modes(dynamic_sparse_tail,dynamic);}catch(const std::runtime_error&){guard_rejected=true;}
    require(guard_rejected && !dynamic_sparse_tail.active && dynamic_sparse_tail.starts==2,
            "dynamic Sparse inactive Dense-capacity tail must be guarded across reused BOs");
    FakeBackend ambiguous(j);ambiguous.throw_start=true;bool rejected=false;
    try{two_modes(ambiguous,j);}catch(const std::runtime_error&){rejected=true;}
    require(rejected && ambiguous.active && ambiguous.starts==1,"ambiguous START lifetime must remain active");
    FakeBackend stalled(j);stalled.never_done=true;rejected=false;
    try{two_modes(stalled,j);}catch(const std::runtime_error&){rejected=true;}
    require(rejected && stalled.active,"deadline must retain active resources");
    FakeBackend corrupt(j);corrupt.corruption=true;rejected=false;
    try{two_modes(corrupt,j);}catch(const std::runtime_error&){rejected=true;}
    require(rejected && !corrupt.active,"redzone corruption must reject after proven DMA completion");
}
} // namespace b24layer02
