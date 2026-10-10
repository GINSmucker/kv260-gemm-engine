// New production owner entry. No programming, retry, queue or unsafe exit.
// Build pins are generated only after the exact B tools/registration freeze.
#include "config.h"
#include "pins.h"
#if defined(KV260_V2B_SOFTWARE_TEST)
#include "software_core.h"
#else
#include "kernel_core.h"
#endif
#include "session.h"
#include <csignal>
#include <memory>
#include <iostream>
namespace V=kv260_stream_v2b;
int main(int argc,char** argv) {
    std::unique_ptr<KernelCore> kernel;std::unique_ptr<V::SerialSession> session;
    try {
#if !defined(KV260_V2B_SOFTWARE_TEST)
        if(argc==3 && std::string(argv[1])=="--check-metadata"){
            xrt::xclbin metadata(argv[2]);metadata_only(metadata);std::cout<<"METADATA_ONLY_PASS "<<metadata.get_uuid().to_string()<<std::endl;return 0;
        }
#endif
        V::require(argc==2 && ::getuid()==1000,"actual uid1000 private config path required");
        // The runner starts the process without XRT, captures its actual PID/
        // birth, then writes this unique 0600 config. No hardware before it.
        std::cout<<"V2B_BOOTSTRAP "<<::getpid()<<' '<<V::process_birth_ticks(::getpid())<<std::endl;
        const std::string file=argv[1];V::require(file.size()>12 && file.substr(file.size()-12)=="/config.json","unique config filename");
        const auto root=file.substr(0,file.size()-12);const auto until=V::monotonic_ns()+V::FRAME_NS;
        struct stat st{};while(::lstat(file.c_str(),&st)!=0){V::require(errno==ENOENT,"config status unavailable");V::before_deadline(until);::usleep(10000);}
        V::require(S_ISREG(st.st_mode) && st.st_uid==1000 && (st.st_mode&0777)==0600 && st.st_nlink==1,"private bootstrap config type/mode");
        V::PrivateFD fd(::open(file.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK));V::require(fd.get()>=0,"private config open");
        const auto raw=V::read_bounded_fd(fd.get(),V::HEADER_LIMIT);auto value=V::parse_header(std::string(raw.begin(),raw.end()));
        const V::BodyDigest hash=[](const V::Bytes& b){return kv260_crypto_sha256(b.data(),b.size());};
        const auto self=V::actual_regular_digest("/proc/self/exe",hash);
        const auto config=V::configuration(value,{self,kv260_v2b_pins::TOOLS_SHA,kv260_v2b_pins::REGISTRATION_SHA},hash);
        V::require(config.context.task_root==root && V::uint_value(config.context.identity.at("owner_pid"),UINT64_MAX)==static_cast<std::uint64_t>(::getpid()) &&
            V::uint_value(config.context.identity.at("owner_birth_ticks"),UINT64_MAX)==V::process_birth_ticks(::getpid()),"actual owner bootstrap binding");
        auto task=V::anchored_task_directory(config.context);auto actual=V::read_private_file(task.get(),"config.json",0600,V::HEADER_LIMIT);
        V::require(actual.bytes==raw,"bootstrap anchored full file changed");
        V::PrivateFD lock(::openat(task.get(),".owner.lock",O_CREAT|O_RDWR|O_NOFOLLOW|O_CLOEXEC,0600));
        V::require(lock.get()>=0 && ::flock(lock.get(),LOCK_EX|LOCK_NB)==0,"unique serial owner lock");
        ::signal(SIGTERM,SIG_IGN);::signal(SIGINT,SIG_IGN);::signal(SIGHUP,SIG_IGN);
        kernel=std::make_unique<KernelCore>(config.metadata,root,0xf04207fb,0xe4ebd6cd);
        session=std::make_unique<V::SerialSession>(config.context,hash,
            [&](const V::Json& r,const V::Bytes& x,const V::Bytes& w,const std::function<void()>& before,const std::function<void(std::uint64_t)>& after){return kernel->execute(r,x,w,before,after);},
            [&]{return kernel->info();},[&]{return kernel->safe();},kernel->info().at("starts").get<std::uint64_t>());
        V::PrivateFD listener(::socket(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0));V::require(listener.get()>=0,"owner listener socket");
        sockaddr_un address{};address.sun_family=AF_UNIX;std::memcpy(address.sun_path,config.socket.c_str(),config.socket.size()+1);
        V::require(::bind(listener.get(),reinterpret_cast<sockaddr*>(&address),sizeof(address))==0 && ::chmod(config.socket.c_str(),0600)==0 &&
            ::listen(listener.get(),1)==0,"unique private owner listener");
        std::cout<<"V2B_LISTEN "<<config.context.identity.dump()<<std::endl;
        while(true){pollfd p{listener.get(),POLLIN,0};const auto ready=::poll(&p,1,1000);
            if(ready<0 && errno==EINTR)continue;
            V::require(ready>=0 && !(p.revents&(POLLERR|POLLNVAL|POLLHUP)),"owner listener poll");if(!ready)continue;
            V::PrivateFD peer(::accept4(listener.get(),nullptr,nullptr,SOCK_NONBLOCK|SOCK_CLOEXEC));
            if(peer.get()<0 && (errno==EAGAIN || errno==EINTR))continue;
            V::require(peer.get()>=0,"owner accept");bool stop=false;
            try {
                V::SocketIOStats received,sent;const auto deadline=V::monotonic_ns()+UINT64_C(130000000000);
                auto response=session->receive_and_dispatch(peer.get(),deadline,received);stop=response.header.at("type")=="STOP_RESULT";
#if defined(KV260_V2B_SOFTWARE_TEST)
                // Drop one actual response only in the no-device test binary.
                // The same dispatcher state remains available to QUERY.
                struct stat fault{},used{};
                if(::fstatat(task.get(),"software_transport.json",&fault,AT_SYMLINK_NOFOLLOW)==0 &&
                    ::fstatat(task.get(),"software_transport_used.json",&used,AT_SYMLINK_NOFOLLOW)!=0 && errno==ENOENT){
                    auto file=V::read_private_file(task.get(),"software_transport.json",0600,V::HEADER_LIMIT);auto control=V::actual_json_file(file);
                    V::exact_keys(control,{"type"});V::require(control.at("type")=="RESULT" || control.at("type")=="ACK_RESULT" || control.at("type")=="ERROR","explicit software response fault");
                    if(control.at("type")==response.header.at("type")){
                        const auto bytes=V::compact_json_bytes(control);
                        V::PrivateFD marker(::openat(task.get(),"software_transport_used.json",O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600));
                        V::require(marker.get()>=0 && ::write(marker.get(),bytes.data(),bytes.size())==static_cast<ssize_t>(bytes.size()),"actual software drop marker");
                        std::cout<<"V2B_SOFTWARE_DROP "<<control.dump()<<std::endl;continue;
                    }
                }
#endif
                V::send_socket_frame(peer.get(),response,config.context,hash,deadline,sent);
            }catch(const std::exception& e){std::cerr<<"V2B_DETACH "<<V::ascii_error(e.what())<<std::endl;}
            // Even a lost STOP response is terminal only after the dispatcher
            // actually accepted STOP and a final independent safety read.
            if(stop){V::require(session->can_stop() && kernel->safe(),"STOP final actual CU/AXI quiet");
                V::require(::unlinkat(task.get(),"owner.sock",0)==0,"remove own terminal socket");
                std::cout<<"V2B_SAFE_STOP "<<kernel->info().dump()<<std::endl;session.reset();kernel.reset();return 0;}
        }
    }catch(const std::exception& e){std::cerr<<"V2B_OWNER_FAULT "<<V::ascii_error(e.what())<<std::endl;
        if(kernel){
            // No stack unwinding may destroy BOs after an uncertain start or
            // discard an unacknowledged result. This is deliberately retained.
            while(true){bool may_exit=false;
                try{may_exit=kernel->safe() && (!session || session->can_stop());}catch(...){}
                if(may_exit)break;
                ::sleep(1);
            }
        }
        return 1;
    }
}
