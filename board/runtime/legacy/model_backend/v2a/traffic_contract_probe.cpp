// Exercise the actual compiled semantic predicate against collected public PL records.
#include "../stream_v2a_semantics.h"
#include <fstream>
#include <iostream>
namespace V=kv260_stream_v2a;
int main(int argc,char** argv){
    if(argc!=2)return 2;
    std::ifstream input(argv[1]);V::Json fixtures;input>>fixtures;
    unsigned accepted=0,rejected=0;
    for(const auto& row:fixtures.at("cases")){
        const auto native=row.at("native");V::TrustedContext context;context.identity=row.at("identity");
        V::native_observation_fields(native,context);++accepted;
        auto deny=[&](const V::Json& bad){
            bool refused=false;try{V::native_observation_fields(bad,context);}catch(const std::exception&){refused=true;}
            V::require(refused,"corrupt traffic/native observation accepted");++rejected;
        };
        for(const char* key:{"16","20","24","28","32","36","40","44","48","52","56","60"}){
            auto bad=native;bad["traffic"][key]=native["traffic"][key].get<std::uint32_t>()+1u;deny(bad);
        }
        for(const char* key:{"tag0","tag1"}){auto bad=native;bad[key]=native[key].get<std::uint32_t>()^1u;deny(bad);}
        for(const char* key:{"one_start","done_latched","guards_pass","inputs_unchanged"}){auto bad=native;bad[key]=false;deny(bad);}
        auto wrong_length=native;wrong_length["Y_bytes"]=native["Y_bytes"].get<std::uint32_t>()-4u;deny(wrong_length);
        auto old=native;const auto m=native["M"].get<std::uint32_t>(),k=native["K"].get<std::uint32_t>(),n=native["N"].get<std::uint32_t>();
        for(const char* key:{"32","36"})old["traffic"][key]=m*k*((n+63)/64);
        for(const char* key:{"40","44"})old["traffic"][key]=k*n*((m+7)/8);
        for(const char* key:{"48","52","56"})old["traffic"][key]=m*n;
        deny(old);
    }
    V::require(accepted==6 && rejected==120,"six public observations and all twenty mutations required");
    std::cout<<V::Json{{"status","PASS_ACTUAL_A3_SEMANTICS_NO_XRT"},{"accepted",accepted},{"rejected",rejected},{"CU_BO_or_start",false}}.dump()<<'\n';
}
