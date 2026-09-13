// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#pragma once
#import <CoreGraphics/CoreGraphics.h>
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>

namespace ipbm {
// IPBM v1: SDR sRGB RGB pixels, carried as HEVC 709/sRGB/709 (ISO 1/13/1).
struct ColorDescription {
    int primaries=2,transfer=2,matrix=2; // ISO 2 means unspecified/unrecognized.
    bool canonical() const { return primaries==1 && transfer==13 && matrix==1; }
    static ColorDescription read(CMFormatDescriptionRef format) {
        if(!format) return {};
        auto string=[&](CFStringRef key) -> CFStringRef {
            CFTypeRef value=CMFormatDescriptionGetExtension(format,key);
            return value && CFGetTypeID(value)==CFStringGetTypeID()?(CFStringRef)value:nullptr;
        };
        return {CVColorPrimariesGetIntegerCodePointForString(string(kCMFormatDescriptionExtension_ColorPrimaries)),
            CVTransferFunctionGetIntegerCodePointForString(string(kCMFormatDescriptionExtension_TransferFunction)),
            CVYCbCrMatrixGetIntegerCodePointForString(string(kCMFormatDescriptionExtension_YCbCrMatrix))};
    }
};

inline bool attachCaptureColor(CVPixelBufferRef pixels) {
    CGColorSpaceRef color=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    if(!color) return false;
    CVBufferSetAttachment(pixels,kCVImageBufferCGColorSpaceKey,color,kCVAttachmentMode_ShouldPropagate);
    CGColorSpaceRelease(color);
    CVBufferSetAttachment(pixels,kCVImageBufferColorPrimariesKey,kCVImageBufferColorPrimaries_ITU_R_709_2,
                          kCVAttachmentMode_ShouldPropagate);
    CVBufferSetAttachment(pixels,kCVImageBufferTransferFunctionKey,kCVImageBufferTransferFunction_sRGB,
                          kCVAttachmentMode_ShouldPropagate);
    CVBufferSetAttachment(pixels,kCVImageBufferYCbCrMatrixKey,kCVImageBufferYCbCrMatrix_ITU_R_709_2,
                          kCVAttachmentMode_ShouldPropagate);
    return true;
}
}
