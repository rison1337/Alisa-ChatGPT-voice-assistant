#pragma once
#include "common.hpp"
#include "web_auth.hpp"
#include "alsa.hpp"
#include "playout.hpp"
#ifdef ALISA_USE_NETEQ
#include "neteq_c.h"
#else
#include "tempo.hpp"
#endif
#ifdef ALISA_TRANSPORT_STUDY
#include "transport_audit.hpp"
#endif
#include "effects.hpp"
#include "voice_state.hpp"
#include <rtc/rtc.hpp>
#include <opus.h>
#include <algorithm>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <functional>
#include <map>
#include <thread>
#include <sys/socket.h>
#include <sys/un.h>

struct CallOptions {
    std::string voice="cove",device="default",microphone;
    std::string conversation_id,parent_message_id,voice_session_id;
    std::string synthetic_text="Проверка экспериментального клиента. Ответь одним словом: готово.";
    std::string expected_reply="готов";
    bool probe=false;
#ifdef ALISA_TRANSPORT_STUDY
    bool normal_speed=false,correct_receiver_reports=false;
#endif
    std::function<void(const json &)> update;
    std::function<void()> connected;
};

class VoiceCall:public std::enable_shared_from_this<VoiceCall> {
    CallOptions options;
    std::shared_ptr<rtc::PeerConnection> pc;
    std::shared_ptr<rtc::Track> track;
    std::shared_ptr<rtc::DataChannel> dc;
    std::shared_ptr<rtc::RtpPacketizationConfig> rtp_config;
#ifdef ALISA_TRANSPORT_STUDY
    std::shared_ptr<TransportAudit> transport_audit;
#endif
    std::mutex mutex;
    std::mutex callback_mutex;bool accept_updates=true;
    std::condition_variable changed;
    std::atomic<bool> stop_requested{false},gathered{false},channel_open{false},failed{false};
    std::atomic<bool> assistant_seen{false},probe_reply_match{false};
    std::atomic<bool> is_speaking{false};
    std::atomic<bool> flush_output{false};
    std::map<std::string,unsigned int> event_types;
    std::string caption_role,probe_caption;
    VoiceServerState server_state;
    std::atomic<rtc::PeerConnection::State> peer_state{rtc::PeerConnection::State::New};
    std::atomic<rtc::PeerConnection::IceState> ice_state{rtc::PeerConnection::IceState::New};
    static const char *peer_name(rtc::PeerConnection::State state){
        using State=rtc::PeerConnection::State;
        switch(state){
            case State::New:return "new";case State::Connecting:return "connecting";
            case State::Connected:return "connected";case State::Disconnected:return "disconnected";
            case State::Failed:return "failed";case State::Closed:return "closed";
        }return "unknown";
    }
    static const char *ice_name(rtc::PeerConnection::IceState state){
        using State=rtc::PeerConnection::IceState;
        switch(state){
            case State::New:return "new";case State::Checking:return "checking";
            case State::Connected:return "connected";case State::Completed:return "completed";
            case State::Failed:return "failed";case State::Disconnected:return "disconnected";
            case State::Closed:return "closed";
        }return "unknown";
    }
#ifdef ALISA_USE_NETEQ
    RtpPlayout playout{true}; // Bounded header/timing telemetry, no audio queue.
    std::unique_ptr<AlisaNetEq,decltype(&alisa_neteq_destroy)> neteq{nullptr,alisa_neteq_destroy};
#else
    RtpPlayout playout;
#endif
    Clock::time_point quiet_until{};
    std::atomic<unsigned int> sent{0},decoded{0},nonzero{0};
    std::atomic<unsigned int> audible_blocks{0};
    std::atomic<unsigned int> loop_overruns{0},max_encode_us{0},max_write_us{0};
    std::atomic<unsigned int> interruptions{0},starvation_events{0},starvation_blocks{0},speech_gap_blocks{0},tempo_blocks{0};
    std::atomic<int> tempo_permille{1000},minimum_tempo_permille{1000},reserve_ms{0};
    std::atomic<unsigned int> max_render_us{0};
    std::atomic<unsigned int> alsa_underruns{0};
#ifdef ALISA_TRANSPORT_STUDY
    std::atomic<unsigned int> max_receive_lock_us{0},max_publish_us{0};
#endif
    std::atomic<bool> open_cue_played{false};
    std::atomic<unsigned int> microphone_frames{0},microphone_nonzero{0},muted_frames{0};
    std::map<int,unsigned int> sample_sizes;
    std::map<int,unsigned int> timestamp_steps;uint32_t last_timestamp=0;
    uint32_t source_ssrc=0;bool have_timestamp=false;
    json identity=json::object();
    json identity_shapes=json::object();
    void publish(const json &values){
        std::lock_guard<std::mutex> lock(callback_mutex);
        if(accept_updates&&options.update){
#ifdef ALISA_TRANSPORT_STUDY
            auto start=Clock::now();
#endif
            options.update(values);
#ifdef ALISA_TRANSPORT_STUDY
            max_publish_us=std::max(max_publish_us.load(),static_cast<unsigned int>(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-start).count()));
#endif
        }
    }
    void relay_text(const std::string &text){
        if(!dc||!dc->isOpen())throw std::runtime_error("voice_not_connected");
        json message={{"id",random_id()},{"author",{{"role","user"}}},
            {"create_time",std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count()},
            {"content",{{"content_type","text"},{"parts",json::array({text})}}},
            {"metadata",{{"serialization_metadata",{{"custom_symbol_offsets",json::array()}}}}},
            {"clientMetadata",{{"isOptimistic",true}}}};
        dc->send(json{{"type","data_message"},{"data",json{{"type","relay_message"},{"payload",{{"type","relay_message"},{"message",message}}}}.dump()}}.dump());
    }
    void data_message(const std::string &raw){
        if(raw.size()>256*1024)return;
        try{
            json obj=json::parse(raw);
            if(obj.value("type",std::string())=="data_message"&&obj.contains("data"))obj=json::parse(obj["data"].get<std::string>());
            const std::string kind=obj.value("type",std::string("unknown"));
            json payload=obj.contains("payload")&&obj["payload"].is_object()?obj["payload"]:obj;
            json updates=json::object();
            {
                std::lock_guard<std::mutex> lock(mutex);
                if(event_types.size()<64)++event_types[kind];
                if(server_state.receive(kind,payload)){
                    failed=true;changed.notify_all();return;
                }
                if(options.probe){
                    std::function<void(const json &,const std::string &,int)> scan;
                    scan=[&](const json &node,const std::string &path,int depth){
                        if(depth>5||!node.is_object())return;
                        for(auto &[key,value]:node.items()){
                            if(key.find("conversation")!=std::string::npos||key.find("parent_message")!=std::string::npos)
                                identity_shapes[kind+path+"/"+key]={{"type",value.type_name()},{"length",value.is_string()?value.get<std::string>().size():0}};
                            if(value.is_object())scan(value,path+"/"+key,depth+1);
                        }
                    };scan(obj,"",0);
                }
                if(kind=="startup_telemetry"||kind=="conversation_update"){
                    if(payload.contains("conversation_id")&&!options.conversation_id.empty()&&
                       payload["conversation_id"]!=options.conversation_id){failed=true;return;}
                    for(const char *key:{"conversation_id","parent_message_id","voice_session_id"})
                        if(payload.contains(key)&&payload[key].is_string()&&payload[key].get<std::string>().size()<128){
                            identity[key]=payload[key];updates[key]=payload[key];
                        }
                }
                if(kind=="chat_message_delta"&&payload.contains("delta")){
                    auto delta=payload["delta"];
                    if(delta.contains("v")&&delta["v"].is_object()&&delta["v"].contains("conversation_id")&&delta["v"]["conversation_id"].is_string()){
                        auto value=delta["v"]["conversation_id"];
                        if(!options.conversation_id.empty()&&value!=options.conversation_id){failed=true;return;}
                        identity["conversation_id"]=value;updates["conversation_id"]=value;
                    }
                    if(delta.contains("v")&&delta["v"].is_object()&&delta["v"].contains("message")){
                        auto msg=delta["v"]["message"];
                        if(msg.contains("author"))caption_role=msg["author"].value("role",std::string());
                        if(caption_role=="assistant")assistant_seen=true;
                        if(msg.contains("id")&&(caption_role=="assistant"||caption_role=="user")){
                            identity["parent_message_id"]=msg["id"];updates["parent_message_id"]=msg["id"];
                        }
                        if(options.probe&&caption_role=="assistant"&&msg.contains("content")){
                            auto content=msg["content"];
                            if(content.contains("parts")&&content["parts"].is_array())
                                for(auto &part:content["parts"])if(probe_caption.size()<2048){
                                    if(part.is_string())probe_caption+=part.get<std::string>();
                                    else if(part.is_object())for(const char *key:{"text","content","transcript"})
                                        if(part.contains(key)&&part[key].is_string()){probe_caption+=part[key].get<std::string>();break;}
                                }
                        }
                    }else if(options.probe&&caption_role=="assistant"&&delta.contains("v")&&delta["v"].is_array()){
                        for(auto &operation:delta["v"]){
                            if(!operation.is_object()||operation.value("o",std::string())!="append")continue;
                            auto path=operation.value("p",std::string());
                            if(path.rfind("/message/content/parts/",0)==0&&path.size()>5&&path.substr(path.size()-5)=="/text"&&
                               operation.contains("v")&&operation["v"].is_string()&&probe_caption.size()<2048)
                                probe_caption+=operation["v"].get<std::string>();
                        }
                    }else if(options.probe&&caption_role=="assistant"&&delta.contains("v")&&delta["v"].is_string()&&probe_caption.size()<2048)
                        probe_caption+=delta["v"].get<std::string>();
                    probe_reply_match=probe_caption.find(options.expected_reply)!=std::string::npos;
                }
                if(kind.find("audio_transcript")!=std::string::npos)assistant_seen=true;
            }
            if(!updates.empty())publish(updates);
        }catch(const std::exception &){/* Unknown forward-compatible event; never log contents. */}
    }
    void receive_rtp(const rtc::binary &data){
        if(data.size()<12)return;
        const auto *p=reinterpret_cast<const unsigned char *>(data.data());
        if((p[0]>>6)!=2||(p[1]>=192&&p[1]<=223))return;
        size_t offset=12+4*(p[0]&15),end=data.size();
        if(offset>end)return;
        if(p[0]&16){if(offset+4>end)return;size_t length=(p[offset+2]*256u+p[offset+3])*4u;offset+=4+length;if(offset>end)return;}
        if(p[0]&32){if(!p[end-1]||p[end-1]>end-offset)return;end-=p[end-1];}
        if(end<=offset||end-offset>4096)return;
        uint32_t timestamp=(uint32_t(p[4])<<24)|(uint32_t(p[5])<<16)|(uint32_t(p[6])<<8)|p[7];
        uint32_t ssrc=(uint32_t(p[8])<<24)|(uint32_t(p[9])<<16)|(uint32_t(p[10])<<8)|p[11];
        auto now=Clock::now();
        std::lock_guard<std::mutex> lock(mutex);
#ifdef ALISA_TRANSPORT_STUDY
        max_receive_lock_us=std::max(max_receive_lock_us.load(),static_cast<unsigned int>(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-now).count()));
#endif
        if(now<quiet_until)return;
        if(have_timestamp&&source_ssrc==ssrc&&timestamp_steps.size()<32)++timestamp_steps[static_cast<int32_t>(timestamp-last_timestamp)];
        last_timestamp=timestamp;source_ssrc=ssrc;have_timestamp=true;
        int duration=opus_packet_get_nb_samples(p+offset,static_cast<int>(end-offset),48000);
#ifdef ALISA_USE_NETEQ
        if(duration<=0||duration>5760)return;
        alisa_neteq_insert(neteq.get(),uint16_t(p[2]*256u+p[3]),timestamp,ssrc,p+offset,end-offset);
        ++sample_sizes[duration];
#endif
        playout.push(timestamp,ssrc,std::vector<unsigned char>(p+offset,p+end),duration,
            std::chrono::duration_cast<std::chrono::microseconds>(now.time_since_epoch()).count());
    }
    void init(){
        auto weak=weak_from_this();
        rtc::Configuration config;
        // In VPN mode use the TUN's IPv4 socket. A dual-stack wildcard also
        // gathers IPv6 STUN candidates when the tunnel has no IPv6 egress,
        // delaying completion beyond the signalling deadline.
        if(const char *address=getenv("ALISA_ICE_BIND_ADDRESS"))
            if(*address)config.bindAddress=address;
        // Optional workaround from the Mini 1 port (ElectWRT, PR #6).
        // Preserve default STUN; bypass only when explicitly configured for the network.
        const char *disable_stun=getenv("ALISA_DISABLE_STUN");
        if(!(disable_stun&&std::string(disable_stun)=="1"))
            config.iceServers.emplace_back("stun:stun.l.google.com:19302");
        config.disableAutoNegotiation=true;config.mtu=1200;config.maxMessageSize=256*1024;
        pc=std::make_shared<rtc::PeerConnection>(config);
        pc->onGatheringStateChange([weak](rtc::PeerConnection::GatheringState state){
            if(auto self=weak.lock())if(state==rtc::PeerConnection::GatheringState::Complete){self->gathered=true;self->changed.notify_all();}
        });
        pc->onStateChange([weak](rtc::PeerConnection::State state){
            if(auto self=weak.lock()){
                self->peer_state=state;
                if(state==rtc::PeerConnection::State::Failed||state==rtc::PeerConnection::State::Closed){self->failed=true;self->changed.notify_all();}
            }
        });
        pc->onIceStateChange([weak](rtc::PeerConnection::IceState state){
            if(auto self=weak.lock())self->ice_state=state;
        });
        rtc::Description::Audio audio("0",rtc::Description::Direction::SendRecv);
        audio.addOpusCodec(111,"minptime=10;useinbandfec=1");audio.addAttribute("ptime:20");
        uint32_t ssrc=0;RAND_bytes(reinterpret_cast<unsigned char *>(&ssrc),sizeof(ssrc));
        audio.addSSRC(ssrc,"alisa","alisa","audio0");
        track=pc->addTrack(audio);
        rtp_config=std::make_shared<rtc::RtpPacketizationConfig>(ssrc,"alisa",111,48000);
        auto packetizer=std::make_shared<rtc::OpusRtpPacketizer>(rtp_config);
        packetizer->addToChain(std::make_shared<rtc::RtcpSrReporter>(rtp_config));
#ifdef ALISA_TRANSPORT_STUDY
        transport_audit=std::make_shared<TransportAudit>(ssrc,options.correct_receiver_reports);
        packetizer->addToChain(transport_audit);
#else
        packetizer->addToChain(std::make_shared<rtc::RtcpReceivingSession>());
#endif
        track->setMediaHandler(packetizer);
        track->onMessage([weak](rtc::binary data){if(auto self=weak.lock())self->receive_rtp(data);},nullptr);
        rtc::DataChannelInit dc_config;dc_config.negotiated=true;dc_config.id=0;
        dc=pc->createDataChannel("oai-events",dc_config);
        dc->onOpen([weak]{if(auto self=weak.lock()){self->channel_open=true;self->changed.notify_all();}});
        dc->onMessage(nullptr,[weak](std::string raw){if(auto self=weak.lock())self->data_message(raw);});
        pc->setLocalDescription();
    }
public:
    explicit VoiceCall(CallOptions settings):options(std::move(settings)){
#ifdef ALISA_USE_NETEQ
        neteq.reset(alisa_neteq_create(80,600,-1));if(!neteq)throw std::runtime_error("neteq_init_failed");
#endif
    }
    void stop(){stop_requested=true;changed.notify_all();std::lock_guard<std::mutex> lock(callback_mutex);accept_updates=false;}
    bool speaking()const{return is_speaking;}
    void check(){relay_text("Проверка автономного клиента на колонке. Ответь только: автономный режим готов.");}
    void interrupt(){
        ++interruptions;
        flush_output=true;
        {std::lock_guard<std::mutex> lock(mutex);quiet_until=Clock::now()+std::chrono::milliseconds(150);}
        if(dc&&dc->isOpen())dc->send(json{{"type","data_message"},{"data",json{{"type","action_request"},{"payload",{{"action","stop_speaking"}}}}.dump()}}.dump());
    }
    json stats(){
        std::lock_guard<std::mutex> lock(mutex);
        json result={{"received",playout.received},{"late",playout.late},{"concealed",playout.concealed},
            {"sent",sent.load()},{"decoded",decoded.load()},{"nonzero",nonzero.load()},
            {"audible_blocks",audible_blocks.load()},
            {"microphone_frames",microphone_frames.load()},{"microphone_nonzero",microphone_nonzero.load()},{"muted_frames",muted_frames.load()},
            {"channel_open",channel_open.load()},{"assistant_text_seen",assistant_seen.load()},
            {"probe_reply_match",probe_reply_match.load()},{"event_types",event_types},
            {"sample_sizes",sample_sizes},{"timestamp_steps",timestamp_steps},{"ssrc_changes",playout.source_changes},
            {"clock_resets",playout.resets},{"idle_restarts",playout.idle_restarts},
            {"rebuffers",playout.rebuffers},
            {"interruptions",interruptions.load()},{"starvation_events",starvation_events.load()},
            {"starvation_blocks",starvation_blocks.load()},{"speech_gap_blocks",speech_gap_blocks.load()},
            {"tempo_blocks",tempo_blocks.load()},{"tempo_permille",tempo_permille.load()},
            {"minimum_tempo_permille",minimum_tempo_permille.load()},{"reserve_ms",reserve_ms.load()},
            {"max_render_us",max_render_us.load()},{"alsa_underruns",alsa_underruns.load()},
            {"audio_pipeline_revision",4},
            {"queue_packets",playout.depth()},{"queue_high_water",playout.high_water},{"queue_overflow",playout.overflow},
            {"max_arrival_gap_ms",playout.maximum_gap_us/1000},{"max_ahead_ms",playout.maximum_ahead_samples/48},
            {"jitter_ms",static_cast<int>(playout.jitter_us/1000)},{"prefill_ms",playout.target_ms()},
            {"loop_overruns",loop_overruns.load()},{"max_encode_us",max_encode_us.load()},{"max_write_us",max_write_us.load()},
            {"open_cue_played",open_cue_played.load()},
            {"identity_shapes",identity_shapes},{"rss_kib",rss_kib()}};
        result["server_close"]=server_state.close;result["last_server_state"]=server_state.last_state;
        result["connection"]={{"ice_state",ice_name(ice_state.load())},{"peer_state",peer_name(peer_state.load())},
            {"gathered",gathered.load()},{"data_channel_open",channel_open.load()}};
        if(options.probe)result["rtp_timing"]=playout.timing();
#ifdef ALISA_USE_NETEQ
        AlisaNetEqStats n{};alisa_neteq_stats(neteq.get(),&n);
        result["audio_engine"]="WebRTC NetEq";result["audio_pipeline_revision"]=5;
        result["neteq"]={{"packets",n.packets},{"insert_errors",n.insert_errors},{"render_errors",n.render_errors},
            {"total_samples",n.total_samples},{"concealed_samples",n.concealed_samples},{"silent_concealed_samples",n.silent_concealed_samples},
            {"concealment_events",n.concealment_events},{"inserted_samples",n.inserted_samples},{"removed_samples",n.removed_samples},
            {"discarded_packets",n.discarded_packets},{"buffer_flushes",n.buffer_flushes},
            {"current_delay_ms",n.current_delay_ms},{"target_delay_ms",n.target_delay_ms},
            {"mean_delay_ms",n.emitted_samples?double(n.delay_sum_ms)/n.emitted_samples:0.0},
            {"interruption_count",n.interruption_count},{"interruption_duration_ms",n.interruption_duration_ms}};
        for(const char *key:{"late","concealed","rebuffers","queue_packets","queue_high_water","queue_overflow","prefill_ms","max_ahead_ms", "tempo_blocks","tempo_permille","minimum_tempo_permille","starvation_events","starvation_blocks","speech_gap_blocks"})result.erase(key);
        result["render_failure_events"]=starvation_events.load();result["render_failure_blocks"]=starvation_blocks.load();
        result["rendered_blocks"]=decoded.load();result.erase("decoded");
        result["packet_sample_sizes"]=sample_sizes;result.erase("sample_sizes");
#else
        result["audio_engine"]="Sonic PI";
#endif
#ifdef ALISA_TRANSPORT_STUDY
        result["normal_speed"]=options.normal_speed;result["max_receive_lock_us"]=max_receive_lock_us.load();
        result["max_publish_us"]=max_publish_us.load();
        if(options.probe&&transport_audit)result["transport"]=transport_audit->stats();
#endif
        return result;
    }
    json run(const json &auth){
        init();
        {
            std::unique_lock<std::mutex> lock(mutex);
            if(!changed.wait_for(lock,std::chrono::seconds(12),[&]{return gathered||failed||stop_requested||stopping;}))throw std::runtime_error("ice_gather_timeout");
        }
        if(failed||stop_requested||stopping){std::lock_guard<std::mutex> lock(mutex);
            throw std::runtime_error(server_state.error.empty()?"call_cancelled":server_state.error);}
        std::string sid=options.voice_session_id.empty()?random_id():options.voice_session_id;
        std::transform(sid.begin(),sid.end(),sid.begin(),[](unsigned char c){return std::toupper(c);});
        {std::lock_guard<std::mutex> lock(mutex);identity["voice_session_id"]=sid;}
        publish({{"voice_session_id",sid}});
        json session={{"backend_reasoning_effort","instant"},{"language_code","ru-RU"},
            {"requested_default_model",""},{"voice",options.voice},{"voice_session_id",sid},
            {"voice_status_request_id",sid},{"timezone_offset_min",-300},{"timezone","Asia/Yekaterinburg"},
            {"voice_mode","wingman"},{"model_slug",""},{"model_slug_advanced",""},
            {"client_tools",json::array()},{"history_and_training_disabled",false},
            {"conversation_mode",{{"kind","primary_assistant"}}},{"enable_message_streaming",true}};
        if(!options.conversation_id.empty())session["conversation_id"]=options.conversation_id;
        if(!options.parent_message_id.empty())session["parent_message_id"]=options.parent_message_id;
        auto answer=exchange_sdp(auth,std::string(*pc->localDescription()),session,&stop_requested);
        pc->setRemoteDescription(rtc::Description(answer,"answer"));
        {
            std::unique_lock<std::mutex> lock(mutex);
            if(!changed.wait_for(lock,std::chrono::seconds(20),[&]{return channel_open||failed||stop_requested||stopping;}))throw std::runtime_error("webrtc_connect_timeout");
        }
        if(failed||stop_requested||stopping){std::lock_guard<std::mutex> lock(mutex);
            throw std::runtime_error(server_state.error.empty()?"call_cancelled":server_state.error);}
        int error=0;
        std::unique_ptr<OpusEncoder,decltype(&opus_encoder_destroy)> encoder(
            opus_encoder_create(16000,1,OPUS_APPLICATION_VOIP,&error),opus_encoder_destroy);
        if(!encoder||error)throw std::runtime_error("codec_init_failed");
#ifndef ALISA_USE_NETEQ
        std::unique_ptr<OpusDecoder,decltype(&opus_decoder_destroy)> decoder(
            opus_decoder_create(48000,2,&error),opus_decoder_destroy);
        if(!decoder||error)throw std::runtime_error("codec_init_failed");
#endif
        opus_encoder_ctl(encoder.get(),OPUS_SET_BITRATE(32000));opus_encoder_ctl(encoder.get(),OPUS_SET_COMPLEXITY(4));
        opus_encoder_ctl(encoder.get(),OPUS_SET_INBAND_FEC(1));opus_encoder_ctl(encoder.get(),OPUS_SET_PACKET_LOSS_PERC(10));
        SocketOwner input_owner;
        if(!options.probe&&!options.microphone.empty()){
            input_owner.fd=socket(AF_UNIX,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
            sockaddr_un addr{};addr.sun_family=AF_UNIX;
            if(input_owner.fd<0||options.microphone.size()>=sizeof(addr.sun_path))throw std::runtime_error("microphone_setup_failed");
            input_owner.path=options.microphone;
            std::memcpy(addr.sun_path,options.microphone.c_str(),options.microphone.size()+1);
            unlink(addr.sun_path);
            if(bind(input_owner.fd,reinterpret_cast<sockaddr *>(&addr),sizeof(addr))<0)throw std::runtime_error("microphone_bind_failed");
        }
        PcmOutput output(options.device.c_str());
        if(!options.probe){
            int block=0;
            for(;block<10&&!failed&&!stop_requested&&!stopping;++block){short samples[1920];mode_cue_block(true,block,samples);output.write(samples,960);}
            open_cue_played=block==10;
        }
        {
            // Serialize readiness with goodbye. A close received during the
            // opening cue must not announce an active call or start the mic.
            std::lock_guard<std::mutex> lock(mutex);
            if(!server_state.can_start_audio(failed||stop_requested||stopping))
                throw std::runtime_error(server_state.error.empty()?"call_cancelled":server_state.error);
            if(options.connected)options.connected();
        }
        auto start=Clock::now(),tick=start;
        bool asked=false;bool starving=false;
#ifndef ALISA_USE_NETEQ
        SpeechTempo tempo;bool tail_flushed=false;
#endif
        std::deque<short> microphone;
        auto last_speech=Clock::time_point::min(),mute_check=Clock::time_point::min();float gain=1.995262f;unsigned int silence=0;
        bool microphone_allowed=false;
        while(!stop_requested&&!stopping&&!failed){
            if(flush_output.exchange(false)){
                std::lock_guard<std::mutex> lock(mutex);playout.clear();starving=false;
#ifdef ALISA_USE_NETEQ
                alisa_neteq_flush(neteq.get());
#else
                tempo.clear();tail_flushed=false;opus_decoder_ctl(decoder.get(),OPUS_RESET_STATE);
#endif
                is_speaking=false;last_speech=Clock::time_point::min();
            }
            if(Clock::now()-tick>std::chrono::milliseconds(30)){tick=Clock::now();++loop_overruns;}
            short in[320]{};unsigned char encoded[400];
            if(input_owner.fd>=0){
                short block[160];
                for(int i=0;i<16;++i){auto count=recv(input_owner.fd,block,sizeof(block),MSG_DONTWAIT);
                    if(count<=0)break;
                    ++microphone_frames;
                    for(int j=0;j<count/2;++j)microphone.push_back(block[j]);
                }
                while(microphone.size()>640)microphone.pop_front();
                for(int i=0;i<320&&!microphone.empty();++i){in[i]=microphone.front();microphone.pop_front();}
                if(mute_check==Clock::time_point::min()||Clock::now()-mute_check>std::chrono::milliseconds(100)){
                    mute_check=Clock::now();microphone_allowed=false;
                    std::ifstream gpio("/sys/kernel/debug/gpio");std::string line;
                    while(std::getline(gpio,line))if(line.find("|mute-switch ")!=std::string::npos){
                        std::istringstream words(line);std::string word;
                        while(words>>word)if(word=="hi")microphone_allowed=true;
                        break;
                    }
                }
                if(!microphone_allowed){std::fill(std::begin(in),std::end(in),0);++muted_frames;}
                else for(short value:in)if(value){++microphone_nonzero;break;}
            }
            auto encode_start=Clock::now();
            int size=opus_encode(encoder.get(),in,320,encoded,sizeof(encoded));
            unsigned int encode_us=std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-encode_start).count();
            max_encode_us=std::max(max_encode_us.load(),encode_us);
            if(size>0&&track->isOpen()){
                rtp_config->timestamp+=960;
                track->send(reinterpret_cast<const rtc::byte *>(encoded),static_cast<size_t>(size));++sent;
            }
            if(options.probe&&!asked&&Clock::now()-start>std::chrono::milliseconds(500)&&dc->isOpen()){
                relay_text(options.synthetic_text);asked=true;
            }
            short samples[960*2]{};
#ifdef ALISA_USE_NETEQ
            auto render_start=Clock::now();int rendered=0;
            {
                std::lock_guard<std::mutex> lock(mutex);
                for(int half=0;half<2;++half)if(alisa_neteq_render(neteq.get(),samples+half*960)==480)rendered+=480;
                AlisaNetEqStats n{};alisa_neteq_stats(neteq.get(),&n);reserve_ms=n.current_delay_ms;
            }
            if(rendered<960){if(!starving)++starvation_events;++starvation_blocks;starving=true;}
            else {starving=false;++decoded;}
            max_render_us=std::max(max_render_us.load(),static_cast<unsigned int>(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-render_start).count()));
#else
            auto render_start=Clock::now();int queued_ms;bool source_idle;
            {
                std::lock_guard<std::mutex> lock(mutex);
                queued_ms=playout.buffered_ms();source_idle=playout.idle(std::chrono::duration_cast<std::chrono::microseconds>(render_start.time_since_epoch()).count());
            }
            reserve_ms=queued_ms+tempo.available()/48;
#ifdef ALISA_TRANSPORT_STUDY
            int speed=options.normal_speed?1000:static_cast<int>(tempo.update(reserve_ms.load())*1000);
#else
            int speed=static_cast<int>(tempo.update(reserve_ms.load())*1000);
#endif
            tempo_permille=speed;minimum_tempo_permille=std::min(minimum_tempo_permille.load(),speed);
            if(speed<999)++tempo_blocks;
            for(int batch=0;batch<6&&tempo.available()<960;++batch){
                std::optional<RtpPlayout::Frame> frame;
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    frame=playout.pop(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now().time_since_epoch()).count());
                }
                if(!frame)break;
                if(frame->reset){opus_decoder_ctl(decoder.get(),OPUS_RESET_STATE);tempo.clear();}
                auto &compressed=frame->bytes;
                short decoded_pcm[5760*2];
                int count=opus_decode(decoder.get(),compressed.empty()?nullptr:compressed.data(),static_cast<int>(compressed.size()),decoded_pcm,compressed.empty()?960:5760,0);
                if(count>0){
                    {std::lock_guard<std::mutex> lock(mutex);++sample_sizes[count];}
                    tempo.write(decoded_pcm,count);tail_flushed=false;++decoded;
                }
            }
            if(source_idle&&!tail_flushed){tempo.flush();tail_flushed=true;}
            // Do not consume a partial block and pad its end inside a word.
            int rendered=(tempo.available()>=960||source_idle)?tempo.read(samples,960):0;
            if(rendered<960&&decoded>0&&!source_idle){
                if(!starving)++starvation_events;
                ++starvation_blocks;starving=true;
                if(last_speech!=Clock::time_point::min()&&Clock::now()-last_speech<std::chrono::milliseconds(200))++speech_gap_blocks;
            }else starving=false;
            max_render_us=std::max(max_render_us.load(),static_cast<unsigned int>(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-render_start).count()));
#endif
            bool sound=false;
            for(short value:samples)sound|=value!=0;
            if(sound)++nonzero;
            int peak=0;long long power=0;
            for(short value:samples){peak=std::max(peak,std::abs(int(value)));power+=int(value)*int(value);}
            if(power>1920LL*128*128){last_speech=Clock::now();++audible_blocks;}
            is_speaking=last_speech!=Clock::time_point::min()&&Clock::now()-last_speech<std::chrono::milliseconds(250);
            silence=peak?0:silence+1;if(silence>=6)gain=1.995262f;
            float wanted=peak?std::min(1.995262f,31128.0f/peak):1.995262f;
            gain=wanted<gain?wanted:gain+(wanted-gain)*0.08f;
            for(auto &value:samples)value=static_cast<short>(std::clamp(int(value*gain),-31128,31128));
            auto write_start=Clock::now();output.write(samples,960);
            alsa_underruns=output.underruns;
            unsigned int write_us=std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-write_start).count();
            max_write_us=std::max(max_write_us.load(),write_us);
            if(options.probe){
                // Wait beyond the complete reply so the server can persist the
                // final parent, rather than closing on the first audio chunk.
                if(audible_blocks>8&&assistant_seen&&Clock::now()-start>std::chrono::seconds(5)&&
                   last_speech!=Clock::time_point::min()&&Clock::now()-last_speech>std::chrono::seconds(2))break;
                if(Clock::now()-start>std::chrono::seconds(30))break;
            }
            tick+=std::chrono::milliseconds(20);std::this_thread::sleep_until(tick);
        }
        json result=stats();result["underruns"]=output.underruns;
        result["success"]=!failed&&audible_blocks>8&&assistant_seen;
        {std::lock_guard<std::mutex> lock(mutex);result["identity"]=identity;
            if(!server_state.error.empty())result["error"]=server_state.error;
            if(options.probe)result["synthetic_caption"]=probe_caption;}
        stop();pc->close();return result;
    }
    ~VoiceCall(){stop();if(pc)pc->close();}
};
