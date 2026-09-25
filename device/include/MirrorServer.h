// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import <Foundation/Foundation.h>
@interface MirrorServer : NSObject
- (instancetype)initWithPort:(uint16_t)port fps:(int)fps bitrate:(int)bitrate;
- (BOOL)start;
- (void)stop;
@end
