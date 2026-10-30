#pragma once
// Fixed memory only during Runner.run. Serialization is requested by INFO AFTER
// the graph has returned and never from BEGIN/END/execute/poll.
#include "model_core.hpp"
#include <cstring>
#include <sstream>

namespace opt50_performance {
enum class Phase { Begin, Active, StartIntent, StartReturned, Complete, Verified, End };
inline const char* name(Phase value) {
    switch(value) {
    case Phase::Begin:return "MODEL_BEGIN";
    case Phase::Active:return "DMA_ACTIVE_BEFORE_START";
    case Phase::StartIntent:return "START_WRITE_INTENT";
    case Phase::StartReturned:return "START_WRITE_RETURNED";
    case Phase::Complete:return "DMA_COMPLETION_PROVEN";
    case Phase::Verified:return "LAYER_COMPLETION_VERIFIED";
    case Phase::End:return "MODEL_END_COMPLETED";
    }
    throw std::runtime_error("unknown performance phase");
}
struct Event {Phase phase{};unsigned layer=49,command=0,mode=0;uint64_t ns=0;};
// These hooks are called at the actual owner-side cache/guard/hash operations.
// No successful timed graph may perform them. INFO records a separate, fresh
// post-graph verification; it does not claim per-layer input immutability.
struct CheckAudit {
    uint64_t graph_owner_sync_calls=0,graph_owner_sync_bytes=0;
    uint64_t per_layer_input_sha_checks=0,per_layer_guard_checks=0,per_layer_y_poison_writes=0;
    uint64_t initial_weight_to_device_sync_calls=0,initial_weight_to_device_sync_bytes=0;
    uint64_t postrun_owner_sync_calls=0,postrun_owner_sync_bytes=0;
    uint64_t postrun_guard_checks=0,postrun_static_weight_sha_checks=0;
    uint64_t postrun_checks_started_ns=0,postrun_checks_completed_ns=0;
    bool postrun_active=false,postrun_verified=false;
    void reset_for_graph() {
        opt50_core::need(!postrun_active,"cannot reset an active post-graph audit");
        const auto count=initial_weight_to_device_sync_calls,bytes=initial_weight_to_device_sync_bytes;
        *this=CheckAudit{};initial_weight_to_device_sync_calls=count;initial_weight_to_device_sync_bytes=bytes;
    }
    void sync(bool hot,size_t bytes,bool initial_weight) {
        if(hot){++graph_owner_sync_calls;graph_owner_sync_bytes+=bytes;}
        opt50_core::need(!hot,"v2 owner must not synchronize BOs inside Runner.run");
        if(initial_weight){++initial_weight_to_device_sync_calls;initial_weight_to_device_sync_bytes+=bytes;}
        if(postrun_active){++postrun_owner_sync_calls;postrun_owner_sync_bytes+=bytes;}
    }
    void guards(bool hot,size_t count) {
        if(hot){per_layer_guard_checks+=count;}
        opt50_core::need(!hot,"v2 owner must not scan BO guards inside Runner.run");
        if(postrun_active){postrun_guard_checks+=count;}
    }
    void static_hashes(bool hot,size_t count) {
        opt50_core::need(!hot,"v2 owner must not hash static weights inside Runner.run");
        if(postrun_active){postrun_static_weight_sha_checks+=count;}
    }
    void begin_postrun(uint64_t ns) {
        opt50_core::need(!postrun_active&&!postrun_verified&&ns>0,"one post-graph INFO check only");
        postrun_active=true;postrun_checks_started_ns=ns;
    }
    void finish_postrun(uint64_t ns) {
        opt50_core::need(postrun_active&&ns>postrun_checks_started_ns,"post-graph checks completion order");
        postrun_active=false;postrun_checks_completed_ns=ns;postrun_verified=true;validate();
    }
    void validate()const {
        using opt50_core::need;
        uint64_t allocation_bytes=0;for(auto n:opt50_core::payload_capacities())allocation_bytes+=opt50_core::allocation(n);
        need(graph_owner_sync_calls==0&&graph_owner_sync_bytes==0&&per_layer_input_sha_checks==0&&
             per_layer_guard_checks==0&&per_layer_y_poison_writes==0,"no owner-side payload work inside measured graph");
        need(initial_weight_to_device_sync_calls==2&&initial_weight_to_device_sync_bytes==
             opt50_core::allocation(opt50_model::data_bytes)+opt50_core::allocation(opt50_model::meta_bytes),
             "static Wdata/Wmeta initialized and synchronized exactly once");
        need(postrun_verified&&!postrun_active&&postrun_owner_sync_calls==4&&postrun_owner_sync_bytes==allocation_bytes&&
             postrun_guard_checks==4&&postrun_static_weight_sha_checks==2&&postrun_checks_started_ns>0&&
             postrun_checks_completed_ns>postrun_checks_started_ns,"fresh four-BO post-graph verification required");
    }
};
class Trace {
    std::array<Event,256> events_{};size_t used_=0;uint64_t operations_=0;
public:
    void reset(){events_={};used_=0;operations_=0;}
    void note(const char* phase,unsigned layer,unsigned command,uint64_t ns) {
        ++operations_;
        Phase selected{};bool important=true;
        if(std::strcmp(phase,"MODEL_BEGIN")==0)selected=Phase::Begin;
        else if(std::strcmp(phase,"DMA_ACTIVE_BEFORE_START")==0)selected=Phase::Active;
        else if(std::strcmp(phase,"START_WRITE_INTENT")==0)selected=Phase::StartIntent;
        else if(std::strcmp(phase,"START_WRITE_RETURNED")==0)selected=Phase::StartReturned;
        else if(std::strcmp(phase,"DMA_COMPLETION_PROVEN")==0)selected=Phase::Complete;
        else if(std::strcmp(phase,"LAYER_COMPLETION_VERIFIED")==0)selected=Phase::Verified;
        else if(std::strcmp(phase,"MODEL_END_COMPLETED")==0)selected=Phase::End;
        else important=false;
        if(!important){return;}
        opt50_core::need(used_<events_.size()&&(!used_||ns>events_[used_-1].ns),"bounded monotonic in-memory performance trace");
        opt50_core::need(layer<49||layer==49,"performance layer bound");
        events_[used_++]={selected,layer,command,layer<49?opt50_model::layers[layer].mode:0,ns};
    }
    void validate(const opt50_core::State& state)const {
        using opt50_core::need;
        need(state.begun&&state.finished&&!state.failed&&!state.active&&state.run>=1&&state.run<=state.max_graphs&&state.calls==36&&
             state.commands==36&&state.next==49,"one complete graph required before INFO publication");
        need(state.total_commands==36*state.run&&state.completed_graphs==state.run,
             "resident cumulative graph/START accounting differs");
        need(used_==182&&events_[0].phase==Phase::Begin&&events_[0].layer==49&&events_[0].command==0&&
             events_[181].phase==Phase::End&&events_[181].layer==49&&events_[181].command==36,
             "single BEGIN/END in-memory proof");
        const std::array<Phase,5> expected={Phase::Active,Phase::StartIntent,Phase::StartReturned,Phase::Complete,Phase::Verified};
        unsigned layer=kv5::next_pl(0);
        for(unsigned call=1;call<=36;++call) {
            for(size_t phase=0;phase<5;++phase) {
                const auto& event=events_[1+5*(call-1)+phase];
                need(event.phase==expected[phase]&&event.layer==layer&&event.command==call&&
                     event.mode==opt50_model::layers[layer].mode,"ordered 12Dense/24Sparse START/completion proof; no per-layer payload scan");
            }
            layer=kv5::next_pl(layer+1);
        }
        need(layer==49,"exact PL route closure");
    }
    std::string json(const opt50_core::State& state,const CheckAudit& audit,const std::string& boot_id)const {
        validate(state);audit.validate();opt50_core::need(opt50_core::canonical_boot(boot_id),"receipt requires the validated current boot UUID");
        opt50_core::need(!state.info_verified&&state.last_verified_run+1==state.run,"new INFO publication must follow exactly the previously verified run");
        opt50_core::need(audit.postrun_checks_started_ns>events_[181].ns,"INFO buffer checks must occur after MODEL_END_COMPLETED");
        std::ostringstream out;
        out<<"{\"schema\":\"opt50_performance_native_resident_v4\",\"profile\":\"dense36-resident-low-overhead-v4\","
           <<"\"boot_id\":\""<<boot_id<<"\",\"source_model_sha256\":\""<<opt50_core::source_model_sha<<"\",\"run_id\":"<<state.run<<",\"START_commands_issued\":36,"
           <<"\"max_graphs\":"<<state.max_graphs<<",\"total_START_budget\":"<<state.max_graphs*36
           <<",\"lifetime_START_commands\":"<<state.total_commands<<",\"lifetime_completed_graphs\":"<<state.completed_graphs
           <<",\"prior_verified_run\":"<<state.last_verified_run<<",\"last_verified_run\":"<<state.run<<','
           <<"\"completed_PL_calls\":36,\"PL_dense\":36,\"PL_sparse\":0,\"device_closed\":false,"
           <<"\"native_END_completed\":true,\"guards_verified_after_graph\":true,\"static_weights_verified_after_graph\":true,"
           <<"\"postrun_buffer_checks_verified\":true,"
           <<"\"graph_owner_sync_calls\":"<<audit.graph_owner_sync_calls<<",\"graph_owner_sync_bytes\":"<<audit.graph_owner_sync_bytes<<','
           <<"\"per_layer_input_sha_checks\":"<<audit.per_layer_input_sha_checks<<",\"per_layer_guard_checks\":"<<audit.per_layer_guard_checks<<','
           <<"\"per_layer_y_poison_writes\":"<<audit.per_layer_y_poison_writes<<','
           <<"\"initial_weight_to_device_sync_calls\":"<<audit.initial_weight_to_device_sync_calls<<','
           <<"\"initial_weight_to_device_sync_bytes\":"<<audit.initial_weight_to_device_sync_bytes<<','
           <<"\"initial_weight_sync_scope\":\"owner_lifetime\","
           <<"\"postrun_owner_sync_calls\":"<<audit.postrun_owner_sync_calls<<",\"postrun_owner_sync_bytes\":"<<audit.postrun_owner_sync_bytes<<','
           <<"\"postrun_guard_checks\":"<<audit.postrun_guard_checks<<",\"postrun_static_weight_sha_checks\":"<<audit.postrun_static_weight_sha_checks<<','
           <<"\"postrun_checks_started_ns\":"<<audit.postrun_checks_started_ns<<",\"postrun_checks_completed_ns\":"<<audit.postrun_checks_completed_ns<<','
           <<"\"hot_path_fsync_count\":0,\"hot_path_disk_events\":0,\"poll_delay_us\":50,\"deadline_seconds\":120,"
           <<"\"ram_event_capacity\":256,\"hot_operations_count\":"<<operations_<<",\"ram_events\":[";
        for(size_t i=0;i<used_;++i) {
            const auto& event=events_[i];if(i){out<<',';}
            out<<"{\"phase\":\""<<name(event.phase)<<"\",\"layer\":"<<event.layer<<",\"command\":"<<event.command
               <<",\"mode\":"<<event.mode<<",\"monotonic_ns\":"<<event.ns<<'}';
        }
        out<<"]}\n";return out.str();
    }
};
} // namespace opt50_performance
