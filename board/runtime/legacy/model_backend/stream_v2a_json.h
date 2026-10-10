// Shared bounded JSON primitives for the proposed V2A bridge/owner.
// No socket, file publication, execution, BO or hardware ownership lives here.
#pragma once
#include "vendor/json.hpp"
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>

namespace kv260_stream_v2a {
using Json = nlohmann::json;
constexpr std::size_t HEADER_LIMIT = 16384;
constexpr unsigned DEPTH_LIMIT = 4;
constexpr const char* SCHEMA = "kv260_gemm_stream_v2a";
constexpr std::uint32_t WIRE_VERSION = 2;

inline void require(bool ok, const char* error) {
    if (!ok) throw std::runtime_error(error);
}

inline std::string ascii_error(const std::string& message) {
    std::string result;
    for (unsigned char c : message) {
        if (result.size() == 512) break;
        result.push_back(c >= 0x20 && c <= 0x7e ? static_cast<char>(c) : '?');
    }
    return result;
}

inline void exact_keys(const Json& value, std::initializer_list<const char*> keys) {
    require(value.is_object() && value.size() == keys.size(), "JSON object key count/type");
    for (const char* key : keys) require(value.contains(key), "JSON missing/unknown key");
}

inline std::uint64_t uint_value(const Json& value, std::uint64_t maximum) {
    require(value.is_number_integer() && !value.is_boolean(), "integer token required");
    if (!value.is_number_unsigned()) require(value.get<std::int64_t>() >= 0, "negative integer");
    const auto result = value.get<std::uint64_t>();
    require(result <= maximum, "integer range");
    return result;
}

inline double finite_nonnegative(const Json& value) {
    require(value.is_number() && !value.is_boolean(), "number token required");
    const double result = value.get<double>();
    require(std::isfinite(result) && result >= 0.0, "nonfinite/negative number");
    return result;
}

inline std::string ascii_string(const Json& value, std::size_t minimum, std::size_t maximum) {
    require(value.is_string(), "string required");
    const std::string result = value.get<std::string>();
    require(result.size() >= minimum && result.size() <= maximum, "string length");
    for (unsigned char c : result) require(c >= 0x20 && c <= 0x7e, "nonprintable/nonASCII string");
    return result;
}

inline bool lower_hex(char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }
inline void sha_string(const Json& value) {
    const auto text = ascii_string(value, 64, 64);
    for (char c : text) require(lower_hex(c), "SHA lowercase hex");
}
inline void uuid_string(const Json& value) {
    const auto text = ascii_string(value, 36, 36);
    for (std::size_t i = 0; i < text.size(); ++i) {
        const bool dash = i == 8 || i == 13 || i == 18 || i == 23;
        require(dash ? text[i] == '-' : lower_hex(text[i]), "UUID format");
    }
}
inline std::string id_string(const Json& value, std::size_t maximum) {
    const auto text = ascii_string(value, 1, maximum);
    require(text != "." && text != "..", "dot path ID");
    for (char c : text) require((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
        (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.', "ID character");
    return text;
}

// Check allocation-independent limits before the general JSON parser runs.
inline void lexical_bounds(const std::string& bytes) {
    require(!bytes.empty() && bytes.size() <= HEADER_LIMIT, "header byte limit");
    require(bytes.size() < 3 || bytes.compare(0, 3, "\xef\xbb\xbf") != 0, "UTF8 BOM forbidden");
    bool quoted = false, escape = false;
    unsigned depth = 0;
    for (std::size_t i = 0; i < bytes.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(bytes[i]);
        require(c != 0, "NUL forbidden");
        if (quoted) {
            if (escape) escape = false;
            else if (c == '\\') escape = true;
            else if (c == '"') quoted = false;
            continue;
        }
        if (c == '"') { quoted = true; continue; }
        if (c == '{' || c == '[') require(++depth <= DEPTH_LIMIT, "JSON nesting limit");
        else if (c == '}' || c == ']') { require(depth > 0, "JSON unbalanced close"); --depth; }
        else if (c == '-' || (c >= '0' && c <= '9')) {
            const auto begin = i;
            while (i + 1 < bytes.size()) {
                const char n = bytes[i + 1];
                if (!((n >= '0' && n <= '9') || n == '.' || n == '-' || n == '+' || n == 'e' || n == 'E')) break;
                ++i;
            }
            require(i - begin + 1 <= 32, "numeric token limit");
        }
    }
    require(!quoted && !escape && depth == 0, "JSON unfinished string/container");
}

inline void reject_decoded_nul(const Json& value) {
    if (value.is_string()) require(value.get<std::string>().find('\0') == std::string::npos, "decoded NUL forbidden");
    else if (value.is_object()) {
        for (auto i = value.begin(); i != value.end(); ++i) {
            require(i.key().find('\0') == std::string::npos, "decoded key NUL forbidden");
            reject_decoded_nul(i.value());
        }
    } else if (value.is_array()) for (const auto& entry : value) reject_decoded_nul(entry);
}

inline Json parse_header(const std::string& bytes) {
    lexical_bounds(bytes);
    std::map<int, std::set<std::string>> keys;
    const auto callback = [&keys](int depth, Json::parse_event_t event, Json& parsed) {
        if (event == Json::parse_event_t::object_start) keys[depth + 1].clear();
        else if (event == Json::parse_event_t::key)
            require(keys[depth].insert(parsed.get<std::string>()).second, "duplicate JSON key");
        else if (event == Json::parse_event_t::object_end) keys.erase(depth + 1);
        return true;
    };
    Json value = Json::parse(bytes, callback); // Rejects invalid UTF8, syntax and trailing data.
    require(value.is_object(), "header root object required");
    reject_decoded_nul(value);
    return value;
}

inline void identity_fields(const Json& value) {
    exact_keys(value, {"candidate_sha256", "ready_sha256", "bridge_sha256", "owner_sha256",
        "driver_sha256", "driver_srcversion", "platform_manifest_sha256", "A2_gate_sha256",
        "B_manifest_sha256", "B_tools_wheel_sha256", "boot_id", "window_id", "owner_pid",
        "owner_birth_ticks", "uuid", "tag0", "tag1"});
    for (const char* key : {"candidate_sha256", "ready_sha256", "bridge_sha256", "owner_sha256",
        "driver_sha256", "platform_manifest_sha256", "A2_gate_sha256", "B_manifest_sha256", "B_tools_wheel_sha256"}) sha_string(value.at(key));
    // The frozen actual module reports 23 hex characters. Bound at 24, then
    // compare exact bytes against the trusted configuration in envelope().
    const auto src = ascii_string(value.at("driver_srcversion"), 1, 24);
    for (char c : src) require(lower_hex(c) || (c >= 'A' && c <= 'F'), "driver srcversion hex");
    uuid_string(value.at("boot_id")); uuid_string(value.at("uuid")); id_string(value.at("window_id"), 95);
    require(uint_value(value.at("owner_pid"), UINT64_MAX) != 0, "zero owner PID");
    require(uint_value(value.at("owner_birth_ticks"), UINT64_MAX) != 0, "zero owner birth");
    uint_value(value.at("tag0"), UINT32_MAX); uint_value(value.at("tag1"), UINT32_MAX);
}

// Structural envelope only. Callers must additionally validate request/state/
// native fields, trusted peer/config identity, full body, hash and lifecycle.
inline std::string envelope(const Json& value, const Json& trusted_identity) {
    require(value.is_object() && value.contains("type"), "message type required");
    const auto type = ascii_string(value.at("type"), 1, 32);
    std::set<std::string> expected = {"schema", "wire_version", "type", "identity", "body_bytes"};
    const std::map<std::string, std::set<std::string>> extra = {
        {"INFO", {}}, {"INFO_RESULT", {"info"}}, {"REQUEST", {"request"}},
        {"RESULT", {"request", "state", "Y_sha256", "starts_after", "native"}},
        {"QUERY", {"request"}}, {"QUERY_RESULT", {"request", "state", "starts_after", "Y_sha256", "native"}},
        {"ACK", {"request", "Y_sha256", "starts_after", "confirmation"}},
        {"ACK_RESULT", {"request", "state", "Y_sha256", "starts_after", "confirmation"}},
        {"STOP", {}}, {"STOP_RESULT", {"safe_stop", "starts", "terminal_index_count"}},
        {"ERROR", {"error_code", "error_text", "request", "state", "starts_before", "starts_after"}}};
    const auto found = extra.find(type); require(found != extra.end(), "unknown message type");
    expected.insert(found->second.begin(), found->second.end());
    require(value.size() == expected.size(), "envelope missing/unknown key");
    for (const auto& key : expected) require(value.contains(key), "envelope key mismatch");
    require(value.at("schema") == SCHEMA, "old/wrong schema");
    require(uint_value(value.at("wire_version"), UINT32_MAX) == WIRE_VERSION, "old/wrong wire version");
    identity_fields(value.at("identity")); identity_fields(trusted_identity);
    require(value.at("identity") == trusted_identity, "trusted identity mismatch");
    const auto length = uint_value(value.at("body_bytes"), UINT32_MAX);
    require((type == "REQUEST" || type == "RESULT") ? length != 0 : length == 0, "envelope body length");
    return type;
}
} // namespace kv260_stream_v2a
