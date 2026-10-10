// Actual four-symbol shared library checks with no open owner, XRT or BO.
#include "gemm_backend_v2b.h"
#include "../vendor/json.hpp"
#include <dlfcn.h>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>
static void need(bool b,const char* s){if(!b)throw std::runtime_error(s);}
int main(int argc,char** argv){try{
    need(argc==2,"actual library argument");void* library=::dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);need(library!=nullptr,"actual shared library load");
    auto open=reinterpret_cast<decltype(&kv260_gemm_open_v2b)>(::dlsym(library,"kv260_gemm_open_v2b"));
    auto run=reinterpret_cast<decltype(&kv260_gemm_run_v2b)>(::dlsym(library,"kv260_gemm_run_v2b"));
    auto info=reinterpret_cast<decltype(&kv260_gemm_info_v2b)>(::dlsym(library,"kv260_gemm_info_v2b"));
    auto close=reinterpret_cast<decltype(&kv260_gemm_close_v2b)>(::dlsym(library,"kv260_gemm_close_v2b"));
    need(open && run && info && close,"all four independent v2b exports");
    for(const char* name:{"kv260_gemm_open_v1","kv260_gemm_run_v1","kv260_gemm_info_v1","kv260_gemm_close_v1"})need(::dlsym(library,name)==nullptr,"old v1 symbol must be absent");
    nlohmann::json results=nlohmann::json::array();char actual[8192]{};void* handle=nullptr;
    auto record=[&](const char* name,int got,int expected){need(got==expected,"actual ABI return differs from expectation");results.push_back({{"name",name},{"returncode",got},{"expected",expected}});};
    record("open_null_config",open(nullptr,&handle,actual,sizeof(actual)),B2_BAD_INPUT);need(handle==nullptr,"failed open no handle");
    record("open_null_handle",open("{}",nullptr,actual,sizeof(actual)),B2_BAD_INPUT);
    record("open_empty_config",open("",&handle,actual,sizeof(actual)),B2_BAD_INPUT);
    record("info_unknown_handle",info(nullptr,actual,sizeof(actual)),B2_NOT_READY);
    record("close_unknown_handle",close(nullptr),B2_NOT_READY);
    std::vector<int8_t> x(192),w(192000);std::vector<int32_t> y(1000);kv260_b_request_v2b r{};kv260_b_receipt_v2b receipt{};
    r.abi_version=3;r.struct_bytes=sizeof(r);r.M=1;r.K=192;r.N=1000;r.layer_index=48;
    receipt.abi_version=3;receipt.struct_bytes=sizeof(receipt);
    record("run_null_request",run(nullptr,nullptr,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_INPUT);
    record("run_null_receipt",run(nullptr,&r,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,nullptr),B2_BAD_INPUT);
    r.abi_version=1;record("run_v1_request",run(nullptr,&r,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_INPUT);r.abi_version=3;
    receipt.abi_version=1;record("run_v1_receipt",run(nullptr,&r,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_INPUT);receipt.abi_version=3;
    ++r.struct_bytes;record("run_wrong_struct_size",run(nullptr,&r,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_INPUT);--r.struct_bytes;
    r.M=2;record("run_bad_full_shape",run(nullptr,&r,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_SHAPE);r.M=1;
    r.layer_index=49;record("run_bad_layer_index",run(nullptr,&r,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_SHAPE);r.layer_index=48;
    record("run_wrong_X_bytes",run(nullptr,&r,x.data(),x.size()-1,w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_INPUT);
    record("run_null_X",run(nullptr,&r,nullptr,x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_INPUT);
    record("run_overlapping_input",run(nullptr,&r,x.data(),x.size(),x.data(),w.size(),y.data(),y.size()*4,&receipt),B2_BAD_INPUT);
    record("run_no_attached_owner",run(nullptr,&r,x.data(),x.size(),w.data(),w.size(),y.data(),y.size()*4,&receipt),B2_NOT_READY);
    need(::dlclose(library)==0,"actual shared library detach");
    std::cout<<nlohmann::json{{"status","PASS_ACTUAL_V2B_EXPORTS_NO_OWNER"},{"cases",results.size()},{"rows",results},
        {"request_bytes",sizeof(r)},{"receipt_bytes",sizeof(receipt)},{"actual_opened_owner",false},{"CU_BO_or_start",false}}.dump()<<'\n';return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
