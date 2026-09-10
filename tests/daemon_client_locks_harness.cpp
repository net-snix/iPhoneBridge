// Exercise the actual try-lock header against the shared snapshot API contract.
// The library's real snapshot/refcount implementation has its own C regression.
#include <rfb/rfb.h>
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <vector>

static std::mutex membership;
static std::vector<rfbClientPtr> clients;
static bool allocationFails=false;
rfbBool rfbGetClientSnapshot(rfbScreenInfoPtr,rfbClientPtr **out,size_t *count) {
    *out=nullptr;*count=0;
    std::lock_guard<std::mutex> lock(membership);
    if(allocationFails)return false;
    if(clients.empty())return true;
    *out=(rfbClientPtr *)malloc(clients.size()*sizeof(rfbClientPtr));assert(*out);
    for(size_t i=0;i<clients.size();++i) { (*out)[i]=clients[i];++clients[i]->refs; }
    *count=clients.size();return true;
}
void rfbReleaseClientSnapshot(rfbClientPtr *snapshot,size_t count) {
    for(size_t i=0;i<count;++i)assert(snapshot[i]->refs.fetch_sub(1)>0);
    free(snapshot);
}
#include "ClientSendLocks.h"

int main() {
    rfbScreenInfo screen;rfbClient first,second,newcomer;
    { TVClientSendLocks empty(&screen);assert(empty.acquired() && empty.count()==0); }

    // Every client is retained before try-locks, including a closed linked client.
    clients={&first,&second};second.sock=-1;
    pthread_mutex_lock(&second.sendMutex);
    { TVClientSendLocks busy(&screen);assert(!busy.acquired() && busy.count()==0); }
    assert(first.refs==0 && second.refs==0);
    assert(pthread_mutex_trylock(&first.sendMutex)==0);pthread_mutex_unlock(&first.sendMutex);
    assert(pthread_mutex_trylock(&second.sendMutex)==EBUSY);pthread_mutex_unlock(&second.sendMutex);

    allocationFails=true;
    { TVClientSendLocks failure(&screen);assert(!failure.acquired()); }
    assert(first.refs==0 && second.refs==0);allocationFails=false;

    // Removal and a new head do not change the original unlock/reference targets.
    {
        TVClientSendLocks locks(&screen);assert(locks.acquired() && locks.count()==2);
        assert(first.refs==1 && second.refs==1);
        { std::lock_guard<std::mutex> lock(membership);clients={&newcomer}; }
        pthread_mutex_lock(&newcomer.sendMutex);
        std::atomic<bool> waiting{false},deleted{false};
        std::thread cleanup([&] {
            waiting=true;
            while(first.refs || second.refs)std::this_thread::yield();
            // Teardown must observe both client locks released before refcount zero.
            assert(pthread_mutex_trylock(&first.sendMutex)==0);
            assert(pthread_mutex_trylock(&second.sendMutex)==0);
            pthread_mutex_unlock(&first.sendMutex);pthread_mutex_unlock(&second.sendMutex);
            deleted=true;
        });
        while(!waiting)std::this_thread::yield();assert(!deleted);
        locks.unlock();cleanup.join();assert(deleted && first.refs==0 && second.refs==0);
        assert(newcomer.refs==0 && pthread_mutex_trylock(&newcomer.sendMutex)==EBUSY);
        pthread_mutex_unlock(&newcomer.sendMutex);locks.unlock();
    }
    // Scope exit also unlocks/releases a client that closes during publication.
    clients={&first};first.sock=1;
    { TVClientSendLocks scoped(&screen);assert(scoped.acquired());first.sock=-1; }
    assert(first.refs==0 && pthread_mutex_trylock(&first.sendMutex)==0);
    pthread_mutex_unlock(&first.sendMutex);
    pthread_mutex_destroy(&first.sendMutex);pthread_mutex_destroy(&second.sendMutex);
    pthread_mutex_destroy(&newcomer.sendMutex);
    puts("5 retained snapshot try-lock cases passed");
}
