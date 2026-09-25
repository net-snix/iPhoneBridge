// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#pragma once
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>
#include "MirrorProtocol.h"
#include "MirrorTiming.h"

struct MirrorSurface : ipbm::SlotLease {
    CVPixelBufferRef pixels=nullptr;
    ~MirrorSurface() { if(pixels) CVPixelBufferRelease(pixels); }
};
struct MirrorFrame {
    std::shared_ptr<MirrorSurface> surface;
    uint64_t pts=0;
    ipbm::FrameTiming timing;
    ~MirrorFrame() { surface->release(); }
    CVPixelBufferRef pixels() const { return surface->pixels; }
};
using MirrorFramePtr=std::shared_ptr<MirrorFrame>;

@interface MirrorCapture : NSObject
@property(nonatomic,readonly) uint32_t width;
@property(nonatomic,readonly) uint32_t height;
- (instancetype)initWithFrameRate:(int)fps tick:(void (^)(void))tick;
- (void)start;
- (void)stop;
- (MirrorFramePtr)captureForced:(BOOL)force;
- (ipbm::CaptureCounters)timingCounters;
@end
