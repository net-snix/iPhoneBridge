/* Single-finger/raw keyboard subset derived from TrollVNC STHIDEventGenerator.mm.
 * Copyright (c) 2025 82Flex <82flex@gmail.com> and contributors.
 * Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
 * The original event masks, sender ID and digitizer normalization are retained.
 * All methods run on main; ACK follows actual synchronous HID dispatch.
 */
#import "MirrorInput.h"
#import "IOKitSPI.h"
#import <mach/mach_time.h>
#include <set>
@implementation MirrorInput {
    IOHIDEventSystemClientRef _client;
    uint32_t _width,_height;
    BOOL _touching,_menu;
    CGPoint _point;
    std::set<uint32_t> _keys;
}
- (instancetype)initWithWidth:(uint32_t)width height:(uint32_t)height {
    if((self=[super init])) {
        _width=width; _height=height;
        _client=IOHIDEventSystemClientCreate(kCFAllocatorDefault);
        if(!_client) return nil;
    } return self;
}
- (BOOL)dispatch:(IOHIDEventRef)event {
    if(!event) return NO;
    IOHIDEventSetSenderID(event,0x8000000817319371);
    IOHIDEventSystemClientDispatchEvent(_client,event);
    CFRelease(event); return YES;
}
- (BOOL)pointer:(uint32_t)action point:(CGPoint)point {
    if(action>2 || (action==1 && _touching) || (action==2 && !_touching)) return NO;
    if(action==0 && !_touching) return YES;
    BOOL touching=action!=0;
    IOHIDDigitizerEventMask mask=action==2 ?
        (kIOHIDDigitizerEventPosition|kIOHIDDigitizerEventAttribute) :
        (kIOHIDDigitizerEventTouch|kIOHIDDigitizerEventIdentity);
    uint64_t stamp=mach_absolute_time();
    IOHIDEventRef event=IOHIDEventCreateDigitizerEvent(kCFAllocatorDefault,stamp,
        kIOHIDDigitizerTransducerTypeHand,0,0,mask,0,0,0,0,0,0,0,touching,0);
    if(!event) return NO;
    IOHIDEventSetIntegerValue(event,kIOHIDEventFieldIsBuiltIn,1);
    IOHIDEventSetIntegerValue(event,kIOHIDEventFieldDigitizerIsDisplayIntegrated,1);
    IOHIDEventRef finger=IOHIDEventCreateDigitizerFingerEvent(kCFAllocatorDefault,stamp,
        2,2,mask,point.x/_width,point.y/_height,0,0,90,touching,touching,0);
    if(!finger) { CFRelease(event); return NO; }
    IOHIDEventSetFloatValue(finger,kIOHIDEventFieldDigitizerMinorRadius,touching?5:0);
    IOHIDEventSetFloatValue(finger,kIOHIDEventFieldDigitizerMajorRadius,touching?5:0);
    IOHIDEventAppendEvent(event,finger,0); CFRelease(finger);
    BOOL success=[self dispatch:event];
    if(success) { _touching=touching; _point=point; } return success;
}
- (BOOL)key:(uint32_t)usage down:(BOOL)down {
    if(usage<4 || usage>231) return NO;
    BOOL success=[self dispatch:IOHIDEventCreateKeyboardEvent(kCFAllocatorDefault,
        mach_absolute_time(),7,usage,down,0)];
    if(success) { if(down) _keys.insert(usage); else _keys.erase(usage); } return success;
}
- (BOOL)menu:(BOOL)down {
    BOOL success=[self dispatch:IOHIDEventCreateKeyboardEvent(kCFAllocatorDefault,
        mach_absolute_time(),12,0x40,down,0)];
    if(success) _menu=down; return success;
}
- (void)releaseAll {
    if(_touching) [self pointer:0 point:_point];
    auto keys=_keys; for(auto key:keys) [self key:key down:NO];
    if(_menu) [self menu:NO];
}
- (void)dealloc { [self releaseAll]; if(_client) CFRelease(_client); }
@end
