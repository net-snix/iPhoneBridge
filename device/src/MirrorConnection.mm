// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import "MirrorConnection.h"
#include <deque>
#include <fcntl.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#include <time.h>

@implementation MirrorConnection {
    int _socket;
    dispatch_queue_t _queue;
    dispatch_source_t _reader,_writer;
    BOOL _writeArmed;
    std::atomic<bool> _closedFlag;
    std::atomic<size_t> _bytes,_commands;
    std::atomic<uint64_t> _written;
    std::deque<ipbm::Bytes> _out;
    size_t _offset;
    ipbm::RequestParser _parser;
    void (^_command)(MirrorConnection*,ipbm::Command);
    void (^_didClose)(MirrorConnection*);
}
- (instancetype)initWithSocket:(int)socket identity:(uint64_t)identity
    command:(void (^)(MirrorConnection*,ipbm::Command))command
    closed:(void (^)(MirrorConnection*))closed {
    if((self=[super init])) {
        _socket=socket; _identity=identity; _command=[command copy]; _didClose=[closed copy];
        _queue=dispatch_queue_create("iphonebridge.peer",DISPATCH_QUEUE_SERIAL);
        fcntl(socket,F_SETFL,fcntl(socket,F_GETFL)|O_NONBLOCK);
        int yes=1; setsockopt(socket,SOL_SOCKET,SO_NOSIGPIPE,&yes,sizeof(yes));
        setsockopt(socket,IPPROTO_TCP,TCP_NODELAY,&yes,sizeof(yes));
        int sendBuffer=128*1024; setsockopt(socket,SOL_SOCKET,SO_SNDBUF,&sendBuffer,sizeof(sendBuffer));
    } return self;
}
- (BOOL)closed { return _closedFlag.load(); }
- (size_t)queuedBytes { return _bytes.load(); }
- (uint64_t)bytesWritten { return _written.load(); }
- (void)start {
    dispatch_async(_queue,^{
        if(self.closed) return;
        self->_reader=dispatch_source_create(DISPATCH_SOURCE_TYPE_READ,self->_socket,0,self->_queue);
        self->_writer=dispatch_source_create(DISPATCH_SOURCE_TYPE_WRITE,self->_socket,0,self->_queue);
        __weak MirrorConnection *weak=self;
        dispatch_source_set_event_handler(self->_reader,^{ [weak readAvailable]; });
        dispatch_source_set_event_handler(self->_writer,^{ [weak writeAvailable]; });
        // Read source exclusively owns closing the fd, after all source activity stops.
        int fd=self->_socket;
        dispatch_source_set_cancel_handler(self->_reader,^{ ::close(fd); });
        dispatch_resume(self->_reader);
    });
}
- (void)readAvailable {
    uint8_t data[1024];
    for(int burst=0;burst<16 && !self.closed;++burst) {
        ssize_t size=recv(_socket,data,sizeof(data),0);
        if(size<0 && errno==EINTR) continue;
        if(size<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) return;
        if(size<=0) { [self close]; return; }
        BOOL valid=_parser.feed(data,size,[&](ipbm::Command command) {
            command.receivedNs=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
            if(_commands.fetch_add(1)>=ipbm::MaxPendingCommands) { --_commands; return false; }
            dispatch_async(dispatch_get_main_queue(),^{
                if(!self.closed && self->_command) self->_command(self,command);
                --self->_commands;
            }); return true;
        });
        if(!valid) { [self close]; return; }
    }
}
- (BOOL)send:(ipbm::Type)type request:(uint32_t)request payload:(const ipbm::Bytes&)payload {
    if(self.closed || payload.size()>ipbm::MaxBody-8) return NO;
    auto message=ipbm::message(type,request,payload);
    size_t reserved=_bytes.fetch_add(message.size());
    if(reserved+message.size()>ipbm::MaxQueuedBytes) {
        _bytes.fetch_sub(message.size()); [self close]; return NO;
    }
    dispatch_async(_queue,^{
        if(self.closed) { self->_bytes.fetch_sub(message.size()); return; }
        self->_out.push_back(message); [self writeAvailable];
    }); return YES;
}
- (void)writeAvailable {
    size_t burst=0;
    while(!_out.empty() && !self.closed && burst<256*1024) {
        auto& bytes=_out.front();
        ssize_t size=::send(_socket,bytes.data()+_offset,bytes.size()-_offset,0);
        if(size<0 && errno==EINTR) continue;
        if(size<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) break;
        if(size<=0) { [self close]; return; }
        _offset+=size; burst+=size; _bytes.fetch_sub(size);_written.fetch_add(uint64_t(size));
        if(_offset==bytes.size()) { _out.pop_front(); _offset=0; }
    }
    BOOL needs=!_out.empty();
    if(_writer && needs!=_writeArmed) {
        if(needs) dispatch_resume(_writer); else dispatch_suspend(_writer);
        _writeArmed=needs;
    }
}
- (void)close {
    if(_closedFlag.exchange(true)) return;
    dispatch_async(_queue,^{
        if(self->_writer) {
            if(!self->_writeArmed) dispatch_resume(self->_writer);
            dispatch_source_cancel(self->_writer); self->_writer=nil;
        }
        if(self->_reader) { dispatch_source_cancel(self->_reader); self->_reader=nil; }
        else ::close(self->_socket);
        self->_socket=-1;
        size_t abandoned=0;
        for(const auto& bytes:self->_out) abandoned+=bytes.size();
        abandoned-=self->_offset;
        self->_bytes.fetch_sub(abandoned);
        self->_out.clear(); self->_offset=0;
        dispatch_async(dispatch_get_main_queue(),^{
            if(self->_didClose) self->_didClose(self);
            self->_command=nil; self->_didClose=nil;
        });
    });
}
@end
