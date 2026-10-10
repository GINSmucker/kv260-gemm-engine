// Linux file/death proof for a different-peer recovery ACK. No XRT or start.
// A production caller must supply the original SO_PEERCRED, retained ledger
// digests and actual CU/AXI safety. File/dir fsync is not power-loss acceptance.
#pragma once
#if !defined(__linux__)
#error V2A sealed recovery requires Linux
#endif
#include "stream_v2a_lifecycle.h"
#include "stream_v2a_socket.h"
#include <sys/stat.h>
#include <sys/vfs.h>
#include <linux/magic.h>

namespace kv260_stream_v2a {
class PrivateFD {
    int fd_=-1;
public:
    explicit PrivateFD(int value=-1):fd_(value){}
    ~PrivateFD(){if(fd_>=0)::close(fd_);}
    PrivateFD(const PrivateFD&)=delete;
    PrivateFD& operator=(const PrivateFD&)=delete;
    PrivateFD(PrivateFD&& other) noexcept:fd_(other.fd_){other.fd_=-1;}
    PrivateFD& operator=(PrivateFD&& other) noexcept {
        if(this!=&other){if(fd_>=0)::close(fd_);fd_=other.fd_;other.fd_=-1;}
        return *this;
    }
    int get() const{return fd_;}
};
inline void recovery_require(bool value,const char* message) {
    ledger_require(value,"RECOVERY_NOT_PROVEN",message);
}
inline Bytes read_bounded_fd(int fd,std::size_t maximum) {
    Bytes result;std::array<std::uint8_t,32768> buffer{};
    for(;;) {
        const auto n=::read(fd,buffer.data(),buffer.size());
        if(n<0 && errno==EINTR)continue;
        recovery_require(n>=0,"actual file read failed");
        if(n==0)break;
        recovery_require(static_cast<std::size_t>(n)<=maximum-result.size(),"actual file size bound");
        result.insert(result.end(),buffer.begin(),buffer.begin()+n);
    }
    return result;
}
enum class ClientDeath { Alive, Gone, Reused, Exited };
inline ClientDeath original_client_death(const ExecutionPeer& original) {
    recovery_require(original.uid==1000 && original.pid>0 && original.pid<=INT_MAX &&
        original.birth_ticks>0,"original actual peer required");
    PrivateFD proc(::open("/proc",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
    struct statfs mount{};
    recovery_require(proc.get()>=0 && ::fstatfs(proc.get(),&mount)==0 &&
        static_cast<unsigned long>(mount.f_type)==PROC_SUPER_MAGIC,"verified actual procfs required");
    const auto pid=std::to_string(original.pid);
    const int opened=::openat(proc.get(),pid.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(opened<0) {
        // Only actual absence on the verified procfs establishes Gone.
        recovery_require(errno==ENOENT,"original process status unavailable");
        return ClientDeath::Gone;
    }
    PrivateFD directory(opened);
    const int stat_open=::openat(directory.get(),"stat",O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    if(stat_open<0) {
        recovery_require(errno==ENOENT || errno==ESRCH,"original process stat unavailable");
        return ClientDeath::Gone;
    }
    PrivateFD file(stat_open);
    struct stat metadata{};
    recovery_require(::fstat(file.get(),&metadata)==0 && S_ISREG(metadata.st_mode),"actual proc stat type");
    const auto bytes=read_bounded_fd(file.get(),4096);
    const std::string text(bytes.begin(),bytes.end());
    const auto begin=text.find('('),end=text.rfind(')');
    recovery_require(begin!=std::string::npos && end>begin && end+2<text.size() &&
        text.substr(0,begin)==pid+" ","actual proc stat PID/format");
    std::istringstream fields(text.substr(end+2));std::string state,token;
    recovery_require(bool(fields>>state) && state.size()==1,"actual proc state");
    for(unsigned i=0;i!=19;++i)recovery_require(bool(fields>>token),"actual proc birth fields");
    std::uint64_t birth=0;
    recovery_require(!token.empty() && token.size()<=20,"actual proc birth bound");
    for(const unsigned char c:token) {
        recovery_require(c>='0' && c<='9' && birth<=(UINT64_MAX-(c-'0'))/10,"actual proc birth integer");
        birth=birth*10+(c-'0');
    }
    recovery_require(birth!=0,"actual proc birth nonzero");
    if(birth!=original.birth_ticks)return ClientDeath::Reused;
    // Exited tasks may have root-owned proc inodes after losing their mm.
    // Original identity was captured by SO_PEERCRED, not this inode owner.
    if(state=="Z" || state=="X")return ClientDeath::Exited;
    recovery_require(metadata.st_uid==original.uid,"original live process status owner uncertain");
    recovery_require(state=="R" || state=="S" || state=="D" || state=="T" ||
        state=="t" || state=="I","original process state uncertain");
    return ClientDeath::Alive;
}
inline PrivateFD private_child_directory(int parent,const std::string& name) {
    recovery_require(!name.empty() && name!="." && name!=".." && name.find('/')==std::string::npos,
        "private directory component");
    PrivateFD child(::openat(parent,name.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
    struct stat st{};
    recovery_require(child.get()>=0 && ::fstat(child.get(),&st)==0 && S_ISDIR(st.st_mode) &&
        st.st_uid==1000 && (st.st_mode&07777)==0700,"private actual directory mode/uid/type");
    return child;
}
inline PrivateFD anchored_task_directory(const TrustedContext& context) {
    trusted_context(context);
    PrivateFD anchor(::open("/",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
    recovery_require(anchor.get()>=0,"root directory anchor unavailable");
    std::istringstream path(context.task_root.substr(1));std::string part;
    while(std::getline(path,part,'/')) {
        recovery_require(!part.empty() && part!="." && part!="..","task path component");
        PrivateFD child(::openat(anchor.get(),part.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        recovery_require(child.get()>=0,"task component missing/symlink/type");
        anchor=std::move(child);
    }
    struct stat st{};
    recovery_require(::fstat(anchor.get(),&st)==0 && S_ISDIR(st.st_mode) &&
        st.st_uid==1000 && (st.st_mode&07777)==0700,"task private mode/uid/type");
    return anchor;
}
inline bool same_file_observation(const struct stat& before,const struct stat& after) {
    return before.st_dev==after.st_dev && before.st_ino==after.st_ino &&
        before.st_mode==after.st_mode && before.st_uid==after.st_uid &&
        before.st_nlink==after.st_nlink && before.st_size==after.st_size &&
        before.st_mtim.tv_sec==after.st_mtim.tv_sec && before.st_mtim.tv_nsec==after.st_mtim.tv_nsec &&
        before.st_ctim.tv_sec==after.st_ctim.tv_sec && before.st_ctim.tv_nsec==after.st_ctim.tv_nsec;
}
struct ActualFile {
    PrivateFD fd;
    Bytes bytes;
    struct stat observation{};
};
inline ActualFile read_private_file(int parent,const std::string& name,mode_t mode,std::size_t maximum) {
    recovery_require(!name.empty() && name.find('/')==std::string::npos && name!="." && name!="..",
        "private file component");
    // NONBLOCK prevents a substituted FIFO from blocking before fstat rejects it.
    ActualFile result{PrivateFD(::openat(parent,name.c_str(),O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)),{}, {}};
    recovery_require(result.fd.get()>=0 && ::fstat(result.fd.get(),&result.observation)==0,
        "actual private file unavailable");
    const auto& st=result.observation;
    recovery_require(S_ISREG(st.st_mode) && st.st_uid==1000 && (st.st_mode&07777)==mode &&
        st.st_nlink==1 && st.st_size>0 && static_cast<std::uint64_t>(st.st_size)<=maximum,
        "actual private regular file mode/uid/link/size");
    result.bytes=read_bounded_fd(result.fd.get(),maximum);
    struct stat after{},named{};
    recovery_require(result.bytes.size()==static_cast<std::size_t>(st.st_size) &&
        ::fstat(result.fd.get(),&after)==0 && ::fstatat(parent,name.c_str(),&named,AT_SYMLINK_NOFOLLOW)==0 &&
        same_file_observation(st,after) && same_file_observation(st,named),"actual file changed during read");
    return result;
}
inline Json actual_json_file(const ActualFile& file) {
    return parse_header(std::string(file.bytes.begin(),file.bytes.end()));
}
inline void journal_fields(const Json& journal,const Frame& result,const ExecutionPeer& original,
        const TrustedContext& context,const std::string& actual_native_digest,const std::string& result_header_digest) {
    recovery_require(journal.is_object() && journal.contains("state"),"actual journal object/state");
    auto keys=std::set<std::string>{"schema","state","request","identity","original_client_pid",
        "original_client_birth_ticks","prepared_monotonic_ns"};
    const auto state=ascii_string(journal.at("state"),1,16);
    recovery_require(state=="SUBMITTED" || state=="UNKNOWN" || state=="FAILED" || state=="COMPLETED",
        "completed recovery requires submitted original journal");
    keys.insert("submitted_monotonic_ns");
    if(state=="COMPLETED")for(const char* key:{"completed_monotonic_ns","starts_after","Y_sha256",
        "native_sha256","native_result_file","result_header_sha256","arrays_retained"})keys.insert(key);
    if(state=="UNKNOWN" || state=="FAILED")for(const char* key:{"failure_monotonic_ns","error_code","error_text"})keys.insert(key);
    recovery_require(journal.size()==keys.size(),"actual journal exact keys");
    for(auto it=journal.begin();it!=journal.end();++it)recovery_require(keys.count(it.key())==1,"actual journal unknown key");
    recovery_require(journal.at("schema")=="kv260_bridge_journal_v2a" && journal.at("request")==result.header.at("request") &&
        journal.at("identity")==context.identity && uint_value(journal.at("original_client_pid"),UINT64_MAX)==original.pid &&
        uint_value(journal.at("original_client_birth_ticks"),UINT64_MAX)==original.birth_ticks,"actual journal binding/version");
    const auto prepared=uint_value(journal.at("prepared_monotonic_ns"),UINT64_MAX);
    const auto submitted=uint_value(journal.at("submitted_monotonic_ns"),UINT64_MAX);
    recovery_require(prepared>0 && submitted>=prepared && submitted<=monotonic_ns(),"actual journal submit ordering");
    if(state=="COMPLETED") {
        const auto completed=uint_value(journal.at("completed_monotonic_ns"),UINT64_MAX);
        recovery_require(completed>=submitted && completed<=monotonic_ns() &&
            journal.at("starts_after")==result.header.at("starts_after") &&
            journal.at("Y_sha256")==result.header.at("Y_sha256") && journal.at("native_sha256")==actual_native_digest &&
            journal.at("native_result_file")==result.header.at("request").at("native_result_file") &&
            journal.at("result_header_sha256")==result_header_digest,"actual completed journal result mismatch");
        boolean(journal.at("arrays_retained"));
    }
    if(state=="UNKNOWN" || state=="FAILED") {
        const auto failed=uint_value(journal.at("failure_monotonic_ns"),UINT64_MAX);
        recovery_require(failed>=submitted && failed<=monotonic_ns(),"actual failure journal ordering");
        const auto code=ascii_string(journal.at("error_code"),1,32);
        const std::set<std::string> allowed{"BAD_FRAME","BAD_IDENTITY","BAD_REQUEST","BAD_SHAPE","BAD_HASH",
            "SHORT_BODY","DEADLINE","DUPLICATE_ID","ACK_PENDING","ACK_MISMATCH","TERMINAL_LIMIT",
            "OWNER_FAULT","RECOVERY_NOT_PROVEN","INTERNAL"};
        recovery_require(allowed.count(code)==1,"actual failure journal error code");
        ascii_string(journal.at("error_text"),0,512);
    }
}
struct RecoveryIOStats {std::uint64_t read_bytes=0,file_fsyncs=0,directory_fsyncs=0,barrier_attempts=0,injected_failures=0;};
// A probe may fail one barrier conservatively; successful barriers always use
// the real syscall. There is no hook that can forge successful persistence.
inline void recovery_fsync(int fd,bool directory,RecoveryIOStats& stats,std::uint64_t fail_barrier=UINT64_MAX) {
    const auto attempt=stats.barrier_attempts++;
    if(attempt==fail_barrier){++stats.injected_failures;throw LedgerError("RECOVERY_NOT_PROVEN","injected recovery fsync failure");}
    int result;
    do {result=::fsync(fd);} while(result<0 && errno==EINTR);
    recovery_require(result==0,"actual sealed recovery fsync failed");
    if(directory)++stats.directory_fsyncs;else ++stats.file_fsyncs;
}
inline void verify_sealed_recovery(const TrustedContext& context,const Json& confirmation,const Frame& result,
        const ExecutionPeer& original,const ExecutionPeer& actual_recovery_peer,const std::string& retained_request_digest,
        const std::string& retained_native_digest,const BodyDigest& digest,const SafeCheck& actual_safe,
        RecoveryIOStats& stats,std::uint64_t fail_barrier=UINT64_MAX) {
    recovery_require(actual_recovery_peer.uid==1000 && actual_recovery_peer.pid>0 &&
        actual_recovery_peer.birth_ticks>0 && !same_peer(original,actual_recovery_peer),"actual different recovery peer");
    recovery_require(actual_recovery_peer.birth_ticks==process_birth_ticks(actual_recovery_peer.pid),
        "actual recovery peer birth changed");
    recovery_require(bool(actual_safe) && actual_safe(),"actual CU/AXI quiet recovery required");
    body_fields(result.header,result.body,context,digest);
    recovery_require(message_fields(result.header,context)=="RESULT","pending full result required");
    const auto& request=result.header.at("request");
    confirmation_fields(confirmation,request,context);
    recovery_require(uint_value(confirmation.at("original_client_pid"),UINT64_MAX)==original.pid &&
        uint_value(confirmation.at("original_client_birth_ticks"),UINT64_MAX)==original.birth_ticks,
        "recovery original peer binding");
    recovery_require(original_client_death(original)!=ClientDeath::Alive,"original caller still alive");
    const auto verify_begin=monotonic_ns();
    auto task=anchored_task_directory(context);
    auto requests=private_child_directory(task.get(),"requests");
    auto execution=private_child_directory(requests.get(),request.at("execution_id").get<std::string>());
    auto recovery=private_child_directory(execution.get(),"recovery");
    auto request_file=read_private_file(execution.get(),"request.json",0600,HEADER_LIMIT);
    auto native_file=read_private_file(execution.get(),"native.json",0600,HEADER_LIMIT);
    auto journal_file=read_private_file(execution.get(),"journal.json",0600,HEADER_LIMIT);
    auto archived_Y=read_private_file(recovery.get(),"Y.bin",0400,BODY_LIMIT);
    auto archived_native=read_private_file(recovery.get(),"native.json",0400,HEADER_LIMIT);
    auto terminal_file=read_private_file(recovery.get(),"terminal.json",0400,HEADER_LIMIT);
    const auto request_header=actual_json_file(request_file);
    recovery_require(message_fields(request_header,context)=="REQUEST" && request_header.at("request")==request &&
        digest(request_file.bytes)==retained_request_digest,"actual original request metadata mismatch");
    const auto native_digest=digest(native_file.bytes);
    recovery_require(native_digest==retained_native_digest && archived_native.bytes==native_file.bytes &&
        actual_json_file(native_file)==result.header.at("native") &&
        native_digest==confirmation.at("native_archive_sha256"),"actual native archive/original mismatch");
    recovery_require(archived_Y.bytes==result.body && digest(archived_Y.bytes)==confirmation.at("Y_archive_sha256") &&
        confirmation.at("Y_archive_sha256")==result.header.at("Y_sha256"),"actual full sealed Y mismatch");
    const auto header_text=encode_message(result.header,context);
    const auto header_digest=digest(Bytes(header_text.begin(),header_text.end()));
    recovery_require(header_digest==confirmation.at("result_header_sha256") &&
        digest(terminal_file.bytes)==confirmation.at("job_terminal_sha256"),"actual result header/terminal digest mismatch");
    const auto terminal=actual_json_file(terminal_file),journal=actual_json_file(journal_file);
    journal_fields(journal,result,original,context,native_digest,header_digest);
    exact_keys(terminal,{"schema","request","identity","original_client_pid","original_client_birth_ticks",
        "journal_state","journal_sha256","status","Y_sha256","native_sha256","result_header_sha256",
        "client_death_observed_monotonic_ns"});
    recovery_require(terminal.at("schema")=="kv260_v2a_terminated_v1" && terminal.at("status")=="TERMINATED_NO_RESUME" &&
        terminal.at("request")==request && terminal.at("identity")==context.identity &&
        uint_value(terminal.at("original_client_pid"),UINT64_MAX)==original.pid &&
        uint_value(terminal.at("original_client_birth_ticks"),UINT64_MAX)==original.birth_ticks &&
        terminal.at("journal_state")==journal.at("state") && terminal.at("journal_sha256")==digest(journal_file.bytes) &&
        terminal.at("Y_sha256")==result.header.at("Y_sha256") && terminal.at("native_sha256")==native_digest &&
        terminal.at("result_header_sha256")==header_digest,"actual terminated journal/result binding");
    const auto observed=uint_value(terminal.at("client_death_observed_monotonic_ns"),UINT64_MAX);
    recovery_require(observed>0 && observed<=verify_begin &&
        observed>=uint_value(journal.at("submitted_monotonic_ns"),UINT64_MAX),"actual death record ordering");
    for(const auto* file:{&request_file,&native_file,&journal_file,&archived_Y,&archived_native,&terminal_file})
        stats.read_bytes+=file->bytes.size();
    // Sync original metadata too: native stream flush alone was never a barrier.
    for(const auto* file:{&request_file,&native_file,&journal_file,&archived_Y,&archived_native,&terminal_file})
        recovery_fsync(file->fd.get(),false,stats,fail_barrier);
    recovery_fsync(recovery.get(),true,stats,fail_barrier);
    recovery_fsync(execution.get(),true,stats,fail_barrier);
    // All observations are rechecked before the ledger releases Y.
    const std::array<ActualFile*,6> files{&request_file,&native_file,&journal_file,&archived_Y,&archived_native,&terminal_file};
    const std::array<const char*,6> names{"request.json","native.json","journal.json","Y.bin","native.json","terminal.json"};
    for(unsigned i=0;i!=files.size();++i) {
        struct stat current{},named{};const auto* file=files[i];const auto parent=i<3?execution.get():recovery.get();
        recovery_require(::fstat(file->fd.get(),&current)==0 &&
            ::fstatat(parent,names[i],&named,AT_SYMLINK_NOFOLLOW)==0 &&
            same_file_observation(file->observation,current) && same_file_observation(file->observation,named),
            "actual sealed proof changed during barriers");
    }
    auto still_task=anchored_task_directory(context);
    auto still_requests=private_child_directory(still_task.get(),"requests");
    auto still_execution=private_child_directory(still_requests.get(),request.at("execution_id").get<std::string>());
    auto still_recovery=private_child_directory(still_execution.get(),"recovery");
    for(const auto& pair:std::array<std::pair<int,int>,4>{{{task.get(),still_task.get()},
            {requests.get(),still_requests.get()},{execution.get(),still_execution.get()},{recovery.get(),still_recovery.get()}}}) {
        struct stat first{},second{};
        recovery_require(::fstat(pair.first,&first)==0 && ::fstat(pair.second,&second)==0 &&
            first.st_dev==second.st_dev && first.st_ino==second.st_ino,"actual recovery directory path replaced");
    }
    recovery_require(original_client_death(original)!=ClientDeath::Alive && actual_safe(),
        "actual death/quiet changed before recovery ACK");
}
} // namespace kv260_stream_v2a
