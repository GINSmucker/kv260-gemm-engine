// Bounded incremental frame codec. It owns one frame, never an execution.
// No socket retry, peer trust, owner state, journaling or hardware lives here.
#pragma once
#include "stream_v2b_semantics.h"
#include <algorithm>
#include <functional>
#include <utility>

namespace kv260_stream_v2b {
using Bytes = std::vector<std::uint8_t>;
using BodyDigest = std::function<std::string(const Bytes&)>;
constexpr std::uint64_t FRAME_NS = UINT64_C(5000000000);
constexpr std::size_t BODY_LIMIT = 605184;
struct Frame { Json header; Bytes body; };

inline void body_fields(const Json& header, const Bytes& body, const TrustedContext& context,
        const BodyDigest& digest) {
    const auto type = message_fields(header, context);
    require(body.size() == uint_value(header.at("body_bytes"),UINT32_MAX) && body.size() <= BODY_LIMIT,
        "exact frame body length");
    if (type == "REQUEST" || type == "RESULT") {
        require(bool(digest), "actual body digest adapter required");
        const auto actual = digest(body); sha_string(Json(actual));
        require(actual == (type == "REQUEST" ? header.at("request").at("X_sha256") : header.at("Y_sha256")),
            "actual frame body SHA mismatch");
    }
}
inline Bytes encode_frame(const Json& header,const Bytes& body,const TrustedContext& context,
        const BodyDigest& digest) {
    // Own both facts before any user-supplied digest callback can run. A
    // mutable alias to the caller's header/body cannot alter the checked frame.
    const Frame owned{header,body};
    body_fields(owned.header,owned.body,context,digest);
    const auto text=owned.header.dump(-1,' ',true,Json::error_handler_t::strict);
    lexical_bounds(text);
    require(text.size() >= 1 && text.size() <= HEADER_LIMIT, "encoded header bound");
    const auto n=static_cast<std::uint32_t>(text.size());
    Bytes result; result.reserve(4+text.size()+body.size());
    for (unsigned i=0;i!=4;++i) result.push_back(static_cast<std::uint8_t>(n>>(8*i)));
    result.insert(result.end(),text.begin(),text.end());
    result.insert(result.end(),owned.body.begin(),owned.body.end());
    return result;
}

class FrameReceiver {
    TrustedContext context_;
    BodyDigest digest_;
    std::array<std::uint8_t,4> prefix_{};
    std::size_t prefix_used_=0,header_target_=0,body_target_=0,body_used_=0;
    std::string header_bytes_;
    Frame frame_;
    std::string expected_body_sha_; // Private, derived only from admitted header.
    bool checking_=false;
    std::uint64_t deadline_,last_now_;
    bool header_ready_=false,complete_=false,failed_=false,delivered_=false;
    void clock(std::uint64_t now) {
        require(now>=last_now_ && now<=deadline_,"absolute frame deadline/clock");
        last_now_=now;
    }
    void complete() {
        require(!checking_ && !failed_,"frame completion lifecycle");checking_=true;
        require(frame_.body.size()==body_target_,"exact private body length");
        if(!expected_body_sha_.empty()) {
            const auto actual=digest_(frame_.body);sha_string(Json(actual));
            require(actual==expected_body_sha_,"actual frame body SHA mismatch");
        }
        require(!failed_,"reentrant frame verification failed");
        checking_=false;complete_=true;
    }
public:
    FrameReceiver(const TrustedContext& context,BodyDigest digest,std::uint64_t begin_ns):
        context_(context),digest_(std::move(digest)),deadline_(0),last_now_(begin_ns) {
        trusted_context(context_);
        require(begin_ns<=UINT64_MAX-FRAME_NS,"frame deadline overflow");
        deadline_=begin_ns+FRAME_NS;
    }
    void feed(const std::uint8_t* input,std::size_t n,std::uint64_t now_ns) {
        try {
            require(!failed_ && !delivered_ && !complete_ && !checking_,"single frame receiver lifecycle");
            require(input!=nullptr || n==0,"null frame fragment"); clock(now_ns);
            while(n) {
                require(!complete_,"bytes after complete frame");
                if(prefix_used_<4) {
                    const auto count=std::min(n,4-prefix_used_);
                    std::copy(input,input+count,prefix_.begin()+prefix_used_);
                    prefix_used_+=count;input+=count;n-=count;
                    if(prefix_used_==4) {
                        const std::uint32_t H=std::uint32_t(prefix_[0]) | std::uint32_t(prefix_[1])<<8 |
                            std::uint32_t(prefix_[2])<<16 | std::uint32_t(prefix_[3])<<24;
                        require(H>=1 && H<=HEADER_LIMIT,"prefix header bound before allocation");
                        header_target_=H;header_bytes_.reserve(H);
                    }
                } else if(!header_ready_) {
                    const auto count=std::min(n,header_target_-header_bytes_.size());
                    header_bytes_.append(reinterpret_cast<const char*>(input),count);
                    input+=count;n-=count;
                    if(header_bytes_.size()==header_target_) {
                        frame_.header=parse_header(header_bytes_);
                        const auto type=message_fields(frame_.header,context_);
                        if(type=="REQUEST" || type=="RESULT") {
                            require(bool(digest_),"actual body digest adapter required");
                            expected_body_sha_=(type=="REQUEST"?frame_.header.at("request").at("X_sha256"):frame_.header.at("Y_sha256")).get<std::string>();
                        }
                        body_target_=static_cast<std::size_t>(uint_value(frame_.header.at("body_bytes"),UINT32_MAX));
                        require(body_target_<=BODY_LIMIT,"body bound before allocation");
                        frame_.body.resize(body_target_);header_ready_=true;
                        if(!body_target_) complete();
                    }
                } else {
                    const auto count=std::min(n,body_target_-body_used_);
                    std::copy(input,input+count,frame_.body.begin()+body_used_);
                    body_used_+=count;input+=count;n-=count;
                    if(body_used_==body_target_) complete();
                }
            }
        } catch(...) {failed_=true;throw;}
    }
    void eof(std::uint64_t now_ns) {
        try {require(!failed_ && !delivered_ && !checking_,"failed/delivered frame EOF");clock(now_ns);
            require(complete_,"short prefix/header/body EOF");}
        catch(...) {failed_=true;throw;}
    }
    std::size_t remaining_body_bytes() const {
        require(header_ready_ && !failed_ && !delivered_ && !checking_,"admitted frame body bound unavailable");
        return body_target_-body_used_;
    }
    bool ready() const {return complete_ && !failed_ && !delivered_;}
    Frame take() {require(ready(),"incomplete or already consumed frame");delivered_=true;return std::move(frame_);}
    std::size_t buffered_payload() const {return header_bytes_.size()+frame_.body.size();}
    std::uint64_t deadline_ns() const {return deadline_;}
};
} // namespace kv260_stream_v2b
