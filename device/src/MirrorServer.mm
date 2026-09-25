// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import "MirrorServer.h"
#import "MirrorConnection.h"
#import "MirrorCapture.h"
#import "HEVCEncoder.h"
#import "MirrorInput.h"
#import "FBSOrientationObserver.h"
#include "MirrorTimingJSON.h"
#include <map>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
using namespace ipbm;
static uint64_t nowNs() { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
struct PendingStill { uint32_t request; uint64_t started; };

@implementation MirrorServer {
    uint16_t _port;
    int _fps,_bitrate,_listener;
    dispatch_source_t _acceptSource,_timer;
    NSMutableDictionary<NSNumber*,MirrorConnection*> *_peers;
    std::map<uint64_t,PendingStill> _stills;
    std::map<uint64_t,WriteProgress> _progress;
    uint64_t _nextPeer,_videoPeer,_buttonPeer,_buttonSerial;
    uint32_t _buttonRequest;
    Geometry _geometry;
    InputLease _lease;
    MirrorCapture *_capture;
    HEVCEncoder *_encoder;
    MirrorInput *_input;
    FBSOrientationObserver *_orientation;
    BOOL _forceKeyframe,_awaitKeyframe,_forceCapture;
    uint64_t _lastSubmit,_retryEncoderAt,_encoded,_encodedBytes,_errors,_skipped;
    uint64_t _latencySum,_latencyMax;
    RecentTimings<InputTiming> _inputTimings;
    RecentTimings<FrameTiming> _frameTimings;
    uint64_t _inputCount,_afterIdleCount;
}
- (instancetype)initWithPort:(uint16_t)port fps:(int)fps bitrate:(int)bitrate {
    if((self=[super init])) {
        _port=port;_fps=fps;_bitrate=bitrate;_listener=-1;
        _peers=[NSMutableDictionary dictionary];
    } return self;
}
- (BOOL)start {
    __weak MirrorServer *weak=self;
    _capture=[[MirrorCapture alloc] initWithFrameRate:_fps tick:^{ [weak captureTick]; }];
    if(!_capture) { NSLog(@"Capture initialization failed"); return NO; }
    _geometry.width=_capture.width;_geometry.height=_capture.height;
    _input=[[MirrorInput alloc] initWithWidth:_geometry.width height:_geometry.height];
    if(!_input) return NO;
    _encoder=[[HEVCEncoder alloc] initWithWidth:_geometry.width height:_geometry.height fps:_fps bitrate:_bitrate
        output:^(std::shared_ptr<MirrorEncoded> frame,int status) { [weak encoded:frame status:status]; }];
    _orientation=[[FBSOrientationObserver alloc] init];
    if(!_orientation) return NO;
    [self updateOrientation:_orientation.activeInterfaceOrientation];
    [_orientation setHandler:^(FBSOrientationUpdate *update) {
        UIInterfaceOrientation value=update.orientation;
        dispatch_async(dispatch_get_main_queue(),^{ [weak updateOrientation:value]; });
    }];
    _listener=socket(AF_INET,SOCK_STREAM,0);
    if(_listener<0) return NO;
    int yes=1; setsockopt(_listener,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));
    fcntl(_listener,F_SETFL,O_NONBLOCK);
    sockaddr_in address={};address.sin_len=sizeof(address);address.sin_family=AF_INET;
    address.sin_port=htons(_port);address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    if(bind(_listener,(sockaddr*)&address,sizeof(address)) || listen(_listener,4)) {
        NSLog(@"Loopback listen failed: %s",strerror(errno)); ::close(_listener);_listener=-1;return NO;
    }
    _acceptSource=dispatch_source_create(DISPATCH_SOURCE_TYPE_READ,_listener,0,dispatch_get_main_queue());
    dispatch_source_set_event_handler(_acceptSource,^{ [weak acceptPeers]; });
    int fd=_listener;dispatch_source_set_cancel_handler(_acceptSource,^{ ::close(fd); });
    dispatch_resume(_acceptSource);
    _timer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,dispatch_get_main_queue());
    dispatch_source_set_timer(_timer,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),NSEC_PER_SEC,10000000);
    dispatch_source_set_event_handler(_timer,^{ [weak maintain]; });dispatch_resume(_timer);
    NSLog(@"IPBM v1 listening on 127.0.0.1:%u; portrait %ux%u; HEVC %d fps %d bps",
        _port,_geometry.width,_geometry.height,_fps,_bitrate);return YES;
}
- (void)acceptPeers {
    for(int i=0;i<8;++i) {
        int fd=accept(_listener,nullptr,nullptr);if(fd<0) return;
        if(_peers.count>=MaxConnections) { ::close(fd);continue; }
        __weak MirrorServer *weak=self;
        MirrorConnection *peer=[[MirrorConnection alloc] initWithSocket:fd identity:++_nextPeer
            command:^(MirrorConnection *p,Command command) { [weak handle:command peer:p]; }
            closed:^(MirrorConnection *p) { [weak disconnected:p]; }];
        _peers[@(peer.identity)]=peer;[peer start];
        Bytes hello={'I','P','B','M',0,1,0,7};auto geometry=_geometry.bytes();
        hello.insert(hello.end(),geometry.begin(),geometry.end());
        [peer send:Hello request:0 payload:hello];
    }
}
- (void)ack:(MirrorConnection*)peer request:(uint32_t)request { [peer send:Ack request:request payload:{}]; }
- (void)error:(MirrorConnection*)peer request:(uint32_t)request code:(uint32_t)code text:(const char*)text {
    Bytes b;put32(b,code);b.insert(b.end(),text,text+strlen(text));[peer send:Error request:request payload:b];
}
- (void)geometryTo:(MirrorConnection*)peer request:(uint32_t)request {
    [peer send:GeometryMessage request:request payload:_geometry.bytes()];
}
- (void)changedGeometry {
    _geometry.changed();[self releaseHeld];_forceKeyframe=YES;_awaitKeyframe=YES;_forceCapture=YES;
    for(MirrorConnection *peer in _peers.allValues) [self geometryTo:peer request:0];
}
- (void)updateOrientation:(UIInterfaceOrientation)orientation {
    uint32_t turns=0;
    switch(orientation) {
    case UIInterfaceOrientationLandscapeLeft:turns=1;break;
    case UIInterfaceOrientationPortraitUpsideDown:turns=2;break;
    case UIInterfaceOrientationLandscapeRight:turns=3;break;
    case UIInterfaceOrientationPortrait:break;
    default:return;
    }
    if(turns!=_geometry.turns) { _geometry.turns=turns;[self changedGeometry]; }
}
- (void)releaseHeld {
    if(_buttonPeer) {
        MirrorConnection *peer=_peers[@(_buttonPeer)];
        if(peer && !peer.closed) [self error:peer request:_buttonRequest code:4 text:"Input sequence interrupted"];
    }
    ++_buttonSerial;_buttonPeer=0;_buttonRequest=0;[_input releaseAll];
}
- (void)disconnected:(MirrorConnection*)peer {
    if(_lease.release(peer.identity)) [self releaseHeld];
    if(_videoPeer==peer.identity) { _videoPeer=0;[_encoder invalidate]; }
    _stills.erase(peer.identity);_progress.erase(peer.identity);[_peers removeObjectForKey:@(peer.identity)];
    [self reconcileCapture];
}
- (void)reconcileCapture { if(_videoPeer || !_stills.empty()) [_capture start];else [_capture stop]; }
- (BOOL)owned:(MirrorConnection*)peer command:(Command)command {
    if(!_lease.touch(peer.identity,nowNs())) {
        [self error:peer request:command.request code:2 text:"Acquire input ownership first"];return NO;
    }
    if(read32(command.payload.data())!=_geometry.generation) {
        [self releaseHeld];[self error:peer request:command.request code:3 text:"Geometry changed; refresh dimensions"];return NO;
    }
    if(_buttonPeer) { [self error:peer request:command.request code:2 text:"Navigation sequence in progress"];return NO; }
    return YES;
}
- (void)handle:(Command)command peer:(MirrorConnection*)peer {
    const uint64_t handled=command.type==Key?nowNs():0;
    const auto& p=command.payload;uint32_t id=command.request;
    size_t required=0;
    switch(command.type) {
    case Subscribe:required=4;break;case Pointer:required=16;break;
    case Key:required=12;break;case Button:required=8;break;
    case Ping:required=p.size();break;
    case RequestKeyframe:case RequestStill:case GetGeometry:case AcquireInput:
    case ReleaseInput:case GetStats:break;
    default:[self error:peer request:id code:1 text:"Unknown command"];return;
    }
    if(p.size()!=required) { [self error:peer request:id code:1 text:"Malformed payload size"];return; }
    if(_lease.expired(nowNs())) { [self releaseHeld];_lease.release(_lease.owner()); }
    switch(command.type) {
    case Subscribe: {
        uint32_t enable=read32(p.data());
        if(enable>1) { [self error:peer request:id code:1 text:"Invalid subscription"];return; }
        if(enable && _videoPeer && _videoPeer!=peer.identity) {
            [self error:peer request:id code:2 text:"Video subscriber already active"];return;
        }
        if(enable) {
            if(_videoPeer!=peer.identity) { [_encoder invalidate];_retryEncoderAt=0; }
            _videoPeer=peer.identity;_forceKeyframe=YES;_awaitKeyframe=YES;_forceCapture=YES;
        } else if(_videoPeer==peer.identity) { _videoPeer=0;[_encoder invalidate]; }
        [self ack:peer request:id];[self reconcileCapture];return;
    }
    case RequestKeyframe:
        if(_videoPeer!=peer.identity) { [self error:peer request:id code:4 text:"Subscribe before requesting video"];return; }
        _forceKeyframe=YES;_forceCapture=YES;[self ack:peer request:id];return;
    case RequestStill:
        if(_stills.count(peer.identity)) { [self error:peer request:id code:2 text:"Still request already pending"];return; }
        _stills[peer.identity]={id,nowNs()};[self reconcileCapture];return;
    case GetGeometry:[self geometryTo:peer request:id];return;
    case AcquireInput:
        if(_lease.acquire(peer.identity,nowNs())) [self ack:peer request:id];
        else [self error:peer request:id code:2 text:"Input is owned by another connection"];return;
    case ReleaseInput:
        if(_lease.release(peer.identity)) [self releaseHeld];
        [self ack:peer request:id];return;
    case Pointer:case Key:case Button:break;
    case Ping:[peer send:Pong request:id payload:p];return;
    case GetStats:[self statsTo:peer request:id];return;
    }
    if(![self owned:peer command:command]) return;
    BOOL success=NO;
    if(command.type==Pointer) {
        uint32_t action=read32(p.data()+4),x=read32(p.data()+8),y=read32(p.data()+12),px=0,py=0;
        if(action<=2 && _geometry.point(x,y,px,py)) success=[_input pointer:action point:CGPointMake(px,py)];
    } else if(command.type==Key) {
        uint32_t usage=read32(p.data()+4),down=read32(p.data()+8);
        const uint64_t dispatchBegin=nowNs();
        if(down<=1) success=[_input key:usage down:down];
        if(success && down) _inputTimings.append({++_inputCount,peer.identity,id,_geometry.generation,
            command.receivedNs,handled,dispatchBegin,nowNs()});
    } else {
        uint32_t button=read32(p.data()+4);
        if(button==1 || button==2) { [self button:button peer:peer request:id];return; }
    }
    if(success) [self ack:peer request:id];
    else [self error:peer request:id code:1 text:"Invalid or unavailable input event"];
}
- (void)button:(uint32_t)button peer:(MirrorConnection*)peer request:(uint32_t)request {
    if(![_input menu:YES]) { [self error:peer request:request code:4 text:"Home input unavailable"];return; }
    _buttonPeer=peer.identity;_buttonRequest=request;uint64_t serial=++_buttonSerial;
    __weak MirrorServer *weak=self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,50000000),dispatch_get_main_queue(),^{
        MirrorServer *s=weak;if(!s || s->_buttonSerial!=serial) return;
        [s->_input menu:NO];
        if(button==1) { s->_buttonPeer=0;s->_buttonRequest=0;[s ack:peer request:request]; }
    });
    if(button==1) return;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200000000),dispatch_get_main_queue(),^{
        MirrorServer *s=weak;if(s && s->_buttonSerial==serial) [s->_input menu:YES];
    });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,250000000),dispatch_get_main_queue(),^{
        MirrorServer *s=weak;if(!s || s->_buttonSerial!=serial) return;
        [s->_input menu:NO];s->_buttonPeer=0;s->_buttonRequest=0;[s ack:peer request:request];
    });
}
- (void)captureTick {
    if(auto *input=_inputTimings.latest();input && !input->nextAttempt) input->nextAttempt=nowNs();
    MirrorConnection *video=_peers[@(_videoPeer)];
    BOOL canVideo=video && !video.closed && video.queuedBytes<VideoHighWater && _encoder.pending<3;
    uint64_t now=nowNs();
    if(canVideo && !_encoder.ready) {
        if(now<_retryEncoderAt || _encoder.pending) canVideo=NO;
        else if([_encoder prepare]) { [self changedGeometry];_lastSubmit=0; }
        else { _retryEncoderAt=now+NSEC_PER_SEC;canVideo=NO;++_errors; }
    }
    if(!canVideo && _videoPeer) { ++_skipped;_forceKeyframe=YES; }
    BOOL wantStill=!_stills.empty();
    if(!canVideo && !wantStill) return;
    const uint64_t submitGap=_lastSubmit?now-_lastSubmit:0;
    // An idle stream retains its reference frames; only recovery requests
    // require an IDR. Keep the gap classification as timing evidence.
    const BOOL afterIdle=_lastSubmit && submitGap>100000000;
    auto frame=[_capture captureForced:wantStill || (canVideo && _forceCapture)];
    if(!frame) return;
    frame->timing.generation=_geometry.generation;
    if(auto *input=_inputTimings.latest()) {
        frame->timing.inputSequence=input->sequence;
        if(!input->firstFramePts) input->firstFramePts=frame->pts;
    }
    if(wantStill) [self deliverStill:frame];
    if(canVideo) {
        frame->timing.submitGap=submitGap;frame->timing.forcedKeyframe=_forceKeyframe;
        frame->timing.afterIdle=afterIdle;
        if([_encoder submit:frame geometry:_geometry keyframe:_forceKeyframe]) {
            if(afterIdle) ++_afterIdleCount;
            _lastSubmit=frame->pts;_forceKeyframe=NO;_forceCapture=NO;
        } else { _forceKeyframe=YES;_awaitKeyframe=YES;_forceCapture=YES;++_errors; }
    } else if(_videoPeer) _forceCapture=YES; // A still consumed dirty pixels while video was gated.
    [self reconcileCapture];
}
- (void)deliverStill:(MirrorFramePtr)frame {
    CVPixelBufferRef pixels=frame->pixels();
    CVReturn status=CVPixelBufferLockBaseAddress(pixels,kCVPixelBufferLock_ReadOnly);
    Bytes b;
    if(status==kCVReturnSuccess) {
        b.reserve(24+size_t(_geometry.width)*_geometry.height*4);
        for(auto x:{_geometry.generation,_geometry.width,_geometry.height,_geometry.turns}) put32(b,x);
        put64(b,frame->pts);
        auto source=static_cast<const uint8_t*>(CVPixelBufferGetBaseAddress(pixels));
        size_t stride=CVPixelBufferGetBytesPerRow(pixels),row=size_t(_geometry.width)*4;
        for(uint32_t y=0;y<_geometry.height;++y) b.insert(b.end(),source+y*stride,source+y*stride+row);
        CVPixelBufferUnlockBaseAddress(pixels,kCVPixelBufferLock_ReadOnly);
    }
    for(auto [identity,request]:_stills) {
        MirrorConnection *peer=_peers[@(identity)];
        if(!peer) continue;
        if(status==kCVReturnSuccess) [peer send:Still request:request.request payload:b];
        else [self error:peer request:request.request code:4 text:"Lossless capture unavailable"];
    }
    _stills.clear();
}
- (void)encoded:(std::shared_ptr<MirrorEncoded>)frame status:(int)status {
    auto& timing=_frameTimings.append(frame->timing);
    timing.status=status;timing.completed=nowNs();timing.keyframe=frame->keyframe;
    timing.bytes=frame->nals.size();
    if(frame->generation!=_geometry.generation) return;
    if(status) {
        ++_errors;_forceKeyframe=YES;_awaitKeyframe=YES;_forceCapture=YES;
        _retryEncoderAt=nowNs()+NSEC_PER_SEC;[_encoder invalidate];return;
    }
    MirrorConnection *peer=_peers[@(_videoPeer)];if(!peer || peer.closed) return;
    if(_awaitKeyframe && !frame->keyframe) { _forceKeyframe=YES;return; }
    if(frame->keyframe) {
        Bytes format;for(auto x:{_geometry.generation,uint32_t(0x68766331),_geometry.width,_geometry.height,_geometry.turns}) put32(format,x);
        format.insert(format.end(),frame->parameters.begin(),frame->parameters.end());
        if(![peer send:Format request:0 payload:format]) return;
        _awaitKeyframe=NO;
    }
    Bytes video;put32(video,frame->generation);put64(video,frame->pts);put32(video,frame->keyframe?1:0);
    video.insert(video.end(),frame->nals.begin(),frame->nals.end());
    if(![peer send:Video request:0 payload:video]) return;
    timing.queued=nowNs();timing.queuedBytes=peer.queuedBytes;
    ++_encoded;_encodedBytes+=frame->nals.size();_latencySum+=frame->latency;
    _latencyMax=std::max(_latencyMax,frame->latency);
}
- (void)maintain {
    uint64_t now=nowNs();
    if(_lease.expired(now)) { [self releaseHeld];_lease.release(_lease.owner()); }
    for(MirrorConnection *peer in _peers.allValues) {
        if(_progress[peer.identity].stalled(peer.queuedBytes,peer.bytesWritten,now)) [peer close];
    }
    for(auto it=_stills.begin();it!=_stills.end();) {
        if(now-it->second.started<3*NSEC_PER_SEC) { ++it;continue; }
        MirrorConnection *peer=_peers[@(it->first)];
        [self error:peer request:it->second.request code:4 text:"Capture timeout; wake and unlock the phone"];
        it=_stills.erase(it);
    }
    [self reconcileCapture];
}
- (void)statsTo:(MirrorConnection*)peer request:(uint32_t)request {
    NSDictionary *stats=@{@"codec":@"hevc",@"encoded_frames":@(_encoded),@"encoded_bytes":@(_encodedBytes),
        @"encode_errors":@(_errors),@"submission_skips":@(_skipped),@"pending_encodes":@(_encoder.pending),
        @"encode_latency_mean_ms":@(_encoded?double(_latencySum)/_encoded/1e6:0),
        @"encode_latency_max_ms":@(double(_latencyMax)/1e6),@"thermal_state":@(NSProcessInfo.processInfo.thermalState),
        @"generation":@(_geometry.generation),@"connections":@(_peers.count),@"fps_limit":@(_fps),
        @"color":_encoder.colorDiagnostics,
        @"timing":timingJSON(_inputTimings,_frameTimings,[_capture timingCounters],_inputCount,_afterIdleCount)};
    NSData *data=[NSJSONSerialization dataWithJSONObject:stats options:0 error:nil];
    const auto *bytes=static_cast<const uint8_t*>(data.bytes);
    [peer send:Stats request:request payload:Bytes(bytes,bytes+data.length)];
}
- (void)stop {
    [self releaseHeld];[_capture stop];[_encoder invalidate];[_orientation invalidate];
    for(MirrorConnection *peer in _peers.allValues) [peer close];
    if(_timer) { dispatch_source_cancel(_timer);_timer=nil; }
    if(_acceptSource) { dispatch_source_cancel(_acceptSource);_acceptSource=nil;_listener=-1; }
}
@end
