// Run the production dispatch socket transport against actual host sockets.
#import <Foundation/Foundation.h>
#import "MirrorConnection.h"
#include <cassert>
#include <sys/socket.h>
#include <unistd.h>
#include <time.h>

static void writeAll(int socket,const ipbm::Bytes& bytes) {
    // Deliberate fragmentation exercises header/body buffering in the real reader.
    for(auto byte:bytes) assert(::send(socket,&byte,1,0)==1);
}
static ipbm::Bytes readBytes(int socket,size_t count) {
    ipbm::Bytes bytes(count);size_t offset=0;
    while(offset<count) { auto n=recv(socket,bytes.data()+offset,count-offset,0);assert(n>0);offset+=n; }
    return bytes;
}
int main(int argc,char**argv) {
    @autoreleasepool {
        assert(argc==2);int sockets[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,sockets)==0);
        int clientSocket=sockets[1];
        std::string mode=argv[1];bool bounded=mode=="bounded",paced=mode=="paced",stopped=mode=="stopped";
        uint64_t began=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
        __attribute__((objc_precise_lifetime)) dispatch_source_t progressTimer=nil;
        __block int commands=0;
        MirrorConnection *peer=[[MirrorConnection alloc] initWithSocket:sockets[0] identity:123
            command:^(MirrorConnection *p,ipbm::Command command) {
                assert(command.type==ipbm::Pointer && command.request==7 && command.payload.size()==16);
                assert(command.receivedNs>=began && command.receivedNs<=clock_gettime_nsec_np(CLOCK_UPTIME_RAW));
                assert(ipbm::read32(command.payload.data()+8)==42);++commands;
                [p send:ipbm::Ack request:command.request payload:{}];
            } closed:^(MirrorConnection *p) {
                assert(p.closed && p.queuedBytes==0);
                assert(commands==((bounded||paced||stopped)?0:1));
                if(paced) assert(clock_gettime_nsec_np(CLOCK_UPTIME_RAW)-began>=7*NSEC_PER_SEC);
                if(stopped) assert(clock_gettime_nsec_np(CLOCK_UPTIME_RAW)-began>=5*NSEC_PER_SEC);
                ::close(clientSocket);exit(0);
            }];
        [peer start];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,9*NSEC_PER_SEC),dispatch_get_main_queue(),^{
            fprintf(stderr,"native transport timeout\n");exit(9);
        });
        if(paced || stopped) {
            assert([peer send:ipbm::Still request:1 payload:ipbm::Bytes(1024*1024,42)]);
            auto progress=std::make_shared<ipbm::WriteProgress>();
            progressTimer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,dispatch_get_main_queue());
            dispatch_source_set_timer(progressTimer,dispatch_time(DISPATCH_TIME_NOW,100000000),100000000,0);
            dispatch_source_set_event_handler(progressTimer,^{
                uint64_t now=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
                if(progress->stalled(peer.queuedBytes,peer.bytesWritten,now)) { [peer close];return; }
                if(paced) {
                    if(now-began>=7*NSEC_PER_SEC) { assert(peer.bytesWritten>256*1024);[peer close];return; }
                    // Produce faster than the reader: queued bytes grow while actual writes progress.
                    assert([peer send:ipbm::Video request:0 payload:ipbm::Bytes(65536,42)]);
                }
            });
            dispatch_resume(progressTimer);
            if(paced) dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{
                uint8_t bytes[4096];while(recv(clientSocket,bytes,sizeof(bytes),0)>0) usleep(20000);
            });
        } else if(bounded) {
            ipbm::Bytes pixels(12*1024*1024,42);
            assert([peer send:ipbm::Still request:1 payload:pixels]);
            assert(![peer send:ipbm::Still request:2 payload:pixels]);assert(peer.closed);
        } else {
            dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{
                ipbm::Bytes payload;for(auto x:{1,1,42,99}) ipbm::put32(payload,x);
                writeAll(clientSocket,ipbm::message(ipbm::Pointer,7,payload));
                assert(readBytes(clientSocket,12)==ipbm::message(ipbm::Ack,7));
                // Invalid framing must close the transport without dispatching a command.
                writeAll(clientSocket,{0,0,0,7});
            });
        }
        CFRunLoopRun();
    } return 3;
}
