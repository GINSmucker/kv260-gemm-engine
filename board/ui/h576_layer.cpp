#include "client.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <vector>

int main(int argc,char** argv) {
    try {
        kv5::require(argc==7,"socket, model digest, run id, target, X path, Y path required");
        std::array<unsigned char,32> digest{};
        const std::string text=argv[2];
        kv5::require(text.size()==64,"model digest length");
        for(unsigned i=0;i<32;++i)digest[i]=static_cast<unsigned char>(std::stoul(text.substr(2*i,2),nullptr,16));
        const uint64_t run=std::stoull(argv[3]);
        const unsigned target=std::stoul(argv[4]);
        kv5::require(target<48 && target%4!=1,"target has fixed CPU route");
        const auto shape=kv5::shape(target);
        std::ifstream input(argv[5],std::ios::binary|std::ios::ate);
        kv5::require(bool(input) && size_t(input.tellg())==shape.x_bytes(),"exact target X length");
        std::vector<int8_t> x(shape.x_bytes());
        input.seekg(0);input.read(reinterpret_cast<char*>(x.data()),x.size());
        kv5::Client client(argv[1],digest);
        auto call=[&](kv5::Op op,unsigned sequence=0,unsigned layer=0){
            kv5::require(client.call({op,run,sequence,layer,0,kv5::Status::Ok}).status==kv5::Status::Ok,"owner operation rejected; no retry");
        };
        call(kv5::Op::Begin);
        unsigned count=0;
        const auto start=std::chrono::steady_clock::now();
        for(unsigned layer=0;layer<=target;++layer) {
            if(layer%4==1)continue;
            const auto s=kv5::shape(layer);
            std::fill_n(client.x_data(),s.x_bytes(),int8_t{0});
            if(layer==target)std::copy(x.begin(),x.end(),client.x_data());
            client.sync_input(s.x_bytes());
            call(kv5::Op::Run,layer+1,layer);
            client.sync_output(s.y_bytes());
            ++count;
        }
        std::ofstream output(argv[6],std::ios::binary);
        output.write(reinterpret_cast<const char*>(client.y_data()),shape.y_bytes());
        kv5::require(bool(output),"Y write failed");
        call(kv5::Op::Abort);
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();
        std::cout<<"{\"status\":\"PASS\",\"PL_starts\":"<<count<<",\"target_PL_starts\":1,\"prefix_ms\":"<<double(ns)/1e6<<"}\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<error.what()<<'\n';return 1;
    }
}
