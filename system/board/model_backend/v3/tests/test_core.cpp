#include "test_sha.h"
#include "../session.h"
#include "../timing.h"
#include <cstring>
#include <fstream>
#include <iostream>
#include <type_traits>
#include <thread>

namespace V=kv260_runtime_v3;
namespace W=kv260_stream_v2b;
using V::Json;using V::Bytes;
static unsigned passed=0;
static std::vector<std::string> names;
static void check(bool ok,const char* text){if(!ok)throw std::runtime_error(text);}
template<class F>void refuses(F f){bool threw=false;try{f();}catch(const std::exception&){threw=true;}check(threw,"expected rejection");}
template<class F>void test(const char* name,F f){f();++passed;names.emplace_back(name);}
static std::string hash(const Bytes& b){return kv260_crypto_sha256(b.data(),b.size());}
static Bytes wire(const std::string& text,const Bytes& body={}) {
    Bytes out;for(unsigned i=0;i<4;++i)out.push_back(static_cast<std::uint8_t>(text.size()>>(8*i)));
    out.insert(out.end(),text.begin(),text.end());out.insert(out.end(),body.begin(),body.end());return out;
}
struct Fixture {
    Json identity;
    std::vector<W::RegisteredLayer> layers;
    std::string root;
    Bytes x,w,y;
    explicit Fixture(Json lock):root("/home/ubuntu/fpga_project/model_b/runs/g1_public/t") {
        for(const char* k:{"candidate_sha256","owner_sha256","bridge_sha256","driver_sha256","driver_srcversion", "platform_manifest_sha256",
            "A2_gate_sha256","B_manifest_sha256","B_tools_wheel_sha256","uuid","tag0","tag1"})identity[k]=lock.at(k);
        identity["ready_sha256"]=std::string(64,'1');identity["boot_id"]="00000000-0000-0000-0000-000000000001";
        identity["window_id"]="g1_public";identity["owner_pid"]=123;identity["owner_birth_ticks"]=456;
        // Public synthetic registration and deterministic zeros. No private holdout.
        x=Bytes(192,0);w=Bytes(192000,1);y=Bytes(4000,0);
        for(unsigned i=0;i<49;++i)layers.push_back({"public_layer_"+std::to_string(i),1,192,1000,{hash(w)}});
    }
    W::TrustedContext context() const {return W::TrustedContext(identity,root,layers);}
    Json request(const std::string& image="img",std::uint64_t starts=0,unsigned index=0) const {
        const auto id="run_"+image+"_l"+std::to_string(index);
        return {{"execution_id",id},{"request_id",id},{"model_run_id","run"},{"image_id",image},{"layer_id",layers.at(index).id},
            {"layer_index",index},{"M",layers.at(index).M},{"K",layers.at(index).K},{"N",layers.at(index).N},{"X_bytes",x.size()},{"W_bytes",w.size()},{"Y_bytes",y.size()},
            {"X_sha256",hash(x)},{"W_sha256",hash(w)},{"starts_before",starts},
            {"W_file",root+"/weights/l"+std::to_string(index)+"_"+hash(w)+".bin"},
            {"native_result_file",root+"/requests/"+id+"/native.json"},{"mode","dense_w8a8"},{"layout","compact_row_major"},
            {"dtype_x","int8"},{"dtype_w","int8"},{"dtype_y","int32"}};
    }
    Json header(const char* type,const Json& r) const {
        return {{"schema",W::SCHEMA},{"wire_version",W::WIRE_VERSION},{"type",type},{"identity",identity},
            {"body_bytes",std::string(type)=="REQUEST"?x.size():0},{"request",r}};
    }
    Json native(const Json& r) const {
        Json traffic;const unsigned M=r.at("M"),K=r.at("K"),N=r.at("N");
        std::uint64_t ar=M*(K/32)*((N+63)/64),wr=K*N/64,aw=M*((N+15)/16);
        const std::uint64_t values[]={0,0,0,0,ar,8*ar,wr,16*wr,aw,M*N,aw,1};
        const char* keys[]={"16","20","24","28","32","36","40","44","48","52","56","60"};
        for(unsigned i=0;i<12;++i)traffic[keys[i]]=values[i];
        return {{"M",M},{"K",K},{"N",N},{"Y_bytes",y.size()},{"Y_sha256",hash(y)},{"copy_us",0.0},
            {"ctrl_after",4},{"done_latched",true},{"guards_pass",true},{"inputs_unchanged",true},
            {"kernel_cycles","UNAVAILABLE"},{"kernel_error",0},{"native_us",0.0},{"one_start",true},
            {"request_id",r.at("request_id")},{"start_to_idle_and_drain_us",0.0},{"starts",r.at("starts_before").get<std::uint64_t>()+1},
            {"status","PASS"},{"sync_in_us",0.0},{"sync_out_us",0.0},{"tag0",identity.at("tag0")},{"tag1",identity.at("tag1")},{"traffic",traffic}};
    }
    Json result(const Json& r) const {
        auto h=header("RESULT",r);h["body_bytes"]=y.size();h["state"]="COMPLETED_UNACKED";h["Y_sha256"]=hash(y);
        h["starts_after"]=r.at("starts_before").get<std::uint64_t>()+1;h["native"]=native(r);return h;
    }
    Json ack(const Json& r) const {
        auto h=header("ACK",r);h["Y_sha256"]=hash(y);h["starts_after"]=r.at("starts_before").get<std::uint64_t>()+1;h["confirmation"]=nullptr;return h;
    }
    Json recovery(const Json& r) const {
        const auto base=root+"/requests/"+r.at("execution_id").get<std::string>()+"/recovery/";
        return {{"schema","kv260_v2b_recovery_ack_v1"},{"execution_id",r.at("execution_id")},
            {"original_client_pid",100},{"original_client_birth_ticks",200},{"Y_archive_file",base+"Y.bin"},
            {"Y_archive_sha256",hash(y)},{"native_archive_file",base+"native.json"},{"native_archive_sha256",std::string(64,'2')},
            {"job_terminal_file",base+"terminal.json"},{"job_terminal_sha256",std::string(64,'3')},{"result_header_sha256",std::string(64,'4')}};
    }
};
static W::ExecutionPeer peer{100,200,1000};
static V::Frame admitted(V::Authority& a,V::Ledger& ledger,const Fixture& f,const Json& r) {
    V::Receiver receiver(a,0,[&](const V::Request& request){ledger.admit(request,peer);},[&](const V::Request& request){ledger.reject(request);});
    auto bytes=wire(f.header("REQUEST",r).dump(),f.x);receiver.feed(bytes.data(),bytes.size(),1);receiver.eof(2);return receiver.take();
}
static void prepare(V::Ledger& l,const V::Request& r) {
    l.storage(r,[](const V::Request&,W::ExecutionPeer){});l.weight(r,[](const V::Request&){});
}
static V::Frame completed(V::Authority& a,V::Ledger& l,const Fixture& f,const V::Frame& x) {
    auto r=a.request(x);prepare(l,r);l.intent(x);l.started(r,r.starts_before()+1);
    auto y=a.seal(f.result(r.json()),f.y);l.complete(y,[]{return true;},[](const V::Frame&){});return y;
}
int main(int argc,char** argv) {
    try {
        check(argc==2,"baseline lock argument required");std::ifstream input(argv[1]);Json lock;input>>lock;Fixture f(lock);
        static_assert(!std::is_constructible<V::Frame,Json,Bytes>::value,"cannot forge credentials");
        static_assert(!std::is_copy_constructible<V::Authority>::value,"authority cannot be cloned");
        static_assert(!std::is_assignable<decltype(std::declval<V::Frame>().header()),Json>::value,"header const");
        test("real_sha_known_answer",[]{Bytes b={'a','b','c'};check(hash(b)=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","SHA answer");});
        test("single_parse_fragmented_admission",[&]{
            V::Authority a(f.context(),hash);V::Ledger l(a,0);unsigned calls=0;auto r=f.request();auto bytes=wire(f.header("REQUEST",r).dump(),f.x);
            V::Receiver rx(a,0,[&](const V::Request& q){++calls;l.admit(q,peer);check(a.counters().parses==1,"admission after single parse");},[&](const V::Request& q){l.reject(q);});
            for(std::size_t i=0;i<bytes.size();++i)rx.feed(&bytes[i],1,i+1);rx.eof(bytes.size()+1);auto got=rx.take();
            check(got.body()==f.x && calls==1 && a.counters().parses==1 && a.counters().body_hashes==1,"single parse/hash");
            auto before=a.counters();got.encode();got.encode();auto copy=got;auto q=a.request(copy);check(q.id()==r.at("execution_id"),"request owned");
            check(a.counters().header_encodes==before.header_encodes && a.counters().body_hashes==before.body_hashes,"private reuse no work");refuses([&]{rx.take();});
        });
        test("alias_mutation_during_digest_and_after_seal",[&]{
            auto h=f.header("REQUEST",f.request());auto b=f.x;V::Authority a(f.context(),[&](const Bytes& owned){h["request"]["image_id"]="mutated";b[0]=99;return hash(owned);});
            auto sealed=a.seal(h,b);check(sealed.body()==f.x && sealed.header().at("request").at("image_id")=="img","deep copy precedes callback");
            h.clear();b.clear();auto copy=sealed;check(copy.body()==f.x,"shared const lifetime");
        });
        test("trusted_context_alias_isolated",[&]{auto id=f.identity;auto layers=f.layers;W::TrustedContext c(id,f.root,layers);id.clear();layers.clear();check(c.layers.size()==49 && c.identity==f.identity,"context owned");});
        test("task_generation_and_credential_lifetime",[&]{
            V::Authority a(f.context(),hash),b(f.context(),hash);auto frame=a.seal(f.header("REQUEST",f.request()),f.x);auto q=a.request(frame);
            check(a.generation()!=b.generation(),"unique generation");refuses([&]{b.check(frame);});refuses([&]{b.check(q);});
            auto changed=f;changed.identity["window_id"]="other";changed.root="/home/ubuntu/fpga_project/model_b/runs/other/t";
            V::Authority other(changed.context(),hash);refuses([&]{other.check(q);});
            auto durable=[&]{V::Authority temporary(f.context(),hash);return temporary.seal(f.header("REQUEST",f.request()),f.x); }();check(durable.body()==f.x,"frame outlives authority");
        });
        auto bad_header=[&](std::string text){V::Authority a(f.context(),hash);V::Receiver rx(a,0);auto b=wire(text,f.x);refuses([&]{rx.feed(b.data(),b.size(),1);});refuses([&]{rx.take();});};
        test("duplicate_top_and_nested_keys",[&]{auto t=f.header("REQUEST",f.request()).dump();bad_header("{\"type\":\"REQUEST\","+t.substr(1));
            auto p=t.find("\"request\":{");check(p!=std::string::npos,"fixture request");t.insert(p+11,"\"M\":1,");bad_header(t);});
        test("escaped_duplicate_key",[&]{auto t=f.header("REQUEST",f.request()).dump();bad_header("{\"t\\u0079pe\":\"REQUEST\","+t.substr(1));});
        test("NUL_raw_decoded_BOM_trailing_UTF8",[&]{auto t=f.header("REQUEST",f.request()).dump();bad_header(std::string(1,'\0')+t);bad_header("{\"\\u0000\":1}");bad_header("\xef\xbb\xbf"+t);bad_header(t+"{}");bad_header(std::string("{\"x\":\"")+char(0xff)+"\"}");});
        test("bool_integer_wrong_shape_unknown_keys",[&]{auto h=f.header("REQUEST",f.request());h["body_bytes"]=true;bad_header(h.dump());h=f.header("REQUEST",f.request());h["request"]["M"]=2;bad_header(h.dump());h=f.header("REQUEST",f.request());h["extra"]=0;bad_header(h.dump());});
        test("prefix_bound_no_admission",[&]{V::Authority a(f.context(),hash);unsigned n=0;V::Receiver rx(a,0,[&](const V::Request&){++n;},[](const V::Request&){});Bytes p={0xff,0xff,0xff,0x7f};refuses([&]{rx.feed(p.data(),4,1);});check(n==0 && a.counters().parses==0,"early bound");});
        test("short_body_preserves_reserved_ID_zero_start",[&]{
            V::Authority a(f.context(),hash);V::Ledger l(a,0);auto r=f.request();auto b=wire(f.header("REQUEST",r).dump(),f.x);b.pop_back();
            V::Receiver rx(a,0,[&](const V::Request& q){l.admit(q,peer);},[&](const V::Request& q){l.reject(q);});rx.feed(b.data(),b.size(),1);refuses([&]{rx.eof(2);});
            auto q=a.request(a.seal(f.header("QUERY",r),{}));check(l.query(q)==W::ExecutionState::RejectedBeforeStart && l.starts()==0,"reserved rejected");refuses([&]{l.admit(q,peer);});
        });
        test("wrong_body_digest_rejected_zero_start",[&]{V::Authority a(f.context(),hash);V::Ledger l(a,0);auto r=f.request();auto b=wire(f.header("REQUEST",r).dump(),f.x);b.back()=1;
            V::Receiver rx(a,0,[&](const V::Request& q){l.admit(q,peer);},[&](const V::Request& q){l.reject(q);});refuses([&]{rx.feed(b.data(),b.size(),1);});auto q=a.request(a.seal(f.header("QUERY",r),{}));check(l.query(q)==W::ExecutionState::RejectedBeforeStart && l.starts()==0,"bad SHA no start");});
        test("clock_deadline_and_trailing_bytes",[&]{auto b=wire(f.header("REQUEST",f.request()).dump(),f.x);V::Authority a(f.context(),hash);V::Receiver rx(a,10);refuses([&]{rx.feed(b.data(),b.size(),9);});V::Receiver late(a,0);refuses([&]{late.feed(b.data(),b.size(),W::FRAME_NS+1);});V::Receiver tail(a,0);b.push_back(0);refuses([&]{tail.feed(b.data(),b.size(),1);});});
        test("reentrant_receiver_poisoned_even_if_swallowed",[&]{
            V::Receiver* rx=nullptr;V::Authority a(f.context(),[&](const Bytes& b){refuses([&]{rx->feed(nullptr,0,2);});return hash(b);});V::Receiver receiver(a,0);rx=&receiver;
            auto b=wire(f.header("REQUEST",f.request()).dump(),f.x);refuses([&]{rx->feed(b.data(),b.size(),1);});refuses([&]{rx->take();});
        });
        test("reentrant_authority_cannot_mint_nested_proof",[&]{V::Authority* ptr=nullptr;auto h=f.header("REQUEST",f.request());V::Authority a(f.context(),[&](const Bytes& b){refuses([&]{ptr->seal(h,f.x);});return hash(b);});ptr=&a;refuses([&]{a.seal(h,f.x);});refuses([&]{a.seal(f.header("QUERY",f.request()),{});});});
        test("request_ID_metadata_and_peer_rejection",[&]{
            V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);auto changed=f.request();changed["X_sha256"]=std::string(64,'5');
            auto wrong=a.request(a.seal(f.header("QUERY",changed),{}));refuses([&]{l.query(wrong);});refuses([&]{l.admit(q,peer);});
            V::Ledger l2(a,0);refuses([&]{l2.admit(q,{100,200,999});});refuses([&]{l2.admit(q,{0,200,1000});});
        });
        test("start_preconditions_and_unknown_no_stop",[&]{V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);refuses([&]{l.intent(x);});prepare(l,q);l.intent(x);check(l.query(q)==W::ExecutionState::Unknown && !l.can_stop([]{return true;}),"intent retains unknown");refuses([&]{l.reject(q);});refuses([&]{l.started(q,2);});l.started(q,1);refuses([&]{l.started(q,1);});});
        test("result_pending_ACK_no_rehash_and_Y_release",[&]{
            V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);auto y=completed(a,l,f,x);auto counts=a.counters();
            auto pending=l.pending(q);pending.encode();l.query(q);check(l.retained_bytes()==4000 && !l.can_stop([]{return true;}),"pending retained");
            auto next=a.request(a.seal(f.header("REQUEST",f.request("next",1)),f.x));++counts.body_hashes;refuses([&]{l.admit(next,peer);});
            auto ack=a.seal(f.ack(q.json()),{});l.ack(ack,peer,[](const V::Result&){});check(l.retained_bytes()==0 && l.can_stop([]{return true;}),"ACK release");
            l.ack(ack,peer,[](const V::Result&){});check(pending.body()==f.y && y.body()==f.y,"shared immutable Y survives ACK");
            l.admit(next,peer);l.reject(next);l.ack(ack,peer,[](const V::Result&){});
            check(a.counters().body_hashes==counts.body_hashes,"ledger/ACK/encode zero extra body hash");
        });
        test("bad_ACK_digest_and_changed_peer_retain_Y",[&]{V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);completed(a,l,f,x);
            auto h=f.ack(q.json());h["Y_sha256"]=std::string(64,'6');auto ack=a.seal(h,{});refuses([&]{l.ack(ack,peer,[](const V::Result&){});});
            ack=a.seal(f.ack(q.json()),{});refuses([&]{l.ack(ack,{100,201,1000},[](const V::Result&){});});check(l.retained_bytes()==4000 && l.query(q)==W::ExecutionState::CompletedUnacked,"bad ACK retains");});
        test("recovery_requires_real_proof_and_exact_original_peer",[&]{
            V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);completed(a,l,f,x);auto h=f.ack(q.json());h["confirmation"]=f.recovery(q.json());auto ack=a.seal(h,{});
            refuses([&]{l.ack(ack,{101,300,1000},[](const V::Result&){});});
            refuses([&]{l.ack(ack,{101,300,1000},[](const V::Result&){},[](const Json&,const V::Frame&,W::ExecutionPeer,W::ExecutionPeer){throw std::runtime_error("archive corrupt/live original");});});
            check(l.retained_bytes()==4000,"failed proof retains Y");unsigned proofs=0;
            l.ack(ack,{101,300,1000},[](const V::Result&){},[&](const Json&,const V::Frame&,W::ExecutionPeer original,W::ExecutionPeer current){check(W::same_peer(original,peer) && current.pid==101,"actual peers");++proofs;});
            check(proofs==1 && l.retained_bytes()==0,"proven recovery released");l.ack(ack,{101,300,1000},[](const V::Result&){});
            refuses([&]{l.ack(ack,{102,301,1000},[](const V::Result&){});});
        });
        test("completion_readback_failure_faults_retains_output",[&]{V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);prepare(l,q);l.intent(x);l.started(q,1);auto y=a.seal(f.result(q.json()),f.y);
            refuses([&]{l.complete(y,[]{return true;},[](const V::Frame&){throw std::runtime_error("short native/ENOSPC");});});check(l.faulted() && l.retained_bytes()==4000 && !l.can_stop([]{return true;}),"fault retains output");});
        test("ACK_COMMITTED_corruption_retains_result",[&]{V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);completed(a,l,f,x);auto ack=a.seal(f.ack(q.json()),{});
            refuses([&]{l.ack(ack,peer,[](const V::Result&){throw std::runtime_error("full COMPLETED mismatch");});});check(l.faulted() && l.retained_bytes()==4000,"corrupt commit retained");});
        test("reentrant_ledger_callback_faults",[&]{V::Authority a(f.context(),hash);V::Ledger l(a,0);auto x=admitted(a,l,f,f.request());auto q=a.request(x);refuses([&]{l.storage(q,[&](const V::Request&,W::ExecutionPeer){refuses([&]{l.query(q);});});});check(l.faulted(),"swallowed reentry still fault");});
        test("native_done_drain_guard_const_traffic_mismatch",[&]{for(const char* key:{"guards_pass","inputs_unchanged","done_latched","one_start"}){auto h=f.result(f.request());h["native"][key]=false;V::Authority a(f.context(),hash);refuses([&]{a.seal(h,f.y);});}auto h=f.result(f.request());h["native"]["ctrl_after"]=1;V::Authority a(f.context(),hash);refuses([&]{a.seal(h,f.y);});h=f.result(f.request());h["native"]["traffic"]["60"]=2;refuses([&]{a.seal(h,f.y);});});
        test("full_W_hash_and_length_preserved",[&]{V::Authority a(f.context(),hash);auto x=a.seal(f.header("REQUEST",f.request()),f.x);auto q=a.request(x);a.weight(q,f.w);auto changed=f.w;changed[0]=2;refuses([&]{a.weight(q,changed);});changed.pop_back();refuses([&]{a.weight(q,changed);});});
        auto adapters=[&](unsigned& submits,unsigned& comps,unsigned& seals,int fault) {
            V::Adapters out;out.submitted_readback=[&,fault](const V::Request& q,W::ExecutionPeer actual){check(W::same_peer(actual,peer) && q.json()==f.request(),"submitted complete request/peer");++submits;if(fault==1)throw std::runtime_error("SUB fsync failed");};
            out.weight_readback=[&](const V::Request&){return f.w;};
            out.execute=[&,fault](const V::Request& q,const Bytes& x,const Bytes& w,const std::function<void()>& intent,const std::function<void(std::uint64_t)>& started){
                check(x==f.x && w==f.w,"actual full payload");intent();if(fault==2)throw std::runtime_error("lost start observation");started(q.starts_before()+1);
                // Deterministic full INT64 Golden, ordinary CPU fake-kernel only.
                Bytes y(f.y.size());for(unsigned n=0;n<1000;++n){std::int64_t acc=0;for(unsigned k=0;k<192;++k)acc+=std::int8_t(x[k])*std::int8_t(w[k*1000+n]);std::int32_t v=static_cast<std::int32_t>(acc);std::memcpy(y.data()+4*n,&v,4);}
                check(y==f.y,"all 1000 INT64 outputs");return std::make_pair(y,f.native(q.json()));};
            out.native_full_readback=[&,fault](const V::Frame& y){check(y.header().at("native")==f.native(f.request()),"native full readback");if(fault==3)throw std::runtime_error("native corruption");};
            out.completed_commit_readback=[&,fault](const V::Result& y){++comps;check(y.header()==f.result(f.request()),"complete record header");if(fault==4)throw std::runtime_error("COMP fsync failure");};
            out.failure_seal=[&](const V::Request&){++seals;};out.quiet=[]{return true;};return out;
        };
        test("portable_session_two_commit_seams_full_INT64",[&]{unsigned sub=0,comp=0,seal=0;V::Authority a(f.context(),hash);V::Session s(a,0,adapters(sub,comp,seal,0));auto x=admitted(a,s.ledger(),f,f.request());auto y=s.run(x);auto q=a.request(x);
            check(sub==1 && comp==0 && y.body()==f.y && s.ledger().starts()==1,"one start before ACK");s.acknowledge(a.seal(f.ack(q.json()),{}),peer);check(comp==1 && seal==0 && s.ledger().retained_bytes()==0,"two ordered commit/readback seams");});
        test("SUB_failure_zero_start_failure_seal",[&]{unsigned sub=0,comp=0,seal=0;V::Authority a(f.context(),hash);V::Session s(a,0,adapters(sub,comp,seal,1));auto x=admitted(a,s.ledger(),f,f.request());refuses([&]{s.run(x);});check(s.ledger().starts()==0 && seal==1 && comp==0 && s.ledger().query(a.request(x))==W::ExecutionState::RejectedBeforeStart,"SUB fail no start");});
        test("start_observation_loss_UNKNOWN_no_auto_replay",[&]{unsigned sub=0,comp=0,seal=0;V::Authority a(f.context(),hash);V::Session s(a,0,adapters(sub,comp,seal,2));auto x=admitted(a,s.ledger(),f,f.request());refuses([&]{s.run(x);});check(seal==1 && s.ledger().query(a.request(x))==W::ExecutionState::Unknown && !s.ledger().can_stop([]{return true;}),"unknown retained");refuses([&]{s.ledger().admit(a.request(x),peer);});});
        test("session_native_corruption_fault_retains_Y",[&]{unsigned sub=0,comp=0,seal=0;V::Authority a(f.context(),hash);V::Session s(a,0,adapters(sub,comp,seal,3));auto x=admitted(a,s.ledger(),f,f.request());refuses([&]{s.run(x);});check(s.ledger().faulted() && seal==1 && s.ledger().retained_bytes()==4000,"full native fail retained");});
        test("session_COMP_failure_no_release",[&]{unsigned sub=0,comp=0,seal=0;V::Authority a(f.context(),hash);V::Session s(a,0,adapters(sub,comp,seal,4));auto x=admitted(a,s.ledger(),f,f.request());s.run(x);auto q=a.request(x);refuses([&]{s.acknowledge(a.seal(f.ack(q.json()),{}),peer);});check(comp==1 && s.ledger().faulted() && s.ledger().retained_bytes()==4000,"COMP failure no release");});
        test("timing_partition_closes_and_rejects_bad_order",[]{V::LayerTiming t({100,10});for(unsigned i=0;i<V::LayerTiming::Count;++i)t.mark(static_cast<V::LayerTiming::Phase>(i),{110+10*i,11+i});auto total=t.total();std::uint64_t wall=0,cpu=0;for(auto v:t.values()){wall+=v.wall_ns;cpu+=v.thread_cpu_ns;}check(wall==total.wall_ns && cpu==total.thread_cpu_ns,"timing closure");refuses([&]{t.mark(V::LayerTiming::Dump,{200,20});});V::LayerTiming incomplete({1,1});refuses([&]{incomplete.values();});refuses([&]{incomplete.mark(V::LayerTiming::Compute,{2,2});});});
        test("real_clock_sample_available",[]{auto a=V::stamp();std::this_thread::sleep_for(std::chrono::milliseconds(2));auto b=V::stamp();check(b.wall_ns>a.wall_ns && b.thread_cpu_ns>=a.thread_cpu_ns,"monotonic real clocks");});
        test("all_five_shape_credentials_and_invalid_tail",[&]{
            const unsigned shapes[][3]={{197,192,576},{197,192,192},{197,192,768},{197,768,192},{1,192,1000}};
            for(const auto& shape:shapes) {
                auto f2=f;f2.x=Bytes(shape[0]*shape[1],0);f2.w=Bytes(shape[1]*shape[2],1);f2.y=Bytes(4*shape[0]*shape[2],0);
                f2.layers[0]={"public_layer_0",shape[0],shape[1],shape[2],{hash(f2.w)}};V::Authority a(f2.context(),hash);V::Ledger l(a,0);
                auto x=admitted(a,l,f2,f2.request());completed(a,l,f2,x);auto bad=f2.header("REQUEST",f2.request());bad["request"]["N"]=shape[2]-1;
                refuses([&]{a.seal(bad,f2.x);});
            }
        });
        test("terminal_budget_rejection_zero_new_start",[&]{
            V::Authority a(f.context(),hash);refuses([&]{V::Ledger bad(a,0,0);});refuses([&]{V::Ledger bad(a,0,1025);});V::Ledger l(a,0,1);
            auto first=admitted(a,l,f,f.request());l.reject(a.request(first));auto q=a.request(a.seal(f.header("REQUEST",f.request("next")),f.x));
            refuses([&]{l.admit(q,peer);});check(l.starts()==0 && l.can_stop([]{return true;}),"bounded retained IDs no start");
        });
        test("reentrant_admission_rejects_reserved_ID",[&]{
            V::Authority a(f.context(),hash);V::Ledger l(a,0);V::Receiver* pointer=nullptr;
            V::Receiver rx(a,0,[&](const V::Request& q){l.admit(q,peer);refuses([&]{pointer->feed(nullptr,0,1);});},[&](const V::Request& q){l.reject(q);});pointer=&rx;
            auto b=wire(f.header("REQUEST",f.request()).dump(),f.x);refuses([&]{rx.feed(b.data(),b.size(),1);});
            auto q=a.request(a.seal(f.header("QUERY",f.request()),{}));check(l.query(q)==W::ExecutionState::RejectedBeforeStart && l.starts()==0,"reentrant reserved ID rejected");
        });
        std::cout<<Json{{"status","PASS"},{"scope","PUBLIC_ORDINARY_CPP_G1_CORE_NOT_ARM_NOT_PL_NOT_DURABILITY"},{"tests",passed},
            {"test_names",names},{"PL_starts",0},{"INT64_outputs_per_fake_kernel_call",1000},{"private_holdout_accessed",false}}.dump(2)<<'\n';return 0;
    }catch(const std::exception& e){std::cerr<<"FAIL after "<<passed<<" tests: "<<e.what()<<'\n';return 1;}
}
