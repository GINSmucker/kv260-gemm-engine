// Persistent XRT owner: a client timeout/disconnect cannot destroy in-flight BOs.
// Compile on KV260 against the installed XRT 2.18 SDK. No FPGA programming here:
// the supplied xclbin must contain metadata only; platform loading is separate.
#include <xrt/xrt_device.h>
#include <xrt/xrt_bo.h>
#include <experimental/xrt_xclbin.h>
#include <experimental/xrt_ip.h>
#include <array>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

static volatile std::sig_atomic_t stop_requested=0;
static void on_signal(int) { stop_requested=1; }
static void require(bool ok, const char* message) { if(!ok) throw std::runtime_error(message); }
static constexpr size_t BO_BYTES=4096;

static void metadata_only(const xrt::xclbin& binary) {
    const axlf* header=binary.get_axlf();
    require(header && header->m_header.m_numSections==6,"unexpected metadata section count");
    unsigned seen=0;
    for(unsigned i=0;i<header->m_header.m_numSections;++i) {
        unsigned flag=0;
        switch(header->m_sections[i].m_sectionKind) {
        case EMBEDDED_METADATA: flag=1; break;
        case IP_LAYOUT: flag=2; break;
        case MEM_TOPOLOGY: flag=4; break;
        case CONNECTIVITY: flag=8; break;
        case ASK_GROUP_TOPOLOGY: flag=16; break;
        case ASK_GROUP_CONNECTIVITY: flag=32; break;
        default: throw std::runtime_error("non-metadata section in xclbin");
        }
        require(!(seen&flag),"duplicate metadata section");
        seen|=flag;
    }
    require(seen==63,"incomplete metadata xclbin");
}

static std::string hex(const unsigned char* p, size_t n) {
    std::ostringstream s; s<<std::hex<<std::setfill('0');
    for(size_t i=0;i<n;++i) s<<std::setw(2)<<unsigned(p[i]);
    return s.str();
}
static std::array<unsigned char,64> unhex64(const std::string& s) {
    require(s.size()==128,"input must contain 64 bytes");
    std::array<unsigned char,64> r{};
    auto digit=[](char c)->unsigned { if(c>='0'&&c<='9') return c-'0'; if(c>='a'&&c<='f') return c-'a'+10; throw std::runtime_error("invalid hex input"); };
    for(size_t i=0;i<64;++i) r[i]=static_cast<unsigned char>((digit(s[2*i])<<4)|digit(s[2*i+1]));
    return r;
}

class DeviceOwner {
    xrt::device device;
    xrt::uuid uuid;
    std::unique_ptr<xrt::ip> gemm, status;
    std::array<std::unique_ptr<xrt::bo>,3> bos;
    std::array<unsigned char*,3> mapped{};
    std::array<std::array<unsigned char,64>,2> inputs{};
    unsigned written=0, synced=0;
    bool submitted=false;
    bool acknowledged=false;
    bool output_synced=false;
    uint32_t tag0,tag1;
public:
    bool quarantined=false;
    DeviceOwner(const std::string& filename,uint32_t expected0,uint32_t expected1)
        :device(0),tag0(expected0),tag1(expected1) {
        xrt::xclbin binary(filename);
        // Metadata-only registration cannot reprogram the PL. The packaging
        // script also checks the full section inventory and hashes before use.
        metadata_only(binary);
        uuid=device.load_xclbin(binary);
        gemm=std::make_unique<xrt::ip>(device,uuid,"gemm_top:{gemm_1}");
        status=std::make_unique<xrt::ip>(device,uuid,"f8_status:{status_1}");
        // Preserve actual readback when a platform identity check fails.
        std::cout<<"F8_STARTUP_REGISTERS"<<std::hex;
        for(uint32_t off=0;off<=0x3c;off+=4)
            std::cout<<" "<<off<<"="<<status->read_register(off);
        std::cout<<" gemm_ctrl="<<gemm->read_register(0)<<std::dec<<std::endl;
        require(status->read_register(0)==0x46383031,"F8 platform identity mismatch");
        require(status->read_register(4)==0x00010000,"F8 status ABI mismatch");
        require(status->read_register(8)==tag0 && status->read_register(12)==tag1,"F8 build tag mismatch");
        require(idle_and_drained(),"CU is not initially idle and drained");
        for(size_t i=0;i<3;++i) {
            bos[i]=std::make_unique<xrt::bo>(device,BO_BYTES,xrt::bo::flags::normal,0);
            mapped[i]=bos[i]->map<unsigned char*>();
            auto address=bos[i]->address();
            bool low=address>0 && address+BO_BYTES<=0x80000000ULL;
            bool high=address>=0x800000000ULL && address+BO_BYTES<=0x880000000ULL;
            require((low||high) && address%64==0,"BO address outside mapped KV260 DDR or alignment");
            std::memset(mapped[i],0x5a,BO_BYTES);
        }
    }
    uint32_t ctrl() { return gemm->read_register(0); }
    bool drained() {
        return status->read_register(0x14)==0 && status->read_register(0x18)==0 &&
               status->read_register(0x1c)==0 && status->read_register(0x3c)==1;
    }
    bool idle_and_drained() {
        uint32_t c=ctrl(); return (c&4) && !(c&0x83) && drained();
    }
    bool safe_to_release() noexcept {
        try {return idle_and_drained();} catch(...) {return false;}
    }
    std::string snapshot() {
        std::ostringstream s;
        s<<ctrl();
        for(uint32_t off=0x10;off<=0x3c;off+=4) s<<' '<<status->read_register(off);
        s<<' '<<submitted<<' '<<quarantined;
        return s.str();
    }
    void client_gone() {
        if(!safe_to_release() || submitted) quarantined=true;
    }
    std::string command(const std::string& request) {
        std::istringstream in(request);
        std::string op; in>>op;
        if(op=="INFO") {
            std::ostringstream s; s<<tag0<<' '<<tag1<<' '<<uuid.to_string()<<' '<<getpid();
            for(auto& bo:bos) s<<' '<<bo->address()<<' '<<bo->size();
            s<<' '<<quarantined;
            return s.str();
        }
        if(op=="STATUS") return snapshot();
        if(op=="RECOVER") {
            // Only a task that actually finishes and drains can be reclaimed.
            // Permanently blocked AXI traffic remains quarantined in this process.
            auto c=ctrl();
            require(!(c&0x80),"auto restart prevents recovery");
            require(drained(),"AXI transactions remain outstanding");
            if(c&2) {
                gemm->write_register(0,16);
                auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(100);
                while(!idle_and_drained()) {
                    require(std::chrono::steady_clock::now()<deadline,"recovery acknowledgement timeout");
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
                }
            }
            require(idle_and_drained(),"kernel has not stopped");
            std::string evidence=snapshot();
            status->write_register(0x40,0x434c5245);
            require(status->read_register(0x10)==0,"status did not clear");
            submitted=false; acknowledged=false; output_synced=false;
            written=synced=0; quarantined=false;
            return evidence;
        }
        if(op=="DETACH") {
            require(!quarantined && !submitted && idle_and_drained(),"unsafe detach");
            return "DETACHED";
        }
        require(!quarantined,"FAULT: explicit recovery required");
        if(op=="INPUT") {
            unsigned idx=99; std::string data; in>>idx>>data;
            require(idx<2 && !submitted && idle_and_drained(),"input requires idle CU");
            inputs[idx]=unhex64(data);
            std::memset(mapped[idx],0x5a,BO_BYTES);
            std::memcpy(mapped[idx],inputs[idx].data(),64);
            written|=1u<<idx; synced&=~(1u<<idx);
            return "INPUT";
        }
        if(op=="SYNCIN") {
            unsigned idx=99; in>>idx;
            require(idx<2 && (written&(1u<<idx)) && !submitted,"input has not been written");
            bos[idx]->sync(XCL_BO_SYNC_BO_TO_DEVICE,BO_BYTES,0);
            synced|=1u<<idx; return "SYNCED";
        }
        if(op=="READ") {
            uint32_t off=~0u; in>>off; require(off==0,"only CTRL can be read through READ");
            auto c=ctrl();
            if(acknowledged && (c&4) && !(c&0x83) && drained()) {
                submitted=false; acknowledged=false; written=synced=0;
            }
            std::ostringstream s; s<<c<<' '<<status->read_register(0x10); return s.str();
        }
        if(op=="WRITE") {
            uint32_t off=~0u,val=0; in>>off>>val;
            if(off==0 && val==1) {
                require(!submitted && idle_and_drained() && synced==3,"start requires clean idle and synced inputs");
                require(status->read_register(0x10)==0,"AXI error latch is set");
                for(unsigned i=0;i<3;++i) {
                    uint32_t base=0x10+i*12;
                    uint64_t addr=gemm->read_register(base) | (uint64_t(gemm->read_register(base+4))<<32);
                    require(addr==bos[i]->address(),"CU address differs from runtime BO");
                }
                status->write_register(0x40,0x434c5245);
                std::memset(mapped[2],0x5a,BO_BYTES);
                std::memset(mapped[2],0xa5,256);
                bos[2]->sync(XCL_BO_SYNC_BO_TO_DEVICE,BO_BYTES,0);
                output_synced=false; acknowledged=false; submitted=true;
                // Set ownership before a possibly ambiguous Start transaction.
                gemm->write_register(0,1); return "STARTED";
            }
            if(off==0 && val==16) {
                require(submitted && (ctrl()&2) && drained(),"continue requires completed and drained task");
                gemm->write_register(0,16); acknowledged=true; return "ACK";
            }
            bool pointer=off==0x10||off==0x14||off==0x1c||off==0x20||off==0x28||off==0x2c;
            bool disable_irq=(off==4||off==8)&&val==0;
            require((pointer||disable_irq) && !submitted && idle_and_drained(),"unsupported or active MMIO write");
            gemm->write_register(off,val); return "WRITTEN";
        }
        if(op=="SYNCOUT") {
            require(submitted && (ctrl()&2) && drained(),"output is not complete");
            require(status->read_register(0x10)==0,"AXI response/counter error");
            for(unsigned i=0;i<3;++i) {
                bos[i]->sync(XCL_BO_SYNC_BO_FROM_DEVICE,BO_BYTES,0);
                size_t valid=i<2?64:256;
                for(size_t j=valid;j<BO_BYTES;++j) require(mapped[i][j]==0x5a,"BO tail guard modified");
                if(i<2) require(std::memcmp(mapped[i],inputs[i].data(),64)==0,"device modified input");
            }
            require(status->read_register(0x20)>0 && status->read_register(0x28)>0 &&
                    status->read_register(0x34)==64 && status->read_register(0x38)>0,"missing PL traffic evidence");
            output_synced=true; return "SYNCED";
        }
        if(op=="OUTPUT") {
            require(output_synced && submitted && (ctrl()&2) && drained(),"output has not been safely synchronized");
            return hex(mapped[2],256);
        }
        throw std::runtime_error("unknown request");
    }
};

int main(int argc,char**argv) {
    if(argc==3 && std::string(argv[1])=="--inspect") {
        try {
            xrt::xclbin binary(argv[2]);
            metadata_only(binary);
            for(const auto& name : {"gemm_top:{gemm_1}","f8_status:{status_1}"}) {
                auto ips=binary.get_ips(name);
                require(ips.size()==1,"metadata IP is missing or ambiguous");
                require(ips[0].get_size()>=0x80,"metadata register aperture too small");
            }
            std::cout<<"F8_METADATA_PARSE_PASS uuid="<<binary.get_uuid().to_string()<<std::endl;
            return 0;
        } catch(const std::exception& e) {std::cerr<<e.what()<<std::endl;return 2;}
    }
    if(argc!=5) {std::cerr<<"usage: f8_xrt_service metadata.xclbin socket-path tag0 tag1\n"; return 2;}
    std::signal(SIGHUP,on_signal); std::signal(SIGTERM,on_signal); std::signal(SIGINT,on_signal);
    std::signal(SIGPIPE,SIG_IGN);
    std::unique_ptr<DeviceOwner> owner;
    int listener=-1, lockfd=-1;
    std::string socket_path=argv[2];
    bool socket_created=false;
    try {
        require(socket_path.size()<sizeof(sockaddr_un::sun_path),"socket path too long");
        umask(0077);
        lockfd=open((socket_path+".lock").c_str(),O_CREAT|O_RDWR,0600);
        require(lockfd>=0 && flock(lockfd,LOCK_EX|LOCK_NB)==0,"service lock unavailable");
        require(access(socket_path.c_str(),F_OK)!=0,"socket already exists; inspect existing owner");
        owner=std::make_unique<DeviceOwner>(argv[1],std::stoul(argv[3],nullptr,0),std::stoul(argv[4],nullptr,0));
        listener=socket(AF_UNIX,SOCK_STREAM,0); require(listener>=0,"socket creation failed");
        sockaddr_un address{}; address.sun_family=AF_UNIX;
        std::memcpy(address.sun_path,socket_path.c_str(),socket_path.size()+1);
        require(bind(listener,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"socket bind failed");
        socket_created=true;
        require(listen(listener,1)==0,"socket listen failed");
        std::cout<<"F8_XRT_SERVICE_READY pid="<<getpid()<<std::endl;
        while(!stop_requested || !owner->safe_to_release()) {
            pollfd poller{listener,POLLIN,0};
            if(poll(&poller,1,500)<=0) continue;
            int client=accept(listener,nullptr,nullptr); if(client<0) continue;
            std::string line; bool detached=false;
            while(true) {
                pollfd peer{client,POLLIN,0};
                int available=poll(&peer,1,500);
                if(available<0 && errno==EINTR) continue;
                if(available==0) {if(stop_requested && owner->safe_to_release()) break; continue;}
                if(available<0) break;
                char c; if(recv(client,&c,1,0)!=1) break;
                if(c!='\n') {line+=c; if(line.size()>2048) break; continue;}
                std::string reply;
                try {
                    reply="OK "+owner->command(line)+"\n";
                    if(line=="DETACH") detached=true;
                } catch(const std::exception& e) {
                    owner->quarantined=true;
                    reply="ERR "+std::string(e.what())+"\n";
                    std::cerr<<"F8_REQUEST_ERROR "<<e.what()<<std::endl;
                }
                line.clear();
                size_t sent=0;
                while(sent<reply.size()) {
                    ssize_t n=send(client,reply.data()+sent,reply.size()-sent,MSG_NOSIGNAL);
                    if(n<=0) break;
                    sent+=size_t(n);
                }
                if(sent!=reply.size() || detached) break;
            }
            close(client);
            if(!detached) owner->client_gone();
        }
        std::cout<<"F8_XRT_SERVICE_SAFE_STOP"<<std::endl;
    } catch(const std::exception& e) {
        std::cerr<<"F8_XRT_SERVICE_ERROR "<<e.what()<<std::endl;
        if(owner && !owner->safe_to_release()) {
            std::cerr<<"F8_QUARANTINE: keeping runtime and BOs alive; process must not be killed"<<std::endl;
            while(!owner->safe_to_release()) std::this_thread::sleep_for(std::chrono::seconds(1));
        }
        if(socket_created) unlink(socket_path.c_str());
        if(listener>=0) close(listener);
        if(lockfd>=0) close(lockfd);
        return 2;
    }
    if(socket_created) unlink(socket_path.c_str());
    if(listener>=0) close(listener);
    if(lockfd>=0) close(lockfd);
    return 0;
}
