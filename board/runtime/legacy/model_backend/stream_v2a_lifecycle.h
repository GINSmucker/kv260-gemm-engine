// Ordinary-memory serial execution ledger for the proposed V2A owner.
// The caller supplies actual peer, storage and CU/AXI observations. This module
// performs none of those observations, allocates no BO and never issues start.
// There is no REQUEST retry, queue, owner restart or automatic timeout release.
#pragma once
#include "stream_v2a_frame.h"
#include <map>
#include <memory>

namespace kv260_stream_v2a {
struct ExecutionPeer {
    std::uint64_t pid, birth_ticks;
    std::uint32_t uid;
};
inline bool same_peer(const ExecutionPeer& a,const ExecutionPeer& b) {
    return a.pid==b.pid && a.birth_ticks==b.birth_ticks && a.uid==b.uid;
}
enum class ExecutionState { Receiving, Executing, Unknown, CompletedUnacked,
    RejectedBeforeStart, FailedSafe, AckedReleased };
inline const char* state_text(ExecutionState state) {
    switch(state) {
    case ExecutionState::Receiving:return "RECEIVING";
    case ExecutionState::Executing:return "EXECUTING";
    case ExecutionState::Unknown:return "UNKNOWN_OR_QUARANTINED";
    case ExecutionState::CompletedUnacked:return "COMPLETED_UNACKED";
    case ExecutionState::RejectedBeforeStart:return "REJECTED_BEFORE_START";
    case ExecutionState::FailedSafe:return "FAILED_SAFE";
    case ExecutionState::AckedReleased:return "ACKED_Y_RELEASED";
    }
    throw std::runtime_error("invalid execution ledger state");
}
struct LedgerError:std::runtime_error {
    std::string code;
    LedgerError(std::string c,const std::string& message):std::runtime_error(message),code(std::move(c)){}
};
inline void ledger_require(bool ok,const char* code,const char* message) {
    if(!ok)throw LedgerError(code,message);
}
struct TerminalRecord {
    ExecutionState state=ExecutionState::Receiving;
    ExecutionPeer original_peer{};
    ExecutionPeer acknowledged_peer{};
    std::uint64_t starts_before=0,starts_after=0;
    bool request_storage_verified=false,X_verified=false,W_verified=false,start_intent=false,start_confirmed=false;
    std::string request_digest,request_file_digest,native_file_digest,Y_digest,ack_confirmation_digest;
};
using SafeCheck=std::function<bool()>;
// A production implementation must read the real sealed files and prove death
// before this returns. A header or socket EOF is not a RecoveryCheck.
using RecoveryCheck=std::function<void(const Json&,const Frame&,const ExecutionPeer&,const ExecutionPeer&)>;

class ExecutionLedger {
    TrustedContext context_;
    BodyDigest digest_;
    std::map<std::string,TerminalRecord> records_;
    std::string current_;
    std::unique_ptr<Frame> pending_;
    std::uint64_t starts_=0,id_text_bytes_=0,id_limit_,text_limit_;
    bool faulted_=false;
    std::string json_digest(const Json& value) const {
        const auto text=value.dump(-1,' ',true,Json::error_handler_t::strict);
        Bytes bytes(text.begin(),text.end());const auto result=digest_(bytes);
        sha_string(Json(result));return result;
    }
    static void peer_fields(const ExecutionPeer& peer) {
        ledger_require(peer.uid==1000 && peer.pid!=0 && peer.birth_ticks!=0,
            "BAD_IDENTITY","actual uid1000 nonzero peer required");
    }
    TerminalRecord& active(const std::string& id) {
        ledger_require(!current_.empty() && current_==id,"BAD_REQUEST","current execution mismatch");
        return records_.at(id);
    }
    const TerminalRecord* matched(const Json& request) const {
        request_fields(request,context_);const auto id=request.at("execution_id").get<std::string>();
        const auto found=records_.find(id);if(found==records_.end())return nullptr;
        ledger_require(json_digest(request)==found->second.request_digest,
            "BAD_REQUEST","original full request mismatch");return &found->second;
    }
public:
    ExecutionLedger(TrustedContext context,BodyDigest digest,std::uint64_t starts,
            std::uint64_t id_limit=65536,std::uint64_t text_limit=16*1024*1024):
        context_(std::move(context)),digest_(std::move(digest)),starts_(starts),
        id_limit_(id_limit),text_limit_(text_limit) {
        trusted_context(context_);
        require(bool(digest_) && id_limit_>0 && id_limit_<=65536 && text_limit_>0 &&
            text_limit_<=16*1024*1024,"bounded ledger configuration");
    }
    void admit(const Json& request,const ExecutionPeer& actual_peer,const std::string& actual_request_file_digest) {
        request_fields(request,context_);peer_fields(actual_peer);
        sha_string(Json(actual_request_file_digest));
        const auto id=request.at("execution_id").get<std::string>();
        ledger_require(records_.count(id)==0,"DUPLICATE_ID","execution ID permanently reserved");
        ledger_require(!faulted_,"OWNER_FAULT","owner observation/storage fault retained");
        ledger_require(!pending_,"ACK_PENDING","completed Y awaits exact confirmation");
        ledger_require(current_.empty(),"OWNER_FAULT","serial owner already active");
        ledger_require(records_.size()<id_limit_ && id.size()<=text_limit_-id_text_bytes_,
            "TERMINAL_LIMIT","retained terminal index bound");
        ledger_require(uint_value(request.at("starts_before"),UINT64_MAX)==starts_,
            "BAD_REQUEST","actual starts_before mismatch");
        TerminalRecord record;record.original_peer=actual_peer;record.starts_before=starts_;
        // Allocation/digest failure after trusted admission faults this owner;
        // it cannot turn an accepted ID into a silently reusable request.
        faulted_=true;
        record.starts_after=starts_;record.request_digest=json_digest(request);
        record.request_file_digest=actual_request_file_digest;
        auto inserted=records_.emplace(id,std::move(record));require(inserted.second,"unique ledger insert");
        id_text_bytes_+=id.size();current_=id;faulted_=false;
    }
    // Actual file type/uid/mode/link and journal SUBMITTED observations remain
    // the production adapter's responsibility. Verify every loaded metadata
    // byte as well as its full strict REQUEST fields before the start intent.
    void verify_request_storage(const Json& request,const Bytes& actual_file_bytes) {
        require(matched(request)!=nullptr,"registered request file");
        auto& record=active(request.at("execution_id").get<std::string>());
        ledger_require(record.state==ExecutionState::Receiving && !record.start_intent,
            "OWNER_FAULT","request file verification only before start");
        if(actual_file_bytes.empty() || actual_file_bytes.size()>HEADER_LIMIT ||
            digest_(actual_file_bytes)!=record.request_file_digest) {
            reject_before_start(request.at("execution_id").get<std::string>());
            throw LedgerError("BAD_HASH","actual complete request file digest/length mismatch");
        }
        try {
            const auto header=parse_header(std::string(actual_file_bytes.begin(),actual_file_bytes.end()));
            require(message_fields(header,context_)=="REQUEST" && header.at("request")==request,
                "actual request file fields mismatch");
        }catch(...) {
            reject_before_start(request.at("execution_id").get<std::string>());throw;
        }
        record.request_storage_verified=true;
    }
    void verify_received_X(const Json& request,const Bytes& actual_body) {
        require(matched(request)!=nullptr,"registered X request");
        auto& record=active(request.at("execution_id").get<std::string>());
        ledger_require(record.state==ExecutionState::Receiving && !record.start_intent,
            "OWNER_FAULT","X verification only before start");
        const auto bytes=request_fields(request,context_);
        if(actual_body.size()!=bytes.x || digest_(actual_body)!=request.at("X_sha256")) {
            reject_before_start(request.at("execution_id").get<std::string>());
            throw LedgerError("BAD_HASH","actual full X body length/digest mismatch");
        }
        record.X_verified=true;
    }
    // The production caller still verifies the real registered file's path,
    // inode/type/uid/mode/link count. This checks all of its loaded W bytes.
    void verify_loaded_W(const Json& request,const Bytes& actual_file_bytes) {
        require(matched(request)!=nullptr,"registered W request");
        auto& record=active(request.at("execution_id").get<std::string>());
        ledger_require(record.state==ExecutionState::Receiving && !record.start_intent,
            "OWNER_FAULT","W verification only before start");
        const auto bytes=request_fields(request,context_);
        if(actual_file_bytes.size()!=bytes.w || digest_(actual_file_bytes)!=request.at("W_sha256")) {
            reject_before_start(request.at("execution_id").get<std::string>());
            throw LedgerError("BAD_HASH","actual full loaded W length/digest mismatch");
        }
        record.W_verified=true;
    }
    // Malformed body/file/hash failures preserve the trusted ID, with zero start.
    void reject_before_start(const std::string& id) {
        auto& record=active(id);
        ledger_require(record.state==ExecutionState::Receiving && !record.start_intent,
            "OWNER_FAULT","cannot reject an uncertain/submitted start as unexecuted");
        record.state=ExecutionState::RejectedBeforeStart;record.starts_after=record.starts_before;
        current_.clear();
    }
    // Call immediately before the only MMIO start write. An exception between
    // intent and confirmed observation leaves UNKNOWN and retains the owner.
    void start_intent(const Json& request) {
        const auto* checked=matched(request);require(checked!=nullptr,"registered execution");
        auto& record=active(request.at("execution_id").get<std::string>());
        ledger_require(!faulted_ && record.state==ExecutionState::Receiving && record.request_storage_verified &&
            record.X_verified && record.W_verified && !record.start_intent &&
            starts_==record.starts_before,"OWNER_FAULT","single start intent required");
        record.start_intent=true;record.state=ExecutionState::Unknown;
    }
    // The number is the owner's actual recorded once-only start observation,
    // supplied by its XRT path. This ledger cannot establish a hardware start.
    void start_recorded(const std::string& id,std::uint64_t actual_starts) {
        auto& record=active(id);
        ledger_require(record.state==ExecutionState::Unknown && record.start_intent &&
            !record.start_confirmed && actual_starts==record.starts_before+1,
            "OWNER_FAULT","confirmed one-start observation required");
        starts_=actual_starts;record.starts_after=actual_starts;record.start_confirmed=true;
        record.state=ExecutionState::Executing;
    }
    void quarantine(const std::string& id) {
        auto& record=active(id);
        ledger_require(record.state==ExecutionState::Receiving || record.state==ExecutionState::Executing ||
            record.state==ExecutionState::Unknown,"OWNER_FAULT","active quarantine only");
        record.state=ExecutionState::Unknown;
    }
    void complete(Frame result,const std::string& actual_native_file_digest,const SafeCheck& actual_safe) {
        require(message_fields(result.header,context_)=="RESULT","RESULT required");
        body_fields(result.header,result.body,context_,digest_);
        const auto& request=result.header.at("request");const auto* checked=matched(request);
        require(checked!=nullptr,"registered result request");
        auto& record=active(request.at("execution_id").get<std::string>());
        ledger_require(!faulted_ && !pending_ && record.start_confirmed &&
            (record.state==ExecutionState::Executing || record.state==ExecutionState::Unknown) &&
            starts_==record.starts_before+1 && bool(actual_safe) && actual_safe(),
            "OWNER_FAULT","actual once-only start and CU/AXI quiet completion required");
        sha_string(Json(actual_native_file_digest));
        auto pending=std::make_unique<Frame>(std::move(result));
        record.native_file_digest=actual_native_file_digest;record.Y_digest=pending->header.at("Y_sha256").get<std::string>();
        record.state=ExecutionState::CompletedUnacked;pending_=std::move(pending);
    }
    void failed_safe(const std::string& id,const SafeCheck& actual_safe) {
        auto& record=active(id);
        ledger_require(record.start_confirmed && record.state==ExecutionState::Executing &&
            bool(actual_safe) && actual_safe(),"OWNER_FAULT","verified one-start safe failure required");
        record.state=ExecutionState::FailedSafe;current_.clear();
    }
    const TerminalRecord* query(const Json& original_request) const {
        return matched(original_request); // nullptr means NOT_FOUND; never retry.
    }
    const Frame& pending_result(const Json& original_request) const {
        ledger_require(!faulted_,"OWNER_FAULT","storage fault retains pending result");
        const auto* record=matched(original_request);
        ledger_require(record && record->state==ExecutionState::CompletedUnacked && pending_ &&
            original_request.at("execution_id")==current_,"BAD_REQUEST","original completed Y unavailable");
        return *pending_;
    }
    void ack(const Json& header,const ExecutionPeer& actual_peer,const RecoveryCheck& actual_recovery={}) {
        require(message_fields(header,context_)=="ACK","ACK required");peer_fields(actual_peer);
        ledger_require(!faulted_,"OWNER_FAULT","storage fault cannot release pending result");
        const auto* matched_record=matched(header.at("request"));
        ledger_require(matched_record!=nullptr,"ACK_MISMATCH","ACK unknown execution");
        auto& record=records_.at(header.at("request").at("execution_id").get<std::string>());
        ledger_require((record.state==ExecutionState::CompletedUnacked || record.state==ExecutionState::AckedReleased) &&
            header.at("Y_sha256")==record.Y_digest &&
            uint_value(header.at("starts_after"),UINT64_MAX)==record.starts_after,
            "ACK_MISMATCH","ACK exact terminal digest/start required");
        const auto& confirmation=header.at("confirmation");const auto confirmation_digest=json_digest(confirmation);
        if(record.state==ExecutionState::AckedReleased) {
            ledger_require(confirmation_digest==record.ack_confirmation_digest &&
                same_peer(actual_peer,record.acknowledged_peer),
                "RECOVERY_NOT_PROVEN","released Y ACK requires same confirmed peer/object");
            return; // Duplicate normal ACK never changes start or releases new Y.
        }
        ledger_require(pending_ && current_==header.at("request").at("execution_id"),
            "ACK_MISMATCH","pending terminal identity required");
        if(same_peer(actual_peer,record.original_peer)) {
            ledger_require(confirmation.is_null(),"ACK_MISMATCH","normal caller confirmation must be null");
        } else {
            ledger_require(!confirmation.is_null() && bool(actual_recovery),
                "RECOVERY_NOT_PROVEN","different peer needs actual death and sealed archive proof");
            ledger_require(uint_value(confirmation.at("original_client_pid"),UINT64_MAX)==record.original_peer.pid &&
                uint_value(confirmation.at("original_client_birth_ticks"),UINT64_MAX)==record.original_peer.birth_ticks,
                "ACK_MISMATCH","recovery original actual peer mismatch");
            actual_recovery(confirmation,*pending_,record.original_peer,actual_peer);
        }
        record.ack_confirmation_digest=confirmation_digest;record.acknowledged_peer=actual_peer;
        record.state=ExecutionState::AckedReleased;
        pending_.reset();current_.clear();
    }
    // Storage corruption is an owner fault, never an absent or reusable ID.
    void storage_fault(){faulted_=true;}
    bool can_safe_stop(const SafeCheck& actual_safe) const {
        return !faulted_ && current_.empty() && !pending_ && bool(actual_safe) && actual_safe();
    }
    std::size_t retained_result_bytes() const{return pending_?pending_->body.size():0;}
    std::size_t terminal_count() const{return records_.size();}
    std::uint64_t id_text_bytes() const{return id_text_bytes_;}
    std::uint64_t starts() const{return starts_;}
    bool faulted() const{return faulted_;}
};
} // namespace kv260_stream_v2a
