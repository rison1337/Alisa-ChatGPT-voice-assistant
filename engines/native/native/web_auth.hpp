#pragma once
#include "common.hpp"
#include "ca_store.hpp"
#include <curl/curl.h>
#include <sstream>
#include <thread>
#include <openssl/evp.h>
#include <vector>

struct HttpReply {long status=0;std::string body;json cookies=json::object();};
inline std::string rejection_kind(const HttpReply &reply){
    if(reply.body.find("<html")!=std::string::npos||reply.body.find("<!DOCTYPE")!=std::string::npos)return "challenge";
    return "upstream";
}
inline bool web_auth_current(const json &auth){
    try{
        const std::string token=auth.at("access_token");
        auto begin=token.find('.'),end=token.find('.',begin+1);
        if(begin==std::string::npos||end==std::string::npos||end-begin>16000)return false;
        std::string payload=token.substr(begin+1,end-begin-1);
        for(auto &c:payload){if(c=='-')c='+';if(c=='_')c='/';}
        while(payload.size()%4)payload+='=';
        std::string decoded(payload.size(),'\0');
        int size=EVP_DecodeBlock(reinterpret_cast<unsigned char *>(decoded.data()),
            reinterpret_cast<const unsigned char *>(payload.data()),static_cast<int>(payload.size()));
        if(size<0)return false;
        while(!payload.empty()&&payload.back()=='='){--size;payload.pop_back();}
        decoded.resize(size);
        auto data=json::parse(decoded);
        double now=std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        // Reading exp is only a refresh scheduling hint, never authentication.
        return data.at("exp").get<double>()>now+90;
    }catch(const std::exception &){return false;}
}
inline size_t http_body(char *p,size_t size,size_t count,void *opaque) {
    auto &body=*static_cast<std::string *>(opaque);size*=count;
    if(body.size()+size>2*1024*1024)return 0;
    body.append(p,size);return size;
}
inline HttpReply web_request(const std::string &url,const json &auth,
                            const std::string &body={},const std::string &content_type={},
                            std::atomic<bool> *cancel=nullptr) {
    if(url.rfind("https://chatgpt.com/",0)!=0)throw std::runtime_error("invalid_upstream");
    CURL *curl=curl_easy_init();if(!curl)throw std::runtime_error("http_init_failed");
    HttpReply reply;curl_slist *headers=nullptr;
    auto header=[&](const std::string &value){headers=curl_slist_append(headers,value.c_str());};
    header("Origin: https://chatgpt.com");header("Referer: https://chatgpt.com/");
    header("OAI-Language: ru-RU");header("Accept: */*");
    if(auth.contains("access_token"))header("Authorization: Bearer "+auth["access_token"].get<std::string>());
    std::string cookies;
    const json cookie_values=auth.value("cookies",json::object());
    for(auto &[name,value]:cookie_values.items())
        cookies+=name+"="+value.get<std::string>()+"; ";
    if(!content_type.empty())header("Content-Type: "+content_type);
    curl_easy_setopt(curl,CURLOPT_URL,url.c_str());
    curl_easy_setopt(curl,CURLOPT_HTTPHEADER,headers);
    curl_easy_setopt(curl,CURLOPT_COOKIE,cookies.c_str());
    curl_easy_setopt(curl,CURLOPT_COOKIEFILE,"");
    curl_easy_setopt(curl,CURLOPT_USERAGENT,auth.value("user_agent",std::string("AlisaStandalone/0.1")).c_str());
    curl_easy_setopt(curl,CURLOPT_TIMEOUT,40L);curl_easy_setopt(curl,CURLOPT_CONNECTTIMEOUT,8L);
    curl_easy_setopt(curl,CURLOPT_NOSIGNAL,1L);
    if(const char *proxy=getenv("ALISA_PROXY_URL"))curl_easy_setopt(curl,CURLOPT_PROXY,proxy);
    configure_ca(curl);
    curl_easy_setopt(curl,CURLOPT_WRITEFUNCTION,http_body);
    curl_easy_setopt(curl,CURLOPT_WRITEDATA,&reply.body);
    curl_easy_setopt(curl,CURLOPT_NOPROGRESS,0L);
    curl_easy_setopt(curl,CURLOPT_XFERINFODATA,cancel);
    curl_easy_setopt(curl,CURLOPT_XFERINFOFUNCTION,+[](void *p,curl_off_t,curl_off_t,curl_off_t,curl_off_t){
        return (stopping.load()||(p&&static_cast<std::atomic<bool> *>(p)->load()))?1:0;});
    if(!body.empty()){
        curl_easy_setopt(curl,CURLOPT_POSTFIELDS,body.data());
        curl_easy_setopt(curl,CURLOPT_POSTFIELDSIZE_LARGE,static_cast<curl_off_t>(body.size()));
    }
    auto result=curl_easy_perform(curl);
    curl_easy_getinfo(curl,CURLINFO_RESPONSE_CODE,&reply.status);
    curl_slist *list=nullptr;curl_easy_getinfo(curl,CURLINFO_COOKIELIST,&list);
    for(auto *item=list;item;item=item->next){
        std::istringstream row(item->data);std::vector<std::string> parts;std::string field;
        while(std::getline(row,field,'\t'))parts.push_back(field);
        if(parts.size()==7&&(parts[0]=="chatgpt.com"||parts[0]==".chatgpt.com"||
                            parts[0]=="#HttpOnly_chatgpt.com"||parts[0]=="#HttpOnly_.chatgpt.com")){
            auto &name=parts[5];
            if(name.find("session-token")!=std::string::npos||name=="oai-did"||name=="cf_clearance")
                reply.cookies[name]=parts[6];
        }
    }
    curl_slist_free_all(list);curl_slist_free_all(headers);curl_easy_cleanup(curl);
    if(result!=CURLE_OK)throw std::runtime_error("https_transport_"+std::to_string(result));
    return reply;
}

inline json refresh_web_auth(const fs::path &file,std::atomic<bool> *cancel=nullptr) {
    json auth=read_json(file);
    auto reply=web_request("https://chatgpt.com/api/auth/session",auth,{}, {},cancel);
    if(reply.status==403){
        for(int i=0;i<10&&!stopping&&!(cancel&&*cancel);++i)std::this_thread::sleep_for(std::chrono::milliseconds(100));
        reply=web_request("https://chatgpt.com/api/auth/session",auth,{}, {},cancel);
    }
    if(reply.status!=200)throw std::runtime_error("session_refresh_"+rejection_kind(reply)+"_"+std::to_string(reply.status));
    json body=json::parse(reply.body);
    if(!body.contains("accessToken")||!body["accessToken"].is_string())throw std::runtime_error("login_required");
    auth["access_token"]=body["accessToken"];
    if(body.contains("expires"))auth["expires"]=body["expires"];
    bool rotated=false;
    for(auto &[name,value]:reply.cookies.items())if(name.find("session-token")!=std::string::npos&&!value.get<std::string>().empty())rotated=true;
    if(rotated){
        std::vector<std::string> old;
        for(auto &[name,value]:auth["cookies"].items())if(name.find("session-token")!=std::string::npos)old.push_back(name);
        for(const auto &name:old)auth["cookies"].erase(name);
    }
    for(auto &[name,value]:reply.cookies.items()){
        if(value.get<std::string>().empty())auth["cookies"].erase(name);
        else auth["cookies"][name]=value;
    }
    write_json(file,auth);return auth;
}

inline json ensure_web_auth(const fs::path &file,std::atomic<bool> *cancel=nullptr){
    auto auth=read_json(file);return web_auth_current(auth)?auth:refresh_web_auth(file,cancel);
}

inline std::string exchange_sdp(const json &auth,const std::string &offer,const json &session,std::atomic<bool> *cancel=nullptr) {
    const std::string boundary="----AlisaStandalone"+random_id();std::string body;
    for(auto &[name,value]:std::vector<std::pair<std::string,std::string>>{{"sdp",offer},{"session",session.dump()}})
        body+="--"+boundary+"\r\nContent-Disposition: form-data; name=\""+name+"\"\r\n\r\n"+value+"\r\n";
    body+="--"+boundary+"--\r\n";
    auto reply=web_request("https://chatgpt.com/realtime/wm?dcid=0",auth,body,"multipart/form-data; boundary="+boundary,cancel);
    if(reply.status==403){
        for(int i=0;i<10&&!stopping&&!(cancel&&*cancel);++i)std::this_thread::sleep_for(std::chrono::milliseconds(100));
        reply=web_request("https://chatgpt.com/realtime/wm?dcid=0",auth,body,"multipart/form-data; boundary="+boundary,cancel);
    }
    if((reply.status!=200&&reply.status!=201)||reply.body.rfind("v=0",0)!=0)
        throw std::runtime_error("voice_signaling_"+rejection_kind(reply)+"_"+std::to_string(reply.status));
    return reply.body;
}
