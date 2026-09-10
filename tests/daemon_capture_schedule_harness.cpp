#include "CaptureSchedule.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <thread>
#include <vector>
int main() {
    const double period=1.0/60.0;
    TVCaptureSchedule schedule;schedule.start(60);
    std::vector<double> starts;
    double wake=-1;uint64_t wakeGeneration=0;
    auto attempt=[&](double now) {
        auto plan=schedule.plan(now);
        if(!plan.generation)return;
        if(plan.delay>0) {
            if(schedule.claimWake(plan.generation)){wake=now+plan.delay+1e-9;wakeGeneration=plan.generation;}
        } else if(schedule.beginCapture(plan.generation,now))starts.push_back(now);
    };
    for(int tick=0;tick<120;++tick) {
        double now=tick*period+(tick%2? -0.00003:0);
        if(wake>=0&&wake<=now){double at=wake;wake=-1;if(schedule.takeWake(wakeGeneration))attempt(at);}
        schedule.request();attempt(now);
    }
    if(wake>=0&&schedule.takeWake(wakeGeneration))attempt(wake);
    assert(starts.size()>=118&&starts.size()<=120);
    for(size_t i=1;i<starts.size();++i)assert(starts[i]-starts[i-1]>=period-1e-12);
    schedule.stop();schedule.start(60);auto old=schedule.request();assert(schedule.claimWake(old));
    schedule.stop();schedule.start(60);auto current=schedule.request();
    assert(!schedule.takeWake(old)&&schedule.beginCapture(current,0));
    schedule.setMaximumFps(30);schedule.request();
    assert(!schedule.beginCapture(current,period));assert(schedule.beginCapture(current,2*period));
    schedule.stop();schedule.start(60);
    std::vector<std::thread> threads;
    for(int i=0;i<8;++i)threads.emplace_back([&]{for(int j=0;j<10000;++j)schedule.request();});
    for(auto &thread:threads)thread.join();
    auto plan=schedule.plan(0);assert(plan.generation&&schedule.claimWake(plan.generation));
    assert(!schedule.claimWake(plan.generation)&&schedule.takeWake(plan.generation));
    assert(schedule.beginCapture(plan.generation,0)&&!schedule.beginCapture(plan.generation,0));
    printf("4 cap cases passed; early jitter retains %.1f fps with 60 fps ceiling\n",starts.size()/2.0);
}
