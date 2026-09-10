// Publication scheduling state; no capture requests are generated on completion.
#include "FrameUpdateRetry.h"
#include <atomic>
#include <cassert>
#include <cstdio>
#include <thread>
#include <vector>

int main() {
    TVFrameUpdateRetry state;
    assert(!state.active() && !state.queuePublication());
    state.start();
    state.started();state.started();
    assert(!state.capacityAvailable(1) && !state.capacityAvailable(2));
    assert(state.capacityAvailable(0) && state.capacityAvailable(-1));
    state.finished();assert(!state.capacityAvailable(1));
    state.finished();assert(state.capacityAvailable(1) && state.inflight()==0);

    // Client threads can all finish under sendMutex without capture/publish work.
    std::atomic<int> queued{0};std::atomic<uint64_t> generation{0};
    std::vector<std::thread> clients;
    for(int i=0;i<8;++i) {
        state.started();
        clients.emplace_back([&] {
            state.finished();
            uint64_t value=state.queuePublication();
            if(value) { ++queued;generation=value; }
        });
    }
    for(auto &client:clients)client.join();
    assert(queued==1 && state.inflight()==0 && state.takePublication(generation));
    assert(!state.takePublication(generation));

    // Old queued work cannot consume a new session's publication obligation.
    uint64_t stale=state.queuePublication();state.stop();state.start();
    assert(!state.queuePublication()); // Still only one physical callback queued.
    assert(!state.takePublication(stale));
    uint64_t fresh=state.queuePublication();assert(fresh && fresh!=stale);
    assert(!state.takePublication(stale) && state.takePublication(fresh));
    fresh=state.queuePublication();state.stop();
    assert(!state.active() && !state.takePublication(fresh) && !state.queuePublication());

    // A failed overwrite cannot publish partial pixels; a force obligation survives.
    TVPreparedFrame frame;assert(!frame.valid && frame.forceFull);
    frame.prepared(1,false,true);assert(frame.valid && frame.forceFull && frame.fullHashes);
    frame.published();assert(!frame.valid && !frame.forceFull && !frame.fullHashes);
    frame.prepared(2,true,true);frame.beginOverwrite();
    assert(!frame.valid && frame.forceFull && !frame.fullHashes);
    frame.prepared(3,false,false);assert(frame.valid && frame.forceFull && frame.rotation==3);
    frame.reset();assert(!frame.valid && frame.forceFull && frame.rotation==0);
    puts("4 publication state cases passed");
}
