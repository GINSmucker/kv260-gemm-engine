// Actual fixed driver/crypto files and bounded negative fixtures. No XRT.
#include "config.h"
#include "../sha256_crypto.h"
#include <iostream>
#include <filesystem>
namespace V=kv260_stream_v2a;
int main(int argc,char** argv){try{
    V::require(argc==2 && ::getuid()==1000,"private software fixture directory argument");
    const std::filesystem::path root=argv[1];V::require(!std::filesystem::exists(root),"new fixture directory only");
    V::require(std::filesystem::create_directory(root),"private fixture directory");::chmod(root.c_str(),0700);
    V::BodyDigest digest=[](const V::Bytes& b){return kv260_crypto_sha256(b.data(),b.size());};
    V::Json rows=V::Json::array();
    auto refused=[&](const char* name,const std::string& path,std::size_t maximum=8*1024*1024){
        bool rejected=false;try{V::actual_regular_digest(path,digest,maximum);}catch(const std::exception&){rejected=true;}
        V::require(rejected,"actual malformed/oversize file was accepted");rows.push_back({{"name",name},{"refused",true}});
    };
    const std::string driver="/home/ubuntu/fpga_project/next_stage/overlay_fix_v2_20260929_01/zocl_candidate.ko";
    struct stat st{};V::require(::stat(driver.c_str(),&st)==0 && st.st_size==9462272 && st.st_nlink==1,"actual frozen driver stat");
    refused("frozen_driver_exceeds_legacy_8MiB",driver);
    const auto dh=V::actual_regular_digest(driver,digest,16*1024*1024);V::require(dh==V::DRIVER_SHA,"actual full frozen driver digest");
    rows.push_back({{"name","frozen_driver_with_explicit_16MiB_bound"},{"sha256",dh},{"bytes",st.st_size},{"nlink",st.st_nlink}});
    const auto ch=V::actual_regular_digest("/usr/lib/aarch64-linux-gnu/libcrypto.so.3",digest);V::require(ch==V::CRYPTO_SHA,"actual full crypto digest");
    rows.push_back({{"name","crypto_keeps_default_8MiB_bound"},{"sha256",ch}});
    auto sparse=[&](const char* name,std::size_t size){const auto file=root/name;V::PrivateFD fd(::open(file.c_str(),O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW,0600));
        V::require(fd.get()>=0 && ::ftruncate(fd.get(),size)==0,"bounded sparse negative fixture");return file.string();};
    refused("default_file_8MiB_plus_one",sparse("over8.bin",8*1024*1024+1));
    refused("explicit_file_16MiB_plus_one",sparse("over16.bin",16*1024*1024+1),16*1024*1024);
    refused("non_regular_directory",root.string());
    const auto small=sparse("small.bin",1);V::require(::link(small.c_str(),(root/"hardlink.bin").c_str())==0,"private hardlink fixture");
    refused("multiply_linked_file",small);
    refused("empty_file",sparse("empty.bin",0));
    std::cout<<V::Json{{"status","PASS_ACTUAL_DRIVER_FILE_BOUND_NO_XRT"},{"cases",rows.size()},{"rows",rows},
        {"driver_maximum_bytes",16*1024*1024},{"default_maximum_bytes",8*1024*1024},{"CU_BO_or_start",false}}.dump()<<'\n';return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
