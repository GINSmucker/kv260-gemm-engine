// V2B task records. No ABI, socket, XRT, BO or execution authority.
// One writer, append-only records; every accepted append uses real fsync and
// readback. A failed/ambiguous write poisons the handle and is never truncated.
#pragma once
#include "../sha256_crypto.h"
#include "../vendor/json.hpp"
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <functional>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace kv260_record_v2b {
using Json=nlohmann::json;
using Bytes=std::vector<unsigned char>;
constexpr std::size_t HEADER_BYTES=96, PAYLOAD_LIMIT=16384;
constexpr std::uint64_t RECORD_LIMIT=65537, FILE_LIMIT=128ULL*1024*1024;
constexpr char NAME[]="records.v2b";
inline void require(bool b,const char* s){if(!b)throw std::runtime_error(s);}
struct FD {
    int value=-1;
    explicit FD(int v=-1):value(v){}
    ~FD(){if(value>=0)::close(value);}
    FD(const FD&)=delete;FD& operator=(const FD&)=delete;
};
struct IO {
    std::function<ssize_t(int,const void*,std::size_t)> write=[](int fd,const void* p,std::size_t n){return ::write(fd,p,n);};
    std::function<int(int)> fsync=[](int fd){return ::fsync(fd);};
};
inline void keys(const Json& v,std::initializer_list<const char*> expected){
    require(v.is_object() && v.size()==expected.size(),"record exact keys");
    for(const auto* k:expected)require(v.contains(k),"record missing key");
}
inline std::string id(const Json& v){
    require(v.is_string(),"execution ID type");const auto s=v.get<std::string>();
    require(!s.empty() && s.size()<=191,"execution ID length");
    for(unsigned char c:s)require((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-',"execution ID character");
    return s;
}
inline std::uint64_t uint(const Json& v){require(v.is_number_unsigned(),"unsigned record integer");return v.get<std::uint64_t>();}
inline void put(Bytes& b,std::size_t p,std::uint64_t n,unsigned size){for(unsigned i=0;i<size;++i)b[p+i]=static_cast<unsigned char>(n>>(8*i));}
inline std::uint64_t get(const Bytes& b,std::size_t p,unsigned size){std::uint64_t n=0;for(unsigned i=0;i<size;++i)n|=std::uint64_t(b[p+i])<<(8*i);return n;}
inline Bytes unhex(const std::string& s){
    require(s.size()==64,"record digest length");Bytes out(32);
    auto digit=[](char c)->unsigned{require((c>='0'&&c<='9')||(c>='a'&&c<='f'),"record digest alphabet");return c<='9'?c-'0':c-'a'+10;};
    for(unsigned i=0;i<32;++i)out[i]=static_cast<unsigned char>((digit(s[i*2])<<4)|digit(s[i*2+1]));
    return out;
}
inline Bytes digest(const Bytes& b){return unhex(kv260_crypto_sha256(b.data(),b.size()));}
inline Bytes encode(std::uint64_t sequence,const Bytes& previous,const Json& record){
    require(previous.size()==32,"chain digest size");
    const auto text=record.dump(-1,' ',true,Json::error_handler_t::strict);
    require(!text.empty() && text.size()<=PAYLOAD_LIMIT,"bounded record payload");
    const Bytes payload(text.begin(),text.end());Bytes out(HEADER_BYTES+payload.size(),0);
    const unsigned char magic[8]={'K','V','2','B','L','O','G',0};std::copy(magic,magic+8,out.begin());
    put(out,8,1,4);put(out,12,HEADER_BYTES,4);put(out,16,payload.size(),8);put(out,24,sequence,8);
    std::copy(previous.begin(),previous.end(),out.begin()+32);
    const auto hash=digest(payload);std::copy(hash.begin(),hash.end(),out.begin()+64);
    std::copy(payload.begin(),payload.end(),out.begin()+HEADER_BYTES);return out;
}
inline bool same_time(const timespec& a,const timespec& b){return a.tv_sec==b.tv_sec && a.tv_nsec==b.tv_nsec;}
inline bool same_stamp(const struct stat& a,const struct stat& b){
    return a.st_dev==b.st_dev && a.st_ino==b.st_ino && a.st_size==b.st_size && a.st_uid==b.st_uid &&
           a.st_mode==b.st_mode && a.st_nlink==b.st_nlink && same_time(a.st_mtim,b.st_mtim) && same_time(a.st_ctim,b.st_ctim);
}
struct Location {std::uint64_t offset=0;std::size_t size=0;Bytes sha;};
struct Entry {
    Json submitted,completed;std::uint64_t submitted_sequence=0,completed_sequence=0;
    Location submitted_location,completed_location;
};
class Log {
    FD directory_,file_;IO io_;struct stat expected_{};Bytes last_=Bytes(32,0);
    std::uint64_t sequence_=0,bytes_=0;bool poisoned_=false,read_only_=false;Json task_;
    Location task_location_,last_location_;
    std::map<std::string,Entry> entries_;std::string pending_;
    struct stat identity() const {
        struct stat s{},named{},dir{};
        require(::fstat(directory_.value,&dir)==0 && S_ISDIR(dir.st_mode) && (dir.st_mode&07777)==0700 && dir.st_uid==::getuid(),"private owned task directory");
        require(::fstat(file_.value,&s)==0 && ::fstatat(directory_.value,NAME,&named,AT_SYMLINK_NOFOLLOW)==0,"record file stat");
        require(S_ISREG(s.st_mode) && (s.st_mode&07777)==0600 && s.st_uid==::getuid() && s.st_nlink==1 &&
                S_ISREG(named.st_mode) && s.st_dev==named.st_dev && s.st_ino==named.st_ino,"record file replaced/type/owner/mode/links");
        require(s.st_size>=0 && std::uint64_t(s.st_size)<=FILE_LIMIT,"record file size bound");return s;
    }
    Bytes read(std::uint64_t at,std::size_t size) const {
        require(size<=HEADER_BYTES+PAYLOAD_LIMIT && at<=FILE_LIMIT && size<=FILE_LIMIT-at,"bounded record read");
        Bytes b(size);std::size_t pos=0;
        while(pos<size){const auto n=::pread(file_.value,b.data()+pos,size-pos,static_cast<off_t>(at+pos));
            if(n<0 && errno==EINTR)continue;
            require(n>0,"partial/failed record read");pos+=static_cast<std::size_t>(n);}
        return b;
    }
    void validate(const Json& r) const {
        require(r.is_object() && r.contains("schema") && r.at("schema")=="kv260_record_v2b_v1" && r.contains("type"),"record version");
        if(sequence_==0){
            keys(r,{"schema","type","task"});require(r.at("type")=="TASK" && r.at("task").is_object() &&
                r.at("task").contains("identity") && r.at("task").at("identity").is_object() && !r.at("task").at("identity").empty(),"first task record");return;
        }
        const auto type=r.at("type");
        if(type=="SUBMITTED"){
            keys(r,{"schema","type","execution_id","request","caller","identity"});const auto name=id(r.at("execution_id"));
            require(pending_.empty() && !entries_.count(name),"pending or reused execution ID");
            require(r.at("request").is_object() && r.at("request").at("execution_id")==name,"full request binding");
            keys(r.at("caller"),{"pid","birth_ticks"});require(uint(r.at("caller").at("pid"))>0 && uint(r.at("caller").at("birth_ticks"))>0,"original caller binding");
            require(r.at("identity")==task_.at("identity"),"submitted task identity");
        }else if(type=="COMPLETED"){
            keys(r,{"schema","type","execution_id","submitted_sequence","result"});const auto name=id(r.at("execution_id"));
            require(!pending_.empty() && name==pending_ && entries_.count(name),"completion without exact pending request");
            const auto& e=entries_.at(name);
            require(uint(r.at("submitted_sequence"))==e.submitted_sequence && e.completed.is_null(),"completion sequence/repetition");
            require(r.at("result").is_object() && r.at("result").at("request")==e.submitted.at("request"),"complete RESULT request binding");
            require(r.at("result").at("identity")==task_.at("identity"),"complete RESULT identity");
        }else throw std::runtime_error("unknown/out-of-order record type");
    }
    void accept(const Json& r,const Location& location){
        ++sequence_;
        last_location_=location;
        if(r.at("type")=="TASK"){task_=r.at("task");task_location_=location;return;}
        const auto name=r.at("execution_id").get<std::string>();
        if(r.at("type")=="SUBMITTED"){
            entries_.emplace(name,Entry{r,nullptr,sequence_,0,location,{}});pending_=name;
        }else{auto& e=entries_.at(name);e.completed=r;e.completed_sequence=sequence_;e.completed_location=location;pending_.clear();}
    }
    void check_location(const Location& location) const {
        require(location.size>0 && digest(read(location.offset,location.size))==location.sha,"retained record bytes changed");
    }
    void scan(){
        expected_=identity();const auto size=std::uint64_t(expected_.st_size);
        while(bytes_<size){
            require(sequence_<RECORD_LIMIT && size-bytes_>=HEADER_BYTES,"record count/partial header");
            auto h=read(bytes_,HEADER_BYTES);const unsigned char magic[8]={'K','V','2','B','L','O','G',0};
            require(std::equal(magic,magic+8,h.begin()) && get(h,8,4)==1 && get(h,12,4)==HEADER_BYTES,"record header version");
            const auto n=get(h,16,8);
            require(n>0 && n<=PAYLOAD_LIMIT && n<=size-bytes_-HEADER_BYTES,"bad length/partial record");
            require(get(h,24,8)==sequence_+1 && std::equal(last_.begin(),last_.end(),h.begin()+32),"record sequence/previous digest");
            auto p=read(bytes_+HEADER_BYTES,static_cast<std::size_t>(n));const auto dh=digest(p);
            require(std::equal(dh.begin(),dh.end(),h.begin()+64),"record payload digest");
            // Keep the parse depth bounded even for a digest-valid foreign file.
            int depth=0;auto callback=[&](int,Json::parse_event_t event,Json&){
                if(event==Json::parse_event_t::object_start || event==Json::parse_event_t::array_start)require(++depth<=32,"record JSON depth");
                if(event==Json::parse_event_t::object_end || event==Json::parse_event_t::array_end)--depth;
                return true;};
            const auto r=Json::parse(p.begin(),p.end(),callback);validate(r);
            // Canonical byte equality also rejects whitespace/noncanonical JSON
            // and duplicate keys that a permissive DOM parser would collapse.
            h.insert(h.end(),p.begin(),p.end());require(encode(sequence_+1,last_,r)==h,"noncanonical/duplicate record JSON");
            last_=digest(h);accept(r,{bytes_,h.size(),last_});bytes_+=h.size();
        }
        require(sequence_>0 && same_stamp(expected_,identity()),"empty/changed record scan");
    }
public:
    Log(int directory,bool create,const Json& task,IO io={},bool read_only=false):
            directory_(::dup(directory)),io_(std::move(io)),read_only_(read_only){
        require(directory_.value>=0,"task fd duplicate");
        require(!read_only || !create,"reader cannot create a log");
        file_.value=::openat(directory_.value,NAME,(read_only?O_RDONLY:(O_RDWR|O_APPEND))|O_NOFOLLOW|O_CLOEXEC|(create?(O_CREAT|O_EXCL):0),0600);
        require(file_.value>=0,"unique record creation/open");
        if(!read_only)require(::flock(file_.value,LOCK_EX|LOCK_NB)==0,"one actual record writer");
        expected_=identity();
        if(create){
            require(expected_.st_size==0,"new record not empty");
            append({{"schema","kv260_record_v2b_v1"},{"type","TASK"},{"task",task}});
            require(io_.fsync(directory_.value)==0,"actual task directory fsync failed");
        }else{scan();require(task_==task,"reopened task identity changed");}
    }
    Log(const Log&)=delete;Log& operator=(const Log&)=delete;
    std::uint64_t append(const Json& r){
        require(!read_only_ && !poisoned_,"reader/poisoned record writer");
        validate(r);require(sequence_<RECORD_LIMIT,"record count limit");
        const auto record=encode(sequence_+1,last_,r);
        require(bytes_<=FILE_LIMIT-record.size(),"task log size budget");
        try{
            require(same_stamp(expected_,identity()),"record changed between commits");
            std::size_t pos=0;
            while(pos<record.size()){
                const auto n=io_.write(file_.value,record.data()+pos,record.size()-pos);
                if(n<0 && errno==EINTR)continue;
                require(n>0 && std::size_t(n)<=record.size()-pos,"actual record write failed");pos+=std::size_t(n);
            }
            require(io_.fsync(file_.value)==0,"actual record fsync failed");
            auto state=identity();require(std::uint64_t(state.st_size)==bytes_+record.size(),"unexpected record append size");
            require(read(bytes_,record.size())==record && same_stamp(state,identity()),"record commit full readback changed");
            last_=digest(record);accept(r,{bytes_,record.size(),last_});bytes_+=record.size();expected_=state;return sequence_;
        }catch(...){poisoned_=true;throw;}
    }
    const Entry& query(const std::string& name){
        require(!poisoned_,"poisoned record query");
        try{
            if(read_only_)refresh();
            require(same_stamp(expected_,identity()),"record changed before query");
            const auto& entry=entries_.at(name);check_location(task_location_);check_location(entry.submitted_location);
            if(!entry.completed.is_null())check_location(entry.completed_location);
            require(same_stamp(expected_,identity()),"record changed during query");return entry;
        }
        catch(...){poisoned_=true;throw;}
    }
    // Only a read-only owner observes growth by the separately locked writer.
    // Recheck the retained chain boundary, then verify every new complete
    // record. Original request/completion bytes are reread on each query;
    // attach/recovery performs a complete scan. Partial growth is a fault.
    void refresh(){
        require(read_only_ && !poisoned_,"refresh requires live reader");
        try{
            const auto now=identity();
            if(same_stamp(now,expected_))return;
            require(now.st_dev==expected_.st_dev && now.st_ino==expected_.st_ino && now.st_size>expected_.st_size,
                "reader observed replacement/non-append change");
            check_location(task_location_);check_location(last_location_);scan();
        }catch(...){poisoned_=true;throw;}
    }
    const std::map<std::string,Entry>& entries() const{return entries_;}
    std::uint64_t sequence() const{return sequence_;}
    bool poisoned() const{return poisoned_;}
    const std::string& unresolved_submission() const{return pending_;}
};
} // namespace kv260_record_v2b
