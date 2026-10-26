// Independent host testbench for H576_SPARSE24_SPLIT128_V1.
// No DUT decoding or sparse arithmetic helper is used by the golden reference.
// Guards detect writes only. They cannot prove that AXI reads stay in range or
// that Dense issues zero metadata reads: an RTL AXI transaction monitor is TODO.
#include "../include/model_gemm_config.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace tb {
using model_gemm::wide_t;
static const unsigned GUARD_WORDS=4;

void require(bool ok,const std::string& text) {
    if(!ok) throw std::runtime_error(text);
}
std::string number(uint64_t n) { std::ostringstream out; out<<n; return out.str(); }
std::string json_string(const std::string& text) {
    std::ostringstream out; out<<'"';
    for(size_t i=0;i<text.size();++i) {
        const unsigned char c=static_cast<unsigned char>(text[i]);
        if(c=='"' || c=='\\') out<<'\\'<<char(c);
        else if(c=='\n') out<<"\\n";
        else if(c=='\r') out<<"\\r";
        else if(c=='\t') out<<"\\t";
        else if(c<32) out<<"?";
        else out<<char(c);
    }
    out<<'"'; return out.str();
}
struct Shape { uint32_t m,k,n; };
size_t x_bytes(Shape s) { return size_t(s.m)*s.k; }
size_t w_bytes(Shape s) { return size_t(s.k)*s.n; }
size_t y_bytes(Shape s) { return size_t(s.m)*s.n*4; }
size_t aligned16(size_t size) { return (size+15)/16*16; }
int8_t signed_byte(unsigned byte) {
    return static_cast<int8_t>(byte<128 ? int(byte) : int(byte)-256);
}

// All external-pointer vectors contain the FULL pragma depth, plus two guards.
// Passing an exact-sized payload vector would permit wrapc transactors to read
// beyond it even if the hardware only accesses the logical payload.
struct Buffer {
    unsigned capacity;
    unsigned tag;
    std::vector<wide_t> storage;
    explicit Buffer(unsigned words,unsigned marker)
        :capacity(words),tag(marker),storage(words+2*GUARD_WORDS) { reset(); }
    wide_t guard(size_t index) const {
        wide_t word=0;
        for(unsigned lane=0;lane<16;++lane) {
            const unsigned value=(0xA5u+tag*29u+unsigned(index)*13u+lane*7u)&255u;
            word.range(lane*8+7,lane*8)=value;
        }
        return word;
    }
    void reset() {
        for(size_t i=0;i<storage.size();++i) storage[i]=guard(i);
    }
    wide_t* data() { return &storage[GUARD_WORDS]; }
    const wide_t* data() const { return &storage[GUARD_WORDS]; }
    void load(const std::vector<uint8_t>& bytes) {
        require(aligned16(bytes.size())<=size_t(capacity)*16,"payload exceeds pointer depth");
        reset();
        const size_t words=(bytes.size()+15)/16;
        // Zero only the final allocated 128-bit payload beat, not unused depth.
        for(size_t q=0;q<words;++q) data()[q]=0;
        for(size_t i=0;i<bytes.size();++i)
            data()[i/16].range(unsigned(i%16)*8+7,unsigned(i%16)*8)=bytes[i];
    }
    void unchanged(const std::vector<wide_t>& before,const std::string& name) const {
        require(before.size()==storage.size(),"snapshot length");
        for(size_t i=0;i<storage.size();++i)
            require(storage[i]==before[i],name+" changed at physical word "+number(i));
    }
    void outside_output_unchanged(size_t payload_words) const {
        require(payload_words<=capacity,"Y length exceeds depth");
        for(size_t i=0;i<storage.size();++i) {
            const bool output=i>=GUARD_WORDS && i<GUARD_WORDS+payload_words;
            if(!output) require(storage[i]==guard(i),"Y guard/unused word overwritten: "+number(i));
        }
    }
};
struct Buffers {
    Buffer x,wd,wm,y;
    Buffers():x(model_gemm::MAX_X_WORDS,1),
        wd(model_gemm::MAX_W_DATA_WORDS,2),
        wm(model_gemm::MAX_W_META_WORDS,3),
        y(model_gemm::MAX_Y_WORDS,4) {}
};

struct Fixture {
    std::string name;
    Shape shape;
    std::vector<int8_t> x,w;
    std::vector<uint8_t> sparse_values,metadata;
};
std::vector<uint8_t> raw_bytes(const std::vector<int8_t>& input) {
    std::vector<uint8_t> output(input.size());
    for(size_t i=0;i<input.size();++i) output[i]=static_cast<uint8_t>(input[i]);
    return output;
}
uint32_t random_next(uint32_t& state) {
    state^=state<<13; state^=state>>17; state^=state<<5; return state;
}

Fixture synthetic(const std::string& name,Shape shape,unsigned seed,bool sparse_weight=true) {
    Fixture f; f.name=name; f.shape=shape;
    f.x.resize(x_bytes(shape)); f.w.assign(w_bytes(shape),0);
    uint32_t state=0x8142AD65u^(seed*0x9E3779B9u);
    for(size_t i=0;i<f.x.size();++i) f.x[i]=signed_byte(random_next(state)&255u);
    if(f.x.size()>1) { f.x[0]=-128; f.x[1]=127; }
    const size_t records=size_t(shape.k/4)*shape.n;
    f.sparse_values.assign(records*2,0);
    f.metadata.assign((records+1)/2,0);
    static const unsigned pairs[6][2]={{0,1},{0,2},{0,3},{1,2},{1,3},{2,3}};
    for(unsigned g=0;g<shape.k/4;++g) {
        for(unsigned n=0;n<shape.n;++n) {
            const size_t t=size_t(g)*shape.n+n;
            const unsigned* pair=pairs[(5*g+n+seed)%6];
            int8_t v0=signed_byte(random_next(state)&255u);
            int8_t v1=signed_byte(random_next(state)&255u);
            if(t%17==0) v0=0;             // Legal retained zero slots.
            if(t%23==0) v1=0;
            if(t==1) { v0=-128; v1=127; } // Signed multiply boundaries.
            f.sparse_values[2*t]=static_cast<uint8_t>(v0);
            f.sparse_values[2*t+1]=static_cast<uint8_t>(v1);
            const unsigned code=pair[0]|(pair[1]<<2);
            f.metadata[t/2]|=uint8_t(code<<((t%2)*4));
            f.w[size_t(4*g+pair[0])*shape.n+n]=v0;
            f.w[size_t(4*g+pair[1])*shape.n+n]=v1;
        }
    }
    if(!sparse_weight) {
        for(size_t i=0;i<f.w.size();++i) f.w[i]=signed_byte(random_next(state)&255u);
        f.w[0]=-128; f.w[1]=127;
    }
    return f;
}

// Full K traversal, no mask or positions: independent INT64 arithmetic golden.
std::vector<int32_t> golden(const Fixture& f) {
    require(f.x.size()==x_bytes(f.shape) && f.w.size()==w_bytes(f.shape),"golden input shape");
    std::vector<int32_t> result(size_t(f.shape.m)*f.shape.n,0);
    for(unsigned m=0;m<f.shape.m;++m) {
        std::vector<int64_t> row(f.shape.n,0);
        for(unsigned k=0;k<f.shape.k;++k) {
            const int64_t x=f.x[size_t(m)*f.shape.k+k];
            for(unsigned n=0;n<f.shape.n;++n)
                row[n]+=x*int64_t(f.w[size_t(k)*f.shape.n+n]);
        }
        for(unsigned n=0;n<f.shape.n;++n) {
            require(row[n]>=std::numeric_limits<int32_t>::min() &&
                    row[n]<=std::numeric_limits<int32_t>::max(),"golden INT32 overflow");
            result[size_t(m)*f.shape.n+n]=int32_t(row[n]);
        }
    }
    return result;
}

void validate_sparse_packet(const Fixture& f) {
    const size_t records=size_t(f.shape.k/4)*f.shape.n;
    require(f.sparse_values.size()==records*2,"Sparse values length");
    require(f.metadata.size()==(records+1)/2,"Sparse metadata length");
    std::vector<int8_t> rebuilt(f.w.size(),0);
    for(size_t t=0;t<records;++t) {
        const unsigned code=(f.metadata[t/2]>>((t%2)*4))&15;
        const unsigned p0=code&3,p1=(code>>2)&3;
        require(p0<p1,"invalid independent fixture positions");
        const size_t g=t/f.shape.n,n=t%f.shape.n;
        rebuilt[(4*g+p0)*f.shape.n+n]=signed_byte(f.sparse_values[2*t]);
        rebuilt[(4*g+p1)*f.shape.n+n]=signed_byte(f.sparse_values[2*t+1]);
    }
    require(rebuilt==f.w,"Sparse packet does not reconstruct source Dense W");
}

struct Row {
    std::string name;
    Shape shape;
    unsigned mode,status;
    uint64_t elements;
    double seconds;
};
struct Report {
    std::string profile;
    std::vector<Row> rows;
    uint64_t elements;
    Report(const std::string& name):profile(name),elements(0) {}
    void save(bool success,const std::string& error) const {
        const std::string base="csim_summary_"+profile;
        std::ofstream json((base+".json").c_str(),std::ios::binary);
        require(bool(json),"cannot write run summary JSON");
        json<<"{\n  \"schema\": \"sparse24_hls_testbench_v1\",\n  \"profile\": "<<json_string(profile)
            <<",\n  \"status\": \""<<(success?"PASS":"FAIL")<<"\",\n  \"calls\": "<<rows.size()
            <<",\n  \"INT32_outputs_checked\": "<<elements
            <<",\n  \"error\": "<<json_string(error)
            <<",\n  \"scope\": \"C testbench/RTL cosim as invoked; not board or model accuracy acceptance\","
            <<"\n  \"axi_transaction_monitor\": \"NOT_IMPLEMENTED: guard checks cannot prove absence of reads\","
            <<"\n  \"cases\": [\n";
        for(size_t i=0;i<rows.size();++i) {
            const Row& r=rows[i];
            json<<"    {\"name\":"<<json_string(r.name)<<",\"M\":"<<r.shape.m
                <<",\"K\":"<<r.shape.k<<",\"N\":"<<r.shape.n<<",\"mode\":"<<r.mode
                <<",\"return_code\":"<<r.status<<",\"outputs\":"<<r.elements
                <<",\"testbench_wall_seconds\":"<<std::setprecision(12)<<r.seconds<<"}"
                <<(i+1<rows.size()?",":"")<<"\n";
        }
        json<<"  ]\n}\n"; require(bool(json),"summary JSON write failure");
        std::ofstream csv((base+".csv").c_str(),std::ios::binary);
        require(bool(csv),"cannot write run summary CSV");
        csv<<"name,M,K,N,mode,return_code,INT32_outputs_checked,testbench_wall_seconds\n";
        for(size_t i=0;i<rows.size();++i) {
            const Row& r=rows[i];
            csv<<r.name<<','<<r.shape.m<<','<<r.shape.k<<','<<r.shape.n<<','<<r.mode<<','
                <<r.status<<','<<r.elements<<','<<std::setprecision(12)<<r.seconds<<'\n';
        }
        require(bool(csv),"summary CSV write failure");
    }
};

void load_buffers(Buffers& b,const Fixture& f,unsigned mode) {
    b.x.load(raw_bytes(f.x));
    if(mode==model_gemm::MODE_SPARSE24) {
        b.wd.load(f.sparse_values); b.wm.load(f.metadata);
    } else {
        b.wd.load(raw_bytes(f.w));
        // Real-record code 0 would be invalid in Sparse. Dense must not use it.
        // This is the format's allocated all-zero 16-byte Dense placeholder.
        b.wm.load(std::vector<uint8_t>(16,0));
    }
    b.y.reset();
}

void invoke(Report& report,Buffers& b,const std::string& name,Shape shape,
            unsigned mode,unsigned expected_status,const std::vector<int32_t>* expected) {
    const std::vector<wide_t> before_x=b.x.storage,before_wd=b.wd.storage,
        before_wm=b.wm.storage,before_y=b.y.storage;
    const auto start=std::chrono::steady_clock::now();
    const uint32_t status=model_gemm_top(b.x.data(),b.wd.data(),b.wm.data(),b.y.data(),
                                         shape.m,shape.k,shape.n,mode);
    const double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    require(status==expected_status,name+": return="+number(status)+", expected="+number(expected_status));
    b.x.unchanged(before_x,name+" X"); b.wd.unchanged(before_wd,name+" W_data");
    b.wm.unchanged(before_wm,name+" W_meta");
    uint64_t checked=0;
    if(expected_status==model_gemm::OK) {
        require(expected && expected->size()==size_t(shape.m)*shape.n,"expected Y shape");
        const size_t size=expected->size();
        size_t errors=0;
        for(size_t q=0;q<(size+3)/4;++q) {
            wide_t word=0;
            for(unsigned lane=0;lane<4;++lane) {
                const size_t index=q*4+lane;
                const uint32_t value=index<size?static_cast<uint32_t>((*expected)[index]):0;
                word.range(lane*32+31,lane*32)=value;
            }
            if(b.y.data()[q]!=word) {
                if(errors<4) std::cerr<<name<<": Y word "<<q<<" got="<<b.y.data()[q]
                                     <<" expected="<<word<<'\n';
                ++errors;
            }
        }
        require(errors==0,name+": mismatched 128-bit Y words="+number(errors));
        b.y.outside_output_unchanged((size+3)/4);
        checked=size;
    } else b.y.unchanged(before_y,name+" rejected Y");
    Row row={name,shape,mode,status,checked,seconds};
    report.rows.push_back(row); report.elements+=checked;
    std::cout<<"PASS "<<name<<" M="<<shape.m<<" K="<<shape.k<<" N="<<shape.n
             <<" mode="<<mode<<" return="<<status<<" outputs="<<checked<<'\n';
}

void successful_pair(Report& report,Buffers& b,const Fixture& f) {
    validate_sparse_packet(f);
    const std::vector<int32_t> reference=golden(f);
    // Same physical host pointer addresses, Dense -> Sparse -> later fixtures.
    for(unsigned mode=0;mode<2;++mode) {
        load_buffers(b,f,mode);
        invoke(report,b,f.name+(mode?"_sparse":"_dense"),f.shape,mode,model_gemm::OK,&reference);
    }
}

void check_documented_bytes() {
    Fixture f; f.name="format_K4_N2"; f.shape={1,4,2};
    f.x={1,2,3,4}; f.w={0,3,-5,0,0,-2,7,0};
    f.sparse_values={0xFB,0x07,0x03,0xFE}; f.metadata={0x8D};
    validate_sparse_packet(f);
    const std::vector<int32_t> reference=golden(f);
    require(reference.size()==2 && reference[0]==18 && reference[1]==-3,"fixed byte example golden");
    Buffer values(1,8),meta(1,9); values.load(f.sparse_values); meta.load(f.metadata);
    require(values.data()[0].range(31,0).to_uint()==0xFE0307FBu,"fixed values lane ordering");
    require(meta.data()[0].range(7,0).to_uint()==0x8Du,"fixed metadata nibble ordering");
    require(values.data()[0].range(127,32)==0 && meta.data()[0].range(127,8)==0,"fixed padding");
    std::cout<<"PASS documented K4/N2 format vector (not a DUT-supported shape)\n";
}

void rejection_tests(Report& report,Buffers& b,bool exhaustive) {
    const Fixture f=synthetic("reject_fixture",{1,16,16},777);
    load_buffers(b,f,0);
    invoke(report,b,"invalid_mode",f.shape,2,model_gemm::INVALID_MODE,0);
    const Shape bad[]={{0,16,16},{198,16,16},{1,0,16},{1,592,16},{1,16,0},
        {1,16,592},{1,15,16},{1,16,15},{1,576,576},{0xFFFFFFFFu,16,16}};
    const unsigned count=exhaustive?sizeof(bad)/sizeof(bad[0]):1;
    for(unsigned i=0;i<count;++i) {
        load_buffers(b,f,0);
        invoke(report,b,"invalid_shape_"+number(i),bad[i],0,model_gemm::INVALID_SHAPE,0);
    }
    const unsigned bad_codes[]={0x0,0x1,0x5,0x2,0xF};
    const unsigned code_count=exhaustive?sizeof(bad_codes)/sizeof(bad_codes[0]):1;
    for(unsigned i=0;i<code_count;++i) {
        Fixture broken=f;
        // Exercise first AND last records; all metadata is validated before Y.
        const size_t records=size_t(f.shape.k/4)*f.shape.n;
        const size_t t=(i&1)?records-1:0;
        const unsigned shift=unsigned(t%2)*4;
        broken.metadata[t/2]=uint8_t((broken.metadata[t/2]&~(15u<<shift))|(bad_codes[i]<<shift));
        load_buffers(b,broken,1);
        invoke(report,b,"invalid_meta_"+number(i),broken.shape,1,model_gemm::INVALID_METADATA,0);
    }
}

void generated_tests(Report& report,Buffers& b,bool rtl) {
    check_documented_bytes();
    successful_pair(report,b,synthetic("reuse_A",{1,16,16},1));
    successful_pair(report,b,synthetic("reuse_B_XW_changed",{33,16,32},2));
    if(!rtl) {
        successful_pair(report,b,synthetic("M31_N32",{31,16,32},3));
        successful_pair(report,b,synthetic("M32_N16",{32,32,16},4));
        successful_pair(report,b,synthetic("M33_N192",{33,32,192},5));
        successful_pair(report,b,synthetic("M197_N16",{197,16,16},6));
        successful_pair(report,b,synthetic("M1_N576",{1,192,576},7));
        Fixture dense=synthetic("all_four_weights_nonzero_dense",{31,32,32},8,false);
        const std::vector<int32_t> reference=golden(dense);
        load_buffers(b,dense,0);
        invoke(report,b,dense.name,dense.shape,0,model_gemm::OK,&reference);
    }
    successful_pair(report,b,synthetic("real_fc1_shape",{197,192,576},9));
    successful_pair(report,b,synthetic("real_fc2_shape",{197,576,192},10));
    // Return to the FIRST shape after large calls, with new X and W contents.
    successful_pair(report,b,synthetic("reuse_C_after_large_calls",{1,16,16},11));
    rejection_tests(report,b,!rtl);
}

std::vector<uint8_t> read_exact(const std::string& path,size_t expected) {
    std::ifstream input(path.c_str(),std::ios::binary|std::ios::ate);
    require(bool(input),"cannot open "+path);
    const std::streamoff length=input.tellg();
    require(length>=0 && uint64_t(length)==expected,"unexpected byte length "+path);
    input.seekg(0);
    std::vector<uint8_t> value(expected);
    if(expected) input.read(reinterpret_cast<char*>(&value[0]),std::streamsize(expected));
    require(bool(input),"short read "+path); return value;
}
std::vector<int8_t> decode_int8(const std::vector<uint8_t>& bytes) {
    std::vector<int8_t> value(bytes.size());
    for(size_t i=0;i<bytes.size();++i) value[i]=signed_byte(bytes[i]);
    return value;
}
std::vector<int32_t> decode_int32_le(const std::vector<uint8_t>& bytes) {
    require(bytes.size()%4==0,"INT32 byte length");
    std::vector<int32_t> value(bytes.size()/4);
    for(size_t i=0;i<value.size();++i) {
        uint32_t bits=0;
        for(unsigned lane=0;lane<4;++lane) bits|=uint32_t(bytes[4*i+lane])<<(8*lane);
        const int64_t signed_value=bits<0x80000000u?int64_t(bits):int64_t(bits)-4294967296LL;
        value[i]=int32_t(signed_value);
    }
    return value;
}

void source_package_tests(Report& report,Buffers& b,const std::string& root) {
    check_documented_bytes();
    const unsigned layers[]={26,27,30,31,34,35,38,39,42,43,46,47};
    const char* kinds[]={"public","edge"};
    for(unsigned i=0;i<sizeof(layers)/sizeof(layers[0]);++i) {
        const unsigned layer=layers[i];
        const bool fc1=layer%4==2;
        const std::string folder=root+"/layers/layer_"+number(layer);
        for(unsigned kind=0;kind<2;++kind) {
            Fixture f;
            f.name="layer_"+number(layer)+"_"+kinds[kind];
            f.shape={kind==0?197u:6u,fc1?192u:576u,fc1?576u:192u};
            f.x=decode_int8(read_exact(folder+"/"+kinds[kind]+"/X.bin",aligned16(x_bytes(f.shape))));
            f.x.resize(x_bytes(f.shape));
            f.w=decode_int8(read_exact(folder+"/weights/W_dense_data.bin",aligned16(w_bytes(f.shape))));
            f.w.resize(w_bytes(f.shape));
            const size_t records=size_t(f.shape.k/4)*f.shape.n;
            f.sparse_values=read_exact(folder+"/weights/W_sparse_data.bin",aligned16(records*2));
            f.sparse_values.resize(records*2);
            f.metadata=read_exact(folder+"/weights/W_sparse_meta.bin",aligned16((records+1)/2));
            f.metadata.resize((records+1)/2);
            const std::vector<uint8_t> dense_meta=read_exact(folder+"/weights/W_dense_meta.bin",16);
            require(std::count(dense_meta.begin(),dense_meta.end(),uint8_t(0))==16,"Dense meta placeholder");
            validate_sparse_packet(f);
            const std::vector<int32_t> saved=decode_int32_le(read_exact(
                folder+"/"+kinds[kind]+"/Y_golden.bin",aligned16(y_bytes(f.shape))));
            const std::vector<int32_t> independent=golden(f);
            require(saved==independent,f.name+": saved Python golden != independent C++ INT64 golden");
            for(unsigned mode=0;mode<2;++mode) {
                load_buffers(b,f,mode);
                invoke(report,b,f.name+(mode?"_sparse":"_dense"),f.shape,mode,model_gemm::OK,&independent);
            }
        }
    }
}
} // namespace tb

int main(int argc,char** argv) {
    const std::string profile=argc>1?argv[1]:"full";
    tb::Report report(profile=="full" || profile=="rtl" || profile=="golden"?profile:"invalid");
    try {
        tb::require(profile=="full" || profile=="rtl" || profile=="golden",
                    "usage: testbench [full|rtl|golden [AXI package root]]");
        tb::require(argc<=(profile=="golden"?3:2),"unexpected testbench arguments");
        tb::Buffers buffers;
        if(profile=="golden") {
            const std::string root=argc>2?argv[2]:"tb/axi_cases";
            tb::source_package_tests(report,buffers,root);
        } else tb::generated_tests(report,buffers,profile=="rtl");
        report.save(true,"");
        std::cout<<"TESTBENCH PASS profile="<<profile<<" calls="<<report.rows.size()
                 <<" INT32_outputs_checked="<<report.elements<<'\n';
        std::cout<<"Scope: exact integer kernel verification, NOT board/model accuracy acceptance.\n"
                    "TODO: RTL AXI monitor for transfer bounds and zero Dense metadata reads.\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<"TESTBENCH FAIL: "<<error.what()<<'\n';
        try { report.save(false,error.what()); } catch(...) {}
        return 1;
    }
}
