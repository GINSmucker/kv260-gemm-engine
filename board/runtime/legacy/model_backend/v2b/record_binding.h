// Domain validation for task records. Storage durability is supplied by Log;
// execution, process death and ACK authority remain in the owner ledger.
#pragma once
#include "protocol/stream_v2b_recovery.h"
#include "record_log.h"
namespace kv260_stream_v2b {
inline Json record_task(const TrustedContext& c) {
    return {{"identity",c.identity},{"task_root",c.task_root}};
}
inline Json record_submitted(const Json& request,const ExecutionPeer& caller,const TrustedContext& c) {
    request_fields(request,c);
    return {{"schema","kv260_record_v2b_v1"},{"type","SUBMITTED"},{"execution_id",request.at("execution_id")},
        {"request",request},{"caller",{{"pid",caller.pid},{"birth_ticks",caller.birth_ticks}}},{"identity",c.identity}};
}
inline void submitted_record_fields(const kv260_record_v2b::Entry& entry,const Json& request,
        const ExecutionPeer& caller,const TrustedContext& context) {
    ledger_require(entry.submitted==record_submitted(request,caller,context),"BAD_REQUEST","actual full submitted record/peer mismatch");
}
inline Json completed_record(const kv260_record_v2b::Entry& entry,const Json& result,const TrustedContext& context) {
    require(message_fields(result,context)=="RESULT" && result.at("request")==entry.submitted.at("request"),"full result record contract");
    return {{"schema","kv260_record_v2b_v1"},{"type","COMPLETED"},{"execution_id",result.at("request").at("execution_id")},
        {"submitted_sequence",entry.submitted_sequence},{"result",result}};
}
} // namespace kv260_stream_v2b
