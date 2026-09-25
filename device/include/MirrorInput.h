// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
@interface MirrorInput : NSObject
- (instancetype)initWithWidth:(uint32_t)width height:(uint32_t)height;
- (BOOL)pointer:(uint32_t)action point:(CGPoint)point;
- (BOOL)key:(uint32_t)usage down:(BOOL)down;
- (BOOL)menu:(BOOL)down;
- (void)releaseAll;
@end
