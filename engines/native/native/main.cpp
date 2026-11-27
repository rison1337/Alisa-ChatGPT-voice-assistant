#include "daemon.hpp"
#include <csignal>
#include <iostream>
#include <sys/file.h>

int main(int argc,char **argv){
    std::signal(SIGINT,+[](int){stopping=true;});std::signal(SIGTERM,+[](int){stopping=true;});
    std::signal(SIGPIPE,SIG_IGN);
    rtc::InitLogger(rtc::LogLevel::None);rtc::SetThreadPoolSize(2);curl_global_init(CURL_GLOBAL_DEFAULT);
    fs::path state=argc>2?argv[2]:"/data/opt/alisa-standalone/state";
    try{
        std::string action=argc>1?argv[1]:"info";
        if(action=="info"){
            std::cout<<json{{"version","0.1-experimental"},
#ifdef ALISA_USE_NETEQ
                {"audio_engine","WebRTC NetEq"},{"audio_pipeline_revision",5},
#else
                {"audio_engine","Sonic PI"},{"audio_pipeline_revision",4},
#endif
                {"live_settings_revision",1},{"ice_stun_toggle_revision",1},{"voice_diagnostics_revision",2},
                {"audio_probe_revision",1},{"pointer_bits",sizeof(void *)*8},
                {"rss_kib",rss_kib()},{"opus",opus_get_version_string()},
                {"curl",curl_version_info(CURLVERSION_NOW)->version}}.dump()<<"\n";return 0;
        }
        if(action=="network-check"||action=="vpn-health"){
            json report={{"country",nullptr},{"voice_verified",false},{"chatgpt_http_status",nullptr}};
            for(const char *url:{"https://ipapi.co/json/","https://ipwho.is/","https://chatgpt.com/"}){
                if(action=="vpn-health"&&std::string(url)!="https://chatgpt.com/")continue;
                if(!report["country"].is_null()&&std::string(url)=="https://ipwho.is/")continue;
                CURL *handle=curl_easy_init();std::string body;
                curl_easy_setopt(handle,CURLOPT_URL,url);curl_easy_setopt(handle,CURLOPT_TIMEOUT,8L);
                curl_easy_setopt(handle,CURLOPT_CONNECTTIMEOUT,5L);curl_easy_setopt(handle,CURLOPT_NOSIGNAL,1L);
                curl_easy_setopt(handle,CURLOPT_USERAGENT,"Mozilla/5.0 AlisaVoiceNetworkCheck");
                configure_ca(handle);
                curl_easy_setopt(handle,CURLOPT_WRITEFUNCTION,http_body);curl_easy_setopt(handle,CURLOPT_WRITEDATA,&body);
                if(const char *proxy=getenv("ALISA_PROXY_URL"))curl_easy_setopt(handle,CURLOPT_PROXY,proxy);
                auto code=curl_easy_perform(handle);long status=0;curl_easy_getinfo(handle,CURLINFO_RESPONSE_CODE,&status);
                curl_easy_cleanup(handle);
                if(std::string(url)!="https://chatgpt.com/"&&code==CURLE_OK&&status==200){
                    try{auto data=json::parse(body);auto country=data.value("country_code",std::string());if(country.size()==2){report["country"]=country;report["ip"]=data.value("ip",std::string());}}catch(...){}
                }else if(std::string(url)=="https://chatgpt.com/"&&code==CURLE_OK)report["chatgpt_http_status"]=status;
            }
            std::cout<<report.dump()<<"\n";return action=="vpn-health"&&report["chatgpt_http_status"].is_null()?2:0;
        }
        if(action=="refresh"){
            refresh_web_auth(state/"web-session.json");
            std::cout<<json{{"ok",true},{"credentials_printed",false}}.dump()<<"\n";return 0;
        }
        if(action=="serve"||action=="serve-test"){
            fs::create_directories(runtime_directory);fs::permissions(runtime_directory,fs::perms::owner_all);
            SocketOwner owner;owner.fd=open((runtime_directory/"daemon.lock").c_str(),O_RDWR|O_CREAT|O_CLOEXEC,0600);
            if(owner.fd<0||flock(owner.fd,LOCK_EX|LOCK_NB)<0)throw std::runtime_error("client_already_running");
            VoiceDaemon daemon(state,action=="serve-test");daemon.serve();return 0;
        }
        if(action=="url"){
            auto auth=read_json(state/"ui-auth.json");
            std::cout<<json{{"port",18080},{"pairing_code",auth["code"]}}.dump()<<"\n";return 0;
        }
        if(action=="status"){
            std::cout<<control_request(state,json::object()).dump(2)<<"\n";return 0;
        }
        if(action=="open"||action=="new"||action=="close"||action=="previous"||action=="select"||action=="voice"||action=="reasoning"||action=="check"||action=="led"){
            json request={{"action",action}};
            if(action=="new"&&argc==4&&std::string_view(argv[3])=="--no-mic")request["no_mic"]=true;
            if(action=="voice"||action=="reasoning"||action=="select"||action=="led"){
                if(argc<4)throw std::runtime_error("missing_argument");
                request[action=="led"?"style":action=="select"?"chat_id":action]=argv[3];
            }
            std::cout<<control_request(state,request).dump()<<"\n";return 0;
        }
        if(action!="probe")throw std::runtime_error("unknown_action");
        CallOptions options;options.probe=true;options.device="null";
        bool audio_probe=argc==6&&std::string_view(argv[4])=="--audio";
        if(argc>4&&!audio_probe)throw std::runtime_error("invalid_probe_arguments");
        if(argc>3){
            auto request=read_json(argv[3]);
            options.synthetic_text=request.value("text",options.synthetic_text);
            options.expected_reply=request.value("expected",options.expected_reply);
            options.conversation_id=request.value("conversation_id",std::string());
            options.parent_message_id=request.value("parent_message_id",std::string());
            options.voice_session_id=request.value("voice_session_id",std::string());
            options.voice=request.value("voice",options.voice);
#ifdef ALISA_TRANSPORT_STUDY
            options.normal_speed=request.value("normal_speed",false);
            options.correct_receiver_reports=request.value("correct_receiver_reports",false);
#endif
        }
        if(audio_probe){
            options.probe_pcm=read_probe_pcm(argv[5]);
            // Use a fresh conversation in the diagnostic, even if the request
            // file was copied from another probe.
            options.conversation_id.clear();
            options.parent_message_id.clear();
            options.voice_session_id.clear();
        }
        auto auth=ensure_web_auth(state/"web-session.json");
        auto call=std::make_shared<VoiceCall>(std::move(options));
        json result;
        try{result=call->run(auth);}
        catch(const std::exception &exc){call->stop();result=call->stats();
            result["success"]=false;result["error"]=safe_error(exc);}
        call.reset();
        if(audio_probe){
            json report=json::object();
            for(const char *key:{"probe_input","success","error","connection","last_server_state",
                "server_close","milestones_ms","sent","uplink_rejected","received","microphone_frames",
                "probe_pcm_frames_sent","probe_pcm_frames_expected","probe_answer_blocks",
                "probe_reply_match","audible_blocks","alsa_underruns"})
                if(result.contains(key))report[key]=result[key];
            std::cout<<report.dump(2)<<"\n";return report.value("success",false)?0:2;
        }
        if(result.contains("identity")){write_json(state/"probe-conversation.json",result["identity"]);result.erase("identity");}
        if(result.contains("synthetic_caption")){
            write_json(state/"probe-synthetic-caption.json",result["synthetic_caption"]);result.erase("synthetic_caption");
        }
        std::cout<<result.dump(2)<<"\n";return result.value("success",false)?0:2;
    }catch(const std::exception &exc){
        std::string reason=exc.what();
        // Only application-owned constant error labels are safe to expose.
        if(reason.size()>80||reason.find_first_not_of("abcdefghijklmnopqrstuvwxyz_0123456789")!=std::string::npos)reason="native_client_failed";
        std::cout<<json{{"ok",false},{"error",reason},{"rss_kib",rss_kib()}}.dump()<<"\n";return 2;
    }
}
