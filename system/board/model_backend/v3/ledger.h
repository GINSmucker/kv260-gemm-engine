// Private credential ledger. Real peer/storage/kernel observations are mandatory
// adapter inputs; the ledger itself establishes no hardware or persistence PASS.
#pragma once
#include "frame.h"
#include "../v2b/protocol/stream_v2b_lifecycle.h"

namespace kv260_runtime_v3 {
using Wire::ExecutionPeer;
using Wire::ExecutionState;
using Wire::same_peer;
using Wire::SafeCheck;
class Ledger {
    struct Record {
        const Request request;
        const ExecutionPeer peer;
        ExecutionState state=ExecutionState::Receiving;
        bool storage=false,weight=false,submitted=false,started=false;
        std::shared_ptr<const Frame> result;
        std::unique_ptr<const Result> result_credential;
        std::string Y_digest;
        ExecutionPeer ack_peer{};
        Json confirmation=nullptr;
        Record(const Request& r,ExecutionPeer p):request(r),peer(p) {}
    };
    Authority& authority_;
    std::map<std::string,std::unique_ptr<Record>> records_;
    std::string active_;
    std::uint64_t starts_,id_bytes_=0;
    const std::size_t record_limit_;
    bool faulted_=false,busy_=false;
    struct Guard {
        Ledger& l;
        explicit Guard(Ledger& value):l(value) {
            if(l.busy_){l.faulted_=true;throw std::runtime_error("reentrant ledger operation");}
            require(!l.faulted_,"faulted owner retains state");l.busy_=true;
        }
        ~Guard(){l.busy_=false;}
    };
    Record& matched(const Request& r) {
        authority_.check(r);auto i=records_.find(r.id());
        require(i!=records_.end() && i->second->request.same(r),"original full request credential mismatch");return *i->second;
    }
    Record& current(const Request& r) {auto& v=matched(r);require(active_==r.id(),"current ID mismatch");return v;}
public:
    // 1024 terminal records cover 20 graphs (980 IDs), plus a bounded margin.
    // Each retains at most two headers and two canonical request strings:
    // <=64 MiB serialized text, plus bounded JSON/map allocations. G2 must
    // measure allocator peaks rather than presenting this bound as heap usage.
    Ledger(Authority& a,std::uint64_t starts,std::size_t limit=1024):authority_(a),starts_(starts),record_limit_(limit) {
        require(limit && limit<=1024,"private terminal record budget");
    }
    void admit(const Request& r,ExecutionPeer peer) {
        Guard guard(*this);authority_.check(r);
        require(r.message_type()=="REQUEST","admission requires REQUEST header credential");
        require(peer.uid==1000 && peer.pid && peer.birth_ticks,"actual peer required");
        require(!records_.count(r.id()) && active_.empty(),"duplicate ID/pending result/active execution");
        require(records_.size()<record_limit_ && r.id().size()<=16*1024*1024-id_bytes_,"terminal index budget");
        require(r.starts_before()==starts_,"actual start count mismatch");
        // Reserve before body receive; allocation failure faults the owner.
        faulted_=true;auto record=std::make_unique<Record>(r,peer);
        records_.emplace(r.id(),std::move(record));active_=r.id();id_bytes_+=r.id().size();faulted_=false;
    }
    void reject(const Request& r) {
        Guard guard(*this);auto& v=current(r);require(v.state==ExecutionState::Receiving,"cannot reject submitted/unknown execution");
        v.state=ExecutionState::RejectedBeforeStart;active_.clear();
    }
    // Each callback must independently read/verify real file and full committed
    // record. No public boolean or caller-constructed validated marker exists.
    void storage(const Request& r,const std::function<void(const Request&,ExecutionPeer)>& check) {
        Guard guard(*this);auto& v=current(r);require(v.state==ExecutionState::Receiving && bool(check),"prestart actual storage adapter");
        check(v.request,v.peer);require(!faulted_,"reentrant storage adapter");v.storage=true;
    }
    void weight(const Request& r,const std::function<void(const Request&)>& check) {
        Guard guard(*this);auto& v=current(r);require(v.state==ExecutionState::Receiving && bool(check),"prestart actual W adapter");
        check(v.request);require(!faulted_,"reentrant W adapter");v.weight=true;
    }
    void intent(const Frame& x) {
        Guard guard(*this);authority_.check(x);require(x.type()=="REQUEST","verified complete X required");
        auto r=authority_.request(x);auto& v=current(r);
        require(v.state==ExecutionState::Receiving && v.storage && v.weight && starts_==r.starts_before(),"single prepared start required");
        v.submitted=true;v.state=ExecutionState::Unknown;
    }
    void started(const Request& r,std::uint64_t actual) {
        Guard guard(*this);auto& v=current(r);
        require(v.state==ExecutionState::Unknown && v.submitted && !v.started && actual==r.starts_before()+1,"one observed start required");
        starts_=actual;v.started=true;v.state=ExecutionState::Executing;
    }
    void quarantine(const Request& r) {
        Guard guard(*this);auto& v=current(r);
        require(v.state==ExecutionState::Receiving || v.state==ExecutionState::Executing || v.state==ExecutionState::Unknown,"active quarantine required");
        v.state=ExecutionState::Unknown;
    }
    void complete(const Frame& y,const SafeCheck& quiet,const std::function<void(const Frame&)>& full_readback) {
        Guard guard(*this);authority_.check(y);require(y.type()=="RESULT","verified RESULT required");
        auto r=authority_.request(y);auto& v=current(r);
        require(v.started && (v.state==ExecutionState::Executing || v.state==ExecutionState::Unknown) &&
            starts_==r.starts_before()+1 && bool(quiet) && bool(full_readback),"actual completion adapters required");
        require(quiet() && !faulted_,"CU/AXI quiet required");
        // Native file/readback failure retains UNKNOWN and all private output.
        v.result=std::make_shared<const Frame>(y);v.state=ExecutionState::Unknown;
        v.result_credential=std::make_unique<const Result>(authority_.result(y));
        v.Y_digest=y.header().at("Y_sha256").get<std::string>();
        try {full_readback(*v.result);require(!faulted_,"reentrant completion adapter");}
        catch(...) {faulted_=true;throw;}
        v.state=ExecutionState::CompletedUnacked;
    }
    void failed_safe(const Request& r,const SafeCheck& quiet) {
        Guard guard(*this);auto& v=current(r);
        require(v.started && v.state==ExecutionState::Executing && bool(quiet) && quiet() && !faulted_,"actual safe failure required");
        v.state=ExecutionState::FailedSafe;active_.clear();
    }
    ExecutionState query(const Request& r) {if(busy_){faulted_=true;throw std::runtime_error("reentrant query");}return matched(r).state;}
    Frame pending(const Request& r) {
        require(!busy_ && !faulted_,"faulted/reentrant result access");auto& v=current(r);
        require(v.state==ExecutionState::CompletedUnacked && bool(v.result),"unacked result required");return *v.result;
    }
    using Recovery=std::function<void(const Json&,const Frame&,ExecutionPeer,ExecutionPeer)>;
    void ack(const Frame& ack,ExecutionPeer peer,const std::function<void(const Result&)>& committed,
             const Recovery& recovery={}) {
        Guard guard(*this);authority_.check(ack);require(ack.type()=="ACK","ACK credential required");
        require(peer.uid==1000 && peer.pid && peer.birth_ticks,"actual ACK peer required");
        auto r=authority_.request(ack);auto& v=matched(r);const auto& h=ack.header();
        require((v.state==ExecutionState::CompletedUnacked || v.state==ExecutionState::AckedReleased) &&
            h.at("Y_sha256")==v.Y_digest && Wire::uint_value(h.at("starts_after"),UINT64_MAX)==r.starts_before()+1,"exact ACK identity/digest/start");
        const auto& confirmation=h.at("confirmation");
        require(v.result_credential && bool(committed),"actual full COMPLETED/native readback required");
        try {committed(*v.result_credential);require(!faulted_,"reentrant terminal readback");}
        catch(...) {faulted_=true;throw;}
        if(v.state==ExecutionState::AckedReleased) {
            require(confirmation==v.confirmation && same_peer(peer,v.ack_peer),"duplicate ACK exact confirmed peer/object");return;
        }
        require(active_==r.id() && v.result,"actual pending result required");
        if(same_peer(peer,v.peer))require(confirmation.is_null(),"normal ACK confirmation must be null");
        else {
            require(!confirmation.is_null() && bool(recovery),"real sealed recovery/death proof required");
            require(Wire::uint_value(confirmation.at("original_client_pid"),UINT64_MAX)==v.peer.pid &&
                Wire::uint_value(confirmation.at("original_client_birth_ticks"),UINT64_MAX)==v.peer.birth_ticks,"recovery original peer mismatch");
            recovery(confirmation,*v.result,v.peer,peer);
        }
        require(!faulted_,"reentrant ACK adapter");v.confirmation=confirmation;v.ack_peer=peer;v.state=ExecutionState::AckedReleased;
        v.result.reset();active_.clear();
    }
    bool can_stop(const SafeCheck& quiet) {
        if(busy_){faulted_=true;throw std::runtime_error("reentrant STOP");}
        if(faulted_)return false;
        Guard guard(*this);return active_.empty() && bool(quiet) && quiet() && !faulted_;
    }
    bool faulted() const {return faulted_;}
    void storage_fault() {faulted_=true;}
    std::uint64_t starts() const {return starts_;}
    std::size_t retained_bytes() const {
        std::size_t n=0;for(const auto& r:records_)if(r.second->result)n+=r.second->result->body().size();return n;
    }
};
} // namespace kv260_runtime_v3
