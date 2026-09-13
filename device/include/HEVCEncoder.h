// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import <Foundation/Foundation.h>
#import "MirrorCapture.h"
#include "MirrorColor.h"
struct MirrorEncoded {
    uint32_t generation=0;
    uint64_t pts=0,latency=0;
    ipbm::FrameTiming timing;
    bool keyframe=false;
    bool colorChecked=false;
    ipbm::ColorDescription color;
    ipbm::Bytes parameters,nals;
};
@interface HEVCEncoder : NSObject
@property(nonatomic,readonly) NSUInteger pending;
@property(nonatomic,readonly) BOOL ready;
@property(nonatomic,readonly) NSDictionary<NSString*,id> *colorDiagnostics;
- (instancetype)initWithWidth:(uint32_t)width height:(uint32_t)height fps:(int)fps bitrate:(int)bitrate
    output:(void (^)(std::shared_ptr<MirrorEncoded>,int))output;
- (BOOL)prepare;
- (void)invalidate;
- (BOOL)submit:(MirrorFramePtr)frame geometry:(ipbm::Geometry)geometry keyframe:(BOOL)force;
@end
