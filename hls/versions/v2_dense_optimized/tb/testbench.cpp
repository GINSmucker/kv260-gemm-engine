#include "../src/model_gemm_core.hpp"
#include "public_cases.hpp"
#include <cstdio>
#include <fstream>
#include <algorithm>

namespace {
const unsigned GUARD=64;
const int8_t INPUT_GUARD=-91;
const int32_t OUTPUT_GUARD=0x5a5a5a5a;
using model_gemm::wide_t;
const wide_t WIDE_GUARD=(wide_t(OUTPUT_GUARD)<<96)|(wide_t(OUTPUT_GUARD)<<64)|(wide_t(OUTPUT_GUARD)<<32)|wide_t(OUTPUT_GUARD);
unsigned passed=0, rejected=0;
uint64_t outputs=0;
const uint32_t PACK_GUARD=0xa5a5a5a;
// TB pack is independent of DUT lane extraction and bank helpers.
void pack(const int8_t* src, unsigned bytes, uint32_t* dst) {
    for(unsigned j=0;j<bytes/4;++j) {
        uint32_t value=0;
        for(unsigned lane=0;lane<4;++lane)
            value|=static_cast<uint32_t>(static_cast<uint8_t>(src[j*4+lane]))<<(8*lane);
        dst[j]=value;
    }
}
void pack_wide(const int8_t* src,unsigned bytes,wide_t* dst) {
    for(unsigned word=0;word<bytes/16;++word) {
        wide_t value=0;
        for(unsigned lane=0;lane<16;++lane)
            value.range(lane*8+7,lane*8)=static_cast<uint8_t>(src[word*16+lane]);
        dst[word]=value;
    }
}
int32_t unpack_y(const wide_t* src,unsigned index) {
    const int64_t value=src[index/4].range(32*(index%4)+31,32*(index%4)).to_uint();
    return static_cast<int32_t>(value>=2147483648LL ? value-4294967296LL : value);
}
void save_input(const std::string& path,const int8_t* data,unsigned count) {
    std::ofstream out(path.c_str(),std::ios::binary);
    out.write(reinterpret_cast<const char*>(data),count);
}

bool run_case(unsigned shape, unsigned test, std::ofstream& rows, int x_test=-1, int w_test=-1) {
    const model_gemm::Shape& s=model_gemm::SHAPES[shape];
    // HLS co-sim serializes declared maximum depth, so allocate that whole capacity.
    // Guard checks do not purport to prove absence of out-of-range reads.
    static std::vector<int8_t> x(model_gemm::MAX_X+2*GUARD,INPUT_GUARD);
    static std::vector<int8_t> w(model_gemm::MAX_W+2*GUARD,INPUT_GUARD);
    static std::vector<wide_t> y(model_gemm::MAX_Y/4+2*GUARD,WIDE_GUARD);
    std::fill(x.begin(),x.end(),INPUT_GUARD);std::fill(w.begin(),w.end(),INPUT_GUARD);
    std::fill(y.begin(),y.end(),WIDE_GUARD);
    int8_t* xp=x.data()+GUARD; int8_t* wp=w.data()+GUARD;
    wide_t* yp=y.data()+GUARD;
    public_cases::fill(s,test,xp,wp);
    if(x_test<0) x_test=test;
    if(w_test<0) w_test=test;
    std::vector<int8_t> other_x(s.M*s.K),other_w(s.K*s.N);
    if(static_cast<unsigned>(x_test)!=test) {
        public_cases::fill(s,x_test,other_x.data(),other_w.data());
        std::copy(other_x.begin(),other_x.end(),xp);
    }
    if(static_cast<unsigned>(w_test)!=test) {
        public_cases::fill(s,w_test,other_x.data(),other_w.data());
        std::copy(other_w.begin(),other_w.end(),wp);
    }
    const std::vector<int8_t> oldx=x, oldw=w;
    const std::vector<int32_t> expected=public_cases::golden(s,xp,wp);
    static std::vector<wide_t> px(model_gemm::MAX_X/16+2*GUARD,wide_t(PACK_GUARD));
    static std::vector<wide_t> pw(model_gemm::MAX_W/16+2*GUARD,wide_t(PACK_GUARD));
    std::fill(px.begin(),px.end(),wide_t(PACK_GUARD));std::fill(pw.begin(),pw.end(),wide_t(PACK_GUARD));
    pack_wide(xp,s.M*s.K,px.data()+GUARD); pack_wide(wp,s.K*s.N,pw.data()+GUARD);
    const std::vector<wide_t> oldpx=px;
    const std::vector<wide_t> oldpw=pw;
    const std::string stem=std::string(s.family)+"_X"+std::to_string(x_test)+"_W"+std::to_string(w_test);
    save_input(stem+"_X.bin",xp,s.M*s.K); save_input(stem+"_W.bin",wp,s.K*s.N);
    const uint32_t error=model_gemm_top(px.data()+GUARD,pw.data()+GUARD,yp,s.M,s.K,s.N);
    bool ok=error==model_gemm::OK && x==oldx && w==oldw && px==oldpx && pw==oldpw;
    for(unsigned i=0;i<s.M*s.N;++i) {
        if(unpack_y(yp,i)!=expected[i]) {
            if(ok) std::printf("MISMATCH %s case=%u output=%u got=%d expected=%d\n",s.family,test,i,unpack_y(yp,i),expected[i]);
            ok=false;
        }
    }
    for(unsigned i=0;i<GUARD;++i) if(y[i]!=WIDE_GUARD) ok=false;
    for(unsigned i=GUARD+s.M*s.N/4;i<y.size();++i) if(y[i]!=WIDE_GUARD) ok=false;
    rows<<s.family<<','<<test<<','<<s.M<<','<<s.K<<','<<s.N<<','<<s.M*s.K<<','<<s.K*s.N<<','<<s.M*s.N*4<<','<<error<<','<<(ok?"PASS":"FAIL")<<','<<x_test<<','<<w_test<<','<<static_cast<const void*>(px.data()+GUARD)<<','<<static_cast<const void*>(pw.data()+GUARD)<<','<<static_cast<const void*>(yp)<<'\n';
    rows.flush();
    std::printf("CASE %s %02u M=%u K=%u N=%u outputs=%u %s\n",s.family,test,s.M,s.K,s.N,s.M*s.N,ok?"PASS":"FAIL");
    if(ok) {++passed;outputs+=s.M*s.N;}
    return ok;
}

bool invalid_cases(bool rtl) {
    std::vector<wide_t> x(model_gemm::MAX_X/16,wide_t(PACK_GUARD));
    std::vector<wide_t> w(model_gemm::MAX_W/16,wide_t(PACK_GUARD));
    std::vector<wide_t> y(model_gemm::MAX_Y/4,WIDE_GUARD);
    const uint32_t bad[][3]={{0,192,1000},{1,193,1000},{197,768,768},{197,192,1000},{8,8,8},{0xffffffffu,192,192},{197,0xffffffffu,192},{197,192,0xffffffffu}};
    for(unsigned i=0;i<8;++i) {
        const uint32_t error=model_gemm_top(x.data(),w.data(),y.data(),bad[i][0],bad[i][1],bad[i][2]);
        if(error!=model_gemm::INVALID_SHAPE || !std::all_of(y.begin(),y.end(),[](wide_t v){return v==WIDE_GUARD;})) return false;
        if(!std::all_of(x.begin(),x.end(),[](wide_t v){return v==wide_t(PACK_GUARD);}) || !std::all_of(w.begin(),w.end(),[](wide_t v){return v==wide_t(PACK_GUARD);})) return false;
        ++rejected;
    }
    // Native C-sim can additionally prove rejection does not dereference null.
    // Co-sim's software memory transactor cannot serialize null pointer inputs.
    if(!rtl && model_gemm_top(0,0,0,0,0,0)!=model_gemm::INVALID_SHAPE) return false;
    return true;
}

bool micro_unit() {
    // K=32 honors the legal loader invariant; this is not a whitelisted top call.
    const model_gemm::Shape s={"micro_unit",8,32,8};
    for(unsigned t=0;t<8;++t) {
        int8_t x[256],w[256]; wide_t px[16],pw[16],y[16];
        public_cases::fill(s,t,x,w);
        const std::vector<int32_t> expected=public_cases::golden(s,x,w);
        std::fill(y,y+16,WIDE_GUARD);
        pack_wide(x,256,px); pack_wide(w,256,pw);
        model_gemm::dense_layer(px,pw,y,8,32,8);
        for(unsigned i=0;i<64;++i) if(unpack_y(y,i)!=expected[i]) return false;
    }
    const int values[4]={-128,-127,0,127};
    for(unsigned lane=0;lane<4;++lane)
        for(unsigned v=0;v<4;++v) {
            const uint32_t word=static_cast<uint32_t>(static_cast<uint8_t>(values[v]))<<(8*lane);
            if(model_gemm::signed_lane(word,lane)!=values[v]) return false;
        }
    return true;
}
}

int main(int argc,char** argv) {
    const std::string profile=argc>1 ? argv[1] : "full";
    const bool rtl=profile=="rtl" || profile=="dev_rtl_reuse_extreme_v1";
    if(profile!="rtl" && profile!="full" && profile!="dev_reuse_extreme_v1" && profile!="dev_rtl_reuse_extreme_v1") return 2;
    std::ofstream rows(rtl?"rtl_cases.csv":"csim_cases.csv");
    rows<<"family,test,M,K,N,X_bytes,W_bytes,Y_bytes,error,status,x_test,w_test,X_pointer,W_pointer,Y_pointer\n";
    bool ok=true;
    if(!rtl) ok=micro_unit();
    if(profile=="dev_reuse_extreme_v1" || profile=="dev_rtl_reuse_extreme_v1") {
        ok=ok && run_case(4,5,rows,5,5);
        ok=ok && run_case(4,5,rows,5,10);
        ok=ok && run_case(4,5,rows,10,10);
        ok=ok && run_case(4,5,rows,5,5);
        ok=ok && run_case(0,9,rows);
        ok=ok && run_case(2,13,rows);
        if(!rtl) {
            ok=ok && run_case(1,10,rows);
            ok=ok && run_case(3,10,rows);
            ok=ok && run_case(4,5,rows);
            ok=ok && run_case(4,11,rows);
        }
    } else if(rtl) {
        for(unsigned i=0;i<8 && ok;++i) ok=run_case(public_cases::RTL_TRANSACTIONS[i].shape,public_cases::RTL_TRANSACTIONS[i].test,rows);
    } else {
        for(unsigned shape=0;shape<5 && ok;++shape)
            for(unsigned test=0;test<32 && ok;++test) ok=run_case(shape,test,rows);
    }
    if(ok) ok=invalid_cases(rtl);
    const unsigned expected=profile=="dev_rtl_reuse_extreme_v1" ? 6 : (profile=="dev_reuse_extreme_v1" ? 10 : (rtl?8:160));
    ok=ok && passed==expected && rejected==8;
    std::ofstream summary(rtl?"rtl_tb_summary.json":"csim_summary.json");
    summary<<"{\"status\":\""<<(ok?"PASS":"FAIL")<<"\",\"profile\":\""<<profile<<"\",\"valid_cases_passed\":"<<passed<<",\"invalid_cases_rejected\":"<<rejected<<",\"output_elements_compared\":"<<outputs<<",\"micro_unit_cases\":"<<(rtl?0:8)<<",\"signed_lane_checks\":"<<(rtl?0:16)<<",\"golden\":\"independent_INT64_complete_matrix\",\"guard_read_coverage\":\"RTL_AXI_MONITOR_REQUIRED\"}\n";
    std::printf("MODEL_GEMM_RESULT %s valid=%u rejected=%u outputs=%llu\n",ok?"PASS":"FAIL",passed,rejected,static_cast<unsigned long long>(outputs));
    return ok?0:1;
}
