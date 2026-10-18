#include "client.h"
#include <chrono>
#include <cerrno>
#include <cstring>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_bo.h>

namespace kv5 {
namespace {
using Clock=std::chrono::steady_clock;
void transfer(int fd, void* data, size_t bytes, bool send, Clock::time_point deadline) {
    auto* p=static_cast<unsigned char*>(data);
    while (bytes) {
        auto n=send ? ::send(fd,p,bytes,MSG_NOSIGNAL|MSG_DONTWAIT) : ::recv(fd,p,bytes,MSG_DONTWAIT);
        const int error=errno;
        if(n<0 && (error==EAGAIN || error==EINTR)) {
            int left=int(std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count());
            require(left>0,"owner response timeout; execution unknown, do not replay");
            if(error==EINTR)continue;
            pollfd wait{fd,short(send?POLLOUT:POLLIN),0};
            int r=::poll(&wait,1,left);
            if(r<0 && errno==EINTR)continue;
            require(r>0,"owner I/O timeout");continue;
        }
        require(n>0,"owner connection lost; execution unknown, do not replay");
        p+=n; bytes-=size_t(n);
    }
}
}
struct FdPair {
    std::array<int,2> fd{{-1,-1}};
    FdPair()=default;
    FdPair(const FdPair&)=delete;
    FdPair(FdPair&& other) noexcept :fd(other.fd) { other.fd={{-1,-1}}; }
    ~FdPair(){for(int v:fd)if(v>=0)::close(v);}
};
struct Client::Mapping {
    FdPair descriptors; // Descriptors outlive the imported BOs.
    xrt::device device{0};
    xrt::bo xb,yb;
    int8_t* x;int32_t* y;
    Mapping(FdPair&& incoming,const SharedInfo& info):descriptors(std::move(incoming)),xb(device,descriptors.fd[0]),yb(device,descriptors.fd[1]) {
        require(xb.size()==info.size[0] && yb.size()==info.size[1] && xb.size()>=151296 && yb.size()>=605184,"shared BO size");
        require(xb.address()==info.physical[0] && yb.address()==info.physical[1],"shared BO address");
        require(uint32_t(xb.get_flags())==info.flags[0] && uint32_t(yb.get_flags())==info.flags[1],"shared BO cache flags");
        x=xb.map<int8_t*>();y=yb.map<int32_t*>();
    }
};
int8_t* Client::x_data() const {return mapping->x;}
int32_t* Client::y_data() const {return mapping->y;}
void Client::sync_input(size_t bytes) {mapping->xb.sync(XCL_BO_SYNC_BO_TO_DEVICE,(bytes+63)&~size_t(63),0);}
void Client::sync_output(size_t bytes) {mapping->yb.sync(XCL_BO_SYNC_BO_FROM_DEVICE,(bytes+63)&~size_t(63),0);}
Client::Client(const std::string& path, const std::array<unsigned char,32>& model) {
    sockaddr_un address{}; address.sun_family=AF_UNIX;
    require(path.size()<sizeof(address.sun_path), "socket path too long");
    std::memcpy(address.sun_path,path.c_str(),path.size()+1);
    fd_=::socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);
    try {
        require(fd_>=0 && ::connect(fd_,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"owner connect");
        ucred peer{}; socklen_t size=sizeof(peer);
        require(::getsockopt(fd_,SOL_SOCKET,SO_PEERCRED,&peer,&size)==0 && peer.uid==::getuid(),"owner uid");
        Header hello; hello.bytes=32;
        require(call(hello,model.data()).status==Status::Ok,"model handshake");
        Header request;request.op=Op::Map;auto wire=encode(request);
        auto deadline=Clock::now()+std::chrono::seconds(130);
        transfer(fd_,wire.data(),wire.size(),true,deadline);
        alignas(cmsghdr) char ancillary[CMSG_SPACE(2*sizeof(int))]{};
        iovec io{wire.data(),wire.size()};msghdr msg{};
        msg.msg_iov=&io;msg.msg_iovlen=1;msg.msg_control=ancillary;msg.msg_controllen=sizeof(ancillary);
        ssize_t n;
        for(;;) {
            int left=int(std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count());
            require(left>0,"shared mapping timeout");pollfd ready{fd_,POLLIN,0};
            int r=::poll(&ready,1,left);if(r<0 && errno==EINTR)continue;require(r>0,"shared mapping poll");
            n=::recvmsg(fd_,&msg,MSG_CMSG_CLOEXEC|MSG_DONTWAIT);
            if(n<0 && (errno==EINTR || errno==EAGAIN))continue;
            break;
        }
        require(n>0 && !(msg.msg_flags&(MSG_TRUNC|MSG_CTRUNC)),"shared descriptors receive");
        auto* c=CMSG_FIRSTHDR(&msg);
        require(c && c->cmsg_level==SOL_SOCKET && c->cmsg_type==SCM_RIGHTS && c->cmsg_len==CMSG_LEN(2*sizeof(int)),"two shared descriptors required");
        FdPair incoming;std::memcpy(incoming.fd.data(),CMSG_DATA(c),2*sizeof(int));
        transfer(fd_,wire.data()+n,wire.size()-size_t(n),false,deadline);
        auto response=decode(wire);
        require(response.op==Op::Map && response.status==Status::Ok && response.bytes==sizeof(SharedInfo),"shared mapping response");
        SharedInfo info{};transfer(fd_,&info,sizeof(info),false,deadline);
        mapping=std::make_unique<Mapping>(std::move(incoming),info);

    } catch (...) { if(fd_>=0) ::close(fd_); fd_=-1; throw; }
}
Client::~Client() { mapping.reset(); if(fd_>=0) ::close(fd_); }
Header Client::call(Header h, const void* input, void* output, size_t capacity) {
    try {
        require(fd_>=0 && (!h.bytes || input),"closed client or missing input");
        auto deadline=Clock::now()+std::chrono::seconds(130);
        auto wire=encode(h);
        transfer(fd_,wire.data(),wire.size(),true,deadline);
        transfer(fd_,const_cast<void*>(input),h.bytes,true,deadline);
        transfer(fd_,wire.data(),wire.size(),false,deadline);
        auto response=decode(wire);
        require(response.op==h.op && response.run==h.run && response.sequence==h.sequence && response.layer==h.layer,"unexpected response");
        require(response.bytes<=capacity && (!response.bytes || output),"response length");
        transfer(fd_,output,response.bytes,false,deadline);
        return response;
    } catch (...) { if(fd_>=0) ::close(fd_); fd_=-1; throw; }
}
}
