#include "voice_state.hpp"
#include <cassert>
#include <iostream>

using json=nlohmann::json;

int main(){
    VoiceServerState call;
    assert(call.can_start_audio(false));
    assert(!call.can_start_audio(true));
    assert(!call.receive("startup_telemetry",json::object()));
    assert(!call.receive("state_update",{{"new_state","listening"}}));
    assert(call.last_state=="listening");
    // Issue #14: goodbye can arrive while the opening cue is playing, before
    // any microphone/audio frames. Once it arrives, readiness stays forbidden.
    assert(call.receive("goodbye",{{"reason","error"},{"detail","internal_error"}}));
    assert(!call.can_start_audio(false));
    assert(call.error=="voice_server_internal_error");
    assert(call.close==json({{"reason","error"},{"detail","internal_error"}}));
    assert(call.receive("state_update",{{"new_state","listening"}}));
    assert(!call.can_start_audio(false));
    assert(call.receive("goodbye",{{"reason","normal"}}));
    assert(call.error=="voice_server_internal_error");

    for(auto mapping:{std::pair{"upstream_error","voice_server_upstream_error"},
                      std::pair{"server_error","voice_server_error"}}){
        VoiceServerState other;
        assert(other.receive("goodbye",{{"reason","error"},{"detail",mapping.first}}));
        assert(other.error==mapping.second);
    }
    VoiceServerState cap;
    assert(cap.receive("goodbye",{{"reason","cap_reached"},{"detail","internal_error"}}));
    assert(cap.error=="voice_daily_limit_reached");
    for(auto kind:{"error","session.error"}){
        VoiceServerState other;
        assert(other.receive(kind,json::object()));
        assert(other.error=="voice_session_ended");
        assert(!other.can_start_audio(false));
    }
    const std::string secret="cookie=private; account=hidden@example.test";
    for(const json &value:json::array({nullptr,false,42,1.5,json::array({"internal_error"}),
                                      json{{"detail","internal_error"}},secret,std::string(300000,'x')})){
        VoiceServerState other;
        assert(!other.receive("state_update",{{"new_state",value}}));
        assert(other.last_state=="unknown");
        // Incorrect reason types must still end the call, not get swallowed
        // by an exception and leave the caller waiting indefinitely.
        assert(other.receive("goodbye",{{"reason",value},{"detail",value}}));
        assert(other.close==json({{"reason","unknown"},{"detail","unknown"}}));
        assert(other.error=="voice_session_ended");
        assert(!other.can_start_audio(false));
        assert(other.close.dump().find(secret)==std::string::npos);
    }
    for(const json &payload:json::array({nullptr,false,42,"arbitrary",json::array()})){
        VoiceServerState other;
        assert(other.receive("goodbye",payload));
        assert(other.error=="voice_session_ended");
    }
    std::cout<<"voice close codes, terminal readiness and redaction: passed\n";
}
