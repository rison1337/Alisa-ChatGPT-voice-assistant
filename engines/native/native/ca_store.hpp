#pragma once
#include <curl/curl.h>
#include <cstdlib>
#include <filesystem>
#include <initializer_list>
#include <string>
#include <unistd.h>

struct CaStore {
    std::string file,directory;
};

inline CaStore ca_store(std::initializer_list<const char *> candidates={
        "/system/vendor/quasar/certs/ca-certificates.crt",
        "/system/vendor/quasar/ca-certificates.crt"}){
    CaStore store;
    for(const char *name:{"ALISA_CA_FILE","SSL_CERT_FILE"})
        if(const char *value=std::getenv(name))if(*value){store.file=value;break;}
    if(store.file.empty())for(const char *path:candidates){
        std::error_code error;
        if(std::filesystem::is_regular_file(path,error)&&access(path,R_OK)==0){store.file=path;break;}
    }
    if(const char *value=std::getenv("SSL_CERT_DIR"))if(*value)store.directory=value;
    return store;
}

inline void configure_ca(CURL *handle,const CaStore &store){
    curl_easy_setopt(handle,CURLOPT_SSL_VERIFYPEER,1L);
    curl_easy_setopt(handle,CURLOPT_SSL_VERIFYHOST,2L);
    if(!store.file.empty())curl_easy_setopt(handle,CURLOPT_CAINFO,store.file.c_str());
    if(!store.directory.empty())curl_easy_setopt(handle,CURLOPT_CAPATH,store.directory.c_str());
}

inline void configure_ca(CURL *handle){configure_ca(handle,ca_store());}
