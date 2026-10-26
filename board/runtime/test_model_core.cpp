#include "source/model_core.hpp"
#include "source/native_gate.hpp"
#include <iostream>
using namespace opt50_core;
template<class F> void refused(F f){bool bad=false;try{f();}catch(const std::exception&){bad=true;}need(bad,"negative case was not rejected");}
int main(int argc,char** argv){try{
#if !defined(__linux__)
    refused([]{opt50_gate::validate(nullptr,-1,nullptr,nullptr,nullptr,nullptr);});
#endif
    validate_catalog();
    need(execution_from_name("mixed")==Execution::Mixed&&execution_from_name("all_dense")==Execution::AllDense,
         "strict two-arm execution selection");
    for(const std::string mode:{"dense","ALL_DENSE","mixed ","","sparse","0","1"})refused([&]{execution_from_name(mode);});
    refused([]{State invalid(static_cast<Execution>(99));(void)invalid;});
    need(payload_capacities()[1]==fair_data_bytes&&fair_data_bytes==3981312,
         "both arms must have equal full-Dense Wdata capacity");
    for(Execution execution:{Execution::Mixed,Execution::AllDense}) {
        size_t data=0;unsigned dense=0,sparse_count=0;
        for(unsigned i=0;i<49;++i) {
            const auto l=execution_layer(execution,i);const auto& original=opt50_model::layers[i];
            need(l.m==original.m&&l.k==original.k&&l.n==original.n&&l.pl==original.pl&&
                 l.canonical_offset==original.canonical_offset&&std::string(l.w_sha)==original.w_sha&&
                 std::string(l.scale_sha)==original.scale_sha&&std::string(l.bias_sha)==original.bias_sha,
                 "execution mode must never change logical shapes/weights/scales/bias/routes");
            if(l.pl) {
                l.mode?++sparse_count:++dense;
                need(l.data_offset==data&&l.data_bytes<=fair_data_bytes-data,"contiguous bounded active data descriptors");
                data+=l.data_bytes;
                if(execution==Execution::AllDense)need(l.mode==0&&l.meta_offset==0&&l.meta_bytes==16&&
                                                     l.data_bytes==size_t(l.k)*l.n,"all Dense full weights/dummy metadata");
            } else need(l.mode==0&&l.data_bytes==0&&l.meta_bytes==0,"CPU route unchanged");
        }
        need(dense==expected_dense(execution)&&sparse_count==expected_sparse(execution)&&
             data==(execution==Execution::AllDense?fair_data_bytes:opt50_model::data_bytes),"execution mode layout/count totals");
    }
    refused([]{execution_layer(Execution::Mixed,49);});
    need(pointer_regs==std::array<uint32_t,4>{0x18,0x24,0x30,0x3c}&&scalar_regs==std::array<uint32_t,4>{0x48,0x50,0x58,0x60},"OPT50 eight-argument ABI");
    auto wire=kv5::encode({kv5::Op::Run,1,27,26,0,kv5::Status::Ok});
    need(kv5::get(wire,0,4)==0x3035504f&&kv5::get(wire,4,2)==50&&kv5::decode(wire).layer==26,"new owner/client wire contract");
    kv5::put(wire,0,0x35564b,4);kv5::put(wire,4,5,2);
    refused([&]{kv5::decode(wire);});
    need(ddr_range(64,192)&&ddr_range(0x800000000ULL,192)&&!ddr_range(65,192)&&!ddr_range(0x87fffffc0ULL,128)&&!ddr_range(UINT64_MAX-63,128),"DDR alignment/range/overflow");
    need(allocation(113472)==114688&&allocation(453888)==454656,"shared page-aligned offset-64 client capacities");
    need(allocation(opt50_model::max_x_bytes)==kv5::shared_x_allocation&&allocation(opt50_model::max_y_bytes)==kv5::shared_y_allocation,"Native/client shared page capacities must not drift");
    need(allocation(3968)==4096&&allocation(3969)==8192&&allocation(SIZE_MAX-4223)%4096==0,"page-allocation boundary/maximum");
    refused([]{allocation(0);});refused([]{allocation(SIZE_MAX-4222);});refused([]{allocation(SIZE_MAX);});
    for(size_t payload:{size_t(113472),size_t(453888)}){
        const auto total=allocation(payload);Bytes memory(total);set_guards(memory.data(),payload,total);check_guards(memory.data(),payload,total);
        need(total%4096==0&&guard+payload+guard<total,"page padding exists after 64-byte suffix");
        for(size_t offset:{size_t(0),guard+payload,guard+payload+guard,total-1}){
            memory[offset]=0;refused([&]{check_guards(memory.data(),payload,total);});memory[offset]=0xa5;
        }
        memory[guard]=1;memory[guard+payload-1]=2;check_guards(memory.data(),payload,total);
    }
    for(Execution execution:{Execution::Mixed,Execution::AllDense})for(bool high:{false,true}){
        State s(execution);Fake fake(s,high);s.begin(1);
        for(unsigned i=0;i<49;++i)if(opt50_model::layers[i].pl){
            execute_layer(fake,s,i);const auto l=execution_layer(execution,i);
            need(fake.read(scalar_regs[3])==l.mode,"selected mode must reach actual control register");
            const uint64_t data_address=uint64_t(fake.read(pointer_regs[1]))|(uint64_t(fake.read(pointer_regs[1]+4))<<32);
            const uint64_t meta_address=uint64_t(fake.read(pointer_regs[2]))|(uint64_t(fake.read(pointer_regs[2]+4))<<32);
            need(data_address==fake.address(1)+guard+l.data_offset&&meta_address==fake.address(2)+guard+l.meta_offset,
                 "actual 64-bit pointer registers must use selected packing offsets");
        }
        s.end(1);need(s.execution()==execution&&s.finished&&!s.failed&&!s.active&&s.calls==36&&s.commands==36&&fake.starts==36&&fake.dense==expected_dense(execution)&&fake.sparse_count==expected_sparse(execution),"one shared engine must execute exactly selected-mode 36 calls");
        refused([&]{s.begin(2);});refused([&]{execute_layer(fake,s,0);});
    }
    {State s;Fake fake(s);refused([&]{s.begin(2);});s.begin(1);refused([&]{execute_layer(fake,s,2);});need(s.failed&&s.commands==0,"out-of-order rejects before START");}
    {State s;Fake fake(s);s.begin(1);fake.bad_high=true;refused([&]{execute_layer(fake,s,0);});need(!s.active&&s.failed&&s.commands==0,"bad pointer high rejects before START");}
    {State s;Fake fake(s);s.begin(1);fake.ambiguous_start=true;refused([&]{execute_layer(fake,s,0);});need(s.active&&s.failed&&s.commands==1,"ambiguous START retains active DMA");refused([&]{s.abort(1);});}
    {State s;Fake fake(s);s.begin(1);fake.never_done=true;refused([&]{execute_layer(fake,s,0);});need(s.active&&s.failed,"deadline retains active DMA");}
    {State s;Fake fake(s);s.begin(1);fake.bad_guard=true;refused([&]{execute_layer(fake,s,0);});need(!s.active&&s.failed&&s.calls==0,"guard failure closes graph only after proven DMA completion");}
    {State s;s.begin(1);s.abort(1);need(s.finished&&s.failed,"abort is terminal, never replay");refused([&]{s.begin(1);});}
    {State s;s.begin(1);s.start();need(!s.observe(2,false)&&s.observe(4,true),"COR done latch persists until idle+drain");}
    if(argc==4){
        const auto canonical=read_file(argv[1]),packed=read_file(argv[2]),meta=read_file(argv[3]);
        validate_pack(canonical,packed,meta);std::cout<<"PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS\n";
        for(Execution execution:{Execution::Mixed,Execution::AllDense}){
            auto active=active_data(execution,canonical,packed,meta);validate_active_data(execution,canonical,active,meta);
            need(sha(active)==(execution==Execution::AllDense?"45d95d610314148a5b63eb9807ce18fe78337df40880cc4c2da29a080a81fb68":"73986692f0f1a9f5a4eccb8f6f5522ad896c84e5892b0ae0939ce640106cec4d"),
                 "independently pinned selected-mode active data identity");
            active[0]^=1;refused([&]{validate_active_data(execution,canonical,active,meta);});active[0]^=1;
            if(execution==Execution::Mixed){active.back()=1;refused([&]{validate_active_data(execution,canonical,active,meta);});}
            auto bad=meta;bad[0]^=1;refused([&]{validate_active_data(execution,canonical,active,bad);});
        }
        std::cout<<"FAIR_ACTIVE_DATA_BOTH_MODES_CANONICAL_EQUIVALENCE_PASS\n";
    }
    else need(argc==1,"usage: test_model_core [weights.bin pl_w_data.bin pl_w_meta.bin]");
    std::cout<<"MODEL_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}}
