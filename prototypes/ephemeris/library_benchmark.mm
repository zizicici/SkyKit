// Same-process simulator comparison. Time preparation and kernel load excluded.
#import <Mooninfo/Mooninfo.h>
#include "native/lunar.h"
extern "C" {
#include "astronomy.h"
}
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

struct Input { astro_time_t time; double tdb,tt,ut1; int64_t unixSeconds; };
static volatile double sink=0;
static double run(void *kernel,int library,int op,const Input &in,int index) {
    double out[8];
    if (library==0) {
        if (op==0) { if(lunar_phase(kernel,in.tdb,in.tt,out)) std::abort(); }
        if (op==1) { if(lunar_illumination(kernel,in.tdb,out)) std::abort(); }
        if (op==2) { if(lunar_quarter(kernel,in.tdb,index%4,40,out)) std::abort(); }
        if (op==3) { if(lunar_horizontal(kernel,in.tdb,in.tt,in.ut1,0,0,1.3521,103.8198,0,out)) std::abort(); }
        return out[0];
    }
    if (library==1) {
        auto time=in.time;
        if (op==0) { auto r=Astronomy_MoonPhase(time);if(r.status)std::abort();return r.angle; }
        if (op==1) { auto r=Astronomy_Illumination(BODY_MOON,time);if(r.status)std::abort();return r.phase_fraction; }
        if (op==2) { auto r=Astronomy_SearchMoonPhase(90*(index%4),time,40);if(r.status)std::abort();return r.time.ut; }
        auto observer=Astronomy_MakeObserver(1.3521,103.8198,0);
        auto equ=Astronomy_Equator(BODY_MOON,&time,observer,EQUATOR_OF_DATE,ABERRATION);
        if(equ.status)std::abort();
        return Astronomy_Horizon(&time,observer,equ.ra,equ.dec,REFRACTION_NONE).azimuth;
    }
    if (op==1) return MooninfoAt(in.unixSeconds);
    switch(index%4) {
        case 0:return MooninfoNextNewMoon(in.unixSeconds);
        case 1:return MooninfoNextWaxingMoon(in.unixSeconds);
        case 2:return MooninfoNextFullMoon(in.unixSeconds);
        default:return MooninfoNextWaningMoon(in.unixSeconds);
    }
}

int main(int argc,char **argv) {
    if(argc!=2)return 2;
    void *kernel=lunar_open(argv[1]);if(!kernel)return 3;
    const char *libraries[]={"nativeDE440","astronomyEngine","mooninfoGo"};
    const char *operations[]={"phaseAngle","illumination","nextQuarter","horizontal"};
    std::puts("{\"microsecondsPerCall\":{");
    bool comma=false;
    // Dense camera dates vs diverse photo dates. Fresh time struct per call
    // prevents Astronomy Engine's per-time cache leaking between operations.
    for(int pattern=0;pattern<2;++pattern) {
        std::vector<Input> inputs;
        for(int i=0;i<1000;++i) {
            double days=pattern?7305.+((i*977)%3652):9500.+i*.0001;
            auto t=Astronomy_TimeFromDays(days);
            // For timing, all libraries see the same approximate instant.
            // TT is supplied by AE's Delta-T model; TDB=TT (<2 ms distinction).
            inputs.push_back({t,t.tt,t.tt,t.ut,int64_t(std::llround((days+10957.5)*86400))});
        }
        for(int op=0;op<4;++op) {
            std::vector<double> samples[3];
            for(int lib=0;lib<3;++lib) {
                if(lib==2 && (op==0 || op==3))continue;
                for(int i=0;i<20;++i)sink=run(kernel,lib,op,inputs[i],i);
            }
            for(int batch=0;batch<7;++batch) {
                // Rotate order across batches to reduce warm-up/thermal bias.
                for(int offset=0;offset<3;++offset) {
                    int lib=(batch+offset)%3;
                    if(lib==2 && (op==0 || op==3))continue;
                    int n=op==2?300:3000; double sum=0;
                    auto start=std::chrono::steady_clock::now();
                    for(int i=0;i<n;++i)sum+=run(kernel,lib,op,inputs[i%inputs.size()],i);
                    double us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count()/n;
                    if(!std::isfinite(sum))return 4;sink=sum;
                    samples[lib].push_back(us);
                }
            }
            for(int lib=0;lib<3;++lib) {
                auto &a=samples[lib];if(a.empty())continue;
                auto sorted=a;std::sort(sorted.begin(),sorted.end());
                std::printf("%s\"%s.%s.%s\":{\"median\":%.6f,\"batches\":[",comma?",\n":"",pattern?"spreadDates":"cameraDates",libraries[lib],operations[op],sorted[3]);
                for(size_t j=0;j<a.size();++j)std::printf("%s%.6f",j?",":"",a[j]);
                std::printf("]}");comma=true;std::fflush(stdout);
            }
        }
    }
    std::puts("\n}}");lunar_close(kernel);
}
