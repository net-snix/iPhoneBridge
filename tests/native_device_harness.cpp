// Native device invariants run on the host without private iOS frameworks.
#include "MirrorProtocol.h"
#include "CaptureSchedule.h"
#include "MirrorTiming.h"
#include <cassert>
#include <iostream>
#include <iomanip>
#include <string>
#include <thread>
#include <array>
using namespace ipbm;

static Bytes unhex(const std::string& input) {
    assert(input.size()%2==0);Bytes out;
    for(size_t i=0;i<input.size();i+=2) out.push_back(uint8_t(std::stoul(input.substr(i,2),nullptr,16)));
    return out;
}
static void parserTests() {
    Bytes payload; for(auto x:{1,1,42,99}) put32(payload,x);
    auto bytes=message(Pointer,7,payload);
    for(size_t cut=0;cut<=bytes.size();++cut) {
        RequestParser parser;int count=0;
        auto consume=[&](Command c) {++count;assert(c.type==Pointer && c.request==7 && c.payload==payload);return true;};
        assert(parser.feed(bytes.data(),cut,consume));assert(parser.feed(bytes.data()+cut,bytes.size()-cut,consume));
        assert(count==1 && parser.buffered()==0);
    }
    RequestParser oneByte;int count=0;
    for(auto byte:bytes) assert(oneByte.feed(&byte,1,[&](Command){++count;return true;}));
    assert(count==1);
    Bytes stream=bytes;stream.insert(stream.end(),bytes.begin(),bytes.end());
    RequestParser multiple;count=0;
    assert(multiple.feed(stream.data(),stream.size(),[&](Command){++count;return true;}));assert(count==2);
    for(uint32_t length:{0U,7U,41U,0xffffffffU}) {
        Bytes b;put32(b,length);RequestParser parser;
        assert(!parser.feed(b.data(),b.size(),[](Command){return true;}));
        assert(parser.buffered()==4); // invalid length rejected before allocating its body
    }
    for(size_t field:{5U,6U,7U}) {
        auto bad=bytes;bad[field]=1;RequestParser parser;
        assert(!parser.feed(bad.data(),bad.size(),[](Command){return true;}));
    }
    auto zero=message(Ping,0);RequestParser parser;
    assert(!parser.feed(zero.data(),zero.size(),[](Command){return true;}));
    auto opaque=message(Ping,9,Bytes(32,42));RequestParser maximum;
    assert(maximum.feed(opaque.data(),opaque.size(),[](Command c){return c.payload.size()==32;}));
    RequestParser saturated;
    assert(!saturated.feed(bytes.data(),bytes.size(),[](Command){return false;}));
}
static void geometryTests() {
    Geometry g{1170,2532,0,1};
    constexpr uint32_t expected[4][4]={{0,0,1169,2531},{0,2531,1169,0},
        {1169,2531,0,0},{1169,0,0,2531}};
    for(uint32_t q=0;q<4;++q) {
        g.turns=q;uint32_t x,y;
        assert(g.point(0,0,x,y) && x==expected[q][0] && y==expected[q][1]);
        assert(g.point(g.displayWidth()-1,g.displayHeight()-1,x,y) && x==expected[q][2] && y==expected[q][3]);
        assert(!g.point(g.displayWidth(),0,x,y));assert(!g.point(0,g.displayHeight(),x,y));
        assert(!g.point(UINT32_MAX,UINT32_MAX,x,y));
        // Every physical point has exactly one displayed inverse across all four orientations.
        for(uint32_t px=0;px<g.width;px+=39) for(uint32_t py=0;py<g.height;py+=37) {
            uint32_t dx=q==0?px:q==1?g.height-1-py:q==2?g.width-1-px:py;
            uint32_t dy=q==0?py:q==1?px:q==2?g.height-1-py:g.width-1-px;
            assert(g.point(dx,dy,x,y) && x==px && y==py);
        }
    }
    g.generation=UINT32_MAX;g.changed();assert(g.generation==1);
}
static void leaseTests() {
    InputLease lease;assert(!lease.acquire(0,0));assert(!lease.touch(1,0));
    assert(lease.acquire(1,100));assert(!lease.acquire(2,101));assert(!lease.release(2));
    assert(!lease.expired(100+InputLease::Timeout-1));assert(lease.expired(100+InputLease::Timeout));
    assert(lease.touch(1,1000));assert(!lease.expired(1000+InputLease::Timeout-1));
    assert(lease.release(1));assert(!lease.expired(UINT64_MAX));assert(lease.acquire(2,3000));
}
static void poolTests() {
    std::array<std::shared_ptr<SlotLease>,3> pool;
    std::vector<std::shared_ptr<SlotClaim>> frames;
    for(auto& slot:pool) { slot=std::make_shared<SlotLease>();assert(slot->claim());frames.push_back(std::make_shared<SlotClaim>(slot)); }
    for(auto& slot:pool) assert(!slot->claim());
    auto encoderToken=frames[0];frames[0].reset();assert(!pool[0]->claim());
    encoderToken.reset();assert(pool[0]->claim());pool[0]->release();
    // Releasing the capture owner cannot destroy a slot still used by a callback.
    auto callback=frames[1];auto retained=pool[1];pool[1].reset();frames[1].reset();
    assert(retained->busy);callback.reset();assert(!retained->busy);
    auto slot=pool[0];std::atomic<int> inside{0},visits{0};
    std::vector<std::thread> threads;
    for(int i=0;i<8;++i) threads.emplace_back([&]{
        for(int j=0;j<2000;++j) if(slot->claim()) {
            assert(inside.fetch_add(1)==0);++visits;assert(inside.fetch_sub(1)==1);slot->release();
        }
    });
    for(auto& thread:threads) thread.join();assert(visits>0);
}
static void writeProgressTests() {
    WriteProgress progress;uint64_t written=0;
    // Stable and growing queues remain healthy past the five-second deadline.
    for(uint64_t seconds=1;seconds<=12;++seconds) {
        written+=4096;
        assert(!progress.stalled(100000+seconds*100,written,seconds*1000000000ULL));
    }
    assert(!progress.stalled(100000,written,16999999999ULL));
    assert(progress.stalled(100000,written,17000000000ULL));
    assert(!progress.stalled(0,written,18000000000ULL));
    assert(!progress.stalled(100,written,19000000000ULL));
    assert(!progress.stalled(100,written+1,23000000000ULL));
    assert(progress.stalled(100,written+1,28000000000ULL));
}
static void captureScheduleTests() {
    constexpr uint64_t period=16666667;
    for(uint64_t displayPeriod:{period,period/2}) {
        CaptureSchedule schedule;schedule.start(60);
        std::vector<uint64_t> starts;
        uint64_t wake=0,wakeGeneration=0;
        auto attempt=[&](uint64_t now) {
            const auto plan=schedule.plan(now);
            if(!plan.generation) return;
            if(plan.delay) {
                if(schedule.claimWake(plan.generation)) {
                    // Model a deadline timer with 0.1 ms scheduling latency.
                    wake=now+plan.delay+100000;wakeGeneration=plan.generation;
                }
                assert(!schedule.claimWake(plan.generation)); // one queued wake
            } else if(schedule.beginCapture(plan.generation,now)) {
                wake=0;starts.push_back(now);
            }
        };
        const uint64_t duration=10ULL*1000000000;
        for(uint64_t tick=0;tick*displayPeriod<duration;++tick) {
            // Alternating late ticks expose the old arrival-time skip gate.
            const uint64_t now=tick*displayPeriod+(tick%2?900000:0);
            while(wake && wake<=now) {
                const uint64_t deadline=wake;wake=0;
                if(schedule.takeWake(wakeGeneration)) attempt(deadline);
            }
            schedule.request();attempt(now);
        }
        if(wake && schedule.takeWake(wakeGeneration)) attempt(wake);
        const auto inWindow=std::count_if(starts.begin(),starts.end(),[&](uint64_t t){return t<duration;});
        assert(inWindow>=590 && inWindow<=600);
        for(size_t i=1;i<starts.size();++i) assert(starts[i]-starts[i-1]>=period);
        assert(!schedule.plan(duration*2).generation); // no work without a request
    }
    CaptureSchedule schedule;const uint64_t old=schedule.start(60);
    schedule.request();assert(schedule.beginCapture(old,0));
    schedule.request();assert(schedule.plan(1000).delay==period-1000);
    assert(schedule.claimWake(old));
    assert(schedule.takeWake(old)); // even an early timer must obey the deadline
    assert(!schedule.beginCapture(old,period-1));
    assert(schedule.claimWake(old));
    schedule.stop();schedule.request();assert(!schedule.plan(period).generation);
    assert(!schedule.takeWake(old));
    const uint64_t current=schedule.start(60);schedule.request();
    assert(schedule.beginCapture(current,period)); // restart is immediately eligible
    schedule.request();assert(schedule.claimWake(current));
    assert(!schedule.takeWake(old)); // stale wake must not consume the new request
    assert(schedule.takeWake(current));assert(schedule.beginCapture(current,10*period));
    schedule.request();assert(!schedule.beginCapture(current,10*period));
    assert(schedule.plan(10*period).delay==period); // stalled timer never catches up
    assert(schedule.beginCapture(current,11*period));
    schedule.stop();const uint64_t thirty=schedule.start(30);schedule.request();
    assert(schedule.beginCapture(thirty,0));schedule.request();
    assert(!schedule.beginCapture(thirty,33333333));
    assert(schedule.beginCapture(thirty,33333334));
}
static void timingHistoryTests() {
    RecentTimings<InputTiming> history;
    assert(!history.latest() && history.size()==0);
    size_t visits=0;history.each([&](const auto&){++visits;});assert(visits==0);
    for(uint64_t i=1;i<=85;++i) {
        InputTiming input;input.sequence=i;input.request=uint32_t(i);
        history.append(input);assert(history.latest()->sequence==i);
    }
    assert(history.size()==40);
    uint64_t expected=46;
    history.each([&](const InputTiming& input){assert(input.sequence==expected++);});
    assert(expected==86);
    history.latest()->firstFramePts=123;
    assert(history.latest()->sequence==85 && history.latest()->firstFramePts==123);
    RecentTimings<FrameTiming,1> single;
    FrameTiming frame;frame.pts=9;single.append(frame);frame.pts=10;single.append(frame);
    assert(single.size()==1 && single.latest()->pts==10);
}
int main(int argc,char**argv) {
    if(argc==4) {
        auto bytes=message(Type(std::stoul(argv[1])),uint32_t(std::stoul(argv[2])),unhex(argv[3]));
        for(auto x:bytes) std::cout<<std::hex<<std::setfill('0')<<std::setw(2)<<unsigned(x);
        std::cout<<'\n';return 0;
    }
    parserTests();geometryTests();leaseTests();poolTests();writeProgressTests();captureScheduleTests();timingHistoryTests();
    std::cout<<"native device invariants passed\n";
}
