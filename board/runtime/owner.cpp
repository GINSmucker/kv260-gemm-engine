#include "kernel.h"
#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fstream>
#include <iostream>
#include <poll.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

// Installed OpenSSL 3 exports the stable one-shot ABI; SDK headers are absent.
extern "C" unsigned char* SHA256(const unsigned char*, size_t, unsigned char*);

namespace {
using namespace kv5;
using Clock=std::chrono::steady_clock;
volatile sig_atomic_t stopping=0;
void stop_signal(int) { stopping=1; }
struct Connection {
    int fd=-1;
    bool hello=false;
    Wire wire{};
    Header header{};
    size_t received=0, sent=0;
    std::vector<unsigned char> body;
    const int32_t* output=nullptr;
    size_t output_bytes=0;
    bool sending=false;
    Clock::time_point deadline{};
    void close() {
        if(fd>=0) ::close(fd);
        fd=-1; hello=false; received=sent=0; body.clear(); sending=false;
    }
    ~Connection() { close(); }
    void reply(Status status, const int32_t* p=nullptr, size_t bytes=0) {
        header.status=status; header.bytes=uint32_t(bytes); wire=encode(header);
        output=p; output_bytes=bytes; sent=0; sending=true;
        deadline=Clock::now()+std::chrono::seconds(5);
    }
};
int listen_at(const std::string& path) {
    sockaddr_un address{}; address.sun_family=AF_UNIX;
    require(path.size()<sizeof(address.sun_path),"socket path too long");
    std::memcpy(address.sun_path,path.c_str(),path.size()+1);
    int fd=::socket(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    require(fd>=0,"socket creation");
    // Existing endpoints are never unlinked to steal another owner's address.
    if(::bind(fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))!=0 || ::listen(fd,1)!=0) {
        ::close(fd); throw std::runtime_error("socket bind/listen");
    }
    return fd;
}
void accept_one(int listener,Connection& c) {
    int fd=::accept4(listener,nullptr,nullptr,SOCK_NONBLOCK|SOCK_CLOEXEC);
    if(fd<0) return;
    ucred peer{}; socklen_t n=sizeof(peer);
    if(c.fd>=0 || ::getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&n)!=0 || peer.uid!=::getuid()) { ::close(fd); return; }
    c.fd=fd; c.deadline=Clock::now()+std::chrono::seconds(5);
}
bool read_frame(Connection& c,bool management) {
    // Incremental bounded input: a half frame cannot block the management FD.
    for (;;) {
        size_t target=c.received<32 ? 32 : 32+c.header.bytes;
        if(c.received==target && c.received>=32) return true;
        auto* p=c.received<32 ? c.wire.data()+c.received : c.body.data()+c.received-32;
        auto n=::recv(c.fd,p,target-c.received,MSG_DONTWAIT);
        if(n<0 && (errno==EAGAIN || errno==EINTR)) return false;
        require(n>0,"disconnected input");
        if(c.received==0) c.deadline=Clock::now()+std::chrono::seconds(5);
        c.received+=size_t(n);
        if(c.received==32) {
            c.header=decode(c.wire);
            require(c.header.status==Status::Ok,"request status");
            uint32_t bytes=0;
            if(c.header.op==Op::Hello) bytes=32;
            else if(c.header.op==Op::Run) { require(!management,"management RUN"); bytes=uint32_t(shape(c.header.layer).x_bytes()); }
            require(c.header.bytes==bytes,"request body length");
            c.body.resize(bytes);
            if(!bytes) return true;
        }
    }
}
void write_frame(Connection& c) {
    while(c.sent<32+c.output_bytes) {
        bool header=c.sent<32;
        const void* p=header ? static_cast<const void*>(c.wire.data()+c.sent) :
            static_cast<const void*>(reinterpret_cast<const unsigned char*>(c.output)+c.sent-32);
        size_t bytes=header ? 32-c.sent : 32+c.output_bytes-c.sent;
        auto n=::send(c.fd,p,bytes,MSG_NOSIGNAL|MSG_DONTWAIT);
        if(n<0 && (errno==EAGAIN || errno==EINTR)) return;
        require(n>0,"disconnected output"); c.sent+=size_t(n);
    }
    c.received=c.sent=0; c.body.clear(); c.sending=false; c.output=nullptr; c.output_bytes=0;
    c.deadline=Clock::now()+std::chrono::seconds(130);
}
}

int main(int argc,char** argv) {
    try {
        require(argc==5,"usage: owner weights.bin socket metadata task_root");
        ::umask(0077);
        // One lifetime lock, before opening XRT; it is not touched per request.
        std::string lock_path=std::string(argv[2])+".lock";
        int lock=::open(lock_path.c_str(),O_CREAT|O_RDWR|O_CLOEXEC,0600);
        require(lock>=0 && ::flock(lock,LOCK_EX|LOCK_NB)==0,"owner already running");
        std::ifstream file(argv[1],std::ios::binary);
        require(bool(file),"weights open");
        std::vector<int8_t> weights((std::istreambuf_iterator<char>(file)),{});
        require(weights.size()==5500416,"fixed model weights length");
        std::array<unsigned char,32> model{};
        SHA256(reinterpret_cast<const unsigned char*>(weights.data()),weights.size(),model.data());
        std::array<size_t,49> offsets{};
        for(unsigned i=1;i<49;++i) offsets[i]=offsets[i-1]+shape(i-1).w_bytes();
        // Signals request a stop; no asynchronous stack unwinding of DMA buffers.
        ::signal(SIGTERM,stop_signal); ::signal(SIGINT,stop_signal); ::signal(SIGHUP,stop_signal);
        auto kernel=make_kernel(argv[3],argv[4]);
        std::string path=argv[2], control_path=path+".ctl";
        int listener=listen_at(path), control=listen_at(control_path);
        Connection data, admin;
        enum class Phase { Idle, Running, ResultReady, Fault };
        Phase phase=Phase::Idle;
        uint64_t run=0, highest_run=0, calls=0;
        unsigned next=0, last=49;
        std::vector<int32_t> y(197*768);
#ifdef KV5_DIAGNOSTIC
        std::array<uint64_t,8> metrics{}; // whole service, six native stages, calls
#endif
        std::cout<<"KV5_READY "<<::getpid()<<std::endl;
        auto dispatch=[&](Connection& c,bool management) {
#ifdef KV5_DIAGNOSTIC
            auto entered=Clock::now();
#endif
            auto h=c.header;
            if(h.op==Op::Hello) {
                bool ok=!c.hello && std::equal(model.begin(),model.end(),c.body.begin());
                c.hello=ok; c.reply(ok?Status::Ok:Status::Invalid); return;
            }
            if(!c.hello) { c.reply(Status::Invalid); return; }
            if(h.op==Op::Info) {
#ifdef KV5_DIAGNOSTIC
                c.reply(Status::Ok,reinterpret_cast<const int32_t*>(metrics.data()),sizeof(metrics));
#else
                c.reply(Status::Ok);
#endif
                return;
            }
            if(h.op==Op::Stop) {
                c.reply(Status::Ok);
                // Loop finishes only after this reply is delivered or times out.
                stopping=1; return;
            }
            if(management) { c.reply(Status::Invalid); return; }
            if(phase==Phase::Fault) { c.reply(Status::Fault); return; }
            if(h.op==Op::Begin) {
                if(!h.run || h.run<=highest_run || h.sequence || h.layer) { c.reply(Status::Invalid); return; }
                run=highest_run=h.run; next=0; last=49; phase=Phase::Idle;
                c.reply(Status::Ok); return;
            }
            if(!run || h.run!=run) { c.reply(Status::Invalid); return; }
            if(h.op==Op::Abort) {
                run=0; last=49; phase=Phase::Idle; c.reply(Status::Ok); return;
            }
            if(h.op==Op::Query) {
                if(phase!=Phase::ResultReady || h.layer!=last || h.sequence!=last+1) c.reply(Status::Missing);
                else c.reply(Status::Ok,y.data(),shape(last).y_bytes());
                return;
            }
            if(h.op==Op::End) {
                if(next!=49 || h.sequence!=50 || h.layer!=49) { c.reply(Status::Invalid); return; }
                run=0; last=49; phase=Phase::Idle; c.reply(Status::Ok); return;
            }
            if(h.op!=Op::Run || h.layer!=next || h.sequence!=next+1) { c.reply(Status::Invalid); return; }
            // The complete next X is admitted before overwriting the previous Y.
            phase=Phase::Running;
            try {
                kernel->execute(shape(next),reinterpret_cast<const int8_t*>(c.body.data()),weights.data()+offsets[next],y.data());
                ++calls; last=next++; phase=Phase::ResultReady;
#ifdef KV5_DIAGNOSTIC
                metrics[0]+=std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-entered).count();
                for(unsigned i=0;i<6;++i) metrics[i+1]+=kernel->timing[i];
                ++metrics[7];
#endif
                c.reply(Status::Ok,y.data(),shape(last).y_bytes());
            } catch(const std::exception& e) {
                phase=Phase::Fault; std::cerr<<"KV5_KERNEL_FAULT "<<e.what()<<'\n'; c.reply(Status::Fault);
            }
        };
        while(!stopping || data.sending || admin.sending) {
            pollfd fds[]={{listener,POLLIN,0},{control,POLLIN,0},
                {data.fd,short(data.sending?POLLOUT:POLLIN),0},{admin.fd,short(admin.sending?POLLOUT:POLLIN),0}};
            int r=::poll(fds,4,100);
            if(r<0 && errno==EINTR) continue;
            require(r>=0,"owner poll");
            if(!stopping && (fds[0].revents&POLLIN)) accept_one(listener,data);
            if(!stopping && (fds[1].revents&POLLIN)) accept_one(control,admin);
            for(unsigned i=0;i<2;++i) {
                auto& c=i==0?data:admin;
                if(c.fd<0) continue;
                try {
                    require(Clock::now()<c.deadline,"connection deadline");
                    if(c.sending) { if(fds[i+2].revents) write_frame(c); }
                    else if(!stopping && fds[i+2].revents && read_frame(c,i==1)) dispatch(c,i==1);
                } catch(const std::exception&) { c.close(); }
            }
        }
        data.close(); admin.close(); ::close(listener); ::close(control);
        ::unlink(path.c_str()); ::unlink(control_path.c_str());
        kernel.reset(); ::close(lock);
        std::cout<<"KV5_STOP calls="<<calls<<std::endl;
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
