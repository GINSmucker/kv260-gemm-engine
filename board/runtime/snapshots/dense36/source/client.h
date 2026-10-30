#pragma once
#include "protocol.h"
#include <string>
#include <memory>

namespace kv5 {
// One synchronous connection. A transport failure closes it; RUN is never replayed.
class Client {
    int fd_ = -1;
    struct Mapping;
    std::unique_ptr<Mapping> mapping;
public:
    Client(const std::string& path, const std::array<unsigned char,32>& model);
    ~Client();
    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;
    int8_t* x_data() const;
    int32_t* y_data() const;
    void sync_input(size_t bytes);
    void sync_output(size_t bytes);
    Header call(Header h, const void* input=nullptr, void* output=nullptr, size_t capacity=0);
};
}
