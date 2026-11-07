#pragma once
#include <nlohmann/json.hpp>
#include <initializer_list>
#include <string>

// Updated under VoiceCall's mutex. Retain fixed protocol codes only: server
// detail is untrusted text and must never become a log or exception message.
struct VoiceServerState {
    using json=nlohmann::json;
    bool ended=false;
    std::string error,last_state="unknown";
    json close=json::object();

    static std::string code(const json &payload,const char *field,
                            std::initializer_list<const char *> allowed){
        auto value=payload.find(field);
        if(value==payload.end()||!value->is_string())return "unknown";
        const auto &text=value->get_ref<const json::string_t &>();
        for(auto candidate:allowed)if(text==candidate)return candidate;
        return "unknown";
    }
    bool receive(const std::string &kind,const json &payload){
        if(ended)return true;
        if(kind=="state_update")
            last_state=code(payload,"new_state",{"listening","thinking","speaking","idle"});
        if(kind=="goodbye"){
            auto reason=code(payload,"reason",{"error","cap_reached","normal","client_request","timeout","session_expired"});
            auto detail=code(payload,"detail",{"internal_error","upstream_error","server_error","unauthorized",
                "quota_exceeded","rate_limit_exceeded","invalid_session","invalid_model","unsupported_country"});
            close={{"reason",reason},{"detail",detail}};
            error="voice_session_ended";
            if(reason=="cap_reached")error="voice_daily_limit_reached";
            else if(reason=="error"){
                if(detail=="internal_error")error="voice_server_internal_error";
                else if(detail=="upstream_error")error="voice_server_upstream_error";
                else if(detail=="server_error")error="voice_server_error";
            }
            ended=true;
        }else if(kind=="error"||kind=="session.error"){
            error="voice_session_ended";ended=true;
        }
        return ended;
    }
    bool can_start_audio(bool cancelled)const{return !ended&&!cancelled;}
};
