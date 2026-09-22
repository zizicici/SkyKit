// Measurement-only harness; does not alter the native library.
#include "native/lunar.h"
extern "C" {
#include "erfa.h"
}
#include <mach/mach.h>
#include <malloc/malloc.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static volatile double sink;
struct Memory { uint64_t footprint,resident,heap; };
Memory memory() {
    task_vm_info_data_t info{}; mach_msg_type_number_t count=TASK_VM_INFO_COUNT;
    if(task_info(mach_task_self(),TASK_VM_INFO,reinterpret_cast<task_info_t>(&info),&count)!=KERN_SUCCESS)std::abort();
    malloc_statistics_t stats{};malloc_zone_statistics(nullptr,&stats);
    return {info.phys_footprint,info.resident_size,stats.size_in_use};
}
void print(const char *name,Memory value,bool comma=true) {
    std::printf("%s\"%s\":{\"footprintBytes\":%llu,\"residentBytes\":%llu,\"heapBytes\":%llu}",
        comma?",":"",name,(unsigned long long)value.footprint,(unsigned long long)value.resident,(unsigned long long)value.heap);
}
int main(int argc,char **argv) {
    if(argc<2)return 2;
    // Warm measurement/stdio bookkeeping before taking the baseline.
    memory();std::printf("{");std::fflush(stdout);
    if(argc==3 && !std::strcmp(argv[2],"map")) {
        auto baseline=memory();int fd=open(argv[1],O_RDONLY);if(fd<0)return 3;
        struct stat stat{};if(fstat(fd,&stat))return 3;
        auto data=static_cast<const unsigned char*>(mmap(nullptr,stat.st_size,PROT_READ,MAP_PRIVATE,fd,0));
        if(data==MAP_FAILED)return 3;close(fd);
        auto mapped=memory();uint64_t sum=0;
        // Scan all bytes, like a whole-file validation. Mapping is not assumed
        // to make resident bytes disappear; this tests clean versus dirty pages.
        for(off_t i=0;i<stat.st_size;++i)sum+=data[i];sink=double(sum);
        auto touched=memory();munmap(const_cast<unsigned char*>(data),stat.st_size);
        auto closed=memory();print("baseline",baseline,false);print("mapped",mapped);
        print("touchedAllBytes",touched);print("closed",closed);std::puts("}");return 0;
    }
    auto baseline=memory();void *kernel=lunar_open(argv[1]);if(!kernel)return 3;
    auto loaded=memory();double v[8];
    if(lunar_observe(kernel,9500,9500,9499.9992,0,0,0,0,89.9,103.8,0,v))return 4;sink=v[0];
    auto first=memory();
    for(int i=0;i<100000;++i) {
        double t=9500+(i%1000)*.0001;
        if(lunar_observe(kernel,t,t,t-.0008,0,0,0,0,89.9,103.8,0,v))return 4;sink=v[0];
    }
    auto steady=memory();
    print("baseline",baseline,false);print("loaded",loaded);print("firstObservation",first);print("after100000",steady);
    std::printf(",\"microsecondsPerCall\":{");
    const char *names[]={"observation","earthRotation","xysSeries","rotationWithPreparedXYS"};
    for(int pattern=0;pattern<2;++pattern) {
        for(int op=0;op<4;++op) {
            std::array<double,5> batches{};double px,py,ps;eraXys06a(2451545,9500,&px,&py,&ps);
            for(auto &elapsed:batches) {
                auto begin=std::chrono::steady_clock::now();double sum=0;
                for(int i=0;i<10000;++i) {
                    double t=pattern?7305+((i*977)%3652):9500+(i%1000)*.0001;
                    double a[9],x,y,s,cirs[3][3],polar[3][3],matrix[3][3];
                    if(op==0) {if(lunar_observe(kernel,t,t,t-.0008,0,0,0,0,1.3521,103.8198,0,a))return 4;sum+=a[0];}
                    if(op==1) {if(lunar_rotation(t,t-.0008,0,0,0,0,a))return 4;sum+=a[0];}
                    if(op==2) {eraXys06a(2451545,t,&x,&y,&s);sum+=x+y+s;}
                    // Cost experiment only: fixed XYS is NOT valid for arbitrary
                    // dates. A production cache would need time interpolation.
                    if(op==3) {eraC2ixys(px,py,ps,cirs);eraPom00(0,0,eraSp00(2451545,t),polar);
                        eraC2tcio(cirs,eraEra00(2451545,t-.0008),polar,matrix);sum+=matrix[0][0];}
                }
                elapsed=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-begin).count()/10000;sink=sum;
            }
            std::sort(batches.begin(),batches.end());std::printf("%s\"%s.%s\":%.6f",pattern||op?",":"",pattern?"spread":"camera",names[op],batches[2]);
        }
    }
    std::printf("}");lunar_close(kernel);print("closed",memory());std::puts("}");
}
