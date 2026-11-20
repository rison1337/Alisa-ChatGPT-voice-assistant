#include "probe_audio.hpp"
#include "web_auth.hpp"
#include <cassert>
#include <cstdlib>

int main(){
    HttpReply reply;
    reply.status=403;
    reply.body=" \n<!DOCTYPE HTML><title>UnableToLoadSite</title>";
    assert(rejection_kind(reply)=="html");
    reply.challenge=true;
    assert(rejection_kind(reply)=="browser_challenge");
    reply.challenge=false;
    reply.body="{\"error\":\"denied\"}";
    assert(rejection_kind(reply)=="upstream");
    reply.html=true;
    assert(rejection_kind(reply)=="html");
    assert(http_token("  Challenge\t")=="challenge");

    char name[]="/tmp/alisa-pcm-test.XXXXXX";
    SocketOwner file;
    file.fd=mkstemp(name);
    assert(file.fd>=0);
    file.path=name;
    std::array<unsigned char,642> pcm{};
    pcm[0]=0x34;pcm[1]=0x12;
    pcm[640]=0x00;pcm[641]=0x80;
    assert(write(file.fd,pcm.data(),pcm.size())==static_cast<ssize_t>(pcm.size()));
    auto samples=read_probe_pcm(file.path);
    assert(samples.size()==321);
    ProbeAudio feeder(samples);
    int16_t frame[320]{};
    assert(feeder.next(frame)&&frame[0]==0x1234&&frame[319]==0);
    assert(!feeder.finished());
    assert(feeder.next(frame)&&frame[0]==-32768&&frame[1]==0);
    assert(feeder.finished()&&!feeder.next(frame));
    auto check_error=[&](const char *expected){
        try{(void)read_probe_pcm(file.path);assert(false);}
        catch(const std::runtime_error &error){assert(std::string(error.what())==expected);}
    };
    pcm.fill(0);
    assert(pwrite(file.fd,pcm.data(),pcm.size(),0)==static_cast<ssize_t>(pcm.size()));
    check_error("silent_probe_pcm");
    assert(ftruncate(file.fd,641)==0);
    check_error("invalid_probe_pcm");
    assert(ftruncate(file.fd,320002)==0);
    check_error("invalid_probe_pcm");
}
