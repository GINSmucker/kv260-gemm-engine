#include "client.h"
#include <chrono>
#include <cerrno>
#include <cstring>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace kv5 {
namespace {
using Clock=std::chrono::steady_clock;
void transfer(int fd, void* data, size_t bytes, bool send, Clock::time_point deadline) {
    auto* p=static_cast<unsigned char*>(data);
    while (bytes) {
        int left=int(std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count());
        require(left>0, "owner response timeout; execution unknown, do not replay");
        pollfd wait{fd,short(send?POLLOUT:POLLIN),0};
        int r=::poll(&wait,1,left);
        if (r<0 && errno==EINTR) continue;
        require(r>0, "owner I/O timeout");
        auto n=send ? ::send(fd,p,bytes,MSG_NOSIGNAL|MSG_DONTWAIT) : ::recv(fd,p,bytes,MSG_DONTWAIT);
        if (n<0 && (errno==EINTR || errno==EAGAIN)) continue;
        require(n>0, "owner connection lost; execution unknown, do not replay");
        p+=n; bytes-=size_t(n);
    }
}
}
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
    } catch (...) { if(fd_>=0) ::close(fd_); fd_=-1; throw; }
}
Client::~Client() { if(fd_>=0) ::close(fd_); }
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
