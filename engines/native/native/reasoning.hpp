#pragma once
#include <nlohmann/json.hpp>
#include <cctype>
#include <stdexcept>
#include <string>

inline bool valid_reasoning(const std::string &s){return s=="instant"||s=="high";}

struct ReasoningRejected : std::runtime_error {
    ReasoningRejected():std::runtime_error("voice_reasoning_rejected"){}
};

// Detect validation of the effort field, never return provider text.
inline bool reasoning_validation_error(const nlohmann::json &node,int depth=0){
    if(depth>5)return false;
    constexpr const char *field="backend_reasoning_effort";
    if(node.is_string()){
        auto text=node.get<std::string>();
        if(text.size()>1024)return false;
        for(auto &c:text)c=static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        return text.find(field)!=std::string::npos&&(text.find("invalid")!=std::string::npos||
            text.find("unsupported")!=std::string::npos||text.find("not supported")!=std::string::npos||
            text.find("not allowed")!=std::string::npos||text.find("must be")!=std::string::npos);
    }
    if(node.is_array()){
        for(const auto &entry:node)if(reasoning_validation_error(entry,depth+1))return true;
    }else if(node.is_object()){
        auto is=[&](const char *key,const char *value){auto it=node.find(key);return it!=node.end()&&*it==value;};
        bool matching=is("param",field)||is("field",field)||is("parameter",field);
        auto loc=node.find("loc");
        if(loc!=node.end()&&loc->is_array()&&!loc->empty()&&loc->back()==field)matching=true;
        if(matching){
            for(const char *value:{"invalid_value","invalid_enum_value","unsupported_value","unsupported_parameter",
                    "invalid_parameter","invalid_request_error","literal_error","enum","value_error"})
                if(is("code",value)||is("type",value))return true;
        }
        for(const char *key:{"error","detail","message","msg"}){
            auto it=node.find(key);if(it!=node.end()&&reasoning_validation_error(*it,depth+1))return true;
        }
    }
    return false;
}

template<class Attempt,class Current,class Fallback>
auto with_reasoning_fallback(const std::string &effort,Attempt attempt,Current current,Fallback fallback){
    try{return attempt(effort);}catch(const ReasoningRejected &){
        if(effort!="high"||!current())throw;
        fallback();
    }
    return attempt("instant");
}
