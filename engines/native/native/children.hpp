#pragma once
#include "common.hpp"
#include <condition_variable>
#include <functional>
#include <spawn.h>
#include <sys/wait.h>
#include <thread>
#include <vector>
#include <cstring>
#include <cstdlib>
#include <signal.h>

extern char **environ;
class Child {
    std::mutex write_mutex;
public:
    pid_t pid=-1;int input=-1,output=-1;
    void start(const std::vector<std::string> &arguments,const std::vector<std::string> &extra){
        int in[2],out[2];if(pipe2(in,O_CLOEXEC)<0)throw std::runtime_error("child_pipe_failed");
        if(pipe2(out,O_CLOEXEC)<0){close(in[0]);close(in[1]);throw std::runtime_error("child_pipe_failed");}
        std::vector<std::string> env;
        for(char **p=environ;*p;++p){
            bool replaced=false;
            for(const auto &value:extra)if(std::strncmp(*p,value.c_str(),value.find('=')+1)==0)replaced=true;
            if(!replaced)env.emplace_back(*p);
        }
        env.insert(env.end(),extra.begin(),extra.end());
        std::vector<char *> argv,envp;
        for(const auto &arg:arguments)argv.push_back(const_cast<char *>(arg.c_str()));argv.push_back(nullptr);
        for(auto &value:env)envp.push_back(value.data());envp.push_back(nullptr);
        posix_spawn_file_actions_t actions;posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_adddup2(&actions,in[0],0);posix_spawn_file_actions_adddup2(&actions,out[1],1);
        // Only our native controller and LED script are spawned here. Keep
        // loader errors in the bounded, temporary supervisor log.
        posix_spawn_file_actions_addclose(&actions,in[1]);posix_spawn_file_actions_addclose(&actions,out[0]);
        int result=posix_spawnp(&pid,argv[0],&actions,nullptr,argv.data(),envp.data());
        posix_spawn_file_actions_destroy(&actions);close(in[0]);close(out[1]);
        if(result){close(in[1]);close(out[0]);pid=-1;throw std::runtime_error("child_spawn_failed");}
        input=in[1];output=out[0];
        fcntl(input,F_SETFL,fcntl(input,F_GETFL)|O_NONBLOCK);
    }
    bool send(const std::string &text){
        std::lock_guard<std::mutex> lock(write_mutex);
        return input>=0&&write(input,text.data(),text.size())==static_cast<ssize_t>(text.size());
    }
    void finish(){
        {std::lock_guard<std::mutex> lock(write_mutex);if(input>=0){close(input);input=-1;}}
        if(pid>0){
            for(int i=0;i<30;++i){if(waitpid(pid,nullptr,WNOHANG)==pid){pid=-1;break;}std::this_thread::sleep_for(std::chrono::milliseconds(50));}
            if(pid>0){kill(pid,SIGTERM);std::this_thread::sleep_for(std::chrono::milliseconds(100));
                if(waitpid(pid,nullptr,WNOHANG)!=pid){kill(pid,SIGKILL);waitpid(pid,nullptr,0);}pid=-1;}
        }
        if(output>=0){close(output);output=-1;}
    }
    ~Child(){finish();}
};

class StationAgent {
    Child child;std::thread reader,heartbeat;std::atomic<bool> ending{false};
    std::mutex mutex;std::condition_variable changed;
    std::atomic<bool> ready{false},focus{false},speaking{false};
    bool focus_required=true;
    std::function<void(const json &)> callback;
public:
    json metadata=json::object();std::mutex metadata_mutex;
    StationAgent(const fs::path &base,std::function<void(const json &)> handler):callback(std::move(handler)){
        const char *donor=std::getenv("ALISA_AGENT_DONOR");
        const char *disable_focus=std::getenv("ALISA_DISABLE_NATIVE_FOCUS");
        focus_required=!(disable_focus&&std::strcmp(disable_focus,"1")==0);
        child.start({donor&&*donor?donor:"aplay","--version","--alice-native-agent",(base/"native_agent.so").string()},
            {"LD_PRELOAD="+(base/"native_agent.so").string(),"ALICE_NATIVE_AGENT=1","ALICE_NATIVE_FOCUS="+std::string(focus_required?"1":"0"),"ALISA_COMMANDS_FILE="+(base/"commands.txt").string(),
             "ALICE_TTS_GATE_FILE="+(runtime_directory/"tts.lease").string(),"ALICE_NATIVE_LOCK="+(runtime_directory/"native-agent.lock").string(),
             "ALICE_FOCUS_TEMPLATE="+(runtime_directory/"focus-0.wav").string(),"ALICE_FOCUS_DIRECTORY="+runtime_directory.string()});
        int readfd=fcntl(child.output,F_DUPFD_CLOEXEC,3);
        reader=std::thread([this,readfd]{
            std::string line;char c;
            while(!ending&&read(readfd,&c,1)==1){
                if(c!='\n'){if(line.size()<8192)line+=c;else line.clear();continue;}
                try{
                    json value=json::parse(line);
                    if(value.value("event",std::string())=="ready")ready=true;
                    if(value.contains("focus_ready"))focus=value["focus_ready"].is_boolean()?
                        value["focus_ready"].get<bool>():value["focus_ready"].get<int>()!=0;
                    {std::lock_guard<std::mutex> lock(metadata_mutex);metadata=value;}
                    changed.notify_all();callback(value);
                }catch(const std::exception &){}line.clear();
            }
            close(readfd);
            if(!ending){ready=false;focus=false;changed.notify_all();callback({{"event","agent_exit"}});}
        });
        heartbeat=std::thread([this]{bool prior=false;unsigned int ticks=0;
            while(!ending&&!stopping){
                if(speaking!=prior){prior=speaking;child.send(prior?"SPEAKING\n":"QUIET\n");}
                if(ticks++%4==0)child.send("PING\n");
                std::this_thread::sleep_for(std::chrono::milliseconds(250));
            }
        });
    }
    bool connected()const{return ready;}
    bool acquire(){
        if(!ready)return false;focus=false;child.send("CHAT\n");
        if(!focus_required)return true;
        std::unique_lock<std::mutex> lock(mutex);
        return changed.wait_for(lock,std::chrono::seconds(5),[&]{return focus||!ready||stopping;})&&focus;
    }
    void idle(){speaking=false;child.send("IDLE\n");}
    void output_active(bool active){speaking=active;}
    ~StationAgent(){
        ending=true;child.send("IDLE\nSTOP\n");child.finish();
        if(reader.joinable())reader.join();if(heartbeat.joinable())heartbeat.join();
    }
};
