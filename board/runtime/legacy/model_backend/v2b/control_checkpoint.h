// RW03: live owner facts at submission/completion/consumption boundaries.
#pragma once
#include "protocol/stream_v2b_semantics.h"
namespace kv260_stream_v2b {
inline void control_checkpoint(const Json& actual,const Json& admitted_bo,
        const TrustedContext& context,std::uint64_t starts,const Json* completed=nullptr) {
    exact_keys(actual,{"pid","uuid","tag0","tag1","starts","active","done_latched","safe","faulted","bo","last"});
    require(uint_value(actual.at("pid"),UINT64_MAX)==uint_value(context.identity.at("owner_pid"),UINT64_MAX),"checkpoint owner PID");
    for(const char* k:{"uuid","tag0","tag1"})require(actual.at(k)==context.identity.at(k),"checkpoint actual hardware identity");
    require(actual.at("bo")==admitted_bo,"checkpoint actual retained BO changed");
    require(uint_value(actual.at("starts"),UINT64_MAX)==starts,"checkpoint actual start counter changed");
    require(boolean(actual.at("safe")) && !boolean(actual.at("active")) && !boolean(actual.at("faulted")),"checkpoint actual active/unsafe/faulted");
    boolean(actual.at("done_latched"));
    if(completed){
        native_observation_fields(*completed,context);
        require(boolean(actual.at("done_latched")) && actual.at("last")==*completed &&
            uint_value(completed->at("starts"),UINT64_MAX)==starts,"checkpoint actual full completed result changed");
    }
}
} // namespace kv260_stream_v2b
