#include "CaptureGovernor.h"
#include <cassert>
#include <cmath>
#include <cstdio>

namespace {

// Drive one evaluation window: report `completedJobs` finished updates and
// `blockedJobs` discarded publications, then evaluate at the window edge.
bool window(TVCaptureGovernor &governor, double &now, int completedJobs, int blockedJobs, double *fps) {
    for (int i = 0; i < completedJobs; ++i) governor.completed();
    for (int i = 0; i < blockedJobs; ++i) governor.blocked();
    now += TVCaptureGovernor::kEvaluateInterval;
    return governor.evaluate(now, fps);
}

const int kHalfSecondJobs = 15; // 30 updates/s sustained

} // namespace

int main() {
    int cases = 0;
    const double interval = TVCaptureGovernor::kEvaluateInterval;

    // 1. Over-drive pacing: back off to exactly the delivered rate, no headroom.
    {
        TVCaptureGovernor governor;
        governor.configure(60);
        double now = 0, fps = 0;
        assert(!governor.evaluate(now, &fps)); // first call only opens the window
        assert(governor.current() == 60);
        assert(window(governor, now, kHalfSecondJobs, 1, &fps));
        assert(std::fabs(fps - 30.0) < 1e-6);
        assert(std::fabs(governor.current() - 30.0) < 1e-6);
        ++cases;
    }

    // 2. A healthy session near the ceiling is never paced down. Occasional
    //    blocking at 59-60 delivered must not clamp a working mirror.
    {
        TVCaptureGovernor governor;
        governor.configure(60);
        double now = 0, fps = 0;
        assert(!governor.evaluate(now, &fps));
        for (int round = 0; round < 10; ++round)
            assert(!window(governor, now, 30, 1, &fps)); // 60/s delivered, some blocking
        assert(std::fabs(governor.current() - 60.0) < 1e-6);
        ++cases;
    }

    // 3. Recovery is slow and stepwise: a sustained clean period is required
    //    before probing upward, and then only by one step.
    {
        TVCaptureGovernor governor;
        governor.configure(60);
        double now = 0, fps = 0;
        assert(!governor.evaluate(now, &fps));
        assert(window(governor, now, kHalfSecondJobs, 1, &fps)); // settle at 30
        assert(std::fabs(governor.current() - 30.0) < 1e-6);
        for (uint32_t i = 0; i < TVCaptureGovernor::kCleanWindowsBeforeRaise - 1; ++i)
            assert(!window(governor, now, kHalfSecondJobs, 0, &fps));
        assert(std::fabs(governor.current() - 30.0) < 1e-6);
        assert(window(governor, now, kHalfSecondJobs, 0, &fps));
        assert(std::fabs(fps - (30.0 + TVCaptureGovernor::kRecoverStepFps)) < 1e-6);
        ++cases;
    }

    // 4. A single blocked window restarts the clean-window requirement, so a
    //    struggling phone cannot ratchet upward one probe at a time.
    {
        TVCaptureGovernor governor;
        governor.configure(60);
        double now = 0, fps = 0;
        assert(!governor.evaluate(now, &fps));
        assert(window(governor, now, kHalfSecondJobs, 1, &fps));
        for (uint32_t i = 0; i < TVCaptureGovernor::kCleanWindowsBeforeRaise - 1; ++i)
            assert(!window(governor, now, kHalfSecondJobs, 0, &fps));
        assert(!window(governor, now, kHalfSecondJobs, 1, &fps)); // same rate, counter reset
        for (uint32_t i = 0; i < TVCaptureGovernor::kCleanWindowsBeforeRaise - 1; ++i)
            assert(!window(governor, now, kHalfSecondJobs, 0, &fps));
        assert(std::fabs(governor.current() - 30.0) < 1e-6);
        ++cases;
    }

    // 5. Bounds: a stalled pipeline stops at the floor, never at zero.
    {
        TVCaptureGovernor governor;
        governor.configure(60);
        double now = 0, fps = 0;
        assert(!governor.evaluate(now, &fps));
        assert(window(governor, now, 0, 1, &fps));
        assert(std::fabs(fps - TVCaptureGovernor::kFloorFps) < 1e-6);
        for (int round = 0; round < 5; ++round) window(governor, now, 0, 1, &fps);
        assert(governor.current() >= TVCaptureGovernor::kFloorFps);
        ++cases;
    }

    // 6. Recovery never exceeds the configured ceiling.
    {
        TVCaptureGovernor governor;
        governor.configure(40);
        double now = 0, fps = 0;
        assert(!governor.evaluate(now, &fps));
        assert(window(governor, now, kHalfSecondJobs, 1, &fps)); // 30
        for (int round = 0; round < 400; ++round) window(governor, now, 1000, 0, &fps);
        assert(governor.current() <= 40.0 + 1e-6);
        assert(std::fabs(governor.current() - 40.0) < 1e-6);
        ++cases;
    }

    // 7. A ceiling of zero disables pacing: no evaluation ever asks for a change.
    {
        TVCaptureGovernor governor;
        governor.configure(0);
        double now = 0, fps = -1;
        for (int round = 0; round < 50; ++round)
            assert(!window(governor, now, 0, 5, &fps));
        assert(fps == -1);
        ++cases;
    }

    // 8. A new capture session starts from the ceiling, never from the paced
    //    rate a previous session settled on.
    {
        TVCaptureGovernor governor;
        governor.configure(60);
        double now = 0, fps = 0;
        assert(!governor.evaluate(now, &fps));
        assert(window(governor, now, kHalfSecondJobs, 1, &fps));
        assert(std::fabs(governor.current() - 30.0) < 1e-6);
        governor.reset();
        assert(std::fabs(governor.current() - 60.0) < 1e-6);
        // Counters cleared too: the first window after reset sees only new work.
        now = 0;
        assert(!governor.evaluate(now, &fps));
        assert(!window(governor, now, 30, 0, &fps));
        ++cases;
    }

    (void)interval;
    printf("%d capture governor cases passed\n", cases);
    return 0;
}
