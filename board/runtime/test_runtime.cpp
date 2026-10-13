#include "model_gemm_runtime.hpp"
#include <iostream>
int main() {
    using namespace kv5;
    Header h{Op::Run, 0x1020304050607080ULL, 0x11223344u, 48, 192, Status::Ok};
    Wire w = encode(h);
    require(w.size()==32 && w[0]==0x4b && w[1]==0x56 && w[2]==0x35 && w[4]==5, "wire5 prefix");
    require(w[8]==0x80 && w[15]==0x10, "64bit little endian run id");
    Header round=decode(w);
    require(round.op==h.op && round.run==h.run && round.sequence==h.sequence && round.layer==48 && round.bytes==192, "wire roundtrip");
    Shape head=shape(48);
    require(head.m==1 && head.k==192 && head.n==1000 && head.x_bytes()==192 && head.w_bytes()==192000 && head.y_bytes()==4000, "complete head byte sizes");
    unsigned rejected=0;
    try { shape(49); } catch (const std::runtime_error&) { ++rejected; }
    w[4]=4;
    try { decode(w); } catch (const std::runtime_error&) { ++rejected; }
    w=encode(h); w[6]=99;
    try { decode(w); } catch (const std::runtime_error&) { ++rejected; }
    require(rejected==3, "invalid version/op/layer rejection");
    for(unsigned i=0;i<48;++i) {
        Shape s=shape(i);
        require(s.m==197 && s.x_bytes()<=151296 && s.w_bytes()<=192000 && s.y_bytes()<=605184, "registered complete layer");
    }
    std::cout << "PASS_WIRE5_AND_49_LAYER_CREDENTIALS SOFTWARE_ONLY PL_STARTS=0\n";
}
