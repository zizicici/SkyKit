#include "lunar.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <vector>

int main(int argc,char **argv) {
    if (argc!=2) return 2;
    auto start=std::chrono::steady_clock::now();
    void *kernel=lunar_open(argv[1]);
    if (!kernel) return 3;
    double load=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
    std::printf("{\"kernelLoadMilliseconds\":%.6f,\"microsecondsPerCall\":{",load);
    const char *names[]={"phaseAngle","nextQuarter","horizontal","illumination","observeWithCIP"};
    for (int op=0;op<5;++op) {
        double checksum=0;
        lunar_benchmark(kernel,op,50,&checksum);
        std::vector<double> samples;
        for (int batch=0;batch<5;++batch) {
            double elapsed=lunar_benchmark(kernel,op,op==1?1000:10000,&checksum);
            if (elapsed<0) {lunar_close(kernel);return 4;}
            samples.push_back(elapsed);
        }
        std::sort(samples.begin(),samples.end());
        std::printf("%s\"%s\":%.6f",op?",":"",names[op],samples[2]);
    }
    std::puts("}}");
    lunar_close(kernel);
}
