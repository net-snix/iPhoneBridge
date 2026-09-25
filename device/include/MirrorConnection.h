// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import <Foundation/Foundation.h>
#include "MirrorProtocol.h"
@interface MirrorConnection : NSObject
@property(nonatomic,readonly) uint64_t identity;
@property(nonatomic,readonly) size_t queuedBytes;
@property(nonatomic,readonly) uint64_t bytesWritten;
@property(nonatomic,readonly) BOOL closed;
// Call handlers only on main; transport parsing/writing stays on its serial queue.
- (instancetype)initWithSocket:(int)socket identity:(uint64_t)identity
    command:(void (^)(MirrorConnection*,ipbm::Command))command
    closed:(void (^)(MirrorConnection*))closed;
- (void)start;
- (BOOL)send:(ipbm::Type)type request:(uint32_t)request payload:(const ipbm::Bytes&)payload;
- (void)close;
@end
