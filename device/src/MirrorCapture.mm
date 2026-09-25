/* Capture mechanism derived from TrollVNC ScreenCapturer.mm, a3e40816.
 * Copyright (c) 2025 82Flex <82flex@gmail.com> and contributors.
 * Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
 * See vendor/COPYING. Original attribution and source inventory: device-sources.lock.json.
 */
#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>
#import "MirrorCapture.h"
#import "IOSurfaceSPI.h"
#import "UIScreen+Private.h"
#include "CaptureSchedule.h"
#include "MirrorColor.h"
#include <array>
#include <cmath>
#include <time.h>
extern "C" CFIndex CARenderServerGetDirtyFrameCount(void *);
extern "C" void CARenderServerRenderDisplay(kern_return_t,CFStringRef,IOSurfaceRef,int,int);

@implementation MirrorCapture {
    std::array<std::shared_ptr<MirrorSurface>,3> _surfaces;
    CADisplayLink *_displayLink;
    void (^_tick)(void);
    CFIndex _lastDirty;
    BOOL _haveFrame;
    int _fps;
    ipbm::CaptureSchedule _schedule;
    dispatch_source_t _wakeTimer;
    ipbm::CaptureCounters _timing;
}
- (instancetype)initWithFrameRate:(int)fps tick:(void (^)(void))tick {
    if (!(self=[super init])) return nil;
    CGSize size=[[UIScreen mainScreen] _unjailedReferenceBoundsInPixels].size;
    _width=uint32_t(lround(size.width)); _height=uint32_t(lround(size.height));
    if (!_width || !_height || uint64_t(_width)*_height*4+32>ipbm::MaxBody) return nil;
    _fps=fps; _tick=[tick copy];
    size_t row=IOSurfaceAlignProperty(kIOSurfaceBytesPerRow,_width*4);
    CGColorSpaceRef color=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CFPropertyListRef colorProperties=CGColorSpaceCopyPropertyList(color);
    CGColorSpaceRelease(color);
    NSDictionary *properties=@{(__bridge NSString*)kIOSurfaceWidth:@(_width),
        (__bridge NSString*)kIOSurfaceHeight:@(_height),
        (__bridge NSString*)kIOSurfaceBytesPerElement:@4,
        (__bridge NSString*)kIOSurfaceBytesPerRow:@(row),
        (__bridge NSString*)kIOSurfaceAllocSize:@(row*_height),
        (__bridge NSString*)kIOSurfacePixelFormat:@(kCVPixelFormatType_32BGRA),
        (__bridge NSString*)kIOSurfaceColorSpace:CFBridgingRelease(colorProperties)};
    for(auto& slot:_surfaces) {
        slot=std::make_shared<MirrorSurface>();
        IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)properties);
        if (!surface) return nil;
        NSDictionary *attrs=@{(NSString*)kCVPixelBufferIOSurfacePropertiesKey:@{}};
        CVReturn status=CVPixelBufferCreateWithIOSurface(kCFAllocatorDefault,surface,
            (__bridge CFDictionaryRef)attrs,&slot->pixels);
        CFRelease(surface); if(status!=kCVReturnSuccess) return nil;
        if(!ipbm::attachCaptureColor(slot->pixels)) return nil;
    }
    return self;
}
- (void)start {
    if (_displayLink) return;
    const uint64_t generation=_schedule.start(_fps);
    _wakeTimer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,DISPATCH_TIMER_STRICT,
                                     dispatch_get_main_queue());
    __weak MirrorCapture *weakSelf=self;
    dispatch_source_set_event_handler(_wakeTimer, ^{
        MirrorCapture *capture=weakSelf;
        if(capture && capture->_schedule.takeWake(generation)) [capture attemptTick];
    });
    dispatch_source_set_timer(_wakeTimer,DISPATCH_TIME_FOREVER,DISPATCH_TIME_FOREVER,0);
    dispatch_activate(_wakeTimer);
    _displayLink=[CADisplayLink displayLinkWithTarget:self selector:@selector(tick:)];
    _displayLink.preferredFrameRateRange=CAFrameRateRangeMake(_fps,_fps,_fps);
    [_displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
}
- (void)stop {
    _schedule.stop();
    [_displayLink invalidate]; _displayLink=nil;
    if(_wakeTimer) dispatch_source_cancel(_wakeTimer);
    _wakeTimer=nil;
}
- (void)tick:(CADisplayLink*)sender {
    if(sender!=_displayLink) return;
    ++_timing.displayTicks;
    _schedule.request();
    [self attemptTick];
}
- (void)attemptTick {
    const uint64_t now=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    const auto plan=_schedule.plan(now);
    if(!plan.generation) return;
    if(plan.delay) {
        // Retain an early display request until the cap deadline instead of
        // dropping it and waiting a full extra display interval.
        if(_schedule.claimWake(plan.generation))
            dispatch_source_set_timer(_wakeTimer,dispatch_time(DISPATCH_TIME_NOW,int64_t(plan.delay)),
                                      DISPATCH_TIME_FOREVER,0);
        return;
    }
    if(!_schedule.beginCapture(plan.generation,now)) return;
    ++_timing.attempts;
    dispatch_source_set_timer(_wakeTimer,DISPATCH_TIME_FOREVER,DISPATCH_TIME_FOREVER,0);
    if(_tick) _tick();
}
- (MirrorFramePtr)captureForced:(BOOL)force {
    const uint64_t began=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    CFIndex dirty=CARenderServerGetDirtyFrameCount(nullptr);
    const uint64_t checked=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    if (!force && _haveFrame && dirty==_lastDirty) { ++_timing.dirtySkips;return {}; }
    std::shared_ptr<MirrorSurface> slot;
    for (auto& candidate:_surfaces) if(candidate->claim()) { slot=candidate; break; }
    if (!slot) { ++_timing.noSurface;return {}; } // Never write a retained surface.
    auto frame=std::make_shared<MirrorFrame>(); frame->surface=slot;
    frame->timing.captureBegin=began;frame->timing.dirtyRead=checked;
    // Render only into the claimed BGRA surface; the frame retains its lease
    // through encoder completion. No intermediate surface or transfer is needed.
    CARenderServerRenderDisplay(0,CFSTR("LCD"),CVPixelBufferGetIOSurface(slot->pixels),0,0);
    frame->timing.renderEnd=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    frame->pts=frame->timing.renderEnd;
    frame->timing.pts=frame->pts;frame->timing.transferEnd=frame->pts;
    _lastDirty=dirty; _haveFrame=YES; return frame;
}
- (ipbm::CaptureCounters)timingCounters { return _timing; }
- (void)dealloc {
    [self stop];
}
@end
