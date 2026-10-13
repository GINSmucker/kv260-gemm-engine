// Proposed serial production dispatcher. Source only; no deployable entry point.
// Execute, info and safety adapters must come from the actual bound kernel core.
#pragma once
#include "../stream_v2a_admission.h"
#include "../stream_v2a_storage.h"

namespace kv260_stream_v2a {
using KernelExecute=std::function<std::pair<Bytes,Json>(const Json&,const Bytes&,const Bytes&,
    const std::function<void()>&,const std::function<void(std::uint64_t)>&)>;
using KernelInfo=std::function<Json()>;
class SerialSession {
    TrustedContext context_;
    BodyDigest digest_;
    KernelExecute execute_;
    KernelInfo kernel_info_;
    SafeCheck safe_;
    ExecutionLedger ledger_;
    Json current_=nullptr;
    Json envelope(const char* type) const {
        return {{"schema","kv260_gemm_stream_v2a"},{"wire_version",2},{"type",type},
            {"identity",context_.identity},{"body_bytes",0}};
    }
    Frame error(const std::string& code,const std::string& text,const Json& request=nullptr) const {
        auto header=envelope("ERROR");header["error_code"]=code;header["error_text"]=ascii_error(text);
        header["request"]=request;header["state"]=nullptr;header["starts_before"]=nullptr;header["starts_after"]=nullptr;
        if(!request.is_null()) {
            const TerminalRecord* record=nullptr;
            try{record=ledger_.query(request);}catch(...){
                // A valid frame can still alter the metadata of an old ID.
                // Do not reflect that altered object as the trusted record.
                header["request"]=nullptr;message_fields(header,context_);return {std::move(header),{}};
            }
            header["starts_before"]=request.at("starts_before");
            header["state"]=record?Json(state_text(record->state)):Json("NOT_FOUND");
            if(record && (record->state==ExecutionState::RejectedBeforeStart || record->state==ExecutionState::FailedSafe ||
                record->state==ExecutionState::CompletedUnacked || record->state==ExecutionState::AckedReleased))
                header["starts_after"]=record->starts_after;
        }
        message_fields(header,context_);return {std::move(header),{}};
    }
    void checked_request(const Json& request,const TerminalRecord& record) {
        auto task=anchored_task_directory(context_);auto requests=private_child_directory(task.get(),"requests");
        auto execution=private_child_directory(requests.get(),request.at("execution_id").get<std::string>());
        auto metadata=read_private_file(execution.get(),"request.json",0600,HEADER_LIMIT);
        ledger_require(actual_json_file(metadata)==request_metadata_header(context_,request) &&
            digest_(metadata.bytes)==record.request_file_digest,"OWNER_FAULT","retained request storage changed");
    }
    Json checked_native(const Json& request,const TerminalRecord& record) {
        checked_request(request,record);
        auto task=anchored_task_directory(context_);auto requests=private_child_directory(task.get(),"requests");
        auto execution=private_child_directory(requests.get(),request.at("execution_id").get<std::string>());
        auto native=read_private_file(execution.get(),"native.json",0600,HEADER_LIMIT);
        ledger_require(digest_(native.bytes)==record.native_file_digest,"OWNER_FAULT","retained native storage changed");
        auto value=actual_json_file(native);native_fields(value,request,record.Y_digest,record.starts_after,context_);return value;
    }
    Frame query(const Json& request) {
        const auto* record=ledger_.query(request);
        if(record) {
            try{checked_request(request,*record);}catch(...){ledger_.storage_fault();throw LedgerError("OWNER_FAULT","retained request storage changed or unavailable");}
        }
        if(record && record->state==ExecutionState::CompletedUnacked) {
            try{checked_native(request,*record);}catch(...){ledger_.storage_fault();throw LedgerError("OWNER_FAULT","retained completion storage changed or unavailable");}
            return ledger_.pending_result(request);
        }
        auto header=envelope("QUERY_RESULT");header["request"]=request;
        header["state"]=record?Json(state_text(record->state)):Json("NOT_FOUND");
        header["starts_after"]=nullptr;header["Y_sha256"]=nullptr;header["native"]=nullptr;
        if(record && (record->state==ExecutionState::RejectedBeforeStart || record->state==ExecutionState::FailedSafe))
            header["starts_after"]=record->starts_after;
        if(record && record->state==ExecutionState::AckedReleased) {
            try{header["native"]=checked_native(request,*record);}catch(...){ledger_.storage_fault();throw LedgerError("OWNER_FAULT","retained released storage changed or unavailable");}
            header["starts_after"]=record->starts_after;header["Y_sha256"]=record->Y_digest;
        }
        message_fields(header,context_);return {std::move(header),{}};
    }
public:
    SerialSession(TrustedContext context,BodyDigest digest,KernelExecute execute,KernelInfo info,SafeCheck safe,
            std::uint64_t actual_starts):context_(std::move(context)),digest_(std::move(digest)),
        execute_(std::move(execute)),kernel_info_(std::move(info)),safe_(std::move(safe)),ledger_(context_,digest_,actual_starts) {
        require(bool(execute_) && bool(kernel_info_) && bool(safe_),"actual kernel adapters required");
        trusted_context(context_);
    }
    Frame info() {
        if(ledger_.faulted())return error("OWNER_FAULT","retained storage observation failed");
        auto actual=kernel_info_();
        if(actual.at("starts")!=ledger_.starts()){ledger_.storage_fault();return error("OWNER_FAULT","actual kernel start counter changed");}
        auto header=envelope("INFO_RESULT");Json value={{"state","IDLE"},{"safe",actual.at("safe")},
            {"active",actual.at("active")},{"faulted",actual.at("faulted")},{"starts",ledger_.starts()},
            {"done_latched",actual.at("done_latched")},{"bo",actual.at("bo")},{"pending",current_},
            {"last",actual.at("last").empty()?Json(nullptr):actual.at("last")},{"terminal_index_count",ledger_.terminal_count()}};
        if(!current_.is_null()) {
            const auto* record=ledger_.query(current_);require(record!=nullptr,"current serial record");value["state"]=state_text(record->state);
            if(record->state==ExecutionState::Receiving || record->state==ExecutionState::Executing){value["active"]=true;value["safe"]=false;}
            if(record->state==ExecutionState::Unknown){value["faulted"]=true;value["safe"]=false;}
            if(record->state==ExecutionState::CompletedUnacked){
                try{value["last"]=checked_native(current_,*record);}catch(...){ledger_.storage_fault();return error("OWNER_FAULT","retained completion storage changed");}
            }
        }
        header["info"]=std::move(value);message_fields(header,context_);return {std::move(header),{}};
    }
    bool can_stop() const {
        return ledger_.can_safe_stop(safe_) && kernel_info_().at("starts")==ledger_.starts();
    }
    std::size_t retained_result_bytes() const{return ledger_.retained_result_bytes();}
    std::size_t terminal_count() const{return ledger_.terminal_count();}
    bool faulted() const{return ledger_.faulted();}
    const TrustedContext& context() const{return context_;}
    // One received command per connection. The bridge may reconnect to QUERY/
    // ACK the original ID; it must never resend REQUEST as recovery.
    Frame receive_and_dispatch(int fd,std::uint64_t call_deadline,SocketIOStats& received) {
        const auto peer=actual_socket_peer(fd);const ExecutionPeer actual_peer{peer.pid,peer.birth_ticks,peer.uid};
        Frame incoming;
        try {
            incoming=receive_admitted_request_frame(fd,context_,digest_,call_deadline,received,
                [&](const Json& request,const ExecutionPeer& admitted_peer){
                    ledger_.admit(request,admitted_peer,digest_(compact_json_bytes(request_metadata_header(context_,request))));
                    current_=request;
                },[&](const Json& request){ledger_.reject_before_start(request.at("execution_id"));current_=nullptr;});
        }catch(const LedgerError& e){return error(e.code,e.what());}
        catch(const std::exception& e){return error("BAD_FRAME",e.what());}
        const auto type=incoming.header.at("type").get<std::string>();
        try {
            if(type=="INFO")return info();
            if(type=="STOP") {
                ledger_require(can_stop(),"OWNER_FAULT","STOP requires actual CU/AXI quiet and no active/unknown/unacked result");
                auto h=envelope("STOP_RESULT");h["safe_stop"]=true;h["starts"]=ledger_.starts();h["terminal_index_count"]=ledger_.terminal_count();
                return {std::move(h),{}};
            }
            const auto& request=incoming.header.at("request");const auto id=request.at("execution_id").get<std::string>();
            if(type=="QUERY")return query(request);
            if(type=="ACK") {
                const auto* record=ledger_.query(request);ledger_require(record!=nullptr,"ACK_MISMATCH","unknown ACK ID");
                const auto request_digest=record->request_file_digest,native_digest=record->native_file_digest;
                if(record->state==ExecutionState::CompletedUnacked || record->state==ExecutionState::AckedReleased) {
                    try{checked_native(request,*record);}catch(...){ledger_.storage_fault();throw LedgerError("OWNER_FAULT","retained ACK storage changed or unavailable");}
                }
                if(record->state==ExecutionState::CompletedUnacked && same_peer(record->original_peer,actual_peer)) {
                    auto task=anchored_task_directory(context_);auto requests=private_child_directory(task.get(),"requests");
                    auto execution=private_child_directory(requests.get(),id);
                    auto file=read_private_file(execution.get(),"journal.json",0600,HEADER_LIMIT);const auto journal=actual_json_file(file);
                    ledger_require(journal.contains("state") && journal.at("state")=="COMPLETED",
                        "ACK_MISMATCH","normal ACK requires actual COMPLETED caller journal");
                    const auto& pending=ledger_.pending_result(request);const auto text=encode_message(pending.header,context_);
                    journal_fields(journal,pending,record->original_peer,context_,native_digest,digest_(Bytes(text.begin(),text.end())));
                }
                ledger_.ack(incoming.header,actual_peer,[&](const Json& confirmation,const Frame& pending,
                        const ExecutionPeer& original,const ExecutionPeer& recovering){
                    RecoveryIOStats stats;verify_sealed_recovery(context_,confirmation,pending,original,recovering,
                        request_digest,native_digest,digest_,safe_,stats);
                });
                if(!current_.is_null() && current_.at("execution_id")==id)current_=nullptr;
                auto h=incoming.header;h["type"]="ACK_RESULT";h["state"]="ACKED_Y_RELEASED";message_fields(h,context_);return {std::move(h),{}};
            }
            require(type=="REQUEST","client request dispatch");
            const auto* record=ledger_.query(request);require(record!=nullptr,"admitted full request");
            auto stored=load_before_start_storage(context_,request,actual_peer,record->request_file_digest,digest_);
            ledger_.verify_request_storage(request,stored.request_metadata);ledger_.verify_received_X(request,incoming.body);
            ledger_.verify_loaded_W(request,stored.weight);
            auto executed=execute_(request,incoming.body,stored.weight,[&]{ledger_.start_intent(request);},
                [&](std::uint64_t actual_starts){ledger_.start_recorded(id,actual_starts);});
            auto h=envelope("RESULT");h["request"]=request;h["state"]="COMPLETED_UNACKED";h["body_bytes"]=executed.first.size();
            h["Y_sha256"]=executed.second.at("Y_sha256");h["starts_after"]=executed.second.at("starts");h["native"]=executed.second;
            Frame completed{std::move(h),std::move(executed.first)};body_fields(completed.header,completed.body,context_,digest_);
            std::string native_digest;
            try{native_digest=write_native_record(context_,request,completed.header.at("native"),digest_);}
            catch(...){ledger_.storage_fault();throw;}
            ledger_.complete(std::move(completed),native_digest,safe_);return ledger_.pending_result(request);
        }catch(const std::exception& e) {
            const Json request=incoming.header.contains("request")?incoming.header.at("request"):Json(nullptr);
            if(type=="REQUEST" && !request.is_null()) {
                const auto* record=ledger_.query(request);
                if(record && record->state==ExecutionState::Receiving){ledger_.reject_before_start(request.at("execution_id"));current_=nullptr;}
                else if(record && record->state==ExecutionState::Executing && safe_()) {
                    ledger_.failed_safe(request.at("execution_id"),safe_);current_=nullptr;
                }else if(record && (record->state==ExecutionState::Executing || record->state==ExecutionState::Unknown))
                    ledger_.quarantine(request.at("execution_id"));
            }
            const auto* typed=dynamic_cast<const LedgerError*>(&e);
            return error(typed?typed->code:"OWNER_FAULT",e.what(),request);
        }
    }
};
} // namespace kv260_stream_v2a
