// Strict V2B bootstrap/bridge configuration. No frame establishes trust.
// Compiled pins and actual process/filesystem/boot/module observations are
// independent of the identity supplied by this private configuration.
#pragma once
#include "protocol/stream_v2b_storage.h"
#include <array>
#include <limits.h>
#include <sys/file.h>

namespace kv260_stream_v2b {
struct BuildPins {std::string owner_sha256,tools_sha256,registration_sha256;};
static constexpr const char* PLATFORM_SHA="c733bf290b7f913852bbfcb919fc40fb548366ccfb9faebcf3d1ff8a91497ff4";
static constexpr const char* A2_SHA="2c25dcf1dc2734bad5a11500de8b0a1b9bf49aa6b56b5c281ecef7c73b3003d0";
static constexpr const char* B_SHA="77680bacbcf6c46571fd6fc99c40778528dd366e950023b73058b6932515962e";
static constexpr const char* DRIVER_SHA="cba324ac56c9bebe84fabbdf2635ea699b10ec718593cba7d44c4b004f782eca";
static constexpr const char* DRIVER_SRC="9806AB60AF7AE94483CDDC9";
static constexpr const char* PLATFORM_UUID="763f3efb-0df3-6ae4-1cc4-974db7b092ca";
static constexpr const char* METADATA_SHA="9c4ad3213e04b5319e1fad0c623a625bf1ac1778ca2d09b50102c6e417c8bee6";
static constexpr const char* CRYPTO_SHA="136996fb4f53b67b36aafb93a37721bc2965fb95ae02cc8477d5fc0374c56156";
inline std::string bounded_system_text(const std::string& path,std::size_t limit) {
    PrivateFD fd(::open(path.c_str(),O_RDONLY|O_CLOEXEC));require(fd.get()>=0,"actual system identity open");
    auto bytes=read_bounded_fd(fd.get(),limit);require(!bytes.empty(),"actual system identity empty");
    while(!bytes.empty() && (bytes.back()=='\n' || bytes.back()=='\r'))bytes.pop_back();
    return std::string(bytes.begin(),bytes.end());
}
inline std::string actual_executable_path(std::uint64_t pid) {
    std::array<char,4096> path{};const auto name="/proc/"+std::to_string(pid)+"/exe";
    const auto n=::readlink(name.c_str(),path.data(),path.size());
    require(n>0 && static_cast<std::size_t>(n)<path.size(),"actual executable path read");
    const std::string value(path.data(),static_cast<std::size_t>(n));
    require(value.front()=='/' && value.find(" (deleted)")==std::string::npos,"actual executable deleted/path");return value;
}
inline std::string actual_regular_digest(const std::string& path,const BodyDigest& digest,std::size_t maximum=8*1024*1024) {
    // /proc/<pid>/exe is deliberately opened to the actual executable inode.
    // Ordinary configuration/request paths use component-anchored NOFOLLOW.
    PrivateFD fd(::open(path.c_str(),O_RDONLY|O_CLOEXEC|O_NONBLOCK));require(fd.get()>=0,"actual identity file open");
    struct stat before{},after{};require(::fstat(fd.get(),&before)==0 && S_ISREG(before.st_mode) && before.st_nlink==1 &&
        before.st_size>0 && static_cast<std::uint64_t>(before.st_size)<=maximum,"actual identity regular file bound");
    const auto bytes=read_bounded_fd(fd.get(),maximum);
    require(bytes.size()==static_cast<std::uint64_t>(before.st_size) && ::fstat(fd.get(),&after)==0 &&
        same_file_observation(before,after),"actual identity file changed");return digest(bytes);
}
struct Configuration {
    TrustedContext context;std::string socket,metadata,scope,window_manifest_sha256;bool keep_arrays=false;
    explicit Configuration(TrustedContext admitted):context(std::move(admitted)) {}
};
inline Configuration configuration(const Json& value,const BuildPins& pins,const BodyDigest& digest) {
    exact_keys(value,{"schema","abi_version","wire_version","uid","identity","task_root","socket","layers",
        "keep_arrays","window_manifest_sha256","metadata_xclbin_file","scope"});
    require(value.at("schema")=="kv260_gemm_bridge_config_v2b" && uint_value(value.at("abi_version"),UINT32_MAX)==3 &&
        uint_value(value.at("wire_version"),UINT32_MAX)==3 && uint_value(value.at("uid"),UINT32_MAX)==1000 && ::getuid()==1000,
        "V2B configuration version/uid");
    for(const auto& s:{pins.owner_sha256,pins.tools_sha256,pins.registration_sha256})sha_string(Json(s));
    const auto& id=value.at("identity");identity_fields(id);
    require(id.at("owner_sha256")==pins.owner_sha256 && id.at("B_tools_wheel_sha256")==pins.tools_sha256 &&
        id.at("platform_manifest_sha256")==PLATFORM_SHA && id.at("A2_gate_sha256")==A2_SHA && id.at("B_manifest_sha256")==B_SHA &&
        id.at("driver_sha256")==DRIVER_SHA && id.at("driver_srcversion")==DRIVER_SRC && id.at("uuid")==PLATFORM_UUID &&
        uint_value(id.at("tag0"),UINT32_MAX)==0xf04207fb && uint_value(id.at("tag1"),UINT32_MAX)==0xe4ebd6cd,
        "compiled owner/tools/platform/A2/B/driver/tag/UUID bindings");
    const auto task_root=ascii_string(value.at("task_root"),1,1023);
    const auto& layers=value.at("layers");require(layers.is_array() && layers.size()==49,"configured 49 layers");
    require(digest(compact_json_bytes(layers))==pins.registration_sha256,"compiled complete layer/W registration binding");
    std::vector<RegisteredLayer> registry;
    for(const auto& layer:layers){exact_keys(layer,{"layer_id","M","K","N","weights"});
        require(layer.at("weights").is_array() && !layer.at("weights").empty() && layer.at("weights").size()<=8,"bounded registered weights");
        RegisteredLayer registered{ascii_string(layer.at("layer_id"),1,95),static_cast<std::uint32_t>(uint_value(layer.at("M"),UINT32_MAX)),
            static_cast<std::uint32_t>(uint_value(layer.at("K"),UINT32_MAX)),static_cast<std::uint32_t>(uint_value(layer.at("N"),UINT32_MAX)),{}};
        for(const auto& wh:layer.at("weights")){sha_string(wh);require(registered.weights.insert(wh.get<std::string>()).second,"duplicate registered weight");}
        registry.push_back(std::move(registered));
    }
    Configuration result{TrustedContext{id,task_root,registry}};
    trusted_context(result.context);result.socket=ascii_string(value.at("socket"),1,107);
    require(result.socket==result.context.task_root+"/owner.sock","derived V2B socket path");
    result.metadata=ascii_string(value.at("metadata_xclbin_file"),1,1023);
    require(result.metadata==result.context.task_root+"/model_metadata.xclbin","derived metadata-only xclbin path");
    result.scope=ascii_string(value.at("scope"),1,32);require(result.scope=="C_S2_VALIDATION_ONLY" || result.scope=="B_SYSTEM_VERIFIED","explicit V2B gate scope");
    result.keep_arrays=boolean(value.at("keep_arrays"));sha_string(value.at("window_manifest_sha256"));
    result.window_manifest_sha256=value.at("window_manifest_sha256");
    auto task=anchored_task_directory(result.context);private_child_directory(task.get(),"requests");private_child_directory(task.get(),"weights");
    require(bounded_system_text("/proc/sys/kernel/random/boot_id",64)==id.at("boot_id"),"actual configured boot changed");
#if !defined(KV260_V2B_SOFTWARE_TEST)
    require(bounded_system_text("/sys/module/zocl/srcversion",64)==DRIVER_SRC,"actual loaded module changed");
    require(actual_regular_digest("/home/ubuntu/fpga_project/next_stage/overlay_fix_v2_20260929_01/zocl_candidate.ko",digest,16*1024*1024)==DRIVER_SHA,"actual candidate module file changed");
    require(actual_regular_digest("/usr/lib/aarch64-linux-gnu/libcrypto.so.3",digest)==CRYPTO_SHA,"actual crypto runtime file changed");
    auto metadata=read_private_file(task.get(),"model_metadata.xclbin",0600,1048576);
    require(digest(metadata.bytes)==METADATA_SHA,"actual metadata-only paired xclbin changed");
#endif
    return result;
}
inline void owner_lifetime(const Configuration& config,const std::string& executable) {
    const auto& identity=config.context.identity;const auto pid=uint_value(identity.at("owner_pid"),UINT64_MAX);
    require(bounded_system_text("/proc/sys/kernel/random/boot_id",64)==identity.at("boot_id") &&
        process_birth_ticks(pid)==uint_value(identity.at("owner_birth_ticks"),UINT64_MAX) && actual_executable_path(pid)==executable,
        "actual owner boot/PID/birth/executable changed");
#if !defined(KV260_V2B_SOFTWARE_TEST)
    require(bounded_system_text("/sys/module/zocl/srcversion",64)==DRIVER_SRC,"actual loaded driver changed");
#endif
}
inline PrivateFD connect_owner(const Configuration& config,const std::string& executable,std::uint64_t deadline) {
    before_deadline(deadline);owner_lifetime(config,executable);auto task=anchored_task_directory(config.context);
    struct stat named{};require(::fstatat(task.get(),"owner.sock",&named,AT_SYMLINK_NOFOLLOW)==0 && S_ISSOCK(named.st_mode) &&
        named.st_uid==1000 && (named.st_mode&0777)==0600,"actual private V2B socket");
    PrivateFD fd(::socket(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0));require(fd.get()>=0,"V2B connect socket");
    sockaddr_un address{};address.sun_family=AF_UNIX;std::memcpy(address.sun_path,config.socket.c_str(),config.socket.size()+1);
    if(::connect(fd.get(),reinterpret_cast<sockaddr*>(&address),sizeof(address))!=0){require(errno==EINPROGRESS || errno==EAGAIN,"V2B connect failure");
        SocketIOStats stats;poll_socket(fd.get(),POLLOUT,deadline,stats);int error=0;socklen_t size=sizeof(error);
        require(::getsockopt(fd.get(),SOL_SOCKET,SO_ERROR,&error,&size)==0 && error==0,"V2B connected socket error");}
    verify_socket_owner(fd.get(),config.context);return fd;
}
inline Json client_envelope(const TrustedContext& c,const char* type) {
    return {{"schema","kv260_gemm_stream_v2b"},{"wire_version",3},{"type",type},{"identity",c.identity},{"body_bytes",0}};
}
inline Frame exchange(const Configuration& c,const std::string& executable,const Frame& sent,
        const BodyDigest& digest,std::uint64_t deadline) {
    auto socket=connect_owner(c,executable,deadline);SocketIOStats wrote,waited,received;
    send_socket_frame(socket.get(),sent,c.context,digest,deadline,wrote);
    // The original call deadline covers waiting for the synchronous kernel.
    // The bounded frame clock starts when the first readable byte/EOF exists.
    poll_socket(socket.get(),POLLIN,deadline,waited);verify_socket_owner(socket.get(),c.context);
    return receive_socket_frame(socket.get(),c.context,digest,deadline,received);
}
} // namespace kv260_stream_v2b
