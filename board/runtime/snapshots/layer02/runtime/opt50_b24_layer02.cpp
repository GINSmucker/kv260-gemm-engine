// Two authorized B24_dev0000_layer02 STARTs, Dense and Sparse encodings of the
// SAME pruned weights. This is not an unpruned baseline/model/benchmark run.
// No driver, overlay, clock, reset,
// recovery, retry, firmware loading, or old-guardian lock manipulation exists.
#include "opt50_b24_layer02_pure.hpp"
#include <chrono>
#include <atomic>
#include <cstring>
#include <iomanip>
#include <set>
#include <thread>

#ifdef OPT50_WITH_XRT
#if !defined(__linux__)
#error OPT50_WITH_XRT requires ARM Linux; offline builds have no hardware code.
#endif
#include <xrt/xrt_device.h>
#include <xrt/xrt_bo.h>
#include <experimental/xrt_xclbin.h>
#include <experimental/xrt_ip.h>
#include <csignal>
#include <cerrno>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/utsname.h>
#include <unistd.h>
#endif

namespace {
using namespace b24layer02;
[[maybe_unused]] std::string quote(const std::string& s) {
    std::ostringstream out;out<<'"';
    for(unsigned char c:s) {
        if(c=='"'||c=='\\')out<<'\\'<<char(c);
        else if(c<0x20)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(c)<<std::dec;
        else out<<char(c);
    }
    out<<'"';return out.str();
}
struct Options {
    std::string job,receipts;int lock_fd=-1;
    bool run=false,authorized=false,self=false,offline=false;
};
Options options(int argc,char** argv) {
    Options o;std::set<std::string> seen;
    for(int i=1;i<argc;++i) {
        const std::string arg=argv[i];
        require(seen.insert(arg).second,"duplicate command option: "+arg);
        auto next=[&](){require(i+1<argc,"missing option value");return std::string(argv[++i]);};
        if(arg=="--job")o.job=next();
        else if(arg=="--receipt-dir")o.receipts=next();
        else if(arg=="--lock-fd") {const auto n=number(next());require(n>=3 && n<=65535,"inherited lock FD range");o.lock_fd=int(n);}
        else if(arg=="--run")o.run=true;
        else if(arg=="--authorized-two-starts")o.authorized=true;
        else if(arg=="--self-test")o.self=true;
        else if(arg=="--offline")o.offline=true;
        else throw std::runtime_error("unknown option: "+arg);
    }
    require(unsigned(o.self)+unsigned(o.offline)+unsigned(o.run)==1,"choose exactly --self-test OR --offline --job OR explicit hardware --run");
    require(!o.self || (o.job.empty()&&o.receipts.empty()&&o.lock_fd<0&&!o.authorized),"self-test hardware/file options refused");
    require(!o.offline || (!o.job.empty()&&o.receipts.empty()&&o.lock_fd<0&&!o.authorized),"offline requires only --offline --job");
    require(!o.run || (!o.job.empty()&&!o.receipts.empty()&&o.lock_fd>=3&&o.authorized),"hardware requires --job --receipt-dir --lock-fd --run --authorized-two-starts");
    return o;
}
#ifdef OPT50_WITH_XRT
volatile sig_atomic_t stopping=0;
void stop_requested(int){stopping=1;}
using Clock=std::chrono::steady_clock;
void arm_signal_handlers() {
    struct sigaction a{};a.sa_handler=stop_requested;::sigemptyset(&a.sa_mask);
    require(::sigaction(SIGINT,&a,nullptr)==0 && ::sigaction(SIGTERM,&a,nullptr)==0 && ::sigaction(SIGHUP,&a,nullptr)==0,"install safe signal handlers");
}
void root_linux() {
    struct utsname u{};
    require(::geteuid()==0 && ::uname(&u)==0 && std::string(u.sysname)=="Linux" && std::string(u.machine)=="aarch64" && std::string(u.release)=="6.8.0-1015-xilinx","root and fixed ARM Linux kernel required");
}
std::string proc_text(const std::string& path,size_t limit=1024*1024) {
    // proc/sysfs have size zero even when readable: stream with an explicit bound.
    const int fd=::open(path.c_str(),O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    require(fd>=0,"readonly process/sysfs file open failed: "+path);
    std::string s;std::array<char,4096> part{};
    for(;;) {
        const auto n=::read(fd,part.data(),part.size());
        if(n==0)break;
        if(n<0 && errno==EINTR)continue;
        if(n<0){::close(fd);throw std::runtime_error("bounded process read failed");}
        s.append(part.data(),size_t(n));
        if(s.size()>limit){::close(fd);throw std::runtime_error("process text exceeds bound");}
    }
    ::close(fd);return s;
}
std::string trim_newline(std::string s){while(!s.empty()&&(s.back()=='\r'||s.back()=='\n'))s.pop_back();return s;}
void no_links(const std::filesystem::path& path) {
    require(path.is_absolute(),"absolute root-owned path required");
    std::filesystem::path walked;
    for(const auto& component:path) {
        require(component!=".." && component!=".","path traversal refused");walked/=component;
        struct stat s{};require(::lstat(walked.c_str(),&s)==0 && !S_ISLNK(s.st_mode),"linked/missing path refused: "+walked.string());
    }
}
void private_dir(const std::filesystem::path& path) {
    no_links(path);struct stat s{};
    require(::lstat(path.c_str(),&s)==0 && S_ISDIR(s.st_mode) && s.st_uid==0 && !(s.st_mode&0077),"root-private ordinary directory required");
}
void private_file(const std::filesystem::path& path) {
    no_links(path);struct stat s{};
    require(::lstat(path.c_str(),&s)==0 && S_ISREG(s.st_mode) && s.st_uid==0 && !(s.st_mode&0077) && s.st_nlink==1,"root-private single-link regular file required");
    private_dir(path.parent_path());
}
bool same_inode(const struct stat& a,const struct stat& b){return a.st_dev==b.st_dev&&a.st_ino==b.st_ino;}
void check_lock(int fd,const Fields& f) {
    require(field(f,"companion_lock")==companion_lock,"fixed global companion lock only");
    private_file(companion_lock);
    struct stat named{},opened{};
    require(::lstat(companion_lock,&named)==0 && ::fstat(fd,&opened)==0 && same_inode(named,opened) && S_ISREG(opened.st_mode),"inherited lock FD inode differs");
    const auto parent=number(field(f,"expected_parent_pid"));
    require(parent==uint64_t(::getppid()) && parent>1,"coordinator PID differs/was orphaned");
    const auto prefix="/proc/"+std::to_string(parent);
    const auto status=proc_text(prefix+"/status");
    require(std::regex_search(status,std::regex("(^|\\n)Uid:\\s+0\\s+0\\s+0\\s+0(\\n|$)")),"coordinator must be root");
    auto cmd=proc_text(prefix+"/cmdline",65536);std::vector<std::string> words;
    for(size_t p=0;p<cmd.size();) {const auto end=cmd.find('\0',p);require(end!=std::string::npos,"parent argv NUL termination");words.push_back(cmd.substr(p,end-p));p=end+1;}
    require(words.size()==7 && words[0]=="/usr/bin/python3" && words[1]=="-B" && words[3]=="run" && words[4]=="--plan" && words[6]=="--authorized-layer02-two-starts","coordinator exact command shape differs");
    const auto token=field(f,"run_id");
    const auto planroot="/var/lib/opt50-b24-layer02-v1/plan_"+token;
    require(words[2]==planroot+"/tools/run_layer02.py" && words[5]==planroot+"/plan.json","coordinator path/token differs");
    struct stat parent_exe{},python_exe{};
    require(::stat((prefix+"/exe").c_str(),&parent_exe)==0 && ::stat("/usr/bin/python3",&python_exe)==0 && same_inode(parent_exe,python_exe),"coordinator interpreter identity differs");
    std::istringstream locks(proc_text("/proc/locks"));std::string line;bool matched=false;
    while(std::getline(locks,line)) {
        std::istringstream row(line);std::string id,kind,advisory,write,pid,device,first,last,extra;
        if(!(row>>id>>kind>>advisory>>write>>pid>>device>>first>>last) || (row>>extra))continue;
        if(kind!="FLOCK" || advisory!="ADVISORY" || write!="WRITE" || pid!=std::to_string(parent) || first!="0" || last!="EOF")continue;
        std::smatch m;
        if(!std::regex_match(device,m,std::regex("([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9]+)")))continue;
        if(std::stoull(m[1],nullptr,16)==major(opened.st_dev) && std::stoull(m[2],nullptr,16)==minor(opened.st_dev) && std::stoull(m[3])==opened.st_ino)matched=true;
    }
    require(matched,"global companion flock not owned by live parent; no flock acquisition/bypass is attempted");
    const auto parentfd=prefix+"/fd/"+std::to_string(fd);
    struct stat parent_open{};
    require(::stat(parentfd.c_str(),&parent_open)==0 && same_inode(parent_open,opened),"parent inherited-FD slot inode differs");
}
class Journal {
    int dir_=-1;unsigned sequence_=0;
    void write_new(const std::string& name,const std::string& raw) {
        require(dir_>=0 && name.find('/')==std::string::npos,"journal directory/name");
        const int fd=::openat(dir_,name.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
        require(fd>=0,"exclusive immutable receipt creation failed");
        size_t at=0;
        while(at<raw.size()) {
            const auto n=::write(fd,raw.data()+at,raw.size()-at);
            if(n<0&&errno==EINTR)continue;
            if(n<=0){::close(fd);throw std::runtime_error("receipt short write");}at+=size_t(n);
        }
        const bool flushed=::fsync(fd)==0;
        const bool closed=::close(fd)==0;
        require(flushed&&closed&&::fsync(dir_)==0,"receipt file/directory fsync failed");
    }
public:
    explicit Journal(const std::string& leaf) {
        std::filesystem::path p(leaf);private_dir(p.parent_path());
        require(::mkdir(p.c_str(),0700)==0,"NEW native receipt directory required; no reuse/overwrite");
        const int parent=::open(p.parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        require(parent>=0,"receipt parent open failed");
        const bool flushed=::fsync(parent)==0;::close(parent);require(flushed,"receipt parent fsync failed");
        private_dir(p);dir_=::open(p.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        require(dir_>=0,"receipt directory open failed");
    }
    ~Journal(){if(dir_>=0)::close(dir_);}
    void event(const std::string& phase,const std::string& details="") {
        require(sequence_<100000,"journal bounded sequence exceeded");
        ++sequence_;std::ostringstream name;name<<"event_"<<std::setw(6)<<std::setfill('0')<<sequence_<<".json";
        const auto raw="{\"schema\":\"opt50_b24_layer02_native_event_v1\",\"sequence\":"+std::to_string(sequence_)+",\"phase\":"+quote(phase)+",\"details\":"+quote(details)+",\"monotonic_ns\":"+std::to_string(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count())+"}\n";
        write_new(name.str(),raw);
    }
    void terminal(const std::string& name,const std::string& raw){write_new(name,raw);}
};
uint32_t physical_read(Journal& journal,uint64_t address,const std::string& label) {
    require(address==0xfd615000ULL || address==0xfd380000ULL || address==0xfd380014ULL ||
            address==gemm_base || (address>=observer_base && address<=observer_base+0x3c && (address%4)==0),
            "fixed PS/observer/initial-control physical read allowlist");
    require(!stopping,"stop requested before physical read");
    journal.event("MMIO_READ_INTENT",label+" address="+std::to_string(address));
    const int fd=::open("/dev/mem",O_RDONLY|O_SYNC|O_CLOEXEC|O_NOFOLLOW);
    require(fd>=0,"readonly /dev/mem open failed");
    struct stat s{};
    if(::fstat(fd,&s)!=0 || !S_ISCHR(s.st_mode) || major(s.st_rdev)!=1 || minor(s.st_rdev)!=1) {
        ::close(fd);throw std::runtime_error("/dev/mem character-device identity differs");
    }
    const auto pagesize=::sysconf(_SC_PAGESIZE);
    if(pagesize<4096 || (address&3)) {::close(fd);throw std::runtime_error("MMIO page/alignment");}
    const uint64_t base=address & ~uint64_t(pagesize-1);
    void* mapped=::mmap(nullptr,size_t(pagesize),PROT_READ,MAP_SHARED,fd,off_t(base));
    if(mapped==MAP_FAILED){::close(fd);throw std::runtime_error("readonly MMIO mmap failed");}
    std::atomic_thread_fence(std::memory_order_seq_cst);
    const auto value=*reinterpret_cast<volatile uint32_t*>(static_cast<unsigned char*>(mapped)+(address-base));
    std::atomic_thread_fence(std::memory_order_seq_cst);
    const bool unmapped=::munmap(mapped,size_t(pagesize))==0;
    const bool closed=::close(fd)==0;
    require(unmapped&&closed,"MMIO mapping cleanup failed");
    journal.event("MMIO_READ_RETURNED",label+" value="+std::to_string(value));return value;
}
class RawPreflight {
    Journal& journal_;
public:
    explicit RawPreflight(Journal& j):journal_(j){}
    uint32_t read_observer(uint32_t offset) {
        require(offset<=0x3c && (offset%4)==0,"observer readonly register allowlist");
        return physical_read(journal_,observer_base+offset,"observer_readonly");
    }
    uint32_t read_gemm(uint32_t offset) {
        require(offset==0,"only initial GEMM AP_CTRL after observer identity");
        return physical_read(journal_,gemm_base,"GEMM_AP_CTRL_COR_DONE_READY");
    }
};
void preflight(Journal& journal) {
    std::array<uint32_t,3> before{},after{};
    const std::array<uint64_t,3> addr={0xfd615000ULL,0xfd380000ULL,0xfd380014ULL};
    for(size_t i=0;i<3;++i)before[i]=physical_read(journal,addr[i],"PS_width_pause");
    for(size_t i=0;i<3;++i)after[i]=physical_read(journal,addr[i],"PS_width_pause_repeat");
    require(ps_fields_match(before)&&ps_fields_match(after),"PS HPM0/HP0 must be 128bit/unpaused");
    RawPreflight raw(journal);
    stable_observer(raw); // MUST precede any GEMM AP_CTRL or XRT device access.
    idle(raw); // COR DONE/READY read effect explicitly recorded; not an inert file read.
    journal.event("RAW_OBSERVER_AND_INITIAL_IDLE_VALIDATED","No physical START count inferred; no CSR writes yet");
}
void metadata_only(const xrt::xclbin& binary,size_t file_size) {
    const axlf* h=binary.get_axlf();
    require(h && h->m_header.m_numSections==6,"exactly six metadata-only xclbin sections required");
    unsigned seen=0;
    for(unsigned i=0;i<6;++i) {
        const auto& s=h->m_sections[i];unsigned flag=0;
        require(s.m_sectionOffset<=file_size && s.m_sectionSize<=file_size-s.m_sectionOffset,"metadata section bounds");
        switch(s.m_sectionKind) {
        case EMBEDDED_METADATA:flag=1;break;case IP_LAYOUT:flag=2;break;
        case MEM_TOPOLOGY:flag=4;break;case CONNECTIVITY:flag=8;break;
        case ASK_GROUP_TOPOLOGY:flag=16;break;case ASK_GROUP_CONNECTIVITY:flag=32;break;
        default:throw std::runtime_error("programming/unexpected section refused");
        }
        require(!(seen&flag),"duplicate metadata section");seen|=flag;
    }
    require(seen==63 && binary.get_uuid().to_string()==uuid,"fixed paired metadata UUID");
}
class Native {
    Journal& journal_;
    xrt::device* device_=nullptr;
    xrt::ip *gemm_=nullptr,*observer_=nullptr;
    std::array<xrt::bo*,4> bo_{};
    std::array<unsigned char*,4> map_{};
    std::array<size_t,4> allocation_{};
    bool active_=false;
    unsigned starts_=0;
    Clock::time_point deadline_{};
public:
    explicit Native(Journal& journal):journal_(journal){}
    // No implicit owning destructor: explicit close is permitted only after DMA
    // completion. In ambiguous paths retain() NEVER returns, keeping pointers,
    // mapped BOs, CU/device and inherited lock alive without repeating START.
    void open(const Fields& f) {
        require(!stopping,"stop requested before XRT open");
        const auto raw=read_file(field(f,"metadata"));
        require(sha256(raw)==metadata_sha,"metadata changed before XRT registration");
        xrt::xclbin binary(field(f,"metadata"));metadata_only(binary,raw.size());
        journal_.event("XRT_DEVICE_OPEN_INTENT","device=0; observer already validated via raw readonly CSR");
        device_=new xrt::device(0);
        journal_.event("XRT_METADATA_REGISTRATION_INTENT","MUTATION: metadata-only load_xclbin; not firmware programming");
        const auto id=device_->load_xclbin(binary);
        require(id.to_string()==uuid,"registered XRT UUID differs");
        journal_.event("XRT_CU_OPEN_INTENT","axi_observer:{status_1}");
        observer_=new xrt::ip(*device_,id,"axi_observer:{status_1}");
        stable_observer(*this);
        journal_.event("XRT_CU_OPEN_INTENT","model_gemm_top:{gemm_1}");
        gemm_=new xrt::ip(*device_,id,"model_gemm_top:{gemm_1}");
    }
    uint32_t read_observer(uint32_t off) {
        require(observer_&&off<=0x3c&&off%4==0,"XRT observer read allowlist");
        journal_.event("XRT_MMIO_READ_INTENT","observer "+std::to_string(off));
        const auto value=observer_->read_register(off);
        journal_.event("XRT_MMIO_READ_RETURNED","observer "+std::to_string(off)+" value="+std::to_string(value));return value;
    }
    uint32_t read_gemm(uint32_t off) {
        require(gemm_ && (off==0 || off==4 || off==8 || off==0x10 || off==0x18 || off==0x1c || off==0x24 || off==0x28 || off==0x30 || off==0x34 || off==0x3c || off==0x40 || off==0x48 || off==0x50 || off==0x58 || off==0x60),"GEMM read register allowlist");
        journal_.event("XRT_MMIO_READ_INTENT","gemm "+std::to_string(off)+(off==0?" AP_CTRL_COR_DONE_READY":""));
        const auto value=gemm_->read_register(off);
        journal_.event("XRT_MMIO_READ_RETURNED","gemm "+std::to_string(off)+" value="+std::to_string(value));return value;
    }
    void write_gemm(uint32_t off,uint32_t value) {
        require(gemm_ && (off==0 || off==4 || off==8 || off==0x18 || off==0x1c || off==0x24 || off==0x28 || off==0x30 || off==0x34 || off==0x3c || off==0x40 || off==0x48 || off==0x50 || off==0x58 || off==0x60),"GEMM write register allowlist");
        require(!stopping,"stop requested before GEMM write");
        if(off==0)require(active_&&value==1&&starts_>=1&&starts_<=2,"explicit START budget/lifetime");
        journal_.event(off==0?"START_WRITE_INTENT":"XRT_MMIO_WRITE_INTENT","gemm "+std::to_string(off)+" value="+std::to_string(value));
        gemm_->write_register(off,value);
        journal_.event(off==0?"START_WRITE_RETURNED":"XRT_MMIO_WRITE_RETURNED","gemm "+std::to_string(off));
    }
    void write_observer(uint32_t off,uint32_t value) {
        require(observer_&&off==0x40&&value==0x434c5245&&!active_,"idle-only observer statistics clear");
        require(!stopping,"stop requested before observer clear");
        journal_.event("XRT_MMIO_WRITE_INTENT","observer idle-only clear 0x40=0x434c5245");
        observer_->write_register(off,value);journal_.event("XRT_MMIO_WRITE_RETURNED","observer clear");
    }
    void allocate(const std::array<size_t,4>& caps) {
        require(device_&&std::all_of(bo_.begin(),bo_.end(),[](const xrt::bo* p){return p==nullptr;}),"allocate four BOs only once");
        for(size_t i=0;i<4;++i) {
            allocation_[i]=((caps[i]+63)/64)*64+128;
            journal_.event("XRT_BO_ALLOC_INTENT","cacheable bank=0 bytes="+std::to_string(allocation_[i])+" slot="+std::to_string(i));
            bo_[i]=new xrt::bo(*device_,allocation_[i],xrt::bo::flags::cacheable,0);
            const uint64_t a=bo_[i]->address();
            const auto description=bo_description(i,a,allocation_[i]);
            journal_.event("XRT_BO_ALLOC_RETURNED",description);
            if(a%64!=0) {
                journal_.event("XRT_BO_ADDRESS_REJECTED","reason=NOT_64_BYTE_ALIGNED "+description);
                throw std::runtime_error("XRT BO alignment differs from 64 bytes: "+description);
            }
            if(!opt50::ddr_range(a,allocation_[i])) {
                journal_.event("XRT_BO_ADDRESS_REJECTED","reason=OUTSIDE_REVIEWED_LOW_OR_HIGH_DDR_FULL_ALLOCATION "+description);
                throw std::runtime_error("XRT BO outside unchanged low [64,0x80000000) or high [0x800000000,0x880000000) DDR allocation contract: "+description);
            }
            journal_.event("XRT_BO_MAP_INTENT",description);
            map_[i]=bo_[i]->map<unsigned char*>();
            journal_.event("XRT_BO_MAP_RETURNED",description+" mapped_nonnull="+(map_[i]?"true":"false"));
            require(map_[i]!=nullptr,"XRT BO mapping returned null: "+description);
        }
        for(size_t i=0;i<4;++i)for(size_t j=i+1;j<4;++j) {
            const auto a=address(i),b=address(j);
            require(a+allocation_[i]<=b || b+allocation_[j]<=a,"XRT BOs overlap");
        }
    }
    unsigned char* map(size_t i){return map_[i];}
    size_t allocation(size_t i)const{return allocation_[i];}
    uint64_t address(size_t i)const{return bo_[i]->address();}
    void sync_to(size_t i) {
        require(!active_,"cannot sync active DMA input");
        journal_.event("XRT_BO_TO_DEVICE_INTENT","whole allocation slot="+std::to_string(i));
        bo_[i]->sync(XCL_BO_SYNC_BO_TO_DEVICE,allocation_[i],0);
        journal_.event("XRT_BO_TO_DEVICE_RETURNED",std::to_string(i));
    }
    void sync_from(size_t i) {
        require(!active_,"cannot invalidate before proven done+idle+DMA drain");
        journal_.event("XRT_BO_FROM_DEVICE_INTENT","whole allocation slot="+std::to_string(i));
        bo_[i]->sync(XCL_BO_SYNC_BO_FROM_DEVICE,allocation_[i],0);
        journal_.event("XRT_BO_FROM_DEVICE_RETURNED",std::to_string(i));
    }
    void before_start(unsigned mode) {
        require(!active_&&!stopping&&starts_==mode&&mode<2,"START order/budget/stop");
        active_=true;++starts_; // BEFORE durable START intent and the MMIO write.
        deadline_=Clock::now()+std::chrono::seconds(120);
        journal_.event("DMA_LIFETIME_ACTIVE_BEFORE_START","mode="+std::to_string(mode)+" command_attempt="+std::to_string(starts_));
    }
    void completed() {
        require(active_,"completion without active run");
        // Lifetime can end only after template proved COR done + idle + drained.
        active_=false;journal_.event("DMA_COMPLETION_PROVEN","seen_done+idle+start_clear+auto_clear+observer_drained");
    }
    void pause_poll() {
        require(!stopping,"stop requested during active DMA; retain without retry");
        require(Clock::now()<deadline_,"120-second completion deadline; retain without retry");
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    bool active()const{return active_;}
    unsigned starts()const{return starts_;}
    void close() {
        require(!active_,"never close active/ambiguous DMA");
        const auto allocated_count=std::count_if(bo_.begin(),bo_.end(),[](const xrt::bo* b){return b!=nullptr;});
        const auto cu_count=unsigned(gemm_!=nullptr)+unsigned(observer_!=nullptr);
        const auto counts="allocated_BO_count="+std::to_string(allocated_count)+" CU_count="+std::to_string(cu_count)+" device_present="+(device_?"true":"false");
        journal_.event("XRT_RESOURCE_CLOSE_INTENT",counts+"; only idle/drained resources; no unload/reset/restoration");
        for(auto& b:bo_){delete b;b=nullptr;}
        delete gemm_;gemm_=nullptr;delete observer_;observer_=nullptr;
        delete device_;device_=nullptr;
        journal_.event("XRT_RESOURCES_CLOSED","released_BO_count="+std::to_string(allocated_count)+" released_CU_count="+std::to_string(cu_count)+" device_closed=true; inherited global lock remains open until child exit");
    }
    [[noreturn]] void retain(const std::string& why) noexcept {
        try{journal_.event("RETAINED_NATIVE_RESOURCES_NO_RETRY",why);}catch(...){}
        std::cerr<<"RETAINED: "<<why<<". Preserve this worker, CUs, BOs, old guardian and both locks. No retry, kill, unload, reset or reboot.\n"<<std::flush;
        // No automatic CSR polling/recovery. The process deliberately remains
        // alive and owns all allocations and inherited lock on uncertainty.
        for(;;)std::this_thread::sleep_for(std::chrono::seconds(30));
    }
};
std::string result_json(const Layer02Job& job,const std::array<Bytes,2>& output) {
    const auto& f=job.fields;
    const auto s=job.dimensions;
    return "{\"schema\":\"opt50_b24_layer02_runtime_result_v1\",\"status\":\"REAL_LAYER_DENSE_SPARSE_EXACT_MATCH\",\"run_id\":"+quote(field(f,"run_id"))+",\"boot_id\":"+quote(field(f,"boot_id"))+",\"metadata_uuid\":"+quote(uuid)+",\"metadata_sha256\":"+quote(metadata_sha)+",\"PL_START_commands_issued_by_worker\":2,\"physical_PL_starts\":\"NOT_MEASURED\",\"compared_INT32_outputs\":"+std::to_string(2*size_t(s.m)*s.n)+",\"modes\":[0,1],\"outputs_sha256\":["+quote(sha256(output[0]))+","+quote(sha256(output[1]))+"],\"same_CU_and_buffers\":true,\"canaries_and_inputs\":\"PASS\",\"hardware_executed\":true,\"device_closed\":true,\"accuracy_verified\":false,\"performance_verified\":false,\"case_id\":"+quote(field(f,"case_id"))+",\"shape_MKN\":["+std::to_string(s.m)+","+std::to_string(s.k)+","+std::to_string(s.n)+"],\"package_manifest_sha256\":"+quote(package_sha)+",\"source_model_sha256\":"+quote(source_model_sha)+",\"source_archive_sha256\":"+quote(archive_sha)+",\"dense_reference_uses_same_pruned_weights\":true}\n";
}
void hardware(const Options& o,const Layer02Job& job) {
    root_linux();arm_signal_handlers();
    private_file(o.job);
    require(trim_newline(proc_text("/proc/sys/kernel/random/boot_id"))==field(job.fields,"boot_id"),"runtime current boot mismatch");
    for(const auto& name:payload_names)private_file(field(job.fields,name));
    private_file(field(job.fields,"package_manifest"));
    private_file(field(job.fields,"metadata"));
    check_lock(o.lock_fd,job.fields);
    // Directory is one new private leaf. No old receipt/status is overwritten.
    Journal journal(o.receipts);
    journal.event("RUNTIME_ENTERED","authorized B24_dev0000_layer02 M197K192N576 two-START integer test; Dense expands the SAME pruned weights; not model accuracy/performance");
    Native native(journal);
    try {
        check_lock(o.lock_fd,job.fields);
        preflight(journal);
        check_lock(o.lock_fd,job.fields);
        native.open(job.fields);
        const auto outputs=two_modes(native,job);
        require(native.starts()==2&&!native.active(),"exact two START commands and safe completion required");
        native.close();
        const auto result=result_json(job,outputs);
        journal.terminal("result.json",result);
        std::cout<<result<<std::flush;
    } catch(const std::exception& error) {
        if(native.active())native.retain(error.what());
        try{native.close();}catch(...){native.retain("resource close/receipt failed; lifetime uncertain");}
        const auto refusal="{\"schema\":\"opt50_b24_layer02_native_refusal_v1\",\"status\":\"REFUSED_OR_FAILED_NO_AUTOMATIC_RETRY\",\"reason\":"+quote(error.what())+",\"PL_START_commands_attempted\":"+std::to_string(native.starts())+",\"physical_PL_starts\":\"NOT_MEASURED\",\"device_closed\":true}\n";
        journal.terminal("refusal.json",refusal);throw;
    }
}
#endif
} // namespace

int main(int argc,char** argv) {
    try {
        const auto o=options(argc,argv);
        if(o.self) {
            pure_self_test();
            std::cout<<"SELF_TEST_PASS_NO_HARDWARE_ACCESS\n";return 0;
        }
        const auto job=load_layer02_job(o.job);
        if(o.offline) {
            FakeBackend fake(job);const auto out=two_modes(fake,job);
            require(out[0]==job.payload[5]&&out[1]==job.payload[5],"offline golden");
            std::cout<<"{\"status\":\"OFFLINE_B24_LAYER02_DENSE_SPARSE_PASS_NO_HARDWARE_ACCESS\",\"case_id\":\"B24_dev0000_layer02\",\"source_model_sha256\":\"ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43\",\"shape_MKN\":[197,192,576],\"compared_INT32_outputs\":226944,\"physical_PL_starts\":0,\"hardware_executed\":false,\"dense_reference_uses_same_pruned_weights\":true}\n";
            return 0;
        }
#ifdef OPT50_WITH_XRT
        hardware(o,job);
#else
        throw std::runtime_error("offline-only binary; hardware mode not compiled");
#endif
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<"REFUSED_OR_FAILED: "<<error.what()<<'\n';return 2;
    }
}
