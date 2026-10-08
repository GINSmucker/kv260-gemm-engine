// G1 ordinary-memory candidate. External semantics stay ABI3/wire3; no devices.
#pragma once
#include "../v2b/protocol/stream_v2b_frame.h"
#include <atomic>
#include <memory>

namespace kv260_runtime_v3 {
namespace Wire = kv260_stream_v2b;
using Wire::Json;
using Wire::Bytes;
using Wire::BodyDigest;
using Wire::require;

struct Counters { std::uint64_t parses=0, header_encodes=0, request_encodes=0, body_hashes=0, weight_hashes=0; };
class Authority;
class Receiver;
class Request;
class Result;
class Frame {
    struct Scope {
        const Wire::TrustedContext context;
        const std::uint64_t generation;
        Scope(const Wire::TrustedContext& c,std::uint64_t g):context(c),generation(g) {}
    };
    struct Header {
        const std::shared_ptr<const Scope> scope;
        const Json json;
        const std::string canonical, request_text, type;
        const std::string request_id;
        const std::uint64_t starts_before;
        const std::size_t body_bytes;
        Header(std::shared_ptr<const Scope> s,Json j,std::string encoded,std::string request,
               std::string t,std::size_t n,std::string id,std::uint64_t starts):scope(std::move(s)),json(std::move(j)),
            canonical(std::move(encoded)),request_text(std::move(request)),type(std::move(t)),
            request_id(std::move(id)),starts_before(starts),body_bytes(n) {}
    };
    struct Data {
        const std::shared_ptr<const Header> header;
        const Bytes body;
        Data(std::shared_ptr<const Header> h,Bytes b):header(std::move(h)),body(std::move(b)) {}
    };
    std::shared_ptr<const Data> data_;
    explicit Frame(std::shared_ptr<const Header> h,Bytes b):
        data_(std::make_shared<const Data>(std::move(h),std::move(b))) {}
    friend class Authority;
    friend class Receiver;
    friend class Request;
    friend class Result;
public:
    Frame(const Frame&)=default;
    Frame(Frame&& other) noexcept:Frame(static_cast<const Frame&>(other)) {}
    Frame& operator=(const Frame&)=default;
    const Json& header() const {return data_->header->json;}
    const Bytes& body() const {return data_->body;}
    const std::string& type() const {return data_->header->type;}
    // Uses already owned canonical bytes. No callbacks, serialization or hash.
    Bytes encode() const {
        const auto& h=data_->header->canonical;
        Bytes out;out.reserve(4+h.size()+body().size());
        for(unsigned i=0;i<4;++i)out.push_back(static_cast<std::uint8_t>(h.size()>>(8*i)));
        out.insert(out.end(),h.begin(),h.end());out.insert(out.end(),body().begin(),body().end());return out;
    }
};
class Request {
    const std::shared_ptr<const Frame::Header> header_;
    explicit Request(std::shared_ptr<const Frame::Header> h):header_(std::move(h)) {
        require(header_->json.contains("request") && !header_->json.at("request").is_null(),"request credential required");
    }
    friend class Authority;
    friend class Receiver;
public:
    Request(const Request&)=default;
    Request(Request&& other) noexcept:Request(static_cast<const Request&>(other)) {}
    Request& operator=(const Request&)=delete;
    const Json& json() const {return header_->json.at("request");}
    const std::string& id() const {return header_->request_id;}
    std::uint64_t starts_before() const {return header_->starts_before;}
    const std::string& message_type() const {return header_->type;}
    bool same(const Request& other) const {
        return header_->scope==other.header_->scope && header_->request_text==other.header_->request_text;
    }
};
class Result {
    const std::shared_ptr<const Frame::Header> header_;
    explicit Result(std::shared_ptr<const Frame::Header> h):header_(std::move(h)) {
        require(header_->type=="RESULT","RESULT credential required");
    }
    friend class Authority;
public:
    Result(const Result&)=default;
    Result(Result&& other) noexcept:Result(static_cast<const Result&>(other)) {}
    const Json& header() const {return header_->json;}
};
class Authority {
    std::shared_ptr<const Frame::Scope> scope_;
    BodyDigest digest_;
    Counters counters_;
    bool busy_=false, poisoned_=false;
    struct Guard {
        Authority& a;
        explicit Guard(Authority& v):a(v) {
            if(a.busy_){a.poisoned_=true;throw std::runtime_error("reentrant credential validation");}
            require(!a.poisoned_,"poisoned validation authority");a.busy_=true;
        }
        ~Guard(){a.busy_=false;}
    };
    std::shared_ptr<const Frame::Header> own_header(Json value) {
        const auto type=Wire::message_fields(value,scope_->context);
        const auto count=static_cast<std::size_t>(Wire::uint_value(value.at("body_bytes"),UINT32_MAX));
        require(count<=Wire::BODY_LIMIT,"body bound before allocation");
        auto encoded=value.dump(-1,' ',true,Json::error_handler_t::strict);++counters_.header_encodes;
        Wire::lexical_bounds(encoded);
        std::string request;
        std::string id;std::uint64_t starts=0;
        if(value.contains("request") && !value.at("request").is_null()) {
            request=value.at("request").dump(-1,' ',true,Json::error_handler_t::strict);++counters_.request_encodes;
            id=value.at("request").at("execution_id").get<std::string>();
            starts=Wire::uint_value(value.at("request").at("starts_before"),UINT64_MAX);
        }
        return std::make_shared<const Frame::Header>(scope_,std::move(value),std::move(encoded),std::move(request),type,count,std::move(id),starts);
    }
    std::shared_ptr<const Frame::Header> parse(const std::string& bytes) {
        Guard guard(*this);++counters_.parses;return own_header(Wire::parse_header(bytes));
    }
    Frame finish(std::shared_ptr<const Frame::Header> h,Bytes body) {
        Guard guard(*this);require(h->scope==scope_ && body.size()==h->body_bytes,"exact owned frame length/scope");
        if(h->type=="REQUEST" || h->type=="RESULT") {
            ++counters_.body_hashes;const auto actual=digest_(body);Wire::sha_string(Json(actual));
            require(!poisoned_,"reentrant digest poisoned authority");
            require(actual==(h->type=="REQUEST"?h->json.at("request").at("X_sha256"):h->json.at("Y_sha256")),"owned body SHA mismatch");
        }
        return Frame(std::move(h),std::move(body));
    }
    friend class Receiver;
public:
    explicit Authority(const Wire::TrustedContext& c,BodyDigest digest):digest_(std::move(digest)) {
        require(bool(digest_),"real digest adapter required");
        // CAS rejects exhaustion rather than wrapping into an old generation.
        static std::atomic<std::uint64_t> next{1};auto value=next.load();
        do {require(value!=UINT64_MAX,"generation exhausted");}while(!next.compare_exchange_weak(value,value+1));
        scope_=std::make_shared<const Frame::Scope>(c,value);
    }
    Authority(const Authority&)=delete;
    Authority& operator=(const Authority&)=delete;
    Frame seal(const Json& external_header,const Bytes& external_body) {
        // Deep-copy *both* before any untrusted callback can change aliases.
        Json header=external_header;Bytes body=external_body;
        std::shared_ptr<const Frame::Header> owned;
        {Guard guard(*this);owned=own_header(std::move(header));}
        return finish(std::move(owned),std::move(body));
    }
    Request request(const Frame& frame) const {check(frame);return Request(frame.data_->header);}
    Result result(const Frame& frame) const {check(frame);return Result(frame.data_->header);}
    void weight(const Request& r,const Bytes& owned) {
        Guard guard(*this);check(r);
        require(owned.size()==Wire::uint_value(r.json().at("W_bytes"),UINT32_MAX),"full owned W length");
        ++counters_.weight_hashes;const auto hash=digest_(owned);Wire::sha_string(Json(hash));
        require(!poisoned_ && hash==r.json().at("W_sha256"),"full owned W SHA mismatch");
    }
    void check(const Frame& f) const {require(!poisoned_ && f.data_->header->scope==scope_,"foreign task/generation frame credential");}
    void check(const Request& r) const {require(!poisoned_ && r.header_->scope==scope_,"foreign task/generation request credential");}
    const Wire::TrustedContext& context() const {return scope_->context;}
    const Counters& counters() const {return counters_;}
    std::uint64_t generation() const {return scope_->generation;}
};

// The admission callback receives the immutable credential immediately after
// the *only* strict parse. It reserves the ID before body allocation/reception.
class Receiver {
    Authority& authority_;
    std::function<void(const Request&)> admit_,reject_;
    Bytes prefix_,body_;
    std::string text_;
    std::shared_ptr<const Frame::Header> header_;
    std::shared_ptr<const Frame> complete_;
    std::uint64_t deadline_,last_;
    std::size_t target_=0,used_=0;
    bool admitted_=false,failed_=false,delivered_=false,busy_=false;
    void fail() {
        failed_=true;
        if(admitted_) {admitted_=false;if(reject_)reject_(Request(header_));}
    }
public:
    Receiver(Authority& a,std::uint64_t now,std::function<void(const Request&)> admit={},
             std::function<void(const Request&)> reject={}):authority_(a),admit_(std::move(admit)),reject_(std::move(reject)),deadline_(0),last_(now) {
        require(bool(admit_)==bool(reject_),"paired admission/rejection callbacks");
        require(now<=UINT64_MAX-Wire::FRAME_NS,"deadline overflow");deadline_=now+Wire::FRAME_NS;
    }
    void feed(const std::uint8_t* p,std::size_t n,std::uint64_t now) {
        if(busy_){fail();throw std::runtime_error("reentrant receiver");}
        busy_=true;
        try {
            require(!failed_ && !delivered_ && !complete_,"receiver lifecycle");
            require((p || !n) && now>=last_ && now<=deadline_,"receiver pointer/absolute clock");last_=now;
            while(n) {
                require(!complete_,"trailing frame bytes");
                if(prefix_.size()<4) {
                    const auto count=std::min(n,4-prefix_.size());prefix_.insert(prefix_.end(),p,p+count);p+=count;n-=count;
                    if(prefix_.size()==4) {
                        target_=std::uint32_t(prefix_[0])|std::uint32_t(prefix_[1])<<8|std::uint32_t(prefix_[2])<<16|std::uint32_t(prefix_[3])<<24;
                        require(target_ && target_<=Wire::HEADER_LIMIT,"prefix bound before allocation");text_.reserve(target_);
                    }
                } else if(!header_) {
                    const auto count=std::min(n,target_-text_.size());text_.append(reinterpret_cast<const char*>(p),count);p+=count;n-=count;
                    if(text_.size()==target_) {
                        header_=authority_.parse(text_);
                        if(admit_) {
                            const auto& t=header_->type;
                            require(t=="INFO" || t=="REQUEST" || t=="QUERY" || t=="ACK" || t=="STOP","client direction required");
                            if(t=="REQUEST") {admit_(Request(header_));admitted_=true;}
                        }
                        require(!failed_,"reentrant admission poisoned receiver");body_.resize(header_->body_bytes);
                        if(body_.empty())complete_=std::make_shared<const Frame>(authority_.finish(header_,std::move(body_)));
                    }
                } else {
                    const auto count=std::min(n,body_.size()-used_);std::copy(p,p+count,body_.begin()+used_);p+=count;n-=count;used_+=count;
                    if(used_==body_.size())complete_=std::make_shared<const Frame>(authority_.finish(header_,std::move(body_)));
                }
            }
            require(!failed_,"receiver poisoned by callback");busy_=false;
        }catch(...) {busy_=false;fail();throw;}
    }
    void eof(std::uint64_t now) {
        try {require(!busy_ && !failed_ && !delivered_ && complete_ && now>=last_ && now<=deadline_,"short frame/deadline EOF");last_=now;}
        catch(...) {fail();throw;}
    }
    Frame take() {require(!busy_ && !failed_ && !delivered_ && bool(complete_),"frame unavailable/consumed");delivered_=true;admitted_=false;return *complete_;}
};
} // namespace kv260_runtime_v3
