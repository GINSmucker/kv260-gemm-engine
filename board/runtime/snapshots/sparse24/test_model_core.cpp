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
    unsigned dense_route=0,sparse_route=0,cpu_route=0;
    for(unsigned i=0;i<49;++i) {
        const bool intended_sparse=i<48 && (i%4==2 || i%4==3);
        need(sparse(i)==intended_sparse && opt50_model::layers[i].mode==unsigned(intended_sparse),"all24 MLP layers use Sparse; qkv/proj/head must not inherit old policy");
        if(!opt50_model::layers[i].pl)++cpu_route;
        else if(opt50_model::layers[i].mode)++sparse_route;else ++dense_route;
    }
    need(dense_route==12&&sparse_route==24&&cpu_route==13,"B24 fixed 49-layer D12/S24/CPU13 route");
    validate_metadata_identity(uuid,metadata_sha,8560);
    refused([]{validate_metadata_identity("0245b248-109b-6bf7-73c7-16b629abe9a1",metadata_sha,8560);});
    refused([]{validate_metadata_identity(uuid,std::string(64,'0'),8560);});
    refused([]{validate_metadata_identity(uuid,metadata_sha,8559);});
    {auto bad=opt50_model::layers[0];bad.m=196;refused([&]{validate_layer_identity(0,bad);});}
    {auto bad=opt50_model::layers[2];bad.mode=0;refused([&]{validate_layer_identity(2,bad);});}
    {auto bad=opt50_model::layers[1];bad.pl=true;refused([&]{validate_layer_identity(1,bad);});}
    refused([]{validate_layer_identity(49,opt50_model::layers[0]);});
    for(unsigned code:{4u,8u,12u,9u,13u,14u})need(sparse_positions(code)[0]<sparse_positions(code)[1],"all six legal sparse codes");
    for(unsigned code:{0u,3u,5u,10u,15u,16u})refused([&]{sparse_positions(code);});
    need(pointer_regs==std::array<uint32_t,4>{0x18,0x24,0x30,0x3c}&&scalar_regs==std::array<uint32_t,4>{0x48,0x50,0x58,0x60},"OPT50 eight-argument ABI");
    auto wire=kv5::encode({kv5::Op::Run,1,3,2,0,kv5::Status::Ok});
    need(kv5::get(wire,0,4)==0x3035504f&&kv5::get(wire,4,2)==50&&kv5::decode(wire).layer==2,"new owner/client wire contract");
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
    for(bool high:{false,true}){
        State s;Fake fake(s,high);s.begin(1);
        for(unsigned i=0;i<49;++i)if(opt50_model::layers[i].pl)execute_layer(fake,s,i);
        s.end(1);need(s.finished&&!s.failed&&!s.active&&s.calls==36&&s.commands==36&&fake.starts==36&&fake.dense==12&&fake.sparse_count==24,"one shared engine must execute exactly 12Dense+24Sparse");
        refused([&]{s.begin(2);});refused([&]{execute_layer(fake,s,0);});
    }
    {State s;Fake fake(s);s.begin(1);refused([&]{execute_layer(fake,s,2);});need(s.failed&&s.commands==0,"out-of-order rejects before START");}
    {State s;Fake fake(s);s.begin(1);fake.bad_high=true;refused([&]{execute_layer(fake,s,0);});need(!s.active&&s.failed&&s.commands==0,"bad pointer high rejects before START");}
    {State s;Fake fake(s);s.begin(1);fake.ambiguous_start=true;refused([&]{execute_layer(fake,s,0);});need(s.active&&s.failed&&s.commands==1,"ambiguous START retains active DMA");refused([&]{s.abort(1);});}
    {State s;Fake fake(s);s.begin(1);fake.never_done=true;refused([&]{execute_layer(fake,s,0);});need(s.active&&s.failed,"deadline retains active DMA");}
    {State s;Fake fake(s);s.begin(1);fake.bad_guard=true;refused([&]{execute_layer(fake,s,0);});need(!s.active&&s.failed&&s.calls==0,"guard failure closes graph only after proven DMA completion");}
    {State s;s.begin(1);s.abort(1);need(s.finished&&s.failed,"abort is terminal, never replay");refused([&]{s.begin(1);});}
    {State s;s.begin(1);s.start();need(!s.observe(2,false)&&s.observe(4,true),"COR done latch persists until idle+drain");}
    const auto graph=[](State& state,Fake& fake,uint64_t run,uint64_t now){
        state.begin(run,now);for(unsigned layer=0;layer<49;++layer)if(opt50_model::layers[layer].pl)execute_layer(fake,state,layer);state.end(run);
    };
    {State s;s.configure(50,1000);Fake fake(s,true,true);need(s.can_stop(),"ctorready empty prefix is closeable");
     for(uint64_t run=1;run<=50;++run){graph(s,fake,run,1000+run);need(!s.can_stop()&&!s.info_verified,"END alone is not a closeable/next-BEGIN boundary");fake.check_buffers();s.mark_info_verified(run);need(s.can_stop(),"verified complete prefix closeable");}
     need(s.total_commands==1800&&s.completed_graphs==50&&s.last_verified_run==50&&fake.starts==1800&&fake.dense==600&&fake.sparse_count==1200,"sealed resident 50 graphs exact D12/S24 each");
     refused([&]{s.begin(51,1100);});need(s.failed&&!s.can_stop()&&fake.starts==1800,"no START beyond sealed resident budget");}
    {State s;s.configure(2,100);Fake fake(s,true,true);graph(s,fake,1,101);refused([&]{s.begin(2,102);});need(s.failed&&s.total_commands==36&&!s.can_stop(),"missing INFO permanently rejects next BEGIN");}
    for(uint64_t bad:{uint64_t(1),uint64_t(3)}){State s;s.configure(3,100);Fake fake(s,true,true);graph(s,fake,1,101);s.mark_info_verified(1);refused([&]{s.begin(bad,102);});need(s.failed&&s.total_commands==36,"duplicate/skipped run cannot retry");refused([&]{s.begin(2,103);});}
    {State s;s.configure(2,100);Fake fake(s,true,true);graph(s,fake,1,101);s.mark_info_verified(1);refused([&]{s.mark_info_verified(1);});need(s.failed,"duplicate INFO state commit forbidden; owner idempotent query never repeats state commit");}
    {State s;s.configure(1,100);refused([&]{s.begin(1,99);});need(s.failed&&!s.can_stop(),"future monotonic plan refused");}
    {State s;s.configure(1,100);refused([&]{s.begin(1,100+State::window_ns+1);});need(s.failed,"expired monotonic plan refused");}
    {State s;s.configure(1,100);s.begin(1,100+State::window_ns);need(s.begun&&!s.failed,"900-second BEGIN boundary allowed");}
    {State s;refused([&]{s.configure(51,1);});need(s.failed,"unbounded resident graph count refused");}
    {State s;s.begin(1);refused([&]{s.mark_info_verified(1);});need(s.failed&&!s.can_stop(),"INFO before END cannot free resources");}
    {std::map<std::string,std::string> job;
     for(const auto* name:{"schema","boot_id","plan_token","expected_parent_pid","companion_lock","model_root","weights","weights_sha256","data","data_sha256","meta","meta_sha256","reference","reference_sha256","metadata","metadata_sha256","run_id","expected_PL_starts","source_model_sha256","sample_count","total_START_budget","created_monotonic_ns"})job[name]="placeholder";
     job["schema"]="opt50_b24_resident_native_job_v4";job["boot_id"]="11111111-2222-3333-4444-555555555555";job["plan_token"]="012345678abc";job["source_model_sha256"]=source_model_sha;job["run_id"]="1";job["expected_PL_starts"]="36";job["sample_count"]="50";job["total_START_budget"]="1800";job["created_monotonic_ns"]="1000";
     need(opt50_gate::resident_scope(job,1000).total_START_budget==1800,"22-field sealed resident budget accepted without hardware");
     for(const auto& change:std::vector<std::pair<std::string,std::string>>{{"sample_count","0"},{"sample_count","51"},{"sample_count","01"},{"total_START_budget","1801"},{"run_id","2"},{"expected_PL_starts","37"},{"schema","opt50_b24_model_native_job_v1"},{"created_monotonic_ns","0"}}){auto bad=job;bad[change.first]=change.second;refused([&]{opt50_gate::resident_scope(bad,1000);});}
     {auto bad=job;bad["extra"]="field";refused([&]{opt50_gate::resident_scope(bad,1000);});}
     refused([&]{opt50_gate::resident_scope(job,999);});refused([&]{opt50_gate::resident_scope(job,1001+State::window_ns);});
    }
    if(argc==4){
        const auto weights=read_file(argv[1]),data=read_file(argv[2]),meta=read_file(argv[3]);
        validate_pack(weights,data,meta);
        auto bad_data=data;bad_data[0]^=1;refused([&]{validate_pack(weights,bad_data,meta);});
        auto bad_meta=meta;bad_meta[64]=0;refused([&]{validate_pack(weights,data,bad_meta);});
        auto old_weights=weights;old_weights[0]^=1;refused([&]{validate_pack(old_weights,data,meta);});
        std::cout<<"PACK_HASH_AND_ALL49_CANONICAL_WQ_EQUIVALENCE_PASS\n";
    }
    else need(argc==1,"usage: test_model_core [weights.bin pl_w_data.bin pl_w_meta.bin]");
    std::cout<<"RESIDENT_NATIVE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\nMODEL_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}}
