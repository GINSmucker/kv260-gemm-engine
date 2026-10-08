// Portable G1 dispatcher seam. Adapters must bind actual peer, sealed files,
// durable record IO and CU/AXI. This is not a production device entry point.
#pragma once
#include "ledger.h"
namespace kv260_runtime_v3 {
struct Adapters {
    std::function<void(const Request&,ExecutionPeer)> submitted_readback;
    std::function<Bytes(const Request&)> weight_readback;
    std::function<std::pair<Bytes,Json>(const Request&,const Bytes&,const Bytes&,
        const std::function<void()>&,const std::function<void(std::uint64_t)>&)> execute;
    std::function<void(const Frame&)> native_full_readback;
    std::function<void(const Result&)> completed_commit_readback;
    std::function<void(const Request&)> failure_seal;
    SafeCheck quiet;
};
class Session {
    Authority& authority_;
    Ledger ledger_;
    Adapters adapters_;
public:
    Session(Authority& a,std::uint64_t starts,Adapters adapters):authority_(a),ledger_(a,starts),adapters_(std::move(adapters)) {
        require(adapters_.submitted_readback && adapters_.weight_readback && adapters_.execute &&
            adapters_.native_full_readback && adapters_.completed_commit_readback && adapters_.failure_seal && adapters_.quiet,"all actual adapters required");
    }
    Ledger& ledger() {return ledger_;}
    Frame run(const Frame& incoming) {
        authority_.check(incoming);require(incoming.type()=="REQUEST","only complete immutable REQUEST executes");
        auto r=authority_.request(incoming);Bytes w;
        try {
            ledger_.storage(r,adapters_.submitted_readback);
            ledger_.weight(r,[&](const Request& request){w=adapters_.weight_readback(request);authority_.weight(request,w);});
            auto executed=adapters_.execute(r,incoming.body(),w,[&]{ledger_.intent(incoming);},
                [&](std::uint64_t count){ledger_.started(r,count);});
            Json header={{"schema",Wire::SCHEMA},{"wire_version",Wire::WIRE_VERSION},{"type","RESULT"},
                {"identity",authority_.context().identity},{"body_bytes",executed.first.size()},
                {"request",r.json()},{"state","COMPLETED_UNACKED"},{"Y_sha256",executed.second.at("Y_sha256")},
                {"starts_after",executed.second.at("starts")},{"native",executed.second}};
            auto result=authority_.seal(header,executed.first);
            ledger_.complete(result,adapters_.quiet,adapters_.native_full_readback);
            return ledger_.pending(r);
        }catch(...) {
            const auto failure=std::current_exception();
            try {if(!ledger_.faulted()) {
                auto state=ledger_.query(r);
                if(state==ExecutionState::Receiving)ledger_.reject(r);
                else if(state==ExecutionState::Executing && adapters_.quiet())ledger_.failed_safe(r,adapters_.quiet);
                else if(state==ExecutionState::Executing || state==ExecutionState::Unknown)ledger_.quarantine(r);
            }}catch(...) {ledger_.storage_fault();}
            try {adapters_.failure_seal(r);}catch(...) {ledger_.storage_fault();}
            std::rethrow_exception(failure);
        }
    }
    void acknowledge(const Frame& ack,ExecutionPeer peer,const Ledger::Recovery& recovery={}) {
        try {ledger_.ack(ack,peer,adapters_.completed_commit_readback,recovery);}
        catch(...) {
            const auto failure=std::current_exception();
            try {auto r=authority_.request(ack);ledger_.query(r);adapters_.failure_seal(r);}
            catch(...) {ledger_.storage_fault();}
            std::rethrow_exception(failure);
        }
    }
};
} // namespace kv260_runtime_v3
