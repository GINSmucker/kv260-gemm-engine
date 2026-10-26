#include "source/performance_trace.hpp"
#include "source/native_gate.hpp"
#include <iostream>
using namespace opt50_core;
template<class F>void refused(F f){bool bad=false;try{f();}catch(const std::exception&){bad=true;}need(bad,"performance negative case not rejected");}
opt50_performance::CheckAudit checked_audit(uint64_t start) {
    opt50_performance::CheckAudit audit;
    audit.sync(false,allocation(fair_data_bytes),true);
    audit.sync(false,allocation(opt50_model::meta_bytes),true);
    audit.begin_postrun(start);
    for(auto n:payload_capacities())audit.sync(false,allocation(n),false);
    audit.guards(false,4);audit.static_hashes(false,2);audit.finish_postrun(start+1);
    return audit;
}
int main(){try{
  for(Execution execution:{Execution::Mixed,Execution::AllDense})for(bool high:{false,true}){
    State state(execution);opt50_performance::Trace trace(execution);uint64_t ns=1;
    state.begin(1);trace.note("MODEL_BEGIN",49,0,ns++);
    Fake fake(state,high,true);
    for(unsigned layer=0;layer<49;++layer)if(opt50_model::layers[layer].pl){
        const unsigned call=state.commands+1;
        trace.note("DMA_ACTIVE_BEFORE_START",layer,call,ns++);
        trace.note("START_WRITE_INTENT",layer,call,ns++);
        trace.note("START_WRITE_RETURNED",layer,call,ns++);
        execute_layer(fake,state,layer);
        trace.note("DMA_COMPLETION_PROVEN",layer,call,ns++);
        trace.note("LAYER_COMPLETION_VERIFIED",layer,call,ns++);
    }
    state.end(1);trace.note("MODEL_END_COMPLETED",49,36,ns++);
    need(fake.guard_scans==0,"v2 fake graph must not perform eager owner guard scans");
    fake.check_buffers();need(fake.guard_scans==4,"post-graph four-buffer guard scan");
    auto audit=checked_audit(ns++);
    const std::string active_sha=execution==Execution::AllDense?"45d95d610314148a5b63eb9807ce18fe78337df40880cc4c2da29a080a81fb68":"73986692f0f1a9f5a4eccb8f6f5522ad896c84e5892b0ae0939ce640106cec4d";
    const auto json=[&](const State& s,const opt50_performance::CheckAudit& a){return trace.json(s,a,active_sha,opt50_model::meta_sha);};
    trace.validate(state);const auto raw=json(state,audit);
    need(raw.find("\"hot_path_fsync_count\":0")!=std::string::npos&&raw.find("\"poll_delay_us\":50")!=std::string::npos&&
         raw.find("\"deadline_seconds\":120")!=std::string::npos&&fake.starts==36&&fake.dense==expected_dense(execution)&&fake.sparse_count==expected_sparse(execution),
         "same shared engine/single 36-call low-IO profile");
    need(raw.find("\"schema\":\"opt50_fair_native_v1\"")!=std::string::npos&&
         raw.find("\"profile\":\"same-weights-fair-single-shot-v1\"")!=std::string::npos&&
         raw.find(std::string("\"execution_mode\":\"")+execution_name(execution)+"\"")!=std::string::npos&&
         raw.find("\"active_data_capacity_bytes\":3981312")!=std::string::npos&&
         raw.find(std::string("\"active_data_sha256\":\"")+active_sha+"\"")!=std::string::npos&&
         raw.find("\"postrun_owner_sync_bytes\":4722688")!=std::string::npos&&
         raw.find("\"initial_weight_to_device_sync_bytes\":4153344")!=std::string::npos&&
         raw.find("guards_verified_at_END")==std::string::npos&&raw.find("LAYER_DMA_AND_GUARDS_VERIFIED")==std::string::npos,
         "honest post-graph counts/profile; no false per-layer or END guard claim");
    {auto bad=state;bad.active=true;refused([&]{json(bad,audit);});bad=state;bad.failed=true;refused([&]{json(bad,audit);});}
    {auto bad=audit;bad.postrun_verified=false;refused([&]{json(state,bad);});bad=audit;bad.postrun_owner_sync_calls=3;refused([&]{json(state,bad);});}
    {auto bad=audit;bad.postrun_checks_started_ns=1;refused([&]{json(state,bad);});bad=audit;bad.initial_weight_to_device_sync_calls=3;refused([&]{json(state,bad);});}
    {auto bad=audit;bad.per_layer_input_sha_checks=1;refused([&]{json(state,bad);});bad=audit;bad.per_layer_y_poison_writes=1;refused([&]{json(state,bad);});}
    refused([&]{trace.json(state,audit,"not-a-sha",opt50_model::meta_sha);});
    refused([&]{trace.json(state,audit,active_sha,active_sha);});
    {State wrong(execution==Execution::Mixed?Execution::AllDense:Execution::Mixed);
     wrong.begun=true;wrong.finished=true;wrong.run=1;wrong.calls=36;wrong.commands=36;wrong.next=49;
     refused([&]{json(wrong,audit);});}
    {opt50_performance::Trace bad;bad.note("MODEL_BEGIN",49,0,1);refused([&]{bad.validate(state);});refused([&]{bad.note("MODEL_END_COMPLETED",49,36,1);});}
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
    std::cout<<"PERFORMANCE_CORE_SELF_TEST_PASS_NO_HARDWARE_ACCESS\n";return 0;
}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 2;}}
