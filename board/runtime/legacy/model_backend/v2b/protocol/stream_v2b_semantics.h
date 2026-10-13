// Shared semantic checks for the proposed V2B bridge and owner.
// TrustedContext is supplied from verified configuration, never from a frame.
// No peer, file, body-hash, lifecycle, BO or hardware proof is performed here.
#pragma once
#include "stream_v2b_json.h"
#include <array>
#include <memory>
#include <vector>

namespace kv260_stream_v2b {
struct RegisteredLayer {
    std::string id;
    std::uint32_t M, K, N;
    std::set<std::string> weights;
};
class TrustedContext {
    struct Data {
        const Json identity;
        const std::string task_root;
        const std::vector<RegisteredLayer> layers;
        Data(const Json& id,const std::string& root,const std::vector<RegisteredLayer>& registry):
            identity(id),task_root(root),layers(registry) {}
    };
    const std::shared_ptr<const Data> data_;
public:
    const Json& identity;
    const std::string& task_root;
    const std::vector<RegisteredLayer>& layers;
    // Take deep copies at admission; aliases into the caller's mutable inputs
    // cannot alter this snapshot. Copies thereafter share only const ownership.
    TrustedContext(const Json& id,const std::string& root,const std::vector<RegisteredLayer>& registry);
    TrustedContext(const TrustedContext&) noexcept = default;
    // A moved-from context also remains valid: every reference retains an owner.
    TrustedContext(TrustedContext&& other) noexcept:TrustedContext(static_cast<const TrustedContext&>(other)) {}
    TrustedContext& operator=(const TrustedContext&) = delete;
    TrustedContext& operator=(TrustedContext&&) = delete;
    bool has_storage() const noexcept {return bool(data_);}
};
struct MatrixBytes { std::uint32_t x, w, y; };

inline bool boolean(const Json& value) {
    require(value.is_boolean(), "boolean required");
    return value.get<bool>();
}
inline std::string invocation_id(const Json& value, std::size_t maximum) {
    const auto result = id_string(value, maximum);
    require(result.find('.') == std::string::npos, "invocation ID character");
    return result;
}
inline MatrixBytes matrix_bytes(std::uint64_t M, std::uint64_t K, std::uint64_t N) {
    require((M == 197 && K == 192 && (N == 576 || N == 192 || N == 768)) ||
        (M == 197 && K == 768 && N == 192) || (M == 1 && K == 192 && N == 1000),
        "five-family shape required");
    // Bounds are proved before multiplication and before caller allocation.
    const auto x = M*K, w = K*N, y = 4*M*N;
    require(x <= 151296 && w <= 192000 && y <= 605184 && y % 4 == 0 &&
        x <= SIZE_MAX && w <= SIZE_MAX && y <= SIZE_MAX, "matrix byte overflow");
    return {static_cast<std::uint32_t>(x), static_cast<std::uint32_t>(w), static_cast<std::uint32_t>(y)};
}
inline void validate_trusted_context_fields(const TrustedContext& context) {
    identity_fields(context.identity);
    const auto root = ascii_string(Json(context.task_root), 1, 1023);
    const std::string prefix = "/home/ubuntu/fpga_project/model_b/runs/";
    require(root == prefix + context.identity.at("window_id").get<std::string>() + "/t",
        "trusted task root derivation");
    require(context.layers.size() == 49, "trusted layer count");
    std::set<std::string> ids;
    for (const auto& layer : context.layers) {
        id_string(Json(layer.id), 95);
        require(ids.insert(layer.id).second && !layer.weights.empty(), "trusted layer registration");
        matrix_bytes(layer.M, layer.K, layer.N);
        for (const auto& weight : layer.weights) sha_string(Json(weight));
    }
}
inline TrustedContext::TrustedContext(const Json& id,const std::string& root,const std::vector<RegisteredLayer>& registry):
    data_(std::make_shared<const Data>(id,root,registry)),identity(data_->identity),task_root(data_->task_root),layers(data_->layers) {
    validate_trusted_context_fields(*this);
}
inline void trusted_context(const TrustedContext& context) {
    // All instances can only be constructed through full validation and retain
    // immutable owned data. Incoming messages and live peer state still validate.
    require(context.has_storage(),"trusted immutable context unavailable");
}
inline MatrixBytes request_fields(const Json& request, const TrustedContext& context) {
    exact_keys(request, {"execution_id", "request_id", "model_run_id", "image_id", "layer_id",
        "layer_index", "M", "K", "N", "X_bytes", "W_bytes", "Y_bytes", "X_sha256", "W_sha256",
        "starts_before", "W_file", "native_result_file", "mode", "layout", "dtype_x", "dtype_w", "dtype_y"});
    const auto id = invocation_id(request.at("request_id"), 191);
    require(invocation_id(request.at("execution_id"), 191) == id, "execution/request ID mismatch");
    const auto model = invocation_id(request.at("model_run_id"), 95);
    const auto image = invocation_id(request.at("image_id"), 95);
    const auto index = uint_value(request.at("layer_index"), 48);
    require(id == model + "_" + image + "_l" + std::to_string(index), "request ID derivation");
    require(context.layers.size() == 49, "trusted layer count");
    const auto& registered = context.layers.at(static_cast<std::size_t>(index));
    require(id_string(request.at("layer_id"), 95) == registered.id, "layer registration mismatch");
    const auto M = uint_value(request.at("M"), UINT32_MAX), K = uint_value(request.at("K"), UINT32_MAX),
        N = uint_value(request.at("N"), UINT32_MAX);
    const auto bytes = matrix_bytes(M, K, N);
    require(M == registered.M && K == registered.K && N == registered.N, "registered layer shape");
    require(uint_value(request.at("X_bytes"), UINT32_MAX) == bytes.x &&
        uint_value(request.at("W_bytes"), UINT32_MAX) == bytes.w &&
        uint_value(request.at("Y_bytes"), UINT32_MAX) == bytes.y, "derived request bytes");
    sha_string(request.at("X_sha256")); sha_string(request.at("W_sha256"));
    const auto wh = request.at("W_sha256").get<std::string>();
    require(registered.weights.count(wh) == 1, "unregistered W digest");
    const auto wp = context.task_root + "/weights/l" + std::to_string(index) + "_" + wh + ".bin";
    const auto np = context.task_root + "/requests/" + id + "/native.json";
    require(ascii_string(request.at("W_file"), 1, 1023) == wp &&
        ascii_string(request.at("native_result_file"), 1, 1023) == np, "exact request path derivation");
    require(uint_value(request.at("starts_before"), UINT64_MAX) != UINT64_MAX, "starts cannot increment");
    require(request.at("mode") == "dense_w8a8" && request.at("layout") == "compact_row_major" &&
        request.at("dtype_x") == "int8" && request.at("dtype_w") == "int8" && request.at("dtype_y") == "int32",
        "request numeric contract");
    return bytes;
}
// INFO.last lacks X/W/request metadata. Validate only its own observations
// here; a successful RESULT additionally binds the complete request below.
inline MatrixBytes native_observation_fields(const Json& native, const TrustedContext& context) {
    exact_keys(native, {"K", "M", "N", "Y_bytes", "Y_sha256", "copy_us", "ctrl_after", "done_latched",
        "guards_pass", "inputs_unchanged", "kernel_cycles", "kernel_error", "native_us", "one_start",
        "request_id", "start_to_idle_and_drain_us", "starts", "status", "sync_in_us", "sync_out_us",
        "tag0", "tag1", "traffic"});
    const auto M = uint_value(native.at("M"),UINT32_MAX), K = uint_value(native.at("K"),UINT32_MAX),
        N = uint_value(native.at("N"),UINT32_MAX);
    const auto bytes = matrix_bytes(M,K,N);
    sha_string(native.at("Y_sha256")); invocation_id(native.at("request_id"),191);
    require(uint_value(native.at("Y_bytes"),UINT32_MAX) == bytes.y &&
        uint_value(native.at("starts"),UINT64_MAX) != 0, "native output bytes/nonzero start");
    require(native.at("status") == "PASS" && native.at("kernel_cycles") == "UNAVAILABLE" &&
        uint_value(native.at("kernel_error"), UINT32_MAX) == 0, "native success contract");
    for (const char* k : {"one_start", "done_latched", "guards_pass", "inputs_unchanged"})
        require(boolean(native.at(k)), "native true success flag");
    const auto ctrl = uint_value(native.at("ctrl_after"), UINT32_MAX);
    require((ctrl & 4) && !(ctrl & 0x81), "native CTRL idle/start contract");
    for (const char* k : {"tag0", "tag1"}) require(uint_value(native.at(k), UINT32_MAX) ==
        uint_value(context.identity.at(k), UINT32_MAX), "native tag mismatch");
    for (const char* k : {"copy_us", "sync_in_us", "sync_out_us", "native_us", "start_to_idle_and_drain_us"})
        finite_nonnegative(native.at(k));
    const auto& traffic = native.at("traffic");
    exact_keys(traffic, {"16", "20", "24", "28", "32", "36", "40", "44", "48", "52", "56", "60"});
    const auto x_ar = M*(K/32)*((N+63)/64), w_ar = K*N/64, y_aw = M*((N+15)/16);
    const std::array<std::uint64_t, 12> expected = {0,0,0,0,x_ar,8*x_ar,w_ar,16*w_ar,y_aw,M*N,y_aw,1};
    const std::array<const char*, 12> keys = {"16","20","24","28","32","36","40","44","48","52","56","60"};
    for (std::size_t i = 0; i != keys.size(); ++i)
        require(uint_value(traffic.at(keys[i]), UINT32_MAX) == expected[i], "fixed DUT traffic mismatch");
    return bytes;
}
inline void native_fields(const Json& native, const Json& request, const Json& y_sha,
        std::uint64_t starts_after, const TrustedContext& context) {
    native_observation_fields(native,context);
    const auto bytes = request_fields(request,context);
    for (const char* k : {"M","K","N"}) require(uint_value(native.at(k),UINT32_MAX) ==
        uint_value(request.at(k),UINT32_MAX), "native shape mismatch");
    sha_string(y_sha);
    require(uint_value(native.at("Y_bytes"),UINT32_MAX) == bytes.y && native.at("Y_sha256") == y_sha &&
        native.at("request_id") == request.at("request_id"), "native output/request binding");
    require(starts_after == uint_value(request.at("starts_before"),UINT64_MAX)+1 &&
        uint_value(native.at("starts"),UINT64_MAX) == starts_after, "native one start binding");
}
inline void info_fields(const Json& info,const TrustedContext& context) {
    exact_keys(info,{"state","safe","active","faulted","starts","done_latched","bo","pending","last","terminal_index_count"});
    const auto state=ascii_string(info.at("state"),1,32);
    const bool safe=boolean(info.at("safe")),active=boolean(info.at("active")),faulted=boolean(info.at("faulted"));
    const auto starts=uint_value(info.at("starts"),UINT64_MAX);
    boolean(info.at("done_latched"));
    const auto count=uint_value(info.at("terminal_index_count"),65536);
    const auto& bo=info.at("bo");require(bo.is_array() && bo.size()==3,"INFO three BO");
    const std::array<std::uint64_t,3> sizes={151552,192512,606208};
    std::array<std::uint64_t,3> begin{},end{};
    for(std::size_t i=0;i!=3;++i) {
        exact_keys(bo.at(i),{"address","bytes","high_address"});
        begin[i]=uint_value(bo.at(i).at("address"),UINT64_MAX);
        require(begin[i]!=0 && begin[i]%64==0 && begin[i]<=UINT64_MAX-sizes[i] &&
            uint_value(bo.at(i).at("bytes"),UINT32_MAX)==sizes[i],"INFO BO size/address/overflow");
        end[i]=begin[i]+sizes[i];
        require(boolean(bo.at(i).at("high_address")) == (begin[i]>>32!=0),"INFO high address flag");
    }
    for(std::size_t i=0;i!=3;++i)for(std::size_t j=i+1;j!=3;++j)
        require(end[i]<=begin[j] || end[j]<=begin[i],"INFO overlapping BO");
    const auto& pending=info.at("pending");const auto& last=info.at("last");
    if(!pending.is_null()){request_fields(pending,context);require(count!=0,"INFO pending index");}
    if(!last.is_null()){native_observation_fields(last,context);require(uint_value(last.at("starts"),UINT64_MAX)<=starts,"INFO future last start");}
    if(state=="IDLE")require(safe && !active && !faulted && pending.is_null(),"INFO IDLE consistency");
    else if(state=="COMPLETED_UNACKED") {
        require(safe && !active && !faulted && !pending.is_null() && !last.is_null() &&
            boolean(info.at("done_latched")),"INFO completed consistency");
        native_fields(last,pending,last.at("Y_sha256"),starts,context);
    } else if(state=="EXECUTING" || state=="RECEIVING") {
        require(!safe && active && !faulted && !pending.is_null(),"INFO active consistency");
        const auto before=uint_value(pending.at("starts_before"),UINT64_MAX);
        require(starts==before+(state=="EXECUTING"?1:0),"INFO active starts");
    } else require(state=="UNKNOWN_OR_QUARANTINED" && !safe && faulted && !pending.is_null(),"INFO unknown consistency");
}
inline void confirmation_fields(const Json& confirmation, const Json& request, const TrustedContext& context) {
    if (confirmation.is_null()) return;
    exact_keys(confirmation, {"schema", "execution_id", "original_client_pid", "original_client_birth_ticks",
        "Y_archive_file", "Y_archive_sha256", "native_archive_file", "native_archive_sha256",
        "job_terminal_file", "job_terminal_sha256", "result_header_sha256"});
    require(confirmation.at("schema") == "kv260_v2b_recovery_ack_v1" &&
        confirmation.at("execution_id") == request.at("execution_id"), "recovery schema/execution mismatch");
    require(uint_value(confirmation.at("original_client_pid"), UINT64_MAX) != 0 &&
        uint_value(confirmation.at("original_client_birth_ticks"), UINT64_MAX) != 0, "recovery client identity");
    const auto root = context.task_root + "/requests/" + request.at("execution_id").get<std::string>() + "/recovery/";
    require(ascii_string(confirmation.at("Y_archive_file"),1,1023) == root+"Y.bin" &&
        ascii_string(confirmation.at("native_archive_file"),1,1023) == root+"native.json" &&
        ascii_string(confirmation.at("job_terminal_file"),1,1023) == root+"terminal.json", "recovery exact paths");
    for (const char* k : {"Y_archive_sha256","native_archive_sha256","job_terminal_sha256","result_header_sha256"})
        sha_string(confirmation.at(k));
    // Actual death, inode/mode/UID/fsync and archive-byte verification belong
    // to the recovery owner, and are not inferred from this valid header.
}
inline std::string message_fields(const Json& header, const TrustedContext& context) {
    trusted_context(context);
    const auto type = envelope(header, context.identity);
    if (type == "INFO" || type == "STOP") return type;
    if (type == "STOP_RESULT") {
        require(boolean(header.at("safe_stop")), "STOP not safe");
        uint_value(header.at("starts"), UINT64_MAX);
        uint_value(header.at("terminal_index_count"), 65536);
        return type;
    }
    if(type=="INFO_RESULT"){info_fields(header.at("info"),context);return type;}
    if (type == "ERROR" && header.at("request").is_null()) {
        require(header.at("state").is_null() && header.at("starts_before").is_null() &&
            header.at("starts_after").is_null(), "untrusted ERROR must not reflect state");
    } else {
        const auto& request = header.at("request");
        const auto bytes = request_fields(request, context);
        const auto before = uint_value(request.at("starts_before"), UINT64_MAX);
        if (type == "REQUEST") require(uint_value(header.at("body_bytes"), UINT32_MAX) == bytes.x, "REQUEST body bytes");
        else if (type == "RESULT") {
            require(header.at("state") == "COMPLETED_UNACKED" &&
                uint_value(header.at("body_bytes"), UINT32_MAX) == bytes.y, "RESULT state/body");
            native_fields(header.at("native"),request,header.at("Y_sha256"),
                uint_value(header.at("starts_after"), UINT64_MAX),context);
        } else if (type == "ACK" || type == "ACK_RESULT") {
            sha_string(header.at("Y_sha256"));
            require(uint_value(header.at("starts_after"), UINT64_MAX) == before+1, "ACK one start");
            if (type == "ACK_RESULT") require(header.at("state") == "ACKED_Y_RELEASED", "ACK_RESULT state");
            confirmation_fields(header.at("confirmation"),request,context);
        } else if (type == "QUERY_RESULT") {
            const auto state = ascii_string(header.at("state"),1,32);
            if (state == "ACKED_Y_RELEASED") native_fields(header.at("native"),request,header.at("Y_sha256"),
                uint_value(header.at("starts_after"),UINT64_MAX),context);
            else {
                require(header.at("Y_sha256").is_null() && header.at("native").is_null(), "QUERY unavailable output");
                if (state == "REJECTED_BEFORE_START") require(uint_value(header.at("starts_after"),UINT64_MAX) == before, "QUERY rejected start");
                else if (state == "FAILED_SAFE") require(uint_value(header.at("starts_after"),UINT64_MAX) == before+1, "QUERY failed one start");
                else require((state == "NOT_FOUND" || state == "EXECUTING" || state == "RECEIVING" || state == "UNKNOWN_OR_QUARANTINED") &&
                    header.at("starts_after").is_null(), "QUERY nullable state");
            }
        } else if (type == "ERROR") {
            require(uint_value(header.at("starts_before"),UINT64_MAX) == before, "ERROR original starts");
            if (header.at("state").is_null()) require(header.at("starts_after").is_null(), "ERROR unknown start");
            else {
                const auto state = ascii_string(header.at("state"),1,32);
                if (state == "REJECTED_BEFORE_START") require(uint_value(header.at("starts_after"),UINT64_MAX) == before, "ERROR rejected start");
                else if (state == "FAILED_SAFE" || state == "COMPLETED_UNACKED" || state == "ACKED_Y_RELEASED")
                    require(uint_value(header.at("starts_after"),UINT64_MAX) == before+1, "ERROR safe start");
                else require((state == "NOT_FOUND" || state == "RECEIVING" || state == "EXECUTING" || state == "UNKNOWN_OR_QUARANTINED") &&
                    header.at("starts_after").is_null(), "ERROR state/start uncertainty");
            }
        }
    }
    if (type == "ERROR") {
        const std::set<std::string> codes = {"BAD_FRAME","BAD_IDENTITY","BAD_REQUEST","BAD_SHAPE","BAD_HASH","SHORT_BODY",
            "DEADLINE","DUPLICATE_ID","ACK_PENDING","ACK_MISMATCH","TERMINAL_LIMIT","OWNER_FAULT","RECOVERY_NOT_PROVEN","INTERNAL"};
        require(codes.count(ascii_string(header.at("error_code"),1,32)) == 1, "ERROR code");
        ascii_string(header.at("error_text"),0,512);
    }
    return type;
}
inline std::string encode_message(const Json& header, const TrustedContext& context) {
    message_fields(header, context);
    const auto encoded = header.dump(-1, ' ', true, Json::error_handler_t::strict);
    lexical_bounds(encoded);
    return encoded;
}
} // namespace kv260_stream_v2b
