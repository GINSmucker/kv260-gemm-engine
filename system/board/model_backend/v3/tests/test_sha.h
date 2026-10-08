// Windows public software tests use the OS SHA-256 provider; ARM uses OpenSSL.
#pragma once
#ifdef _WIN32
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0a00
#endif
#include <windows.h>
#include <bcrypt.h>
#include <limits>
extern "C" inline unsigned char* SHA256(const unsigned char* input,size_t n,unsigned char* output) {
    if(n>std::numeric_limits<ULONG>::max())return nullptr;
    BCRYPT_ALG_HANDLE algorithm=nullptr;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)return nullptr;
    const auto status=BCryptHash(algorithm,nullptr,0,const_cast<PUCHAR>(input),static_cast<ULONG>(n),output,32);
    BCryptCloseAlgorithmProvider(algorithm,0);return status<0?nullptr:output;
}
#endif
#ifndef KV260_V3_SHA_PROVIDER_ONLY
#include "../../sha256_crypto.h"
#endif
