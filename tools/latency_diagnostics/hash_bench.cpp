#include "picosha2.h"
#include "sha256_crypto.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

int main(int argc,char** argv) {
    if(argc!=3)return 2;
    for(int i=1;i<argc;++i) {
        std::ifstream input(argv[i],std::ios::binary);
        if(!input)return 3;
        std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)),{});
        const auto expected=picosha2::hash256_hex_string(bytes);
        for(unsigned algorithm=0;algorithm<3;++algorithm) {
            for(unsigned repeat=0;repeat<5;++repeat) {
                const auto began=std::chrono::steady_clock::now();
                std::string actual;
                if(algorithm==0){auto copy=bytes;actual=picosha2::hash256_hex_string(copy);}
                if(algorithm==1){auto copy=bytes;actual=kv260_crypto_sha256(copy.data(),copy.size());}
                if(algorithm==2){actual=kv260_crypto_sha256(bytes.data(),bytes.size());}
                const double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-began).count();
                if(actual!=expected)return 4;
                std::cout<<"{\"file\":\""<<argv[i]<<"\",\"bytes\":"<<bytes.size()
                         <<",\"algorithm\":"<<algorithm<<",\"repeat\":"<<repeat<<",\"ms\":"<<ms
                         <<",\"sha256\":\""<<actual<<"\",\"match\":true}\n";
            }
        }
    }
}
