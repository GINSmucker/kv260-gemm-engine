// DEMO V1 derived bootstrap: installed systemd supervisor; immutable Sparse24 arithmetic and bounded State.
#pragma once
// File/process gate ONLY. This header never opens a hardware device, maps MMIO,
// loads a driver/overlay, sends a signal, or takes/transfers the old board lock.
// A separately reviewed root-copied B24 run_model.py owns authorization and lifecycle.
#include "model_core.hpp"
#include <sstream>
#include <chrono>
#if defined(__linux__)
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/utsname.h>
#include <unistd.h>
#endif

namespace opt50_gate {
using opt50_core::need;
struct Config {
    std::string root,plan_root,task_root,socket,job,weights,data,meta,reference,metadata,token,boot;
    int lock_fd=-1;uint64_t parent=0;
    uint64_t sample_count=0,total_START_budget=0,created_monotonic_ns=0;
};
namespace detail {inline Config config;inline bool validated=false;}
inline const Config& checked(){need(detail::validated,"Native is unavailable without the separately authorized future model window gate");return detail::config;}
inline uint64_t monotonic_ns(){return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count());}
inline uint64_t number(const std::string& s){need(std::regex_match(s,std::regex("0|[1-9][0-9]*")),"strict decimal field");return std::stoull(s);}
inline std::map<std::string,std::string> fields(const opt50_core::Bytes& raw) {
    need(std::find(raw.begin(),raw.end(),0)==raw.end(),"NUL in native job");std::istringstream in(std::string(raw.begin(),raw.end()));std::string line;std::map<std::string,std::string> f;
    while(std::getline(in,line)){if(!line.empty()&&line.back()=='\r')line.pop_back();const auto at=line.find('=');need(at!=std::string::npos&&at>0&&at+1<line.size()&&line.find('=',at+1)==std::string::npos,"native job key=value required");need(f.emplace(line.substr(0,at),line.substr(at+1)).second,"duplicate native job field");}
    return f;
}
struct Budget {uint64_t sample_count,total_START_budget,created_monotonic_ns;};
inline Budget resident_scope(const std::map<std::string,std::string>& f,uint64_t now) {
    const std::array<const char*,22> names={"schema","boot_id","plan_token","expected_parent_pid","companion_lock","model_root","weights","weights_sha256","data","data_sha256","meta","meta_sha256","reference","reference_sha256","metadata","metadata_sha256","run_id","expected_PL_starts","source_model_sha256","sample_count","total_START_budget","created_monotonic_ns"};
    need(f.size()==names.size()&&std::all_of(names.begin(),names.end(),[&](const char* k){return f.count(k)==1;}),"exact 22-field B24 resident native job required");
    const auto get=[&](const char* k)->const std::string&{return f.at(k);};
    need(get("schema")=="opt50_b24_resident_native_job_v4"&&opt50_core::canonical_boot(get("boot_id"))&&
         get("source_model_sha256")==opt50_core::source_model_sha&&get("run_id")=="1"&&
         get("expected_PL_starts")=="36"&&std::regex_match(get("plan_token"),std::regex("[0-9a-f]{12}")),
         "resident model scope/token differs");
    const Budget b{number(get("sample_count")),number(get("total_START_budget")),number(get("created_monotonic_ns"))};
    need(b.sample_count>=1&&b.sample_count<=50&&b.total_START_budget==36*b.sample_count,
         "resident sealed count/total START budget differs");
    need(b.created_monotonic_ns>0&&now>=b.created_monotonic_ns&&now-b.created_monotonic_ns<=opt50_core::State::window_ns,
         "resident native job expired or creation is in the future");
    return b;
}
#if defined(__linux__)
inline void no_links(const std::filesystem::path& path) {
    need(path.is_absolute(),"absolute private path required");std::filesystem::path current;
    for(const auto& p:path){need(p!=".."&&p!=".","path traversal refused");current/=p;struct stat s{};need(::lstat(current.c_str(),&s)==0&&!S_ISLNK(s.st_mode),"linked/missing path refused: "+current.string());}
}
inline void private_dir(const std::filesystem::path& path){no_links(path);struct stat s{};need(::lstat(path.c_str(),&s)==0&&S_ISDIR(s.st_mode)&&s.st_uid==0&&!(s.st_mode&0077),"root-private directory required: "+path.string());}
inline opt50_core::Bytes private_file(const std::filesystem::path& path,size_t limit=64*1024*1024) {
    no_links(path);private_dir(path.parent_path());struct stat before{},opened{},after{};
    need(::lstat(path.c_str(),&before)==0&&S_ISREG(before.st_mode)&&before.st_uid==0&&!(before.st_mode&0077)&&before.st_nlink==1&&before.st_size>=0&&uint64_t(before.st_size)<=limit,"root-private bounded single-link file required: "+path.string());
    const int fd=::open(path.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC);need(fd>=0,"private file open");
    const auto same=[](const struct stat& a,const struct stat& b){return a.st_dev==b.st_dev&&a.st_ino==b.st_ino&&a.st_size==b.st_size&&a.st_mtim.tv_sec==b.st_mtim.tv_sec&&a.st_mtim.tv_nsec==b.st_mtim.tv_nsec&&a.st_ctim.tv_sec==b.st_ctim.tv_sec&&a.st_ctim.tv_nsec==b.st_ctim.tv_nsec;};
    if(::fstat(fd,&opened)!=0||!same(before,opened)){::close(fd);throw std::runtime_error("private file replaced before read");}
    opt50_core::Bytes raw(size_t(before.st_size));size_t done=0;
    while(done<raw.size()){const auto n=::read(fd,raw.data()+done,raw.size()-done);if(n<0&&errno==EINTR)continue;if(n<=0){::close(fd);throw std::runtime_error("private short read");}done+=size_t(n);}
    const bool stable=::fstat(fd,&opened)==0&&same(before,opened)&&::lstat(path.c_str(),&after)==0&&same(before,after);::close(fd);need(stable,"private file changed during read");return raw;
}
inline std::string proc_text(const std::string& path,size_t limit=1024*1024) {
    const int fd=::open(path.c_str(),O_RDONLY|O_CLOEXEC|O_NOFOLLOW);need(fd>=0,"readonly process file open");std::string out;std::array<char,4096> part{};
    for(;;){auto n=::read(fd,part.data(),part.size());if(!n)break;if(n<0&&errno==EINTR)continue;if(n<0){::close(fd);throw std::runtime_error("process text read failed");}out.append(part.data(),size_t(n));if(out.size()>limit){::close(fd);throw std::runtime_error("process text exceeds bound");}}
    ::close(fd);return out;
}
inline const Config& validate(const char* job,int fd,const char* weights,const char* metadata,const char* task_root,const char* socket) {
    need(!detail::validated,"native gate is single-use");struct utsname u{};
    need(::geteuid()==0&&::uname(&u)==0&&std::string(u.sysname)=="Linux"&&std::string(u.machine)=="aarch64"&&std::string(u.release)=="6.8.0-1015-xilinx","root/fixed ARM Linux required for Native gate");
    need(job&&weights&&metadata&&task_root&&socket&&fd>=3&&fd<=4096,"explicit native job and inherited companion FD required");
    const auto f=fields(private_file(job,65536));
    const auto budget=resident_scope(f,monotonic_ns());
    const auto get=[&](const char* k)->const std::string&{return f.at(k);};
    need(proc_text("/proc/sys/kernel/random/boot_id")==get("boot_id")+"\n","native gate current boot differs from the newly frozen plan");
    Config c;c.token=get("plan_token");c.boot=get("boot_id");c.plan_root="/var/lib/opt50-demo-v1/session_"+c.token;c.root="/var/lib/opt50-demo-v1/model";c.task_root=c.plan_root+"/execution";c.socket=c.task_root+"/runtime.sock";c.job=c.plan_root+"/job.kv";c.lock_fd=fd;c.parent=number(get("expected_parent_pid"));
    c.sample_count=budget.sample_count;c.total_START_budget=budget.total_START_budget;c.created_monotonic_ns=budget.created_monotonic_ns;
    c.weights=c.root+"/weights.bin";c.data=c.root+"/pl_w_data.bin";c.meta=c.root+"/pl_w_meta.bin";c.reference=c.root+"/reference.onnx";c.metadata=c.plan_root+"/metadata.xclbin";
    need(std::string(job)==c.job&&std::string(weights)==c.weights&&std::string(metadata)==c.metadata&&std::string(task_root)==c.task_root&&std::string(socket)==c.socket&&get("model_root")==c.root&&get("weights")==c.weights&&get("data")==c.data&&get("meta")==c.meta&&get("reference")==c.reference&&get("metadata")==c.metadata,"root-copied model/job/CLI path identity differs");
    private_dir(c.plan_root);private_dir(c.root);private_dir(c.task_root);private_dir("/opt/opt50-demo-v1/private");
    private_file("/opt/opt50-demo-v1/private/backend.py",2*1024*1024);
    private_file("/opt/opt50-demo-v1/private/install_receipt.json",2*1024*1024);
    private_file(c.plan_root+"/plan.json",65536);
    const auto artifact=[&](const std::string& path,const char* field,const char* expected,size_t size){const auto b=private_file(path);need(get(field)==expected&&(!size||b.size()==size)&&opt50_core::sha(b)==expected,"native root artifact SHA/size differs: "+path);};
    artifact(c.weights,"weights_sha256",opt50_model::weights_sha,opt50_model::weights_bytes);artifact(c.data,"data_sha256",opt50_model::data_sha,opt50_model::data_bytes);artifact(c.meta,"meta_sha256",opt50_model::meta_sha,opt50_model::meta_bytes);artifact(c.reference,"reference_sha256",opt50_model::reference_sha,0);artifact(c.metadata,"metadata_sha256",opt50_core::metadata_sha,8560);
    const std::string lock="/var/lib/opt50-first-access-fd96539d-v1/companion.lock";need(get("companion_lock")==lock&&c.parent>1&&c.parent==uint64_t(::getppid()),"inherited global companion/parent identity differs");
    private_file(lock,65536);struct stat named{},opened{},parentfd{};
    need(::lstat(lock.c_str(),&named)==0&&::fstat(fd,&opened)==0&&named.st_dev==opened.st_dev&&named.st_ino==opened.st_ino,"companion FD inode differs");
    const auto proc="/proc/"+std::to_string(c.parent);
    need(::stat((proc+"/fd/"+std::to_string(fd)).c_str(),&parentfd)==0&&parentfd.st_dev==opened.st_dev&&parentfd.st_ino==opened.st_ino,"parent no longer holds same companion FD");
    // Explicit NUL assembly, not shell parsing.
    std::string argv;
    for(const auto& s:std::array<std::string,4>{"/home/ubuntu/fpga_project/model_b/.venv/bin/python","-B","/opt/opt50-demo-v1/private/backend.py","serve"}){argv+=s;argv.push_back('\0');}
    need(proc_text(proc+"/cmdline",65536)==argv,"future model coordinator exact argv differs; direct manual Native launch is forbidden");
    const auto uid=proc_text(proc+"/status");need(std::regex_search(uid,std::regex("(^|\\n)Uid:\\s+0\\s+0\\s+0\\s+0(\\n|$)")),"coordinator must be root");
    const auto group="0::/system.slice/opt50-demo-hardware.service\n";
    need(proc_text(proc+"/cgroup")==group&&proc_text("/proc/self/cgroup")==group,"coordinator/owner unit cgroup differs");
    struct stat python{},exe{},owner{},running{};
    need(::stat("/usr/bin/python3",&python)==0&&::stat((proc+"/exe").c_str(),&exe)==0&&python.st_dev==exe.st_dev&&python.st_ino==exe.st_ino,"coordinator interpreter differs");
    private_file(c.plan_root+"/native_owner");need(::stat((c.plan_root+"/native_owner").c_str(),&owner)==0&&::stat("/proc/self/exe",&running)==0&&owner.st_dev==running.st_dev&&owner.st_ino==running.st_ino,"only root-copied native_owner may enter Native");
    bool locked=false;std::istringstream lines(proc_text("/proc/locks"));std::string line;const std::regex pattern("^\\s*[0-9]+:\\s+FLOCK\\s+ADVISORY\\s+WRITE\\s+([0-9]+)\\s+([a-fA-F0-9]+):([a-fA-F0-9]+):([0-9]+)\\s+0\\s+EOF\\s*$");
    while(std::getline(lines,line)){std::smatch m;if(std::regex_match(line,m,pattern)&&std::stoull(m[1])==c.parent&&std::stoull(m[2],nullptr,16)==major(opened.st_dev)&&std::stoull(m[3],nullptr,16)==minor(opened.st_dev)&&std::stoull(m[4])==opened.st_ino)locked=true;}
    need(locked,"new companion flock must remain owned by coordinator; original guardian is NOT delegation");
    detail::config=c;detail::validated=true;return detail::config;
}
#else
inline const Config& validate(const char*,int,const char*,const char*,const char*,const char*){throw std::runtime_error("Native model gate requires the separately authorized ARM Linux window; offline code cannot enter it");}
#endif
} // namespace opt50_gate
