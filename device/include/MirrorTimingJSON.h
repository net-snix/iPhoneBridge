// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#pragma once
#import <Foundation/Foundation.h>
#include "MirrorTiming.h"

namespace ipbm {
// Called only on GET_STATS, never in the capture or HID hot paths.
inline NSDictionary *timingJSON(const RecentTimings<InputTiming>& inputs,
    const RecentTimings<FrameTiming>& frames,CaptureCounters capture,
    uint64_t inputCount,uint64_t afterIdleCount) {
    NSMutableArray *inputRows=[NSMutableArray arrayWithCapacity:inputs.size()];
    inputs.each([&](const InputTiming& t) {
        [inputRows addObject:@{@"sequence":@(t.sequence),@"peer":@(t.peer),@"request":@(t.request),
            @"generation":@(t.generation),@"received_ns":@(t.received),@"handled_ns":@(t.handled),
            @"dispatch_begin_ns":@(t.dispatchBegin),@"dispatch_end_ns":@(t.dispatchEnd),
            @"next_attempt_ns":@(t.nextAttempt),@"first_frame_pts_ns":@(t.firstFramePts)}];
    });
    NSMutableArray *frameRows=[NSMutableArray arrayWithCapacity:frames.size()];
    frames.each([&](const FrameTiming& t) {
        [frameRows addObject:@{@"input_sequence":@(t.inputSequence),@"pts_ns":@(t.pts),
            @"generation":@(t.generation),@"capture_begin_ns":@(t.captureBegin),@"dirty_read_ns":@(t.dirtyRead),
            @"render_end_ns":@(t.renderEnd),@"transfer_end_ns":@(t.transferEnd),
            @"submit_begin_ns":@(t.submitBegin),@"submit_end_ns":@(t.submitEnd),
            @"callback_ns":@(t.callback),@"completed_ns":@(t.completed),@"queued_ns":@(t.queued),
            @"submit_gap_ns":@(t.submitGap),@"bytes":@(t.bytes),@"queued_bytes":@(t.queuedBytes),
            @"status":@(t.status),@"forced_keyframe":@(t.forcedKeyframe),
            @"after_idle":@(t.afterIdle),@"keyframe":@(t.keyframe)}];
    });
    return @{@"clock":@"CLOCK_UPTIME_RAW",@"capacity":@40,@"key_down_count":@(inputCount),
        @"after_idle_submissions":@(afterIdleCount),@"inputs":inputRows,@"frames":frameRows,
        @"capture":@{@"display_ticks":@(capture.displayTicks),@"attempts":@(capture.attempts),
            @"dirty_skips":@(capture.dirtySkips),@"no_surface":@(capture.noSurface),
            @"transfer_errors":@(capture.transferErrors)}};
}
} // namespace ipbm
