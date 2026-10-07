// Actual Linux file/syscall boundary tests. No XRT or software kernel substitute.
#include "record_log.h"
#include <iostream>
#include <sys/wait.h>
namespace R=kv260_record_v2b;
using R::Json;using R::Bytes;
static Json task={{"identity",{{"boot","storage-only-fixture"},{"owner",123U}}},{"registration","public-fixture"}};
static Json submitted(const std::string& name){return {{"schema","kv260_record_v2b_v1"},{"type","SUBMITTED"},{"execution_id",name},
    {"request",{{"execution_id",name},{"X_sha256",std::string(64,'a')},{"starts_before",0U}}},
    {"caller",{{"pid",123U},{"birth_ticks",456U}}},{"identity",task.at("identity")}};}
static Json completed(const std::string& name,std::uint64_t seq){return {{"schema","kv260_record_v2b_v1"},{"type","COMPLETED"},
    {"execution_id",name},{"submitted_sequence",seq},
    {"result",{{"request",submitted(name).at("request")},{"identity",task.at("identity")},
               {"native",{{"scope","STORAGE_FIXTURE_NOT_HARDWARE"}}},{"Y_sha256",std::string(64,'b')}}}};}
template<class F>static void rejects(F f){bool caught=false;try{f();}catch(const std::exception&){caught=true;}R::require(caught,"expected rejection missing");}
static void append_bytes(int dir,const Bytes& b){R::FD f(::openat(dir,R::NAME,O_WRONLY|O_APPEND|O_CLOEXEC));R::require(f.value>=0,"fixture open");
    R::require(::write(f.value,b.data(),b.size())==static_cast<ssize_t>(b.size()),"fixture append");R::require(::fsync(f.value)==0,"fixture fsync");}
static Bytes raw(int dir){R::FD f(::openat(dir,R::NAME,O_RDONLY|O_CLOEXEC));struct stat s{};R::require(::fstat(f.value,&s)==0,"fixture stat");
    Bytes b(static_cast<std::size_t>(s.st_size));R::require(::pread(f.value,b.data(),b.size(),0)==static_cast<ssize_t>(b.size()),"fixture read");return b;}
static void overwrite(int dir,const Bytes& b){R::FD f(::openat(dir,R::NAME,O_WRONLY|O_CLOEXEC));R::require(::pwrite(f.value,b.data(),b.size(),0)==static_cast<ssize_t>(b.size()),"fixture rewrite");}
int main(int argc,char** argv){
    try{
        R::require(argc==2,"fixture root required");::umask(0077);R::require(::mkdir(argv[1],0700)==0,"unique fixture root");
        R::FD root(::open(argv[1],O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));Json rows=Json::array();unsigned index=0;
        auto test=[&](const char* name,auto body){const auto dir="c"+std::to_string(++index);R::require(::mkdirat(root.value,dir.c_str(),0700)==0,"case dir");
            R::FD fd(::openat(root.value,dir.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));body(fd.value);
            rows.push_back({{"name",name},{"status","PASS"}});};
        test("two_actual_commit_fsync_and_readback",[](int d){unsigned syncs=0;R::IO io;io.fsync=[&](int f){++syncs;return ::fsync(f);};
            {R::Log l(d,true,task,io);R::require(syncs==2,"task file and directory sync");syncs=0;
             const auto seq=l.append(submitted("one"));l.append(completed("one",seq));R::require(syncs==2 && l.sequence()==3,"exact two request record syncs");
             R::require(l.query("one").completed.at("type")=="COMPLETED","complete query");}
            R::Log reopened(d,false,task);R::require(reopened.sequence()==3 && reopened.query("one").submitted_sequence==2,"reattach index");});
        test("short_writes_and_EINTR_complete",[](int d){unsigned calls=0;R::IO io;io.write=[&](int f,const void* p,std::size_t n)->ssize_t{
                if(++calls==1){errno=EINTR;return -1;}return ::write(f,p,std::min<std::size_t>(n,17));};
            R::Log l(d,true,task,io);const auto s=l.append(submitted("short"));l.append(completed("short",s));R::require(calls>10 && l.sequence()==3,"short write completion");});
        test("ENOSPC_partial_record_poison_preserve",[](int d){unsigned calls=0;bool fault=false;R::IO io;
            io.write=[&](int f,const void* p,std::size_t n)->ssize_t{if(fault){if(++calls>1){errno=ENOSPC;return -1;}n=11;}return ::write(f,p,n);};
            {R::Log l(d,true,task,io);fault=true;rejects([&]{l.append(submitted("full"));});const auto bytes=raw(d);
             R::require(l.poisoned(),"ENOSPC handle poison");rejects([&]{l.append(submitted("next"));});R::require(raw(d)==bytes,"partial evidence retained");}
            rejects([&]{R::Log l(d,false,task);});});
        for(unsigned fail_at:{3U,4U})test(fail_at==3?"SUBMITTED_fsync_failure_no_send":"COMPLETED_fsync_failure_no_Y_ACK",[&](int d){
            unsigned syncs=0,send=0,deliver=0,ack=0;R::IO io;io.fsync=[&](int f){if(++syncs==fail_at){errno=EIO;return -1;}return ::fsync(f);};
            R::Log l(d,true,task,io);rejects([&]{const auto s=l.append(submitted("fault"));++send;l.append(completed("fault",s));++deliver;++ack;});
            R::require(l.poisoned() && send==(fail_at==3?0U:1U) && deliver==0 && ack==0,"commit gate ordering");
            const auto bytes=raw(d);rejects([&]{l.append(submitted("new"));});R::require(raw(d)==bytes,"failed sync bytes preserved");});
        test("directory_initial_sync_failure",[](int d){unsigned syncs=0;R::IO io;io.fsync=[&](int f){if(++syncs==2){errno=EIO;return -1;}return ::fsync(f);};
            rejects([&]{R::Log l(d,true,task,io);});R::require(!raw(d).empty(),"initial failure preserved");});
        test("partial_tail_header",[](int d){{R::Log l(d,true,task);}append_bytes(d,Bytes(7,0));const auto b=raw(d);rejects([&]{R::Log l(d,false,task);});R::require(raw(d)==b,"tail not truncated");});
        test("partial_tail_payload",[](int d){{R::Log l(d,true,task);}auto b=raw(d);auto p=R::encode(2,R::digest(b),submitted("tail"));p.pop_back();append_bytes(d,p);rejects([&]{R::Log l(d,false,task);});});
        test("oversize_length_before_allocation",[](int d){{R::Log l(d,true,task);}auto b=raw(d);R::put(b,16,UINT64_MAX,8);overwrite(d,b);rejects([&]{R::Log l(d,false,task);});});
        test("payload_digest_corruption",[](int d){{R::Log l(d,true,task);}auto b=raw(d);b.back()^=1;overwrite(d,b);rejects([&]{R::Log l(d,false,task);});});
        test("previous_hash_corruption",[](int d){{R::Log l(d,true,task);}auto b=raw(d);b[32]^=1;overwrite(d,b);rejects([&]{R::Log l(d,false,task);});});
        test("unknown_format_version",[](int d){{R::Log l(d,true,task);}auto b=raw(d);R::put(b,8,2,4);overwrite(d,b);rejects([&]{R::Log l(d,false,task);});});
        test("valid_digest_wrong_sequence",[](int d){{R::Log l(d,true,task);}auto b=raw(d);append_bytes(d,R::encode(9,R::digest(b),submitted("wrong")));rejects([&]{R::Log l(d,false,task);});});
        test("duplicate_ID_and_completion_order",[](int d){R::Log l(d,true,task);rejects([&]{l.append(completed("early",2));});
            const auto s=l.append(submitted("one"));rejects([&]{l.append(submitted("two"));});rejects([&]{l.append(completed("one",s+1));});
            l.append(completed("one",s));rejects([&]{l.append(submitted("one"));});rejects([&]{l.append(completed("one",s));});});
        test("task_and_result_binding",[](int d){R::Log l(d,true,task);auto bad=submitted("one");bad["identity"]["owner"]=124U;
            rejects([&]{l.append(bad);});const auto s=l.append(submitted("one"));auto c=completed("one",s);c["result"]["request"]["X_sha256"]=std::string(64,'c');rejects([&]{l.append(c);});});
        test("second_live_writer_refused",[](int d){R::Log l(d,true,task);rejects([&]{R::Log second(d,false,task);});});
        test("file_replacement_live_refused",[](int d){R::Log l(d,true,task);R::require(::renameat(d,R::NAME,d,"old.v2b")==0,"fixture rename");
            R::FD other(::openat(d,R::NAME,O_WRONLY|O_CREAT|O_EXCL,0600));rejects([&]{l.append(submitted("one"));});R::require(l.poisoned(),"replacement poison");});
        test("same_size_old_bytes_mutation_refused",[](int d){R::Log l(d,true,task);auto b=raw(d);b.back()^=1;overwrite(d,b);
            rejects([&]{l.append(submitted("one"));});R::require(l.poisoned(),"old bytes mutation poison");});
        test("hardlink_rejected",[](int d){{R::Log l(d,true,task);}R::require(::linkat(d,R::NAME,d,"alias",0)==0,"fixture link");rejects([&]{R::Log l(d,false,task);});});
        test("mode_change_rejected",[](int d){{R::Log l(d,true,task);}R::require(::fchmodat(d,R::NAME,0644,0)==0,"fixture mode");rejects([&]{R::Log l(d,false,task);});});
        test("changed_task_on_reattach_rejected",[](int d){{R::Log l(d,true,task);}auto changed=task;changed["identity"]["owner"]=124U;rejects([&]{R::Log l(d,false,changed);});});
        test("real_process_exit_before_submit_zero_dispatch",[](int d){
            const auto pid=::fork();R::require(pid>=0,"fork");if(pid==0){R::Log l(d,true,task);::_exit(0);}
            int status=0;R::require(::waitpid(pid,&status,0)==pid && status==0,"child exit");R::Log l(d,false,task);R::require(l.sequence()==1,"no submit persisted");});
        test("real_process_exit_submitted_preserves_original_ID",[](int d){
            const auto pid=::fork();R::require(pid>=0,"fork");if(pid==0){R::Log l(d,true,task);l.append(submitted("crashed"));::_exit(0);}
            int status=0;R::require(::waitpid(pid,&status,0)==pid && status==0,"child exit");R::Log l(d,false,task);
            R::require(l.unresolved_submission()=="crashed" && l.query("crashed").completed.is_null(),"unresolved original ID");rejects([&]{l.append(submitted("new"));});});
        test("real_process_exit_completed_retains_receipt_no_consumption_claim",[](int d){
            const auto pid=::fork();R::require(pid>=0,"fork");if(pid==0){R::Log l(d,true,task);const auto s=l.append(submitted("done"));l.append(completed("done",s));::_exit(0);}
            int status=0;R::require(::waitpid(pid,&status,0)==pid && status==0,"child exit");R::Log l(d,false,task);
            R::require(l.query("done").completed.at("result")==completed("done",2).at("result"),"complete receipt retained");rejects([&]{l.append(submitted("done"));});});
        test("incremental_reader_original_lookup_and_no_write",[](int d){
            R::Log writer(d,true,task);R::Log reader(d,false,task,{},true);
            auto s=writer.append(submitted("one"));R::require(reader.query("one").completed.is_null(),"reader saw submit");
            writer.append(completed("one",s));R::require(!reader.query("one").completed.is_null(),"reader saw complete");
            s=writer.append(submitted("two"));writer.append(completed("two",s));
            R::require(reader.query("one").completed_sequence==3 && reader.query("two").completed_sequence==5,"retained lookup");
            rejects([&]{reader.append(submitted("forbidden"));});});
        test("incremental_reader_partial_tail_fault",[](int d){R::Log writer(d,true,task);R::Log reader(d,false,task,{},true);
            append_bytes(d,Bytes(3,0));rejects([&]{reader.refresh();});R::require(reader.poisoned(),"reader tail poison");});
        test("incremental_reader_modified_original_and_growth",[](int d){R::Log writer(d,true,task);const auto s=writer.append(submitted("old"));
            writer.append(completed("old",s));R::Log reader(d,false,task,{},true);auto bytes=raw(d);
            const auto location=writer.query("old").submitted_location;const auto completed_location=reader.query("old").completed_location;
            bytes.at(location.offset+R::HEADER_BYTES+3)^=1;overwrite(d,bytes);
            auto extra=R::encode(4,R::digest(Bytes(bytes.begin()+completed_location.offset,bytes.end())),submitted("new"));
            append_bytes(d,extra);rejects([&]{reader.query("old");});});
        std::cout<<Json({{"status","PASS_V2B_RECORD_STORAGE_ONLY"},{"cases",rows.size()},{"tests",rows},
            {"physical_PL_starts",0},{"software_kernel_requests",0},{"normal_request_record_fsync",2},
            {"not_covered",{"socket/ABI integration","actual owner QUERY/ACK recovery","power loss","hardware"}}}).dump()<<'\n';
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
