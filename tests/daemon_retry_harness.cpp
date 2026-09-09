// Exercise the production retry state from the maintained daemon source patch.
#include "FrameUpdateRetry.h"
#include <atomic>
#include <cassert>
#include <string>
#include <thread>
#include <vector>

struct Screen {
    TVFrameUpdateRetry updates;
    int lastCaptured = 0;
    int published = 0;
    int publications = 0;
    bool forced = false;

    void tick(int dirtyCounter, int limit = 1) {
        bool changed = forced || dirtyCounter != lastCaptured;
        forced = false;
        if (!changed)
            return;
        // ScreenCapturer has already recorded this counter when the daemon's
        // frame handler is entered. A later resnapshot must override it.
        lastCaptured = dirtyCounter;
        if (updates.deferCapture(limit)) {
            forced |= updates.takeRetryIfReady(limit);
            return;
        }
        published = dirtyCounter;
        ++publications;
    }

    void finish(int limit = 1) { forced |= updates.finished(limit); }
};

void finalFrame() {
    Screen screen;
    screen.tick(1);
    screen.updates.started();
    screen.tick(2);
    assert(screen.lastCaptured == 2 && screen.published == 1);
    assert(!screen.forced); // No extra captures while the client is still busy.
    screen.finish();
    assert(screen.forced);
    screen.tick(2); // The physical screen is now unchanged.
    assert(screen.published == 2 && screen.publications == 2);
    screen.tick(2);
    assert(screen.publications == 2); // No polling redraw after recovery.
}

void latestFrame() {
    Screen screen;
    screen.tick(1);
    screen.updates.started();
    for (int counter = 2; counter <= 100; ++counter)
        screen.tick(counter);
    assert(screen.published == 1 && !screen.forced);
    screen.finish();
    screen.tick(100);
    assert(screen.published == 100 && screen.publications == 2);
    assert(!screen.updates.takeRetryIfReady(1));
}

void multipleClients() {
    for (int limit : {1, 2}) {
        Screen screen;
        screen.tick(1, limit);
        for (int i = 0; i < 3; ++i)
            screen.updates.started();
        screen.tick(2, limit);
        for (int remaining = 2; remaining >= 0; --remaining) {
            screen.finish(limit);
            assert(screen.updates.inflight() == remaining);
            if (remaining >= limit)
                assert(!screen.forced);
            else {
                assert(screen.forced);
                screen.tick(2, limit);
                assert(screen.published == 2);
                break;
            }
        }
        while (screen.updates.inflight())
            screen.finish(limit);
        assert(!screen.forced);
    }
    TVFrameUpdateRetry unlimited;
    unlimited.started();
    assert(!unlimited.deferCapture(0));
    assert(!unlimited.finished(0));
}

void busyAgain() {
    Screen screen;
    screen.tick(1);
    screen.updates.started();
    screen.tick(2);
    screen.finish();
    screen.updates.started(); // Another client starts before the next tick.
    screen.tick(2);
    assert(screen.published == 1 && !screen.forced);
    screen.finish();
    screen.tick(2);
    assert(screen.published == 2 && screen.publications == 2);
}

void concurrentCompletion() {
    // If completion races pending publication, either the completing client or
    // the capture-side recheck must request a retry. There is exactly one bit.
    for (int round = 0; round < 2000; ++round) {
        TVFrameUpdateRetry updates;
        updates.started();
        std::atomic<bool> go{false};
        bool completionRetry = false;
        std::thread client([&] {
            while (!go.load()) {}
            completionRetry = updates.finished(1);
        });
        go.store(true);
        if (round % 2)
            std::this_thread::yield();
        bool dropped = updates.deferCapture(1);
        bool captureRetry = dropped && updates.takeRetryIfReady(1);
        client.join();
        assert(updates.inflight() == 0);
        assert(int(completionRetry) + int(captureRetry) == int(dropped));
        assert(!updates.takeRetryIfReady(1));
    }
    TVFrameUpdateRetry updates;
    for (int i = 0; i < 8; ++i)
        updates.started();
    assert(updates.deferCapture(1));
    std::atomic<int> retries{0};
    std::vector<std::thread> clients;
    for (int i = 0; i < 8; ++i)
        clients.emplace_back([&] { if (updates.finished(1)) ++retries; });
    for (auto &client : clients)
        client.join();
    assert(retries.load() == 1 && updates.inflight() == 0);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    std::string test = argv[1];
    if (test == "final") finalFrame();
    else if (test == "latest") latestFrame();
    else if (test == "clients") multipleClients();
    else if (test == "busy-again") busyAgain();
    else if (test == "race") concurrentCompletion();
    else return 2;
}
