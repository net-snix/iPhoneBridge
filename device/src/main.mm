// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import "MirrorServer.h"
#include <signal.h>
#include <unistd.h>
#include <cstdlib>
#include <cstring>
#include <initializer_list>

static int number(const char *text,int low,int high) {
    char *end=nullptr;long value=strtol(text,&end,10);
    return text[0] && end && !*end && value>=low && value<=high?int(value):-1;
}
int main(int argc,char **argv) {
    @autoreleasepool {
        int port=15901,fps=60,bitrate=40000000,opt;
        while((opt=getopt(argc,argv,"b:p:F:R:h"))!=-1) {
            switch(opt) {
            case 'b':if(strcmp(optarg,"127.0.0.1")) return 2;break;
            case 'p':port=number(optarg,1024,65535);break;
            case 'F':fps=number(optarg,1,60);break;
            case 'R':bitrate=number(optarg,1000000,100000000);break;
            default:fprintf(stderr,"Usage: trollvncserver [-b 127.0.0.1] [-p 15901] [-F 60] [-R 40000000]\n");
                return opt=='h'?0:2;
            }
        }
        if(optind!=argc || port<0 || fps<0 || bitrate<0) return 2;
        // Jetsam setup runs before main. Preserve TrollVNC's mobile execution identity.
        if(getgid()==0 && setgid(501)) { perror("setgid mobile");return 1; }
        if(getuid()==0 && setuid(501)) { perror("setuid mobile");return 1; }
        __attribute__((objc_precise_lifetime)) MirrorServer *server=
            [[MirrorServer alloc] initWithPort:uint16_t(port) fps:fps bitrate:bitrate];
        if(![server start]) return 1;
        signal(SIGPIPE,SIG_IGN);
        __attribute__((objc_precise_lifetime)) NSMutableArray *signals=[NSMutableArray array];
        for(int sig:{SIGTERM,SIGINT,SIGHUP}) {
            signal(sig,SIG_IGN);
            dispatch_source_t source=dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL,sig,0,dispatch_get_main_queue());
            dispatch_source_set_event_handler(source,^{
                [server stop];
                // Let cancellation handlers close owned sockets before graceful process exit.
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100000000),dispatch_get_main_queue(),^{ exit(0); });
            });
            [signals addObject:source];dispatch_resume(source);
        }
        CFRunLoopRun();
        [server stop];
    } return 0;
}
