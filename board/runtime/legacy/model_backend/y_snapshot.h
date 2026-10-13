#ifndef KV260_Y_SNAPSHOT_H
#define KV260_Y_SNAPSHOT_H
#include <cstddef>
#include <cstring>
#include <vector>

// The owner calls this only after Done/idle/drain, full sync, guards and const.
// This owns exactly the valid output bytes and never borrows the BO mapping.
inline std::vector<unsigned char> kv260_y_snapshot(const unsigned char* mapped,
                                                  std::size_t bytes) {
    std::vector<unsigned char> output(bytes);
    if (bytes) std::memcpy(output.data(), mapped, bytes);
    return output;
}
#endif
