// Owner-side receive: reserve a trusted REQUEST ID before receiving its body.
// Admission/rejection callbacks maintain the same serial ledger. No BO/start.
// A duplicate or untrusted header must never reject an existing execution.
#pragma once
#include "stream_v2a_socket.h"
#include "stream_v2a_lifecycle.h"

namespace kv260_stream_v2a {
using RequestAdmission=std::function<void(const Json&,const ExecutionPeer&)>;
using RequestRejection=std::function<void(const Json&)>;
inline Frame receive_admitted_request_frame(int fd,const TrustedContext& context,const BodyDigest& digest,
        std::uint64_t call_deadline,SocketIOStats& stats,const RequestAdmission& admit,
        const RequestRejection& reject_before_start) {
    require(bool(admit) && bool(reject_before_start),"serial admission/rejection adapters required");
    const auto actual_peer=actual_socket_peer(fd);stats.begin_ns=monotonic_ns();
    stats.deadline_ns=bounded_frame_deadline(stats.begin_ns,call_deadline);
    FrameReceiver receiver(context,digest,stats.begin_ns);
    std::array<std::uint8_t,4> prefix{};
    receive_exact(fd,prefix.data(),prefix.size(),stats.deadline_ns,stats);
    const auto H=std::uint32_t(prefix[0])|std::uint32_t(prefix[1])<<8|
        std::uint32_t(prefix[2])<<16|std::uint32_t(prefix[3])<<24;
    require(H>=1 && H<=HEADER_LIMIT,"prefix header bound before admission/allocation");
    receiver.feed(prefix.data(),prefix.size(),monotonic_ns());
    std::string text(H,'\0');receive_exact(fd,reinterpret_cast<std::uint8_t*>(&text[0]),H,stats.deadline_ns,stats);
    const auto header=parse_header(text);const auto type=message_fields(header,context);
    require(type=="INFO" || type=="REQUEST" || type=="QUERY" || type=="ACK" || type=="STOP",
        "owner accepts only client direction messages");
    auto remaining=static_cast<std::size_t>(uint_value(header.at("body_bytes"),UINT32_MAX));
    require(remaining<=BODY_LIMIT,"body bound before admission/allocation");
    if(type=="REQUEST")require(bool(digest),"actual body digest adapter required");
    bool admitted=false;
    if(type=="REQUEST") {
        // This callback only registers the trusted ID. File/journal/W/BO work
        // happens after complete reception. A thrown duplicate leaves admitted
        // false, so an old record/pending Y cannot be reclassified as rejected.
        admit(header.at("request"),{actual_peer.pid,actual_peer.birth_ticks,actual_peer.uid});
        admitted=true;
    }
    try {
        before_deadline(stats.deadline_ns);
        receiver.feed(reinterpret_cast<const std::uint8_t*>(text.data()),text.size(),monotonic_ns());
        std::array<std::uint8_t,32768> chunk{};
        while(remaining) {
            const auto wanted=std::min(remaining,chunk.size());
            receive_exact(fd,chunk.data(),wanted,stats.deadline_ns,stats);
            receiver.feed(chunk.data(),wanted,monotonic_ns());remaining-=wanted;
        }
        before_deadline(stats.deadline_ns);receiver.eof(monotonic_ns());
        auto result=receiver.take();stats.finish_ns=monotonic_ns();return result;
    }catch(...) {
        if(admitted)reject_before_start(header.at("request"));
        stats.finish_ns=monotonic_ns();throw;
    }
}
} // namespace kv260_stream_v2a
