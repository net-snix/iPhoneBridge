// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#import "HEVCEncoder.h"
#import <VideoToolbox/VideoToolbox.h>
#include <time.h>

@interface HEVCEncoder ()
- (void)complete:(std::shared_ptr<MirrorEncoded>)frame status:(int)status epoch:(uint64_t)epoch;
@end

static std::shared_ptr<MirrorEncoded> encoded(MirrorFramePtr frame,ipbm::Geometry geometry,
    OSStatus& status,VTEncodeInfoFlags flags,CMSampleBufferRef sample,uint64_t callback) {
    @autoreleasepool {
        auto out=std::make_shared<MirrorEncoded>();
        out->generation=geometry.generation;
        out->pts=frame->pts;
        out->timing.callback=callback;
        out->latency=out->timing.callback-out->pts;
        if(status==noErr && (!sample || (flags&kVTEncodeInfo_FrameDropped))) status=kVTVideoEncoderMalfunctionErr;
        if(status==noErr) {
            CFArrayRef attachments=CMSampleBufferGetSampleAttachmentsArray(sample,false);
            bool notSync=false;
            if(attachments && CFArrayGetCount(attachments)) {
                auto entry=(CFDictionaryRef)CFArrayGetValueAtIndex(attachments,0);
                notSync=CFDictionaryGetValue(entry,kCMSampleAttachmentKey_NotSync)==kCFBooleanTrue;
            }
            out->keyframe=!notSync;
            CMFormatDescriptionRef format=CMSampleBufferGetFormatDescription(sample);
            out->color=ipbm::ColorDescription::read(format);out->colorChecked=true;
            if(!out->color.canonical()) status=kVTParameterErr;
            size_t count=0; int lengthSize=0;
            if(status==noErr)
                status=CMVideoFormatDescriptionGetHEVCParameterSetAtIndex(format,0,nullptr,nullptr,&count,&lengthSize);
            if(status==noErr && (lengthSize!=4 || count!=3)) status=kVTParameterErr;
            if(status==noErr) {
                ipbm::put32(out->parameters,uint32_t(count));
                for(size_t i=0;i<count && status==noErr;++i) {
                    const uint8_t *p=nullptr; size_t size=0;
                    status=CMVideoFormatDescriptionGetHEVCParameterSetAtIndex(format,i,&p,&size,nullptr,nullptr);
                    if(status==noErr && (!p || !size || size>65536)) status=kVTParameterErr;
                    if(status==noErr) { ipbm::put32(out->parameters,uint32_t(size));
                        out->parameters.insert(out->parameters.end(),p,p+size); }
                }
            }
            CMBlockBufferRef block=CMSampleBufferGetDataBuffer(sample);
            size_t size=block?CMBlockBufferGetDataLength(block):0;
            if(status==noErr && (!size || size>ipbm::MaxBody-24)) status=kVTParameterErr;
            if(status==noErr) {
                out->nals.resize(size);
                status=CMBlockBufferCopyDataBytes(block,0,size,out->nals.data());
            }
        }
        return out;
    }
}

@implementation HEVCEncoder {
    VTCompressionSessionRef _session;
    uint32_t _width,_height;
    int _fps,_bitrate;
    uint64_t _epoch;
    uint64_t _colorChecks,_colorErrors;
    ipbm::ColorDescription _lastColor;
    void (^_output)(std::shared_ptr<MirrorEncoded>,int);
}
- (instancetype)initWithWidth:(uint32_t)width height:(uint32_t)height fps:(int)fps bitrate:(int)bitrate
    output:(void (^)(std::shared_ptr<MirrorEncoded>,int))output {
    if((self=[super init])) { _width=width;_height=height;_fps=fps;_bitrate=bitrate;_output=[output copy]; }
    return self;
}
- (BOOL)ready { return _session!=nullptr; }
- (NSDictionary<NSString*,id>*)colorDiagnostics {
    return @{@"capture":@"sRGB",@"expected_iso":@[@1,@13,@1],
        @"last_format_iso":@[@(_lastColor.primaries),@(_lastColor.transfer),@(_lastColor.matrix)],
        @"format_checks":@(_colorChecks),@"format_errors":@(_colorErrors)};
}
- (BOOL)prepare {
    if(_session) return YES;
    OSStatus result=VTCompressionSessionCreate(kCFAllocatorDefault,_width,_height,kCMVideoCodecType_HEVC,
        nullptr,nullptr,nullptr,nullptr,nullptr,&_session);
    if(result!=noErr || !_session) { NSLog(@"HEVC session create failed: %d",result); return NO; }
    NSDictionary *properties=@{(__bridge NSString*)kVTCompressionPropertyKey_RealTime:@YES,
        (__bridge NSString*)kVTCompressionPropertyKey_AllowFrameReordering:@NO,
        (__bridge NSString*)kVTCompressionPropertyKey_MaxKeyFrameInterval:@120,
        (__bridge NSString*)kVTCompressionPropertyKey_ExpectedFrameRate:@(_fps),
        (__bridge NSString*)kVTCompressionPropertyKey_ColorPrimaries:(__bridge NSString*)kCVImageBufferColorPrimaries_ITU_R_709_2,
        (__bridge NSString*)kVTCompressionPropertyKey_TransferFunction:(__bridge NSString*)kCVImageBufferTransferFunction_sRGB,
        (__bridge NSString*)kVTCompressionPropertyKey_YCbCrMatrix:(__bridge NSString*)kCVImageBufferYCbCrMatrix_ITU_R_709_2,
        (__bridge NSString*)kVTCompressionPropertyKey_AverageBitRate:@(_bitrate)};
    result=VTSessionSetProperties(_session,(__bridge CFDictionaryRef)properties);
    if(result==noErr) result=VTCompressionSessionPrepareToEncodeFrames(_session);
    if(result!=noErr) { NSLog(@"HEVC configure failed: %d",result); [self invalidate]; return NO; }
    return YES;
}
- (void)invalidate {
    if(!_session) return;
    // Per-frame output blocks own their surfaces even when teardown cancels output.
    // Invalidate the epoch before teardown can invoke callbacks for the old session.
    ++_epoch;_pending=0;
    VTCompressionSessionInvalidate(_session); CFRelease(_session); _session=nullptr;
}
- (BOOL)submit:(MirrorFramePtr)frame geometry:(ipbm::Geometry)geometry keyframe:(BOOL)force {
    if(!_session || _pending>=3) return NO;
    NSDictionary *options=force?@{(__bridge NSString*)kVTEncodeFrameOptionKey_ForceKeyFrame:@YES}:nil;
    ++_pending;uint64_t epoch=_epoch;
    __weak HEVCEncoder *weak=self;
    frame->timing.submitBegin=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    OSStatus result=VTCompressionSessionEncodeFrameWithOutputHandler(_session,frame->pixels(),
        CMTimeMake(int64_t(frame->pts),1000000000),CMTimeMake(1,_fps),
        (__bridge CFDictionaryRef)options,nullptr,^(OSStatus status,VTEncodeInfoFlags flags,CMSampleBufferRef sample) {
            const uint64_t callback=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
            // Capturing the shared frame in the copied output block owns pixel storage
            // through completion OR cancellation; no manually freed callback token.
            auto out=encoded(frame,geometry,status,flags,sample,callback);
            dispatch_async(dispatch_get_main_queue(),^{
                // Main copies after submit has returned, including inline callbacks.
                // The callback never reads timing fields that submit is still updating.
                uint64_t callback=out->timing.callback;
                out->timing=frame->timing;out->timing.callback=callback;
                [weak complete:out status:status epoch:epoch];
            });
        });
    frame->timing.submitEnd=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    if(result!=noErr) {
        NSLog(@"HEVC submit failed: %d",result); [self invalidate]; return NO;
    } return YES;
}
- (void)complete:(std::shared_ptr<MirrorEncoded>)frame status:(int)status epoch:(uint64_t)epoch {
    if(epoch!=_epoch) return;
    if(_pending) --_pending;
    if(frame->colorChecked) {
        ++_colorChecks;_lastColor=frame->color;
        if(!_lastColor.canonical()) ++_colorErrors;
    }
    if(_output) _output(frame,status);
}
- (void)dealloc { [self invalidate]; }
@end
