#pragma once
#include "common.hpp"
#include "reasoning.hpp"
#include <algorithm>
#include <set>

class Settings {
    fs::path file;mutable std::mutex mutex;json doc;
    void save(){write_json(file,doc);}
    json &find(const std::string &id){
        for(auto &chat:doc["chats"])if(chat.value("id",std::string())==id)return chat;
        throw std::runtime_error("chat_not_found");
    }
public:
    explicit Settings(fs::path path):file(std::move(path)){
        if(fs::exists(file))doc=read_json(file);
        else {doc={{"version",1},{"voice","cove"},{"reasoning","instant"},{"selected",""},{"previous",""},{"chats",json::array()}};save();}
        if(doc.value("version",0)!=1||!doc["chats"].is_array()||doc["chats"].size()>64)throw std::runtime_error("invalid_settings");
        auto it=doc.find("reasoning");
        if(it==doc.end()||!it->is_string()||!valid_reasoning(it->get<std::string>()))doc["reasoning"]="instant";
    }
    json view()const{std::lock_guard<std::mutex> lock(mutex);return doc;}
    void voice(std::string value){
        static const std::set<std::string> allowed={"breeze","cove","ember","fathom","glimmer","juniper","maple","orbit","vale"};
        if(value=="arbor")value="fathom";if(value=="sol")value="glimmer";if(value=="spruce")value="orbit";
        if(!allowed.count(value))throw std::runtime_error("invalid_voice");
        std::lock_guard<std::mutex> lock(mutex);doc["voice"]=value;save();
    }
    void reasoning(const std::string &value){
        if(!valid_reasoning(value))throw std::runtime_error("invalid_reasoning");
        std::lock_guard<std::mutex> lock(mutex);doc["reasoning"]=value;save();
    }
    json select(const std::string &action,const std::string &id={}){
        std::lock_guard<std::mutex> lock(mutex);
        std::string selected=doc.value("selected",std::string()),target=selected;
        if(action=="new"||(selected.empty()&&action=="open")){
            if(doc["chats"].size()>=64)throw std::runtime_error("chat_limit");
            target=random_id();
            doc["chats"].push_back({{"id",target},{"title","Чат "+std::to_string(doc["chats"].size()+1)},
                {"conversation_id",""},{"parent_message_id",""},{"account_id",""},{"confirmed",false}});
        }else if(action=="previous"){
            target=doc.value("previous",std::string());if(target.empty())throw std::runtime_error("no_previous_chat");
        }else if(action=="select")target=id;
        auto entry=find(target);
        if(target!=selected){doc["previous"]=selected;doc["selected"]=target;save();}
        return entry;
    }
    void update(const std::string &id,const json &values,const std::string &account){
        std::lock_guard<std::mutex> lock(mutex);auto &entry=find(id);bool modified=false;
        for(const char *key:{"conversation_id","parent_message_id","voice_session_id"}){
            if(values.contains(key)&&values[key].is_string()&&values[key].get<std::string>().size()<128&&entry[key]!=values[key]){
                entry[key]=values[key];modified=true;
            }
        }
        if(!entry.value("conversation_id",std::string()).empty()){
            entry["confirmed"]=true;entry["account_id"]=account;
        }
        if(modified)save();
    }
    void rename(const std::string &id,const std::string &name){
        if(name.empty()||name.size()>120||name.find_first_of("\r\n\t")!=std::string::npos)throw std::runtime_error("invalid_title");
        std::lock_guard<std::mutex> lock(mutex);find(id)["title"]=name;save();
    }
};
