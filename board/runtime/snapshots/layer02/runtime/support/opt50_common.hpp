#pragma once
#include "picosha2.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <map>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace opt50 {
using Bytes = std::vector<unsigned char>;
using Fields = std::map<std::string, std::string>;
inline constexpr const char* format_id = "H576_SPARSE24_SPLIT128_V1";
inline constexpr const char* abi_id = "OPT50_SPLIT128_AP_CTRL_HS_V1";
inline constexpr const char* package_sha = "c0f35179ea2edd7fe848cc5643232da86b795b92a18fa27624f5770cad02f769";
inline constexpr const char* register_header_sha = "fc243d24a6e87de438fa0db99e9ade55a3c1467b9ed9346a58eb34c95cf91c73";
namespace reg {
inline constexpr uint32_t control=0x00, gie=0x04, ier=0x08, result=0x10;
inline constexpr uint32_t x=0x18, w_data=0x24, w_meta=0x30, y=0x3c;
inline constexpr uint32_t m=0x48, k=0x50, n=0x58, mode=0x60;
}
inline void require(bool ok, const std::string& why) {
    if (!ok) throw std::runtime_error(why);
}
inline uint64_t number(const std::string& text) {
    require(std::regex_match(text,std::regex("(0|[1-9][0-9]*|0[xX][0-9a-fA-F]+)")), "invalid unsigned integer");
    size_t used=0;
    uint64_t value=std::stoull(text,&used,0);
    require(used==text.size(), "trailing integer characters");
    return value;
}
inline std::string field(const Fields& f, const std::string& key) {
    auto it=f.find(key); require(it!=f.end() && !it->second.empty(), "missing field: "+key);
    return it->second;
}
inline bool hash_text(const std::string& text) {
    return std::regex_match(text,std::regex("[0-9a-f]{64}"));
}
inline Bytes read_file(const std::string& path, size_t max_bytes=2*1024*1024) {
    std::ifstream f(std::filesystem::u8path(path),std::ios::binary|std::ios::ate);
    require(bool(f), "cannot open: "+path);
    auto end=f.tellg();
    require(end>=0 && uint64_t(end)<=max_bytes, "file length exceeds limit: "+path);
    Bytes b(static_cast<size_t>(end)); f.seekg(0);
    if (!b.empty()) f.read(reinterpret_cast<char*>(b.data()),std::streamsize(b.size()));
    require(bool(f), "short read: "+path); return b;
}
inline std::string sha256(const Bytes& b) { return picosha2::hash256_hex_string(b); }
inline Fields read_fields(const std::string& path) {
    auto bytes=read_file(path,65536);
    require(std::find(bytes.begin(),bytes.end(),0)==bytes.end(), "NUL in configuration");
    std::istringstream input(std::string(bytes.begin(),bytes.end()));
    Fields result; std::string line;
    while (std::getline(input,line)) {
        if (!line.empty() && line.back()=='\r') line.pop_back();
        if (line.empty() || line[0]=='#') continue;
        auto at=line.find('=');
        require(at!=std::string::npos && at>0, "invalid key=value line");
        auto key=line.substr(0,at), value=line.substr(at+1);
        require(std::regex_match(key,std::regex("[A-Za-z_][A-Za-z_0-9]*")), "invalid key");
        require(result.emplace(key,value).second,"duplicate key: "+key);
    }
    return result;
}
struct Shape {
    uint32_t m,k,n,mode;
    std::array<size_t,4> sizes() const {
        size_t weights=size_t(k)*n;
        return {size_t(m)*k, mode?weights/2:weights, mode?weights/8:16, size_t(m)*n*4};
    }
};
inline Shape shape(const Fields& f) {
    auto m=number(field(f,"M")), k=number(field(f,"K"));
    auto n=number(field(f,"N")), mode=number(field(f,"mode"));
    require(m>=1 && m<=197 && k>=1 && k<=576 && n>=1 && n<=576,"unsupported shape");
    require((k%16)==0 && (n%16)==0 && k*n<=110592 && mode<=1,"unsupported shape/mode");
    return {uint32_t(m),uint32_t(k),uint32_t(n),uint32_t(mode)};
}
inline void validate_payload(const Shape& s, const std::array<Bytes,4>& data) {
    auto sizes=s.sizes();
    for(size_t i=0;i<4;++i) require(data[i].size()==sizes[i],"incorrect buffer length");
    if(s.mode==0) {
        require(std::all_of(data[2].begin(),data[2].end(),[](unsigned char c){return c==0;}),"Dense metadata must be zero");
    } else {
        for(auto byte:data[2]) for(unsigned shift:{0u,4u}) {
            unsigned code=(byte>>shift)&15;
            require((code&3)<(code>>2),"invalid sparse position code");
        }
    }
}
struct Job { Fields fields; Shape dimensions; std::array<Bytes,4> data; };
// Only the exact B24 layer02 26-field loader in the companion pure header is
// exposed. The copied historical generic 23-field job entry point is omitted.
inline int signed8(unsigned char b) { return b<128?int(b):int(b)-256; }
inline int32_t signed32(const unsigned char* b) {
    uint32_t u=uint32_t(b[0])|(uint32_t(b[1])<<8)|(uint32_t(b[2])<<16)|(uint32_t(b[3])<<24);
    return int32_t(u<0x80000000u?int64_t(u):int64_t(u)-0x100000000LL);
}
inline Bytes cpu_reference(const Job& job) {
    auto s=job.dimensions; Bytes w(size_t(s.k)*s.n,0);
    if(!s.mode) w=job.data[1];
    else for(size_t t=0;t<size_t(s.k/4)*s.n;++t) {
        auto code=(job.data[2][t/2]>>(4*(t%2)))&15;
        w[(4*(t/s.n)+(code&3))*s.n+t%s.n]=job.data[1][2*t];
        w[(4*(t/s.n)+(code>>2))*s.n+t%s.n]=job.data[1][2*t+1];
    }
    Bytes y(s.sizes()[3]);
    for(size_t m=0;m<s.m;++m) {
        std::vector<int64_t> row(s.n,0);
        for(size_t k=0;k<s.k;++k) {
            int x=signed8(job.data[0][m*s.k+k]);
            for(size_t n=0;n<s.n;++n) row[n]+=int64_t(x)*signed8(w[k*s.n+n]);
        }
        for(size_t n=0;n<s.n;++n) {
            require(row[n]>=INT32_MIN && row[n]<=INT32_MAX,"CPU reference INT32 overflow");
            uint32_t value=uint32_t(int32_t(row[n]));
            for(unsigned lane=0;lane<4;++lane) y[4*(m*s.n+n)+lane]=static_cast<unsigned char>(value>>(8*lane));
        }
    }
    return y;
}
// Bounds copied from C's accepted KV260 DDR policy; not universal AXI limits.
inline bool ddr_range(uint64_t address,size_t bytes) {
    if(!bytes || (address%64)!=0) return false;
    auto fits=[&](uint64_t low,uint64_t high){return address>=low && address<high && bytes<=high-address;};
    return fits(64,0x80000000ULL)||fits(0x800000000ULL,0x880000000ULL);
}
inline bool drained(uint32_t r,uint32_t w,uint32_t response,uint32_t quiet) {
    return r==0 && w==0 && response==0 && quiet==1;
}
inline bool completion(bool seen_done,uint32_t control,bool bus_drained) {
    return seen_done && (control&4) && !(control&0x81) && bus_drained;
}
inline void self_test() {
    require(sha256(Bytes{'a','b','c'})=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","SHA256 known vector");
    require(signed8(128)==-128 && signed8(255)==-1,"signed INT8");
    Bytes b={0xfd,0xff,0xff,0xff}; require(signed32(b.data())==-3,"little endian INT32");
    require(ddr_range(64,16) && !ddr_range(65,16) && !ddr_range(0x7fffffc0,128),"DDR bounds");
    require(completion(true,4,true) && !completion(false,4,true) && !completion(true,0x84,true) && !completion(true,4,false),"completion and DMA drain");
    Job sparse{{},{2,16,16,1},{}};
    auto counts=sparse.dimensions.sizes();
    for(size_t i=0;i<4;++i) sparse.data[i].resize(counts[i]);
    for(size_t i=0;i<sparse.data[0].size();++i) sparse.data[0][i]=static_cast<unsigned char>(i*17);
    constexpr unsigned codes[]={4,8,12,9,13,14};
    for(size_t t=0;t<64;++t) {
        sparse.data[1][2*t]=static_cast<unsigned char>(t==0?128:t*5);
        sparse.data[1][2*t+1]=static_cast<unsigned char>(t==1?0:t*11);
        sparse.data[2][t/2]|=static_cast<unsigned char>(codes[t%6]<<(4*(t%2)));
    }
    validate_payload(sparse.dimensions,sparse.data);
    Job dense{{},{2,16,16,0},{}};
    counts=dense.dimensions.sizes();
    for(size_t i=0;i<4;++i) dense.data[i].resize(counts[i]);
    dense.data[0]=sparse.data[0];
    for(size_t t=0;t<64;++t) {
        auto code=codes[t%6];
        dense.data[1][(4*(t/16)+(code&3))*16+t%16]=sparse.data[1][2*t];
        dense.data[1][(4*(t/16)+(code>>2))*16+t%16]=sparse.data[1][2*t+1];
    }
    require(cpu_reference(sparse)==cpu_reference(dense),"six sparse codes, retained zero, signed Dense/Sparse equivalence");
    sparse.data[2][0]=0;
    bool rejected=false;
    try {validate_payload(sparse.dimensions,sparse.data);} catch(const std::runtime_error&) {rejected=true;}
    require(rejected,"invalid metadata must be rejected");
}
} // namespace opt50
