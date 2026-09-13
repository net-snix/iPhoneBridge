// Validate production color attachment and output-format checks with Apple frameworks.
#import <Foundation/Foundation.h>
#include "MirrorColor.h"
#include "MirrorTimingJSON.h"
#include <cassert>
#include <cstdio>

static ipbm::ColorDescription describe(NSDictionary *extensions) {
    CMVideoFormatDescriptionRef format=nullptr;
    assert(CMVideoFormatDescriptionCreate(kCFAllocatorDefault,kCVPixelFormatType_32BGRA,2,2,
        (__bridge CFDictionaryRef)extensions,&format)==noErr);
    const auto result=ipbm::ColorDescription::read(format);
    CFRelease(format);return result;
}

static void timingJSONTests() {
    ipbm::RecentTimings<ipbm::InputTiming> inputs;
    ipbm::RecentTimings<ipbm::FrameTiming> frames;
    for(uint64_t sequence=1;sequence<=45;++sequence) {
        ipbm::InputTiming input;input.sequence=sequence;input.request=uint32_t(sequence);
        input.received=UINT64_MAX;inputs.append(input);
        ipbm::FrameTiming frame;frame.inputSequence=sequence;frame.pts=UINT64_MAX;
        frame.afterIdle=true;frames.append(frame);
    }
    NSDictionary *value=ipbm::timingJSON(inputs,frames,{},45,45);
    assert([value[@"capacity"] unsignedIntValue]==40 && [value[@"key_down_count"] unsignedIntValue]==45);
    NSData *data=[NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    assert(data && data.length<65536); // Full rings remain a small bounded GET_STATS response.
    NSDictionary *roundtrip=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    NSArray *rows=roundtrip[@"inputs"],*output=roundtrip[@"frames"];
    assert(rows.count==40 && output.count==40);
    assert([rows.firstObject[@"sequence"] unsignedIntValue]==6);
    assert([rows.lastObject[@"sequence"] unsignedIntValue]==45);
    assert([rows.lastObject[@"received_ns"] unsignedLongLongValue]==UINT64_MAX);
    assert([output.lastObject[@"after_idle"] boolValue]);
    assert(![output.lastObject[@"forced_keyframe"] boolValue]);
    assert([roundtrip[@"after_idle_submissions"] unsignedIntValue]==45);
    assert(!roundtrip[@"idle_forced_submissions"] && !output.lastObject[@"idle_forced"]);
    assert(inputs.size()==40 && inputs.latest()->sequence==45); // Observing does not consume history.
}

int main() {
    @autoreleasepool {
        timingJSONTests();
        CVPixelBufferRef pixels=nullptr;
        assert(CVPixelBufferCreate(kCFAllocatorDefault,2,2,kCVPixelFormatType_32BGRA,
                                  nullptr,&pixels)==kCVReturnSuccess);
        assert(ipbm::attachCaptureColor(pixels));
        CVAttachmentMode mode=kCVAttachmentMode_ShouldNotPropagate;
        CFTypeRef color=CVBufferCopyAttachment(pixels,kCVImageBufferCGColorSpaceKey,&mode);
        assert(color && CFGetTypeID(color)==CGColorSpaceGetTypeID());
        assert(mode==kCVAttachmentMode_ShouldPropagate);
        CFStringRef name=CGColorSpaceCopyName((CGColorSpaceRef)color);
        assert(name && CFEqual(name,kCGColorSpaceSRGB));CFRelease(name);CFRelease(color);
        CMVideoFormatDescriptionRef fromPixels=nullptr;
        assert(CMVideoFormatDescriptionCreateForImageBuffer(kCFAllocatorDefault,pixels,&fromPixels)==noErr);
        assert(ipbm::ColorDescription::read(fromPixels).canonical());
        CFRelease(fromPixels);CVPixelBufferRelease(pixels);

        NSMutableDictionary *tags=[@{
            (__bridge NSString*)kCMFormatDescriptionExtension_ColorPrimaries:(__bridge NSString*)kCVImageBufferColorPrimaries_ITU_R_709_2,
            (__bridge NSString*)kCMFormatDescriptionExtension_TransferFunction:(__bridge NSString*)kCVImageBufferTransferFunction_sRGB,
            (__bridge NSString*)kCMFormatDescriptionExtension_YCbCrMatrix:(__bridge NSString*)kCVImageBufferYCbCrMatrix_ITU_R_709_2} mutableCopy];
        assert(describe(tags).canonical());
        assert(!describe(@{}).canonical());assert(!ipbm::ColorDescription::read(nullptr).canonical());
        for(NSString *key in tags.allKeys) {
            NSMutableDictionary *missing=tags.mutableCopy;[missing removeObjectForKey:key];
            assert(!describe(missing).canonical());
            NSMutableDictionary *wrongType=tags.mutableCopy;wrongType[key]=@42;
            assert(!describe(wrongType).canonical());
        }
        tags[(__bridge NSString*)kCMFormatDescriptionExtension_ColorPrimaries]=(__bridge NSString*)kCVImageBufferColorPrimaries_P3_D65;
        const auto p3=describe(tags);assert(p3.primaries==12 && !p3.canonical());
        tags[(__bridge NSString*)kCMFormatDescriptionExtension_ColorPrimaries]=(__bridge NSString*)kCVImageBufferColorPrimaries_ITU_R_709_2;
        tags[(__bridge NSString*)kCMFormatDescriptionExtension_TransferFunction]=(__bridge NSString*)kCVImageBufferTransferFunction_ITU_R_709_2;
        const auto gamma=describe(tags);assert(gamma.transfer==1 && !gamma.canonical());
        tags[(__bridge NSString*)kCMFormatDescriptionExtension_TransferFunction]=(__bridge NSString*)kCVImageBufferTransferFunction_sRGB;
        tags[(__bridge NSString*)kCMFormatDescriptionExtension_YCbCrMatrix]=(__bridge NSString*)kCVImageBufferYCbCrMatrix_ITU_R_601_4;
        assert(!describe(tags).canonical());
        std::puts("native color metadata invariants passed");
    }
}
