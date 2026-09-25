// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace ipbm {
// Main owns these fixed-size histories. No allocation or logging in frame/input paths.
template<class Record, size_t Capacity=40> class RecentTimings {
    std::array<Record,Capacity> records_{};
    size_t next_=0,count_=0;
public:
    static_assert(Capacity>0);
    Record& append(const Record& record) {
        auto& slot=records_[next_];slot=record;next_=(next_+1)%Capacity;
        if(count_<Capacity) ++count_;
        return slot;
    }
    Record* latest() { return count_?&records_[(next_+Capacity-1)%Capacity]:nullptr; }
    size_t size() const { return count_; }
    template<class Visit> void each(Visit visit) const {
        for(size_t i=0;i<count_;++i) visit(records_[(next_+Capacity-count_+i)%Capacity]);
    }
};
struct InputTiming {
    uint64_t sequence=0,peer=0;
    uint32_t request=0,generation=0;
    uint64_t received=0,handled=0,dispatchBegin=0,dispatchEnd=0;
    uint64_t nextAttempt=0,firstFramePts=0;
};
struct FrameTiming {
    uint64_t inputSequence=0,pts=0;
    uint32_t generation=0;
    uint64_t captureBegin=0,dirtyRead=0,renderEnd=0,transferEnd=0;
    uint64_t submitBegin=0,submitEnd=0,callback=0,completed=0,queued=0;
    uint64_t submitGap=0,bytes=0,queuedBytes=0;
    int status=0;
    bool forcedKeyframe=false,afterIdle=false,keyframe=false;
};
struct CaptureCounters {
    uint64_t displayTicks=0,attempts=0,dirtySkips=0,noSurface=0,transferErrors=0;
};
} // namespace ipbm
