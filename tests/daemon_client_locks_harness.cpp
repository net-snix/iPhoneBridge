// Exercise the production scoped locks with LibVNC's iterator/refcount contract.
#include <atomic>
#include <cassert>
#include <cerrno>
#include <pthread.h>
#include <rfb/rfb.h>
#include <string>
#include <thread>
#include <vector>

struct rfbClient {
    pthread_mutex_t sendMutex = PTHREAD_MUTEX_INITIALIZER;
    std::atomic<int> refs{0};
    int sock = 1;
    rfbClient *next = nullptr;
};
struct rfbScreenInfo { rfbClient *head; };
struct rfbClientIterator { rfbScreenInfo *screen; rfbClient *next = nullptr; };

void rfbIncrClientRef(rfbClientPtr client) { client->refs.fetch_add(1); }
void rfbDecrClientRef(rfbClientPtr client) { assert(client->refs.fetch_sub(1) > 0); }
rfbClientIteratorPtr rfbGetClientIterator(rfbScreenInfoPtr screen) {
    auto iterator = new rfbClientIterator;
    iterator->screen = screen;
    return iterator;
}
rfbClientPtr rfbClientIteratorNext(rfbClientIteratorPtr iterator) {
    if (!iterator)
        return nullptr;
    if (!iterator->next)
        iterator->next = iterator->screen->head;
    else {
        auto previous = iterator->next;
        iterator->next = previous->next;
        rfbDecrClientRef(previous);
    }
    // The pinned LibVNC iterator excludes disconnected sockets even if their
    // client records have not yet been removed from the linked list.
    while (iterator->next && iterator->next->sock < 0)
        iterator->next = iterator->next->next;
    if (iterator->next)
        rfbIncrClientRef(iterator->next);
    return iterator->next;
}
void rfbReleaseClientIterator(rfbClientIteratorPtr iterator) {
    if (iterator && iterator->next)
        rfbDecrClientRef(iterator->next);
    delete iterator;
}

std::vector<pthread_mutex_t *> acquired;
std::vector<pthread_mutex_t *> released;
int trackedLock(pthread_mutex_t *mutex) {
    acquired.push_back(mutex);
    int result = pthread_mutex_lock(mutex);
    assert(result == 0);
    return result;
}
int trackedUnlock(pthread_mutex_t *mutex) {
    released.push_back(mutex);
    int result = pthread_mutex_unlock(mutex);
    assert(result == 0);
    return result;
}
// Observe the actual production header's lock operations; the fake LibVNC
// teardown below uses real pthread operations, outside these macros.
#define pthread_mutex_lock trackedLock
#define pthread_mutex_unlock trackedUnlock
#include "ClientSendLocks.h"
#undef pthread_mutex_lock
#undef pthread_mutex_unlock

void disconnectedClient() {
    rfbClient client;
    rfbScreenInfo screen{&client};
    TVClientSendLocks locks(&screen);
    assert(client.refs.load() == 1); // Survives iterator advancement/release.
    client.sock = -1;
    screen.head = nullptr; // Removal precedes LibVNC's refcount wait.
    std::atomic<bool> waiting{false};
    std::atomic<bool> deleted{false};
    std::thread cleanup([&] {
        waiting.store(true);
        while (client.refs.load() != 0)
            std::this_thread::yield();
        // Real LibVNC eventually locks sendMutex before destroying it.
        assert(pthread_mutex_trylock(&client.sendMutex) == 0);
        pthread_mutex_unlock(&client.sendMutex);
        deleted.store(true);
    });
    while (!waiting.load()) {}
    assert(!deleted.load());
    locks.unlock();
    cleanup.join();
    assert(deleted.load() && client.refs.load() == 0);
    assert(acquired == released && acquired.size() == 1);
    locks.unlock(); // Also safe when the destructor runs afterward.
    assert(released.size() == 1);
    pthread_mutex_destroy(&client.sendMutex);
}

void membershipChanges() {
    rfbClient first, second, newcomer;
    first.next = &second;
    rfbScreenInfo screen{&first};
    TVClientSendLocks locks(&screen);
    assert(first.refs.load() == 1 && second.refs.load() == 1);
    first.sock = -1;
    newcomer.next = &second;
    screen.head = &newcomer;
    pthread_mutex_lock(&newcomer.sendMutex);
    locks.unlock();
    assert(first.refs.load() == 0 && second.refs.load() == 0);
    assert(newcomer.refs.load() == 0);
    assert(acquired == released && acquired.size() == 2);
    assert(acquired[0] == &first.sendMutex && acquired[1] == &second.sendMutex);
    assert(pthread_mutex_trylock(&first.sendMutex) == 0);
    pthread_mutex_unlock(&first.sendMutex);
    assert(pthread_mutex_trylock(&second.sendMutex) == 0);
    pthread_mutex_unlock(&second.sendMutex);
    assert(pthread_mutex_trylock(&newcomer.sendMutex) == EBUSY);
    pthread_mutex_unlock(&newcomer.sendMutex);
    pthread_mutex_destroy(&first.sendMutex);
    pthread_mutex_destroy(&second.sendMutex);
    pthread_mutex_destroy(&newcomer.sendMutex);
}

void scopedCleanup() {
    rfbClient client;
    rfbScreenInfo screen{&client};
    {
        TVClientSendLocks locks(&screen);
        assert(client.refs.load() == 1);
        client.sock = -1; // Closed but still linked: the old iterator skips it.
    }
    assert(client.refs.load() == 0);
    assert(acquired == released && acquired.size() == 1);
    assert(pthread_mutex_trylock(&client.sendMutex) == 0);
    pthread_mutex_unlock(&client.sendMutex);
    pthread_mutex_destroy(&client.sendMutex);
}

void concurrentCleanup() {
    rfbClient clients[8];
    for (int i = 0; i < 7; ++i)
        clients[i].next = &clients[i + 1];
    rfbScreenInfo screen{clients};
    TVClientSendLocks locks(&screen);
    screen.head = nullptr;
    std::vector<std::thread> cleanup;
    for (auto &client : clients) {
        assert(client.refs.load() == 1);
        client.sock = -1;
        auto pointer = &client;
        cleanup.emplace_back([pointer] {
            while (pointer->refs.load() != 0)
                std::this_thread::yield();
            assert(pthread_mutex_trylock(&pointer->sendMutex) == 0);
            pthread_mutex_unlock(&pointer->sendMutex);
        });
    }
    locks.unlock();
    for (auto &thread : cleanup)
        thread.join();
    assert(acquired == released && acquired.size() == 8);
    for (auto &client : clients) {
        assert(client.refs.load() == 0);
        pthread_mutex_destroy(&client.sendMutex);
    }
}

int main(int argc, char **argv) {
    assert(argc == 2);
    std::string test = argv[1];
    if (test == "disconnect") disconnectedClient();
    else if (test == "membership") membershipChanges();
    else if (test == "scope") scopedCleanup();
    else if (test == "concurrent") concurrentCleanup();
    else return 2;
}
