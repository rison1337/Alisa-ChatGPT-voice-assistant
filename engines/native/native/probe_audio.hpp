#pragma once
#include "common.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <sys/stat.h>
#include <vector>

// The probe reads at most ten seconds of raw s16le, 16 kHz mono PCM.
inline std::vector<int16_t> read_probe_pcm(const fs::path &path){
    SocketOwner input;
    input.fd=open(path.c_str(),O_RDONLY|O_CLOEXEC|O_NONBLOCK);
    struct stat st{};
    constexpr int max_bytes=10*16000*2;
    if(input.fd<0||fstat(input.fd,&st)!=0||!S_ISREG(st.st_mode)||
       st.st_size<640||st.st_size>max_bytes||st.st_size%2!=0)
        throw std::runtime_error("invalid_probe_pcm");
    std::vector<unsigned char> bytes(static_cast<size_t>(st.st_size));
    for(size_t offset=0;offset<bytes.size();){
        const auto count=read(input.fd,bytes.data()+offset,bytes.size()-offset);
        if(count<0&&errno==EINTR)continue;
        if(count<=0)throw std::runtime_error("invalid_probe_pcm");
        offset+=static_cast<size_t>(count);
    }
    std::vector<int16_t> samples;
    samples.reserve(bytes.size()/2);
    for(size_t i=0;i<bytes.size();i+=2){
        const int value=bytes[i]|(int(bytes[i+1])<<8);
        samples.push_back(static_cast<int16_t>(value<32768?value:value-65536));
    }
    if(std::none_of(samples.begin(),samples.end(),[](int16_t value){return value!=0;}))
        throw std::runtime_error("silent_probe_pcm");
    return samples;
}

class ProbeAudio {
    const std::vector<int16_t> &samples;
    size_t cursor=0;
public:
    explicit ProbeAudio(const std::vector<int16_t> &pcm):samples(pcm){}
    bool finished()const{return cursor==samples.size();}
    bool next(int16_t (&frame)[320]){
        if(finished())return false;
        const auto count=std::min(samples.size()-cursor,std::size(frame));
        std::fill(frame,frame+std::size(frame),0);
        std::copy_n(samples.data()+cursor,count,frame);
        cursor+=count;
        return true;
    }
};
