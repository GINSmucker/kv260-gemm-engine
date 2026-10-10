// Linux AF_UNIX transport for the isolated V2A software checks.
// This module never submits REQUEST, owns BO, infers hardware safety or ACKs Y.
// All descriptors must already be connected and nonblocking. Closing a socket
// is only detach; callers must preserve their UNKNOWN/execution state.
#pragma once
#if !defined(__linux__)
#error V2A native socket transport requires Linux
#endif
#include "stream_v2a_frame.h"
#include <cerrno>
#include <climits>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iterator>
#include <poll.h>
#include <sstream>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

namespace kv260_stream_v2a {
struct SocketPeer {
    std::uint64_t pid, birth_ticks;
    std::uint32_t uid, gid;
};
struct SocketIOStats {
    std::uint64_t bytes=0, calls=0, short_io=0, eagain=0, eintr=0, polls=0;
    std::uint64_t begin_ns=0, deadline_ns=0, finish_ns=0;
};
inline std::uint64_t monotonic_ns() {
    timespec value{};
    require(::clock_gettime(CLOCK_MONOTONIC,&value)==0 && value.tv_sec>=0 &&
        value.tv_nsec>=0 && value.tv_nsec<1000000000,"socket monotonic clock");
    const auto seconds=static_cast<std::uint64_t>(value.tv_sec);
    require(seconds<=(UINT64_MAX-UINT64_C(999999999))/UINT64_C(1000000000),
        "socket clock overflow");
    return seconds*UINT64_C(1000000000)+static_cast<std::uint64_t>(value.tv_nsec);
}
inline void before_deadline(std::uint64_t deadline) {
    require(monotonic_ns()<deadline,"absolute socket deadline");
}
inline std::uint64_t process_birth_ticks(std::uint64_t pid) {
    require(pid!=0 && pid<=static_cast<std::uint64_t>(INT_MAX),"peer PID bound");
    std::ifstream input("/proc/"+std::to_string(pid)+"/stat",std::ios::binary);
    require(bool(input),"actual peer process stat unavailable");
    // Linux comm can contain spaces and closing parentheses. Parse after the
    // final ')' and count from field 3; starttime is field 22 (index 19).
    std::string text;
    char c=0;
    while(input.get(c)) {
        require(text.size()<4096,"peer process stat bound"); text.push_back(c);
    }
    require(input.eof(),"peer process stat read failure");
    const auto end=text.rfind(')');
    require(end!=std::string::npos && end+2<text.size(),"peer process stat format");
    std::istringstream fields(text.substr(end+2)); std::string token;
    for(unsigned index=0;index!=20;++index)require(bool(fields>>token),"peer process stat fields");
    require(!token.empty() && token.size()<=20,"peer birth token bound");
    std::uint64_t birth=0;
    for(const unsigned char digit:token) {
        require(digit>='0' && digit<='9' && birth<=(UINT64_MAX-(digit-'0'))/10,
            "peer birth integer");
        birth=birth*10+(digit-'0');
    }
    require(birth!=0,"peer birth nonzero");return birth;
}
inline void connected_nonblocking_unix_stream(int fd) {
    const int flags=::fcntl(fd,F_GETFL);
    require(flags>=0 && (flags&O_NONBLOCK),"nonblocking socket required");
    int type=0;socklen_t length=sizeof(type);
    require(::getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&length)==0 &&
        length==sizeof(type) && type==SOCK_STREAM,"stream socket required");
    sockaddr_un peer{};length=sizeof(peer);
    require(::getpeername(fd,reinterpret_cast<sockaddr*>(&peer),&length)==0 &&
        peer.sun_family==AF_UNIX,"connected AF_UNIX required");
}
inline SocketPeer actual_socket_peer(int fd,std::uint32_t required_uid=1000) {
    connected_nonblocking_unix_stream(fd);
    ucred peer{};socklen_t length=sizeof(peer);
    require(::getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&length)==0 &&
        length==sizeof(peer) && peer.pid>0,"actual SO_PEERCRED required");
    require(peer.uid==required_uid,"actual peer uid mismatch");
    return {static_cast<std::uint64_t>(peer.pid),process_birth_ticks(peer.pid),
        static_cast<std::uint32_t>(peer.uid),static_cast<std::uint32_t>(peer.gid)};
}
inline void verify_socket_owner(int fd,const TrustedContext& context) {
    trusted_context(context);const auto peer=actual_socket_peer(fd);
    require(peer.pid==uint_value(context.identity.at("owner_pid"),UINT64_MAX) &&
        peer.birth_ticks==uint_value(context.identity.at("owner_birth_ticks"),UINT64_MAX),
        "actual owner PID/birth mismatch");
}
inline void poll_socket(int fd,short events,std::uint64_t deadline,SocketIOStats& stats) {
    while(true) {
        const auto now=monotonic_ns();require(now<deadline,"absolute socket deadline");
        const auto remaining=deadline-now;
        const auto milliseconds=remaining/UINT64_C(1000000)+(remaining%UINT64_C(1000000)!=0);
        const int timeout=milliseconds>INT_MAX?INT_MAX:static_cast<int>(milliseconds);
        pollfd descriptor{fd,events,0};++stats.polls;
        const int ready=::poll(&descriptor,1,timeout);
        if(ready<0 && errno==EINTR){++stats.eintr;continue;}
        require(ready>=0,"socket poll failed");before_deadline(deadline);
        if(!ready)continue;
        require(!(descriptor.revents&POLLNVAL),"invalid socket poll descriptor");
        require(!(descriptor.revents&POLLERR),"socket poll error");
        // HUP is observed by recv/send themselves. Pending readable bytes may
        // still exist. EOF is never an execution or client-death proof.
        if(descriptor.revents&(events|POLLHUP))return;
    }
}
inline void receive_exact(int fd,std::uint8_t* output,std::size_t bytes,
        std::uint64_t deadline,SocketIOStats& stats) {
    require(output!=nullptr || bytes==0,"null socket read buffer");
    std::size_t offset=0;
    while(offset<bytes) {
        before_deadline(deadline);const auto wanted=std::min<std::size_t>(bytes-offset,32768);
        ++stats.calls;const auto count=::recv(fd,output+offset,wanted,MSG_DONTWAIT);
        if(count<0 && errno==EINTR){++stats.eintr;continue;}
        if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) {
            ++stats.eagain;poll_socket(fd,POLLIN,deadline,stats);continue;
        }
        require(count>=0,"socket recv failed");require(count!=0,"socket EOF before exact frame");
        if(static_cast<std::size_t>(count)<wanted)++stats.short_io;
        offset+=static_cast<std::size_t>(count);stats.bytes+=static_cast<std::uint64_t>(count);
        before_deadline(deadline);
    }
}
inline void send_exact(int fd,const std::uint8_t* input,std::size_t bytes,
        std::uint64_t deadline,SocketIOStats& stats) {
    require(input!=nullptr || bytes==0,"null socket write buffer");
    std::size_t offset=0;
    while(offset<bytes) {
        before_deadline(deadline);const auto wanted=std::min<std::size_t>(bytes-offset,32768);
        ++stats.calls;const auto count=::send(fd,input+offset,wanted,MSG_DONTWAIT|MSG_NOSIGNAL);
        if(count<0 && errno==EINTR){++stats.eintr;continue;}
        if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) {
            ++stats.eagain;poll_socket(fd,POLLOUT,deadline,stats);continue;
        }
        require(count>=0,"socket send failed");require(count!=0,"zero socket send");
        if(static_cast<std::size_t>(count)<wanted)++stats.short_io;
        offset+=static_cast<std::size_t>(count);stats.bytes+=static_cast<std::uint64_t>(count);
        before_deadline(deadline);
    }
}
inline std::uint64_t bounded_frame_deadline(std::uint64_t begin,std::uint64_t call_deadline) {
    require(begin<=UINT64_MAX-FRAME_NS && begin<call_deadline,"frame/call deadline bound");
    return std::min(begin+FRAME_NS,call_deadline);
}
inline Frame receive_socket_frame(int fd,const TrustedContext& context,const BodyDigest& digest,
        std::uint64_t call_deadline,SocketIOStats& stats) {
    actual_socket_peer(fd);stats.begin_ns=monotonic_ns();
    stats.deadline_ns=bounded_frame_deadline(stats.begin_ns,call_deadline);
    FrameReceiver receiver(context,digest,stats.begin_ns);
    std::array<std::uint8_t,4> prefix{};
    receive_exact(fd,prefix.data(),prefix.size(),stats.deadline_ns,stats);
    const std::uint32_t H=std::uint32_t(prefix[0]) | std::uint32_t(prefix[1])<<8 |
        std::uint32_t(prefix[2])<<16 | std::uint32_t(prefix[3])<<24;
    require(H>=1 && H<=HEADER_LIMIT,"prefix header bound before allocation");
    receiver.feed(prefix.data(),prefix.size(),monotonic_ns());
    std::string header(H,'\0');
    receive_exact(fd,reinterpret_cast<std::uint8_t*>(&header[0]),H,stats.deadline_ns,stats);
    receiver.feed(reinterpret_cast<const std::uint8_t*>(header.data()),header.size(),monotonic_ns());
    const auto parsed=parse_header(header);const auto type=message_fields(parsed,context);
    if(type=="REQUEST" || type=="RESULT")require(bool(digest),"actual body digest adapter required");
    auto remaining=static_cast<std::size_t>(uint_value(parsed.at("body_bytes"),UINT32_MAX));
    require(remaining<=BODY_LIMIT,"body bound before socket read");
    std::array<std::uint8_t,32768> chunk{};
    while(remaining) {
        const auto wanted=std::min(remaining,chunk.size());
        receive_exact(fd,chunk.data(),wanted,stats.deadline_ns,stats);
        receiver.feed(chunk.data(),wanted,monotonic_ns());remaining-=wanted;
    }
    before_deadline(stats.deadline_ns);receiver.eof(monotonic_ns());
    auto frame=receiver.take();stats.finish_ns=monotonic_ns();return frame;
}
inline void send_socket_frame(int fd,const Frame& frame,const TrustedContext& context,
        const BodyDigest& digest,std::uint64_t call_deadline,SocketIOStats& stats) {
    actual_socket_peer(fd);stats.begin_ns=monotonic_ns();
    stats.deadline_ns=bounded_frame_deadline(stats.begin_ns,call_deadline);
    const auto wire=encode_frame(frame.header,frame.body,context,digest);
    send_exact(fd,wire.data(),wire.size(),stats.deadline_ns,stats);
    stats.finish_ns=monotonic_ns();
}
} // namespace kv260_stream_v2a
