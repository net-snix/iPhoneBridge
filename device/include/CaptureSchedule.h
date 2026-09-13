// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include <algorithm>
#include <cstdint>

namespace ipbm {
// Main-thread-owned state: coalesce display requests behind one deadline wake.
// Deadlines follow actual starts, so late callbacks never cause catch-up bursts.
class CaptureSchedule {
public:
    struct Plan { uint64_t generation, delay; };

    uint64_t start(uint32_t fps) {
        ++generation_;
        active_ = true;
        requested_ = wakeQueued_ = captured_ = false;
        fps = std::max(1U, fps);
        interval_ = (1000000000ULL + fps - 1) / fps;
        return generation_;
    }
    void stop() {
        active_ = requested_ = wakeQueued_ = false;
        ++generation_;
    }
    void request() { if (active_) requested_ = true; }
    Plan plan(uint64_t now) const {
        if (!active_ || !requested_) return {0, 0};
        const uint64_t earliest = lastStart_ + interval_;
        return {generation_, captured_ && now < earliest ? earliest - now : 0};
    }
    bool claimWake(uint64_t generation) {
        if (!active_ || generation != generation_ || !requested_ || wakeQueued_) return false;
        wakeQueued_ = true;
        return true;
    }
    bool takeWake(uint64_t generation) {
        if (!active_ || generation != generation_ || !wakeQueued_) return false;
        wakeQueued_ = false;
        return requested_;
    }
    bool beginCapture(uint64_t generation, uint64_t now) {
        if (!active_ || generation != generation_ || !requested_ ||
            (captured_ && now < lastStart_ + interval_)) return false;
        requested_ = wakeQueued_ = false;
        captured_ = true;
        lastStart_ = now;
        return true;
    }

private:
    uint64_t generation_ = 0, interval_ = 16666667, lastStart_ = 0;
    bool active_ = false, requested_ = false, wakeQueued_ = false, captured_ = false;
};
}
