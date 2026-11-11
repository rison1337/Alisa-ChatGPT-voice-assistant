#include "ca_store.hpp"
#include <iostream>

int main(int argc,char **argv){
    if(argc!=5)return 100;
    const auto store=ca_store({argv[3],argv[4]});
    if(std::string(argv[1])=="select"){
        std::cout<<store.file<<"\n"<<store.directory<<"\n";return 0;
    }
    curl_global_init(CURL_GLOBAL_DEFAULT);
    CURL *curl=curl_easy_init();if(!curl)return 101;
    configure_ca(curl,store);
    curl_easy_setopt(curl,CURLOPT_URL,argv[2]);
    curl_easy_setopt(curl,CURLOPT_PROXY,"");
    curl_easy_setopt(curl,CURLOPT_TIMEOUT,3L);
    curl_easy_setopt(curl,CURLOPT_WRITEFUNCTION,+[](char *,size_t size,size_t count,void *){return size*count;});
    auto result=curl_easy_perform(curl);
    curl_easy_cleanup(curl);curl_global_cleanup();
    return static_cast<int>(result);
}
