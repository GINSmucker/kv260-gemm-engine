// Versioned request/journal/W storage adapter for the proposed production V2A.
// This file supplies no XRT execution or production ready declaration.
#pragma once
#include "stream_v2a_recovery.h"

namespace kv260_stream_v2a {
inline Json request_metadata_header(const TrustedContext& context,const Json& request) {
    request_fields(request,context);
    return {{"schema","kv260_gemm_stream_v2a"},{"wire_version",2},{"type","REQUEST"},
        {"identity",context.identity},{"body_bytes",request.at("X_bytes")},{"request",request}};
}
inline Bytes compact_json_bytes(const Json& value) {
    const auto text=value.dump(-1,' ',true,Json::error_handler_t::strict);
    lexical_bounds(text);return Bytes(text.begin(),text.end());
}
inline void submitted_journal_fields(const Json& journal,const Json& request,const TrustedContext& context,
        const ExecutionPeer& original) {
    exact_keys(journal,{"schema","state","request","identity","original_client_pid",
        "original_client_birth_ticks","prepared_monotonic_ns","submitted_monotonic_ns"});
    ledger_require(journal.at("schema")=="kv260_bridge_journal_v2a" && journal.at("state")=="SUBMITTED" &&
        journal.at("request")==request && journal.at("identity")==context.identity &&
        uint_value(journal.at("original_client_pid"),UINT64_MAX)==original.pid &&
        uint_value(journal.at("original_client_birth_ticks"),UINT64_MAX)==original.birth_ticks,
        "BAD_REQUEST","actual SUBMITTED journal binding/version/state");
    const auto prepared=uint_value(journal.at("prepared_monotonic_ns"),UINT64_MAX);
    const auto submitted=uint_value(journal.at("submitted_monotonic_ns"),UINT64_MAX);
    ledger_require(prepared>0 && submitted>=prepared && submitted<=monotonic_ns(),
        "BAD_REQUEST","actual SUBMITTED journal clock ordering");
}
struct BeforeStartStorage {Bytes request_metadata,weight;std::string journal_digest;};
inline BeforeStartStorage load_before_start_storage(const TrustedContext& context,const Json& request,
        const ExecutionPeer& original,const std::string& retained_request_digest,const BodyDigest& digest) {
    request_fields(request,context);sha_string(Json(retained_request_digest));
    auto task=anchored_task_directory(context);
    auto requests=private_child_directory(task.get(),"requests");
    auto execution=private_child_directory(requests.get(),request.at("execution_id").get<std::string>());
    auto weights=private_child_directory(task.get(),"weights");
    auto metadata=read_private_file(execution.get(),"request.json",0600,HEADER_LIMIT);
    auto journal=read_private_file(execution.get(),"journal.json",0600,HEADER_LIMIT);
    const auto wanted=request_metadata_header(context,request);
    ledger_require(actual_json_file(metadata)==wanted && digest(metadata.bytes)==retained_request_digest,
        "BAD_HASH","actual full request metadata mismatch");
    submitted_journal_fields(actual_json_file(journal),request,context,original);
    const auto filename="l"+std::to_string(uint_value(request.at("layer_index"),48))+"_"+
        request.at("W_sha256").get<std::string>()+".bin";
    auto weight=read_private_file(weights.get(),filename,0400,192000);
    ledger_require(weight.bytes.size()==uint_value(request.at("W_bytes"),UINT32_MAX) &&
        digest(weight.bytes)==request.at("W_sha256"),"BAD_HASH","actual registered W length/full digest mismatch");
    struct stat native{};errno=0;
    ledger_require(::fstatat(execution.get(),"native.json",&native,AT_SYMLINK_NOFOLLOW)!=0 && errno==ENOENT,
        "BAD_REQUEST","native record already exists or status unavailable");
    return {std::move(metadata.bytes),std::move(weight.bytes),digest(journal.bytes)};
}
inline std::string write_native_record(const TrustedContext& context,const Json& request,const Json& native,
        const BodyDigest& digest) {
    request_fields(request,context);native_fields(native,request,native.at("Y_sha256"),
        uint_value(native.at("starts"),UINT64_MAX),context);
    auto task=anchored_task_directory(context);auto requests=private_child_directory(task.get(),"requests");
    auto execution=private_child_directory(requests.get(),request.at("execution_id").get<std::string>());
    PrivateFD file(::openat(execution.get(),"native.json",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));
    ledger_require(file.get()>=0,"OWNER_FAULT","unique native record creation failed");
    const auto bytes=compact_json_bytes(native);std::size_t offset=0;
    while(offset<bytes.size()) {
        const auto n=::write(file.get(),bytes.data()+offset,bytes.size()-offset);
        if(n<0 && errno==EINTR)continue;
        ledger_require(n>0,"OWNER_FAULT","native record full write failed");offset+=static_cast<std::size_t>(n);
    }
    // As in the parent, this is a completed write, not a persistence claim.
    // Normal C journal barriers and sealed recovery fsync remain independent.
    auto actual=read_private_file(execution.get(),"native.json",0600,HEADER_LIMIT);
    ledger_require(actual.bytes==bytes,"OWNER_FAULT","native record readback changed");return digest(actual.bytes);
}
} // namespace kv260_stream_v2a
