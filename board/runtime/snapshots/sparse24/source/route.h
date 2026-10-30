#pragma once
#include <algorithm>
#include "protocol.h"
namespace kv5 {
inline bool offloaded(unsigned i) {return i<48 && ((13u>>(i%4))&1u);}
inline unsigned next_pl(unsigned i) {while(i<49 && !offloaded(i))++i;return i;}
inline size_t cpu_y_capacity() {size_t n=0;for(unsigned i=0;i<49;++i)if(!offloaded(i))n=std::max(n,size_t(shape(i).y_bytes()/4));return n;}
}
