#pragma once
#include "protocol.h"
#include <string>

namespace kv5 {
// One synchronous connection. A transport failure closes it; RUN is never replayed.
class Client {
    int fd_ = -1;
public:
    Client(const std::string& path, const std::array<unsigned char,32>& model);
    ~Client();
    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;
    Header call(Header h, const void* input=nullptr, void* output=nullptr, size_t capacity=0);
};
}
