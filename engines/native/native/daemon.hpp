#pragma once
#include "voice_call.hpp"
#include "settings.hpp"
#include "children.hpp"
#include "http_server.hpp"
#include <cmath>

inline std::string safe_error(const std::exception &exc){
    std::string value=exc.what();
    if(value.size()>80||value.find_first_not_of("abcdefghijklmnopqrstuvwxyz_0123456789")!=std::string::npos)return "native_client_failed";
    return value;
}

class VoiceDaemon {
    fs::path state,base;Settings settings;bool test_mode;
    std::unique_ptr<StationAgent> agent;
    std::shared_ptr<VoiceCall> call;
    std::shared_ptr<Child> indicator;
    std::mutex mutex;std::condition_variable changed;
    std::deque<json> requests;std::thread worker,heartbeat;
    std::atomic<bool> ending{false};std::string phase="idle",error;
    std::string led_style=indicator_environment_style();
    std::atomic<bool> preparing_cancel{false};unsigned long generation=0;
    json last_stats=json::object();unsigned int calls=0;
    void close_cue(){
        if(test_mode||stopping||ending)return;
        try{
            PcmOutput pcm("default");
            for(int block=0;block<10;++block){
                short samples[960*2];
                mode_cue_block(false,block,samples);pcm.write(samples,960);
            }pcm.drain();
        }catch(const std::exception &){}
    }
    void run_calls(){
        while(!ending&&!stopping){
            json request;
            {
                std::unique_lock<std::mutex> lock(mutex);
                changed.wait_for(lock,std::chrono::seconds(1),[&]{return !requests.empty()||ending||stopping;});
                if(ending||stopping)break;if(requests.empty())continue;
                request=std::move(requests.front());requests.pop_front();
                phase="connecting";error.clear();last_stats=json::object();preparing_cancel=false;
            }
            std::string action=request.value("action",std::string());
            if(action=="close")continue;
            std::thread indicator_reader;
            std::shared_ptr<Child> light;
            std::shared_ptr<VoiceCall> active;
            try{
                if(agent&&!agent->acquire())throw std::runtime_error("native_focus_not_ready");
                auto still_current=[&]{std::lock_guard<std::mutex> lock(mutex);
                    return request.value("_generation",0UL)==generation&&!stopping&&!ending;};
                if(!still_current())throw std::runtime_error("call_cancelled");
                if(!test_mode){
                    light=std::make_shared<Child>();light->start({"sh",(base/"indicator.sh").string()},{});
                    int readfd=fcntl(light->output,F_DUPFD_CLOEXEC,3);
                    indicator_reader=std::thread([readfd]{char b[128];while(read(readfd,b,sizeof(b))>0){}close(readfd);});
                    std::lock_guard<std::mutex> lock(mutex);indicator=light;
                }
                auto entry=settings.select(action,request.value("chat_id",std::string()));
                auto auth=ensure_web_auth(state/"web-session.json",&preparing_cancel);
                if(!still_current())throw std::runtime_error("call_cancelled");
                std::string account=auth.contains("account_id")&&auth["account_id"].is_string()?auth["account_id"].get<std::string>():"";
                if(!entry.value("account_id",std::string()).empty()&&entry["account_id"]!=account)throw std::runtime_error("chat_account_mismatch");
                CallOptions options;
                options.voice=settings.view().value("voice",std::string("cove"));
                const char *pcm_device=std::getenv("ALISA_PCM_DEVICE");
                options.device=test_mode?"null":(pcm_device&&*pcm_device?pcm_device:"default");
                if(!test_mode)options.microphone=(runtime_directory/"microphone.sock").string();
                options.conversation_id=entry.value("conversation_id",std::string());
                options.parent_message_id=entry.value("parent_message_id",std::string());
                options.voice_session_id=entry.value("voice_session_id",std::string());
                auto call_generation=request.value("_generation",0UL);
                options.connected=[this,call_generation]{std::lock_guard<std::mutex> lock(mutex);
                    if(call_generation==generation&&!stopping&&!ending)phase="active";};
                std::string id=entry["id"].get<std::string>();
                options.update=[this,id,account](const json &values){settings.update(id,values,account);};
                active=std::make_shared<VoiceCall>(options);
                {std::lock_guard<std::mutex> lock(mutex);
                    if(request.value("_generation",0UL)!=generation)throw std::runtime_error("call_cancelled");
                    call=active;phase="connecting";error.clear();++calls;}
                json result=active->run(auth);
                {std::lock_guard<std::mutex> lock(mutex);last_stats=result;last_stats.erase("identity");
                    if(result.contains("error"))error=result["error"].get<std::string>();}
            }catch(const std::exception &exc){
                std::string reason=safe_error(exc);
                json diagnostic=json::object();
                if(active){active->stop();diagnostic=active->stats();
                    diagnostic["success"]=false;diagnostic["error"]=reason;}
                std::lock_guard<std::mutex> lock(mutex);
                last_stats=std::move(diagnostic);
                if(reason!="call_cancelled"&&reason!="https_transport_42")error=reason;
            }
            // Close an early-failed PeerConnection before returning the device
            // to Alice; no callbacks from this call should outlive cleanup.
            if(active)active->stop();
            {std::lock_guard<std::mutex> lock(mutex);call.reset();}
            active.reset();
            if(agent)agent->idle();
            bool cue=false;
            {
                std::lock_guard<std::mutex> lock(mutex);call.reset();indicator.reset();phase="idle";
                cue=requests.empty();
            }
            if(light){light->send("0\n");light->finish();}
            if(indicator_reader.joinable())indicator_reader.join();
            if(cue)close_cue();
        }
        if(agent)agent->idle();
    }
public:
    VoiceDaemon(fs::path directory,bool synthetic):state(std::move(directory)),base(state.parent_path()),settings(state/"settings.json"),test_mode(synthetic){
        if(!test_mode){
            fs::create_directories(runtime_directory);fs::permissions(runtime_directory,fs::perms::owner_all);
            fs::copy_file(base/"focus-0.wav",runtime_directory/"focus-0.wav",fs::copy_options::overwrite_existing);
            agent=std::make_unique<StationAgent>(base,[this](const json &event){
                auto name=event.value("event",std::string());
                if(name=="command")submit({{"action",event.value("action",std::string())}});
                if(name=="wake"){
                    std::shared_ptr<VoiceCall> active;{std::lock_guard<std::mutex> lock(mutex);active=call;}
                    if(active)active->interrupt();
                }
                if(name=="agent_exit"||name=="heartbeat_timeout"||name=="focus_error"){
                    std::lock_guard<std::mutex> lock(mutex);if(call)call->stop();error="station_agent_unavailable";
                    preparing_cancel=true;ending=true;stopping=true;changed.notify_all();
                }
            });
        }
        worker=std::thread([this]{run_calls();});
        heartbeat=std::thread([this]{
            while(!ending&&!stopping){
                std::shared_ptr<VoiceCall> active;std::shared_ptr<Child> light;std::string style;
                {std::lock_guard<std::mutex> lock(mutex);active=call;light=indicator;style=led_style;}
                bool speaking=active&&active->speaking();
                if(agent)agent->output_active(speaking);
                if(light)light->send(indicator_frame(speaking,std::chrono::duration<double>(Clock::now().time_since_epoch()).count(),style));
                std::this_thread::sleep_for(std::chrono::milliseconds(125));
            }
        });
    }
    json status(){
        json info;std::shared_ptr<VoiceCall> active;
        {std::lock_guard<std::mutex> lock(mutex);info={{"phase",phase},{"error",error},{"calls",calls},{"last_call",last_stats},{"led_style",led_style}};active=call;}
        if(active)info["audio"]=active->stats();
        info["rss_kib"]=rss_kib();info["pid"]=getpid();info["test_mode"]=test_mode;
        info["agent_connected"]=test_mode||(agent&&agent->connected());
        if(agent){
            std::lock_guard<std::mutex> lock(agent->metadata_mutex);info["native"]=json::object();
            for(const char *key:{"mode","focus_ready","native_state","commands","recognition_starts","wake_blocks","wire_format"})
                if(agent->metadata.contains(key))info["native"][key]=agent->metadata[key];
        }
        info["login_saved"]=fs::exists(state/"web-session.json");
        auto preferences=settings.view();
        for(auto &chat:preferences["chats"]){chat.erase("conversation_id");chat.erase("parent_message_id");chat.erase("voice_session_id");chat.erase("account_id");}
        info["settings"]=preferences;return info;
    }
    json submit(const json &request){
        const std::string action=request.value("action",std::string());
        if(action=="check"){
            std::shared_ptr<VoiceCall> active;
            {std::lock_guard<std::mutex> lock(mutex);if(phase!="active"||!call)throw std::runtime_error("voice_not_connected");active=call;}
            active->check();return {{"ok",true},{"synthetic_input",true}};
        }
        if(action=="voice"){settings.voice(request.value("voice",std::string()));return {{"ok",true}};}
        if(action=="led"){
            auto style=request.value("style",std::string());
            if(!valid_indicator_style(style))throw std::runtime_error("invalid_led_style");
            std::lock_guard<std::mutex> lock(mutex);led_style=style;
            return {{"ok",true},{"led_style",led_style}};
        }
        if(action=="rename"){settings.rename(request.value("chat_id",std::string()),request.value("title",std::string()));return {{"ok",true}};}
        if(action!="open"&&action!="new"&&action!="close"&&action!="previous"&&action!="select")throw std::runtime_error("unknown_action");
        std::lock_guard<std::mutex> lock(mutex);
        if(action=="open"&&(phase=="connecting"||phase=="active"))return {{"ok",true},{"already_open",true}};
        if(action=="select"&&request.value("chat_id",std::string()).empty())throw std::runtime_error("missing_chat");
        if(call)call->stop();
        preparing_cancel=true;++generation;
        requests.clear();
        if(action=="close"){
            if(agent)agent->idle();phase=call?"stopping":"idle";
        }else {error.clear();json queued=request;queued["_generation"]=generation;requests.push_back(std::move(queued));}
        changed.notify_all();return {{"ok",true},{"queued",true}};
    }
    void serve(){
        ControlServer server(18080,state,[this]{return status();},[this](const json &request){return submit(request);});
        server.run();
    }
    ~VoiceDaemon(){
        ending=true;preparing_cancel=true;changed.notify_all();
        {std::lock_guard<std::mutex> lock(mutex);if(call)call->stop();}
        if(worker.joinable())worker.join();if(heartbeat.joinable())heartbeat.join();agent.reset();
    }
};
