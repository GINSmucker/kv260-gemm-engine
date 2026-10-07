// Independent V2B C ABI. Synchronous ordinary memory and private files only.
// XRT, BO lifetime and the only MMIO start belong exclusively to the owner.
#include "gemm_backend_v2b.h"
#include "config.h"
#include "pins.h"
#include "record_binding.h"
#include "../sha256_crypto.h"
#include <dlfcn.h>
#include <dirent.h>
#include <memory>
#include <mutex>
#include <sys/statvfs.h>
#include <unordered_map>
#if defined(KV260_V2B_SOFTWARE_TEST)
#include <iostream>
#endif
namespace V=kv260_stream_v2b;
using V::Json;using V::Bytes;using V::PrivateFD;
static_assert(sizeof(kv260_b_request_v2b)==572 && sizeof(kv260_b_receipt_v2b)==1808,"V2B native ABI layout");
namespace {
#if defined(KV260_V2B_SOFTWARE_TEST)
thread_local unsigned* rw03_calls=nullptr;
struct RW03Probe {unsigned calls=0;std::string id;bool passed=false;
    RW03Probe(){rw03_calls=&calls;}
    ~RW03Probe(){rw03_calls=nullptr;std::cerr<<"RW03_RPC "<<Json{{"request_id",id},{"calls",calls},{"passed",passed}}.dump()<<std::endl;}};
#endif
struct BridgeError:std::runtime_error {int code;BridgeError(int c,const std::string& t):std::runtime_error(t),code(c){}};
void need(bool condition,int code,const char* message){if(!condition)throw BridgeError(code,message);}
V::BodyDigest hash=[](const Bytes& b){return kv260_crypto_sha256(b.data(),b.size());};
std::string hash_borrowed(const void* bytes,std::size_t n){return kv260_crypto_sha256(bytes,n);}
std::string text(const char* p,std::size_t n){need(p && std::memchr(p,0,n),B2_BAD_INPUT,"unterminated ABI string");return p;}
void copy(char* out,std::size_t capacity,const std::string& s){need(out && s.size()<capacity,B2_BAD_INPUT,"ABI output capacity");std::memcpy(out,s.c_str(),s.size()+1);}
bool overlap(const void* a,std::size_t n,const void* b,std::size_t m){const auto x=reinterpret_cast<std::uintptr_t>(a),y=reinterpret_cast<std::uintptr_t>(b);
    need(x && y && n<=UINTPTR_MAX-x && m<=UINTPTR_MAX-y,B2_BAD_INPUT,"ABI pointer null/overflow");return x<y+m && y<x+n;}
int code_for(const std::exception& error){
    if(const auto* e=dynamic_cast<const BridgeError*>(&error))return e->code;
    if(const auto* e=dynamic_cast<const V::LedgerError*>(&error)){
        if(e->code=="BAD_IDENTITY")return B2_BAD_IDENTITY;
        if(e->code=="BAD_SHAPE")return B2_BAD_SHAPE;
        if(e->code=="BAD_HASH" || e->code=="BAD_REQUEST" || e->code=="BAD_FRAME")return B2_BAD_INPUT;
        if(e->code=="DEADLINE")return B2_TIMEOUT;
        return B2_OWNER_FAULT;
    }
    if(std::string(error.what()).find("absolute socket deadline")!=std::string::npos)return B2_TIMEOUT;
    return B2_OWNER_FAULT;
}
void barrier(int fd){need(::fsync(fd)==0,B2_INTERNAL,"actual file/directory fsync failed");}
void full_write(int fd,const Bytes& bytes){std::size_t pos=0;while(pos<bytes.size()){
    const auto n=::write(fd,bytes.data()+pos,bytes.size()-pos);if(n<0 && errno==EINTR)continue;
    need(n>0,B2_INTERNAL,"actual complete file write failed");pos+=static_cast<std::size_t>(n);}}
void publish(int directory,const std::string& name,const Bytes& bytes,bool replace=false,mode_t mode=0600){
    const auto temporary=name+".tmp";PrivateFD fd(::openat(directory,temporary.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));
    need(fd.get()>=0,B2_BAD_INPUT,"unique actual publish temporary creation");full_write(fd.get(),bytes);
    need(::fchmod(fd.get(),mode)==0,B2_INTERNAL,"actual published file mode");barrier(fd.get());
    if(replace){V::read_private_file(directory,name,mode,V::HEADER_LIMIT);
        need(::renameat(directory,temporary.c_str(),directory,name.c_str())==0,B2_INTERNAL,"actual journal replacement");}
    else {
        // linkat fails atomically if an original ID/file already exists. The
        // temporary name is removed before any caller can use this record.
        need(::linkat(directory,temporary.c_str(),directory,name.c_str(),0)==0,B2_BAD_INPUT,"actual file already published");
        need(::unlinkat(directory,temporary.c_str(),0)==0,B2_INTERNAL,"actual temporary unlink");
    }
    barrier(directory);auto observed=V::read_private_file(directory,name,mode,std::max<std::size_t>(V::HEADER_LIMIT,bytes.size()));
    need(observed.bytes==bytes,B2_BAD_IDENTITY,"actual published full file changed");
}
void publish_json(int directory,const std::string& name,const Json& value,bool replace=false){publish(directory,name,V::compact_json_bytes(value),replace);}
void audit(int directory,const char* name,const Bytes& bytes){
    PrivateFD file(::openat(directory,name,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));need(file.get()>=0,B2_BAD_INPUT,"unique actual audit array");
    full_write(file.get(),bytes);auto actual=V::read_private_file(directory,name,0600,605184);need(actual.bytes==bytes,B2_BAD_IDENTITY,"actual audit array changed");
    // Audit writes are independently collected/hashed; no persistence promise.
}
std::uint64_t deadline(){const auto t=V::monotonic_ns();need(t<=UINT64_MAX-UINT64_C(130000000000),B2_INTERNAL,"call deadline overflow");return t+UINT64_C(130000000000);}
kv260_record_v2b::IO record_io(int task){
    kv260_record_v2b::IO io;
#if defined(KV260_V2B_SOFTWARE_TEST)
    struct stat status{};errno=0;
    if(::fstatat(task,"software_log_fault.json",&status,AT_SYMLINK_NOFOLLOW)==0){
        const auto file=V::read_private_file(task,"software_log_fault.json",0600,V::HEADER_LIMIT);
        const auto value=V::actual_json_file(file);V::exact_keys(value,{"fail_fsync"});
        const auto fail=V::uint_value(value.at("fail_fsync"),4);need(fail>=3,B2_BAD_INPUT,"only submit/complete software fault");
        auto count=std::make_shared<unsigned>(0);
        io.fsync=[count,fail](int fd){if(++*count==fail){errno=EIO;return -1;}return ::fsync(fd);};
    }else need(errno==ENOENT,B2_BAD_INPUT,"software fault fixture status");
#else
    (void)task;
#endif
    return io;
}
struct Handle {
    V::Configuration config;std::string executable;Json bo,identity;std::mutex mutex;bool faulted=false;
    std::unique_ptr<kv260_record_v2b::Log> records;
    std::uint64_t confirmed_starts=0; // Seed only from admitted INFO; advance only after full verified ACK.
    V::Frame rpc(const V::Frame& value,std::uint64_t until){
#if defined(KV260_V2B_SOFTWARE_TEST)
        if(rw03_calls)++*rw03_calls;
#endif
        return V::exchange(config,executable,value,hash,until);}
    Json info(std::uint64_t until,bool require_idle=true){auto frame=rpc({V::client_envelope(config.context,"INFO"),{}},until);
        need(frame.header.at("type")=="INFO_RESULT",B2_OWNER_FAULT,"owner INFO did not provide actual observations");
        const auto& value=frame.header.at("info");
        if(require_idle)need(value.at("state")=="IDLE" && value.at("safe")==true && value.at("active")==false && value.at("faulted")==false,
            B2_OWNER_FAULT,"owner active/unknown/faulted or Y awaits confirmation");
        if(!bo.is_null())need(value.at("bo")==bo,B2_BAD_IDENTITY,"actual retained three BO changed");
        return value;
    }
    explicit Handle(const Json& cfg,std::uint64_t until):
        config(V::configuration(cfg,{kv260_v2b_pins::OWNER_SHA,kv260_v2b_pins::TOOLS_SHA,kv260_v2b_pins::REGISTRATION_SHA},hash)) {
        const auto pid=V::uint_value(config.context.identity.at("owner_pid"),UINT64_MAX);executable=V::actual_executable_path(pid);
        need(V::actual_regular_digest("/proc/"+std::to_string(pid)+"/exe",hash)==kv260_v2b_pins::OWNER_SHA,B2_BAD_IDENTITY,"actual compiled owner file mismatch");
        Dl_info library{};need(::dladdr(reinterpret_cast<void*>(&kv260_gemm_open_v2b),&library)!=0 && library.dli_fname,B2_BAD_IDENTITY,"actual bridge library path");
        need(V::actual_regular_digest(library.dli_fname,hash)==config.context.identity.at("bridge_sha256"),B2_BAD_IDENTITY,"actual bridge library digest mismatch");
        auto initial=info(until);bo=initial.at("bo");confirmed_starts=V::uint_value(initial.at("starts"),UINT64_MAX);const std::array<std::uint64_t,3> sizes={151552,192512,606208};
        for(unsigned i=0;i!=3;++i){const auto address=V::uint_value(bo.at(i).at("address"),UINT64_MAX);
            need(address+sizes[i]<=0x80000000ULL || (address>=0x800000000ULL && address+sizes[i]<=0x880000000ULL),B2_BAD_IDENTITY,"actual paired DDR BO region");}
        identity={{"schema","kv260_gemm_actual_identity_v2b"},{"abi_version",3},{"wire_version",3},{"identity",config.context.identity},
            {"bo",bo},{"scope",config.scope},{"window_manifest_sha256",config.window_manifest_sha256},
#if defined(KV260_V2B_SOFTWARE_TEST)
            {"software_test",true}};
#else
            {"software_test",false}};
#endif
        auto task=V::anchored_task_directory(config.context);struct stat status{};
        errno=0;const bool exists=::fstatat(task.get(),kv260_record_v2b::NAME,&status,AT_SYMLINK_NOFOLLOW)==0;
        need(exists || errno==ENOENT,B2_BAD_IDENTITY,"task record status unavailable");
        records=std::make_unique<kv260_record_v2b::Log>(task.get(),!exists,V::record_task(config.context),record_io(task.get()));
        // Prove the task entry itself persisted once at attach. Per-request
        // directories contain diagnostics, not the authoritative task records.
        const auto slash=config.context.task_root.rfind('/');
        PrivateFD parent(::open(config.context.task_root.substr(0,slash).c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        need(parent.get()>=0,B2_BAD_IDENTITY,"task parent unavailable");barrier(parent.get());
        errno=0;need(::fstatat(task.get(),"failure.v2b.json",&status,AT_SYMLINK_NOFOLLOW)!=0 && errno==ENOENT,
            B2_OWNER_FAULT,"failed task cannot resume");
        for(const auto& item:records->entries()){
            const auto& entry=records->query(item.first);const auto& r=entry.submitted.at("request");V::request_fields(r,config.context);
            auto requests=V::private_child_directory(task.get(),"requests");auto execution=V::private_child_directory(requests.get(),item.first);
            errno=0;need(::fstatat(execution.get(),"recovery",&status,AT_SYMLINK_NOFOLLOW)!=0 && errno==ENOENT,
                B2_OWNER_FAULT,"terminated recovered task cannot resume");
            if(entry.completed.is_null()){faulted=true;continue;}
            const auto& completed=entry.completed.at("result");
            need(V::message_fields(completed,config.context)=="RESULT",B2_BAD_IDENTITY,"reattach complete record result");
            auto q=V::client_envelope(config.context,"QUERY");q["request"]=r;const auto released=rpc({std::move(q),{}},until);
            need(released.header.at("type")=="QUERY_RESULT" && released.header.at("state")=="ACKED_Y_RELEASED" &&
                released.header.at("request")==r && released.header.at("native")==completed.at("native") &&
                released.header.at("Y_sha256")==completed.at("Y_sha256") && released.header.at("starts_after")==completed.at("starts_after"),
                B2_OWNER_FAULT,"reattach requires original owner-confirmed released result");
        }
    }
};
std::mutex registry_mutex;std::unordered_map<void*,std::shared_ptr<Handle>> registry;std::uintptr_t generation=0;
std::shared_ptr<Handle> attached(void* key){std::lock_guard<std::mutex> lock(registry_mutex);const auto it=registry.find(key);
    need(it!=registry.end(),B2_NOT_READY,"closed/unknown V2B handle");return it->second;}
Json request(const kv260_b_request_v2b& r,const V::TrustedContext& context,std::uint64_t starts,const std::string& xh,const std::string& wh){
    const auto id=text(r.request_id,sizeof(r.request_id));const auto sizes=V::matrix_bytes(r.M,r.K,r.N);
    Json value={{"execution_id",id},{"request_id",id},{"model_run_id",text(r.model_run_id,sizeof(r.model_run_id))},
        {"image_id",text(r.image_id,sizeof(r.image_id))},{"layer_id",text(r.layer_id,sizeof(r.layer_id))},{"layer_index",r.layer_index},
        {"M",r.M},{"K",r.K},{"N",r.N},{"X_bytes",sizes.x},{"W_bytes",sizes.w},{"Y_bytes",sizes.y},
        {"X_sha256",xh},{"W_sha256",wh},{"starts_before",starts},{"W_file",context.task_root+"/weights/l"+std::to_string(r.layer_index)+"_"+wh+".bin"},
        {"native_result_file",context.task_root+"/requests/"+id+"/native.json"},{"mode","dense_w8a8"},
        {"layout","compact_row_major"},{"dtype_x","int8"},{"dtype_w","int8"},{"dtype_y","int32"}};
    V::request_fields(value,context);return value;
}
V::Frame query(Handle& h,const Json& r,std::uint64_t until){auto header=V::client_envelope(h.config.context,"QUERY");header["request"]=r;return h.rpc({std::move(header),{}},until);}
void reply_success(const V::Frame& value,const Json& r){
    if(value.header.at("type")=="ERROR")throw V::LedgerError(value.header.at("error_code"),value.header.at("error_text"));
    need(value.header.at("type")=="RESULT" && value.header.at("request")==r,B2_OWNER_FAULT,"original exact RESULT required; no REQUEST retry");
}
bool execution_uncertain(const V::Frame& value,const Json& original){
    // An accepted submission stays uncertain until the original request has a
    // verified terminal observation. A generic error or NOT_FOUND is not proof
    // that the MMIO start was never issued. QUERY never submits it again.
    if(!value.header.contains("request") || value.header.at("request")!=original ||
        !value.header.contains("state") || value.header.at("state").is_null())return true;
    const auto& state=value.header.at("state");
    return state!="REJECTED_BEFORE_START" && state!="FAILED_SAFE" &&
        state!="COMPLETED_UNACKED" && state!="ACKED_Y_RELEASED";
}
bool acked(const V::Frame& value,const V::Frame& original){
    const auto type=value.header.at("type");return (type=="ACK_RESULT" || (type=="QUERY_RESULT" && value.header.at("state")=="ACKED_Y_RELEASED")) &&
        value.header.at("request")==original.header.at("request") && value.header.at("Y_sha256")==original.header.at("Y_sha256") &&
        value.header.at("starts_after")==original.header.at("starts_after") &&
        (type=="ACK_RESULT"?value.header.at("confirmation").is_null():value.header.at("native")==original.header.at("native"));
}
} // namespace
extern "C" __attribute__((visibility("default"))) int kv260_gemm_open_v2b(const char* cfg,void** out,char* actual,std::size_t capacity){
    if(out)*out=nullptr;
    try {need(cfg && out && actual,B2_BAD_INPUT,"null V2B open argument");const auto n=::strnlen(cfg,V::HEADER_LIMIT+1);
        need(n>0 && n<=V::HEADER_LIMIT,B2_BAD_INPUT,"V2B config size bound");auto h=std::make_shared<Handle>(V::parse_header(std::string(cfg,n)),deadline());
        copy(actual,capacity,h->identity.dump());std::lock_guard<std::mutex> lock(registry_mutex);need(generation<UINTPTR_MAX,B2_INTERNAL,"V2B handle generations exhausted");
        void* key=reinterpret_cast<void*>(++generation);registry.emplace(key,h);*out=key;return B2_GEMM_OK;
    }catch(const std::exception& e){if(actual && capacity){const auto s=V::ascii_error(e.what());std::strncpy(actual,s.c_str(),capacity-1);actual[capacity-1]=0;}return code_for(e);}catch(...){return B2_INTERNAL;}
}
extern "C" __attribute__((visibility("default"))) int kv260_gemm_info_v2b(void* key,char* actual,std::size_t capacity){
    try{auto h=attached(key);std::lock_guard<std::mutex> lock(h->mutex);auto value=h->info(deadline(),false);
        value["actual_identity"]=h->identity;value["bridge_faulted"]=h->faulted;copy(actual,capacity,value.dump());return B2_GEMM_OK;
    }catch(const std::exception& e){return code_for(e);}catch(...){return B2_INTERNAL;}
}
extern "C" __attribute__((visibility("default"))) int kv260_gemm_close_v2b(void* key){std::lock_guard<std::mutex> lock(registry_mutex);return registry.erase(key)==1?B2_GEMM_OK:B2_NOT_READY;}
extern "C" __attribute__((visibility("default"))) int kv260_gemm_run_v2b(void* key,const kv260_b_request_v2b* r,const int8_t* x,std::size_t nx,
        const int8_t* w,std::size_t nw,int32_t* y,std::size_t ny,kv260_b_receipt_v2b* receipt){
#if defined(KV260_V2B_SOFTWARE_TEST)
    RW03Probe rw03_probe;
#endif
    std::shared_ptr<Handle> h;PrivateFD execution;Json original_record;bool attempted_record=false,submitted=false,receipt_valid=false,uncertain=true;std::uint64_t until=0;
    try {
        until=deadline();
        need(r && receipt,B2_BAD_INPUT,"null V2B request/receipt");
        need(r->abi_version==3 && r->struct_bytes==sizeof(*r) && receipt->abi_version==3 && receipt->struct_bytes==sizeof(*receipt),B2_BAD_INPUT,"old/wrong ABI or struct size");
        const void* pointers[]={r,receipt,x,w,y};const std::size_t lengths[]={sizeof(*r),sizeof(*receipt),nx,nw,ny};
        for(unsigned i=0;i!=5;++i)for(unsigned j=0;j<i;++j)need(!overlap(pointers[i],lengths[i],pointers[j],lengths[j]),B2_BAD_INPUT,"borrowed/meta/output overlap");
        std::memset(receipt,0,sizeof(*receipt));receipt->abi_version=3;receipt->struct_bytes=sizeof(*receipt);receipt_valid=true;
        V::MatrixBytes sizes{};try{sizes=V::matrix_bytes(r->M,r->K,r->N);}catch(...){throw BridgeError(B2_BAD_SHAPE,"unsupported full model shape");}
        need(r->layer_index<49,B2_BAD_SHAPE,"layer index outside49");need(nx==sizes.x && nw==sizes.w && ny==sizes.y,B2_BAD_INPUT,"exact complete compact bytes");
        h=attached(key);std::unique_lock<std::mutex> local(h->mutex,std::try_to_lock);need(local.owns_lock(),B2_NOT_READY,"synchronous bridge busy");
        need(!h->faulted,B2_OWNER_FAULT,"original failed/unknown task requires inspection");
        auto task=V::anchored_task_directory(h->config.context);PrivateFD lock(::openat(task.get(),".bridge.lock",O_CREAT|O_RDWR|O_NOFOLLOW|O_CLOEXEC,0600));
        need(lock.get()>=0 && ::flock(lock.get(),LOCK_EX|LOCK_NB)==0,B2_NOT_READY,"another actual bridge owns task");
        const auto wh=text(r->expected_W_sha256,sizeof(r->expected_W_sha256));V::sha_string(Json(wh));
        need(hash_borrowed(w,nw)==wh,B2_BAD_INPUT,"original full borrowed W hash mismatch");const auto xh=hash_borrowed(x,nx);
        struct statvfs space{};need(::fstatvfs(task.get(),&space)==0 && space.f_frsize>0 && space.f_bavail<=UINT64_MAX/space.f_frsize &&
            static_cast<std::uint64_t>(space.f_bavail)*space.f_frsize>=UINT64_C(4)*1024*1024*1024,B2_NOT_READY,"actual disk below4GiB");
        const auto starts=h->confirmed_starts;const auto original=request(*r,h->config.context,starts,xh,wh);
        auto requests=V::private_child_directory(task.get(),"requests");const auto id=original.at("execution_id").get<std::string>();
#if defined(KV260_V2B_SOFTWARE_TEST)
        rw03_probe.id=id;
#endif
        need(::mkdirat(requests.get(),id.c_str(),0700)==0,B2_BAD_INPUT,"duplicate execution ID/private directory failed");
        execution=V::private_child_directory(requests.get(),id);auto weights=V::private_child_directory(task.get(),"weights");
        const auto weight="l"+std::to_string(r->layer_index)+"_"+wh+".bin";struct stat existing{};errno=0;
        if(::fstatat(weights.get(),weight.c_str(),&existing,AT_SYMLINK_NOFOLLOW)!=0){need(errno==ENOENT,B2_BAD_INPUT,"actual W status unavailable");publish(weights.get(),weight,Bytes(w,w+nw),false,0400);}
        const auto actual_w=V::read_private_file(weights.get(),weight,0400,192000);need(actual_w.bytes.size()==nw && hash(actual_w.bytes)==wh,B2_BAD_INPUT,"registered W actual full byte/hash mismatch");
        const V::ExecutionPeer caller{static_cast<std::uint64_t>(::getpid()),V::process_birth_ticks(::getpid()),1000};
        original_record=V::record_submitted(original,caller,h->config.context);
        attempted_record=true;h->records->append(original_record);submitted=true;V::before_deadline(until);
        auto header=V::request_metadata_header(h->config.context,original);
        const Bytes sent_x(x,x+nx);V::Frame incoming;
        try{incoming=h->rpc({header,sent_x},until);}catch(const std::exception&){V::before_deadline(until);incoming=query(*h,original,until);}
        uncertain=execution_uncertain(incoming,original);
        reply_success(incoming,original);V::body_fields(incoming.header,incoming.body,h->config.context,hash);V::before_deadline(until);
        const auto& native=incoming.header.at("native");const auto native_file=V::read_private_file(execution.get(),"native.json",0600,V::HEADER_LIMIT);
        need(V::actual_json_file(native_file)==native,B2_BAD_IDENTITY,"actual native file differs from full socket RESULT");
        need(hash_borrowed(x,nx)==xh && hash_borrowed(w,nw)==wh && hash(sent_x)==xh,B2_BAD_INPUT,"borrowed X/W changed during synchronous execution");
        // This bound owner checks actual BO/state/counter/full native before
        // publishing RESULT and again before ACK. RESULT already carries the
        // exact request, full native, one-start observation and complete Y.
        if(h->config.keep_arrays){audit(execution.get(),"X.audit.bin",sent_x);audit(execution.get(),"Y.audit.bin",incoming.body);}
        receipt->one_start=1;receipt->done=1;receipt->guards_ok=1;receipt->inputs_unchanged=1;receipt->tag0=0xf04207fb;receipt->tag1=0xe4ebd6cd;
        receipt->owner_pid=V::uint_value(h->config.context.identity.at("owner_pid"),UINT64_MAX);receipt->starts_before=starts;receipt->starts_after=starts+1;
        receipt->native_us=V::finite_nonnegative(native.at("native_us"));copy(receipt->X_sha256,sizeof(receipt->X_sha256),xh);copy(receipt->W_sha256,sizeof(receipt->W_sha256),wh);
        copy(receipt->Y_sha256,sizeof(receipt->Y_sha256),incoming.header.at("Y_sha256"));copy(receipt->native_result_file,sizeof(receipt->native_result_file),original.at("native_result_file"));
        const auto& entry=h->records->query(id);
        h->records->append(V::completed_record(entry,incoming.header,h->config.context));
        V::before_deadline(until);std::memcpy(y,incoming.body.data(),ny);
#if defined(KV260_V2B_SOFTWARE_TEST)
        struct stat pause{},already{};
        if(::fstatat(task.get(),"software_pause_ack.json",&pause,AT_SYMLINK_NOFOLLOW)==0 &&
            ::fstatat(task.get(),"software_ready_for_ack.json",&already,AT_SYMLINK_NOFOLLOW)!=0 && errno==ENOENT){
            auto file=V::read_private_file(task.get(),"software_pause_ack.json",0600,V::HEADER_LIMIT);const auto control=V::actual_json_file(file);
            V::exact_keys(control,{"delay_ms"});const auto delay=V::uint_value(control.at("delay_ms"),2000);
            publish_json(task.get(),"software_ready_for_ack.json",{{"execution_id",id}});::usleep(delay*1000);
        }
#endif
        auto acknowledgment=V::client_envelope(h->config.context,"ACK");acknowledgment["request"]=original;acknowledgment["Y_sha256"]=incoming.header.at("Y_sha256");
        acknowledgment["starts_after"]=starts+1;acknowledgment["confirmation"]=nullptr;V::Frame accepted;
        try{accepted=h->rpc({acknowledgment,{}},until);}catch(const std::exception&){V::before_deadline(until);accepted=query(*h,original,until);
            if(accepted.header.at("type")=="RESULT"){need(accepted.body==incoming.body && accepted.header==incoming.header,B2_BAD_IDENTITY,"original pending Y changed after caller copy");
                accepted=h->rpc({acknowledgment,{}},until);}}
        uncertain=execution_uncertain(accepted,original);
        need(acked(accepted,incoming),B2_OWNER_FAULT,"original copied RESULT was not confirmed; no replay");V::before_deadline(until);h->confirmed_starts=starts+1;
#if defined(KV260_V2B_SOFTWARE_TEST)
        rw03_probe.passed=true;
#endif
        return B2_GEMM_OK;
    }catch(const std::exception& e){const auto code=code_for(e);const auto message=V::ascii_error(e.what());
        if(attempted_record && h){h->faulted=true;
            // Ambiguous tails remain intact. Failure seals are outside the
            // normal two-barrier path and cannot authorize result release.
            try{auto task=V::anchored_task_directory(h->config.context);
                publish_json(task.get(),"failure.v2b.json",{{"schema","kv260_v2b_task_failure_v1"},{"submitted",submitted},
                    {"original",original_record},{"uncertain",uncertain},{"code",code},{"error",message}});}catch(...){}
        }
        if(receipt_valid){receipt->status=code;std::strncpy(receipt->error,message.c_str(),sizeof(receipt->error)-1);receipt->error[sizeof(receipt->error)-1]=0;}
        return code;
    }catch(...){if(attempted_record && h)h->faulted=true;if(receipt_valid)receipt->status=B2_INTERNAL;return B2_INTERNAL;}
}
