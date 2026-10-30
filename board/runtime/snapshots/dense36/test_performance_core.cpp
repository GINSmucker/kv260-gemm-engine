#include "source/performance_trace.hpp"
#include "source/native_gate.hpp"
#include <iostream>
using namespace opt50_core;
const std::string test_boot="11111111-2222-3333-4444-555555555555";
template<class F>void refused(F f){bool bad=false;try{f();}catch(const std::exception&){bad=true;}need(bad,"performance negative case not rejected");}
int main(int argc,char** argv){try{
  need(argc==1 || (argc==2 && std::string(argv[1])=="--emit-json"),"usage: test_performance_core [--emit-json]");
  std::string emitted;
  for(bool high:{false,true}){
    State state;opt50_performance::Trace trace;uint64_t ns=1;
    state.configure(2,1);
    opt50_performance::CheckAudit lifetime_audit;
    lifetime_audit.sync(false,allocation(opt50_model::data_bytes),true);
    lifetime_audit.sync(false,allocation(opt50_model::meta_bytes),true);
    Fake fake(state,high,true);
    for(uint64_t run=1;run<=2;++run){
    trace.reset();lifetime_audit.reset_for_graph();
    state.begin(run,run);trace.note("MODEL_BEGIN",49,0,ns++);
    for(unsigned layer=0;layer<49;++layer)if(opt50_model::layers[layer].pl){
        const unsigned call=state.commands+1;
        trace.note("DMA_ACTIVE_BEFORE_START",layer,call,ns++);
        trace.note("START_WRITE_INTENT",layer,call,ns++);
        trace.note("START_WRITE_RETURNED",layer,call,ns++);
        execute_layer(fake,state,layer);
        trace.note("DMA_COMPLETION_PROVEN",layer,call,ns++);
        trace.note("LAYER_COMPLETION_VERIFIED",layer,call,ns++);
    }
    state.end(run);trace.note("MODEL_END_COMPLETED",49,36,ns++);
    need(fake.guard_scans==4*(run-1),"resident fake graph must not perform eager owner guard scans");
    fake.check_buffers();need(fake.guard_scans==4*run,"post-graph four-buffer guard scan each run");
    lifetime_audit.begin_postrun(ns++);
    for(auto n:payload_capacities())lifetime_audit.sync(false,allocation(n),false);
    lifetime_audit.guards(false,4);lifetime_audit.static_hashes(false,2);lifetime_audit.finish_postrun(ns++);
    auto audit=lifetime_audit;
    trace.validate(state);const auto raw=trace.json(state,audit,test_boot);emitted=raw;
    refused([&]{trace.json(state,audit,"not-a-boot");});
    need(raw.find("\"PL_dense\":36,\"PL_sparse\":0")!=std::string::npos&&
         raw.find(source_model_sha)!=std::string::npos&&raw.find(test_boot)!=std::string::npos,
         "native receipt binds B24 route, source model and plan boot");
    need(raw.find("\"hot_path_fsync_count\":0")!=std::string::npos&&raw.find("\"poll_delay_us\":50")!=std::string::npos&&
         raw.find("\"deadline_seconds\":120")!=std::string::npos&&fake.starts==36*run&&fake.dense==36*run&&fake.sparse_count==0*run,
         "same shared engine/single 36-call low-IO profile");
    need(raw.find("\"profile\":\"dense36-resident-low-overhead-v4\"")!=std::string::npos&&
         raw.find("\"postrun_owner_sync_bytes\":4558848")!=std::string::npos&&
         raw.find("\"initial_weight_to_device_sync_bytes\":3989504")!=std::string::npos&&
         raw.find("guards_verified_at_END")==std::string::npos&&raw.find("LAYER_DMA_AND_GUARDS_VERIFIED")==std::string::npos,
         "honest post-graph counts/profile; no false per-layer or END guard claim");
    {auto bad=state;bad.active=true;refused([&]{trace.json(bad,audit,test_boot);});bad=state;bad.failed=true;refused([&]{trace.json(bad,audit,test_boot);});}
    {auto bad=audit;bad.postrun_verified=false;refused([&]{trace.json(state,bad,test_boot);});bad=audit;bad.postrun_owner_sync_calls=3;refused([&]{trace.json(state,bad,test_boot);});}
    {auto bad=audit;bad.postrun_checks_started_ns=1;refused([&]{trace.json(state,bad,test_boot);});bad=audit;bad.initial_weight_to_device_sync_calls=3;refused([&]{trace.json(state,bad,test_boot);});}
    {auto bad=audit;bad.per_layer_input_sha_checks=1;refused([&]{trace.json(state,bad,test_boot);});bad=audit;bad.per_layer_y_poison_writes=1;refused([&]{trace.json(state,bad,test_boot);});}
    {opt50_performance::Trace bad;bad.note("MODEL_BEGIN",49,0,1);refused([&]{bad.validate(state);});refused([&]{bad.note("MODEL_END_COMPLETED",49,36,1);});}
    need(raw.find("\"run_id\":"+std::to_string(run)+",")!=std::string::npos&&
         raw.find("\"lifetime_START_commands\":"+std::to_string(36*run)+",")!=std::string::npos&&
         raw.find("\"prior_verified_run\":"+std::to_string(run-1)+",")!=std::string::npos,
         "per-run receipt includes monotonic resident lifetime proof");
    state.mark_info_verified(run);
    }
    need(lifetime_audit.initial_weight_to_device_sync_calls==2&&state.last_verified_run==2,
         "static weights synchronized only once and INFO verified twice");
  }
    {opt50_performance::Trace bad;for(unsigned i=1;i<=256;++i)bad.note("MODEL_BEGIN",49,0,i);refused([&]{bad.note("MODEL_BEGIN",49,0,257);});}
    {opt50_performance::Trace bad;refused([&]{bad.note("MODEL_BEGIN",50,0,1);});}
    {State pending;Fake f(pending);pending.begin(1);f.never_done=true;refused([&]{execute_layer(f,pending,0);});need(pending.active&&pending.failed&&pending.commands==1,"monotonic timeout retains pending DMA; no retry");}
    {State pending;Fake f(pending);pending.begin(1);f.ambiguous_start=true;refused([&]{execute_layer(f,pending,0);});need(pending.active&&pending.failed,"ambiguous START must retain");}
    // Deferring a canary scan does not turn a later failed post-graph check into
    // a successful receipt. This checks the changed timing scope explicitly.
    {State pending;Fake f(pending,true,true);pending.begin(1);f.bad_guard=true;
     for(unsigned layer=0;layer<49;++layer)if(opt50_model::layers[layer].pl)execute_layer(f,pending,layer);
     pending.end(1);need(pending.calls==36&&!pending.active&&f.guard_scans==0,"only completion was committed inside graph");
     refused([&]{f.check_buffers();});}
    {opt50_performance::CheckAudit a;refused([&]{a.sync(true,114688,false);});need(a.graph_owner_sync_calls==1,"hot sync attempt is counted and rejected");}
    {opt50_performance::CheckAudit a;refused([&]{a.guards(true,4);});need(a.per_layer_guard_checks==4,"hot guard attempt is counted and rejected");}
    {opt50_performance::CheckAudit a;refused([&]{a.static_hashes(true,2);});refused([&]{a.validate();});}
    // A DONE pulse observed before drain must stay latched across later reads.
    {State pending;pending.begin(1);pending.start();need(!pending.observe(2,false)&&pending.observe(4,true),"COR done latch preserved");}
    if(argc==2)std::cout<<emitted;
    else std::cout<<"PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n";
    return 0;
}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 2;}}
