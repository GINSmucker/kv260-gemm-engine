#include "diagnostic.hpp"
#include "picosha2.h"
#include "sha256_crypto.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

void need(bool ok,const char* message) {if(!ok)throw std::runtime_error(message);}
template<class F> void refused(F action) {
    bool caught=false;
    try{action();}catch(const std::exception&){caught=true;}
    need(caught,"negative case accepted");
}

#ifdef OPT50_TEST_SHA_FAILURE
extern "C" unsigned char* SHA256(const unsigned char*,size_t,unsigned char*) {return nullptr;}
#endif

int main(int argc,char** argv) {
    try {
#ifdef OPT50_TEST_SHA_FAILURE
        (void)argc;(void)argv;
        refused([]{kv260_crypto_sha256("abc",3);});
        std::cout<<"SHA_FAILURE_PROPAGATED_PASS_NO_HARDWARE_ACCESS\n";
#else
        need(argc==3,"two actual packed-weight files required");
        need(kv260_crypto_sha256("abc",3)=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","SHA abc known answer");
        for(size_t count:{0u,1u,55u,56u,63u,64u,65u,257u}) {
            std::vector<unsigned char> raw(count+128,0xa5);
            for(size_t i=0;i<count;++i)raw[64+i]=static_cast<unsigned char>(i*37);
            std::vector<unsigned char> expected(raw.begin()+64,raw.begin()+64+count);
            need(kv260_crypto_sha256(raw.data()+64,count)==picosha2::hash256_hex_string(expected),"guarded range SHA mismatch");
        }
        for(int file=1;file<argc;++file) {
            std::ifstream stream(argv[file],std::ios::binary);need(bool(stream),"packed file open");
            const std::vector<unsigned char> expected((std::istreambuf_iterator<char>(stream)),{});
            need(!expected.empty(),"real packed weights empty");
            const auto wanted=picosha2::hash256_hex_string(expected);
            std::vector<unsigned char> guarded(expected.size()+128,0x5a);
            std::copy(expected.begin(),expected.end(),guarded.begin()+64);
            need(kv260_crypto_sha256(guarded.data()+64,expected.size())==wanted,"real packed payload SHA mismatch");
            guarded[64+expected.size()/2]^=1;
            need(kv260_crypto_sha256(guarded.data()+64,expected.size())!=wanted,"weight corruption undetected");
        }
        opt50_diagnostic::Linear linear;
        linear.record(0,{1,2,3,4,5,6,7});
        refused([&]{linear.record(0,{1,2,3,4,5,6,7});});
        refused([&]{linear.record(49,{1,2,3,4,5,6,7});});
        refused([&]{linear.record(1,{1,2,3,2,5,6,7});});
        linear.reset();need(linear.stamps[0]==0,"linear reset");
        opt50_diagnostic::Control control;
        control.mark(0,0);refused([&]{control.mark(0,0);});
        refused([&]{control.json();});
        for(size_t point=1;point<5;++point)control.mark(0,point);
        control.pause(0);need(control.json().find("\"layer\":0")!=std::string::npos,"control JSON");
        control.reset();need(control.json()=="[]","control reset");
        std::cout<<"HOST_SHA_ACTUAL_WEIGHTS_AND_DIAGNOSTIC_BOUNDARIES_PASS_NO_HARDWARE_ACCESS\n";
#endif
        return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
