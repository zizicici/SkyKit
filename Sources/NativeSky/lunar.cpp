#include "lunar.h"
extern "C" {
#include "erfa.h"
}
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
constexpr double pi = 3.14159265358979323846;
constexpr double c = 299792.458, au = 149597870.7, day = 86400.0;
constexpr double eventTolerance = 0.0001 / day;
using Vec = std::array<double, 3>;
Vec add(Vec a, Vec b) { for (int j=0;j<3;++j) a[j]+=b[j]; return a; }
Vec sub(Vec a, Vec b) { for (int j=0;j<3;++j) a[j]-=b[j]; return a; }
Vec scale(Vec a, double s) { for (double &x:a) x*=s; return a; }
double dot(Vec a, Vec b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
double norm(Vec a) { return std::sqrt(dot(a,a)); }
Vec unit(Vec a) { return scale(a,1/norm(a)); }
Vec rotate(double m[3][3], Vec a, bool transpose=false) {
    Vec b{};
    for (int i=0;i<3;++i) for (int j=0;j<3;++j) b[i]+=(transpose?m[j][i]:m[i][j])*a[j];
    return b;
}
double wrap(double x) { return x-360*std::floor(x/360); }
double residual(double x) { return wrap(x+180)-180; }
void require(bool condition) { if (!condition) throw std::runtime_error("invalid data or range"); }
void earthRotation(double tt,double ut1,double xp,double yp,double dx,double dy,double matrix[3][3]) {
    for (double x:{tt,ut1,xp,yp,dx,dy}) require(std::isfinite(x));
    require(tt>=-54786.5-1/day && tt<237407.5+1/day && std::abs(ut1-tt)<=.01);
    require(std::abs(xp)<=.001 && std::abs(yp)<=.001 && std::abs(dx)<=1e-4 && std::abs(dy)<=1e-4);
    double x,y,s,cirs[3][3],polar[3][3];
    // SOFA Earth Attitude cookbook 5.5: preserve the model CIO locator s,
    // add observed CIP offsets to X/Y, then apply ERA and terrestrial polar motion.
    eraXys06a(2451545,tt,&x,&y,&s);
    eraC2ixys(x+dx,y+dy,s,cirs);
    eraPom00(xp,yp,eraSp00(2451545,tt),polar);
    eraC2tcio(cirs,eraEra00(2451545,ut1),polar,matrix);
}
template<class T> T read(std::ifstream &f) {
    T value; f.read(reinterpret_cast<char*>(&value),sizeof(value)); require(bool(f)); return value;
}
struct State { Vec p{}, v{}; };
struct Segment {
    uint32_t center, body, records, terms;
    double epoch, interval;
    std::vector<double> coefficients;
    State at(double t) const {
        double n=(t-epoch)/interval;
        require(std::isfinite(n) && n>=0 && n<records);
        auto record=static_cast<size_t>(std::floor(n));
        // Form the local interval offset directly. Subtracting a far-away
        // kernel epoch before extracting the fraction loses precision as
        // coverage grows (centimetres in barycentric positions over 300 yr).
        double x=2*((t-(epoch+record*interval))/interval)-1;
        State result;
        // Forward Chebyshev recurrence; retain original binary64 coefficients.
        for (int axis=0;axis<3;++axis) {
            const double *a=coefficients.data()+(record*3+axis)*terms;
            double t0=1,t1=x,d0=0,d1=1,p=a[0]+a[1]*x,v=a[1];
            for (uint32_t k=2;k<terms;++k) {
                double t2=2*x*t1-t0, d2=2*t1+2*x*d1-d0;
                p+=a[k]*t2; v+=a[k]*d2;
                t0=t1;t1=t2;d0=d1;d1=d2;
            }
            result.p[axis]=p; result.v[axis]=v*2/(interval*day);
        }
        return result;
    }
};
class Kernel {
public:
    double start,end;
    std::vector<Segment> segments;
    explicit Kernel(const char *path) {
        const uint16_t endian=1; require(*reinterpret_cast<const char*>(&endian)==1);
        std::ifstream f(path,std::ios::binary); require(bool(f));
        char magic[8];f.read(magic,8);require(bool(f));require(std::string(magic,8)=="LUNAR001");
        require(read<uint32_t>(f)==4);
        start=read<double>(f);end=read<double>(f);
        require(std::isfinite(start) && std::isfinite(end) && start<end);
        const std::array<std::array<uint32_t,2>,4> ids{{{0,3},{0,10},{3,399},{3,301}}};
        for (auto id:ids) {
            Segment s; s.center=read<uint32_t>(f);s.body=read<uint32_t>(f);
            s.epoch=read<double>(f);s.interval=read<double>(f);
            s.records=read<uint32_t>(f);s.terms=read<uint32_t>(f);
            require(s.center==id[0] && s.body==id[1]);
            require(std::isfinite(s.epoch) && std::isfinite(s.interval) && s.interval>0);
            require(s.records>0 && s.records<1000000 && s.terms>=2 && s.terms<=32);
            require(s.epoch<=start-1 && s.epoch+s.interval*s.records>end+1);
            auto offset=f.tellg();f.seekg(0,std::ios::end);auto remaining=f.tellg()-offset;f.seekg(offset);
            size_t count=size_t(s.records)*3*s.terms;
            require(remaining>=0 && uint64_t(remaining)>=count*sizeof(double) && count*sizeof(double)<100000000);
            s.coefficients.resize(count);
            f.read(reinterpret_cast<char*>(s.coefficients.data()),s.coefficients.size()*sizeof(double));
            require(bool(f));
            for (double a:s.coefficients) require(std::isfinite(a));
            segments.push_back(std::move(s));
        }
        require(f.peek()==std::ifstream::traits_type::eof());
    }
    void check(double t) const { require(std::isfinite(t) && t>=start && t<end); }
    void checkTT(double t,double tt) const {
        check(t);
        // These two arguments must describe the SAME instant. TT-TDB is under
        // 2 ms here; allow one second to accommodate caller approximations.
        require(std::isfinite(tt) && std::abs(tt-t)<=1/day);
    }
    // Internal times may use the padding for light time; public API checks coverage.
    State state(int body,double t) const {
        if (body==3) return segments[0].at(t);
        if (body==10) return segments[1].at(t);
        require(body==301 || body==399);
        State emb=segments[0].at(t), relative=segments[body==399?2:3].at(t);
        return {add(emb.p,relative.p),add(emb.v,relative.v)};
    }
    Vec apparent(int body,double t,State observer) const {
        double light=0;
        Vec r{};
        bool converged=false;
        for (int k=0;k<5;++k) {
            r=sub(state(body,t-light/day).p,observer.p);
            double next=norm(r)/c;
            if (std::abs(next-light)<1e-8) {converged=true;break;}
            light=next;
        }
        require(converged);
        Vec p=unit(r),v=scale(observer.v,1/c),out{};
        // Finite-source solar deflection BEFORE aberration. The Moon is not a
        // star at infinity: using eraLdsun(p,p,...) would introduce a false large
        // correction near new Moon. Evaluate the Sun near the closest point on
        // the actual observer-to-emission light segment (0 <= delay <= light).
        Vec sun=state(10,t).p;
        double closest=std::clamp(dot(p,sub(sun,observer.p))/c,0.0,light);
        sun=state(10,t-closest/day).p;
        Vec e=sub(observer.p,sun),q=unit(add(r,e)),eu=unit(e),deflected{};
        double distance=norm(e)/au;
        eraLd(1,p.data(),q.data(),eu.data(),distance,1e-6,deflected.data());
        p=unit(deflected);
        // Special-relativistic aberration plus ERFA's solar-potential term.
        distance=norm(sub(state(10,t).p,observer.p))/au;
        eraAb(p.data(),v.data(),distance,std::sqrt(1-dot(v,v)),out.data());
        return out;
    }
    double phase(double t,double tt) const {
        checkTT(t,tt);
        double ecliptic[3][3];eraEcm06(2451545.0,tt,ecliptic);
        // Reproduce Liu's planetary-aberration approximation: evaluate the
        // geocentric vector at retarded time (paper eq. 41). Nutation cancels
        // in the difference of the two ecliptic longitudes (paper section 7).
        auto longitude=[&](int body) {
            double light=norm(sub(state(body,t).p,state(399,t).p))/c/day;
            Vec r=sub(state(body,t-light).p,state(399,t-light).p);
            Vec e=rotate(ecliptic,r);
            return std::atan2(e[1],e[0])*180/pi;
        };
        return wrap(longitude(301)-longitude(10));
    }
    double quarter(double from,int q,double limit) const {
        check(from);require(q>=0 && q<4 && std::isfinite(limit) && limit>0 && limit<=40);
        double stop=std::min(from+limit,std::nextafter(end,start));
        auto value=[&](double t){return residual(phase(t,t)-90*q);};
        // TT=TDB only for the ecliptic rotation here (<2 ms time difference).
        // Bracket the forward crossing; reject the wrap discontinuity.
        double a=from,fa=value(a);
        if (std::abs(fa)<1e-6) {
            // Treat the start as inclusive within the solver's time resolution.
            // An angular epsilon smaller than the returned root's uncertainty
            // could otherwise skip a whole lunation when searching that root again.
            double other=from+1/day<end?from+1/day:from-1/day;
            if (other>=start) {
                double rate=std::abs(residual(value(other)-fa)/(other-from));
                if (std::abs(fa)<=rate*eventTolerance) return a;
            }
        }
        while (a<stop) {
            double b=std::min(a+1,stop),fb=value(b);
            if (fa<=0 && fb>=0 && fb-fa<180) {
                // Bisection is bounded and cannot jump to a different lunation.
                for (int n=0;n<45 && b-a>eventTolerance;++n) {
                    double m=(a+b)/2,fm=value(m);
                    if (fm<0) a=m; else b=m;
                }
                return (a+b)/2;
            }
            a=b;fa=fb;
        }
        throw std::runtime_error("no event within coverage/window");
    }
    double illumination(double t) const {
        check(t);
        State earth=state(399,t);
        double emission=t-norm(sub(state(301,t).p,earth.p))/c/day;
        Vec moon=state(301,emission).p;
        Vec sun=state(10,emission).p;
        double solarLight=norm(sub(sun,moon))/c/day;
        Vec incoming=sub(state(10,emission-solarLight).p,moon);
        return std::clamp((1+dot(unit(incoming),unit(sub(earth.p,moon))))/2,0.0,1.0);
    }
    std::array<double,10> surfaceVectors(double t,double tt,double ut1,double xp,double yp,double dx,double dy,
                                         int observer,double lat,double lon,double height) const {
        checkTT(t,tt);
        require(observer==0 || observer==1);
        for (double x:{lat,lon,height}) require(std::isfinite(x));
        require(std::abs(lat)<=90 && std::abs(lon)<=180 && height>=-1000 && height<=100000);
        // Validate the explicit time/EOP arguments even for a geocentric caller.
        double rotation[3][3];earthRotation(tt,ut1,xp,yp,dx,dy,rotation);
        Vec location=state(399,t).p;
        if (observer==1) {
            Vec terrestrial{};
            require(eraGd2gc(1,lon*pi/180,lat*pi/180,height,terrestrial.data())==0);
            location=add(location,rotate(rotation,scale(terrestrial,.001),true));
        } else {
            require(lat==0 && lon==0 && height==0);
        }
        double emission=t;
        for (int i=0;i<5;++i) emission=t-norm(sub(state(301,emission).p,location))/c/day;
        Vec moon=state(301,emission).p;
        double solarEmission=emission;
        for (int i=0;i<5;++i) solarEmission=emission-norm(sub(state(10,solarEmission).p,moon))/c/day;
        Vec toObserver=sub(location,moon),toSun=sub(state(10,solarEmission).p,moon);
        // Position angle is measured from true equatorial north of reception date,
        // not J2000 north. Omitting this distinction accumulates precession error.
        double equator[3][3];eraPnm06a(2451545.0,tt,equator);
        Vec north=rotate(equator,{0,0,1},true);
        return {emission,toObserver[0],toObserver[1],toObserver[2],
                toSun[0],toSun[1],toSun[2],north[0],north[1],north[2]};
    }
    Vec observerDirection(double t,double tt,double ut1,double xp,double yp,double dx,double dy,
                          double lat,double lon,double height,double matrix[3][3]) const {
        checkTT(t,tt);
        for (double x:{tt,ut1,xp,yp,lat,lon,height}) require(std::isfinite(x));
        // Broad physical bounds for the supported modern interval; catch mixed
        // Julian-date/day/second units before ERFA can produce NaN or bogus angles.
        require(std::abs(ut1-tt)<=.01 && std::abs(xp)<=.001 && std::abs(yp)<=.001);
        require(std::abs(lat)<=90 && std::abs(lon)<=180 && height>=-1000 && height<=100000);
        lat*=pi/180;lon*=pi/180;
        earthRotation(tt,ut1,xp,yp,dx,dy,matrix);
        Vec terrestrial{};require(eraGd2gc(1,lon,lat,height,terrestrial.data())==0);
        terrestrial=scale(terrestrial,.001);
        Vec offset=rotate(matrix,terrestrial,true);
        // Earth spins around the TIRS z axis, not the ITRS z axis when polar
        // motion is nonzero. Neglect the much smaller precession/polar-motion rates.
        double polar[3][3];eraPom00(xp,yp,eraSp00(2451545,tt),polar);
        Vec tirs=rotate(polar,terrestrial,true);
        constexpr double omega=2*pi*1.00273781191135448/day; // derivative of IAU ERA
        Vec terrestrialVelocity=rotate(polar,{-omega*tirs[1],omega*tirs[0],0});
        Vec velocity=rotate(matrix,terrestrialVelocity,true);
        State earth=state(399,t),observer{add(earth.p,offset),add(earth.v,velocity)};
        return apparent(301,t,observer);
    }
    std::array<double,8> observe(double t,double tt,double ut1,double xp,double yp,double dx,double dy,
                                    double lat,double lon,double height) const {
        double matrix[3][3];
        Vec direction=observerDirection(t,tt,ut1,xp,yp,dx,dy,lat,lon,height,matrix);
        Vec v=rotate(matrix,direction);
        lat*=pi/180;lon*=pi/180;
        double east=-std::sin(lon)*v[0]+std::cos(lon)*v[1];
        double north=-std::sin(lat)*std::cos(lon)*v[0]-std::sin(lat)*std::sin(lon)*v[1]+std::cos(lat)*v[2];
        double up=std::cos(lat)*std::cos(lon)*v[0]+std::cos(lat)*std::sin(lon)*v[1]+std::sin(lat)*v[2];
        double horizontal=std::hypot(east,north);
        double az=horizontal<1e-12?0:wrap(std::atan2(east,north)*180/pi);
        return {direction[0],direction[1],direction[2],east,north,up,az,std::atan2(up,horizontal)*180/pi};
    }
    std::array<double,2> horizontal(double t,double tt,double ut1,double xp,double yp,
                                    double lat,double lon,double height) const {
        auto result=observe(t,tt,ut1,xp,yp,0,0,lat,lon,height);
        return {result[6],result[7]};
    }
    std::array<double,2> equatorial(double t,double tt,double ut1,double xp,double yp,
                                    double lat,double lon,double height) const {
        double matrix[3][3];
        Vec v=observerDirection(t,tt,ut1,xp,yp,0,0,lat,lon,height,matrix);
        return {wrap(std::atan2(v[1],v[0])*180/pi),std::atan2(v[2],std::hypot(v[0],v[1]))*180/pi};
    }
};
Kernel &kernel(void *p) { require(p!=nullptr); return *static_cast<Kernel*>(p); }
}

void *lunar_open(const char *path) { try {require(path);return new Kernel(path);} catch (...) {return nullptr;} }
void lunar_close(void *p) {delete static_cast<Kernel*>(p);}
int lunar_state(void *p,int body,double t,double *out) {
    try {require(out);auto &k=kernel(p);k.check(t);auto s=k.state(body,t);
        for (double x:s.p) require(std::isfinite(x));
        for (double x:s.v) require(std::isfinite(x));
        std::copy(s.p.begin(),s.p.end(),out);std::copy(s.v.begin(),s.v.end(),out+3);return 0;
    } catch (...) {return -1;}
}
int lunar_phase(void *p,double t,double tt,double *out) {
    try {require(out);double r=kernel(p).phase(t,tt);require(std::isfinite(r));*out=r;return 0;} catch (...) {return -1;}
}
int lunar_quarter(void *p,double t,int q,double limit,double *out) {
    try {require(out);double r=kernel(p).quarter(t,q,limit);require(std::isfinite(r));*out=r;return 0;} catch (...) {return -1;}
}
int lunar_illumination(void *p,double t,double *out) {
    try {require(out);double r=kernel(p).illumination(t);require(std::isfinite(r));*out=r;return 0;} catch (...) {return -1;}
}
int lunar_horizontal(void *p,double t,double tt,double ut1,double xp,double yp,double lat,double lon,double height,double *out) {
    try {require(out);auto r=kernel(p).horizontal(t,tt,ut1,xp,yp,lat,lon,height);
        for (double x:r) require(std::isfinite(x));
        std::copy(r.begin(),r.end(),out);return 0;
    } catch (...) {return -1;}
}
int lunar_equatorial(void *p,double t,double tt,double ut1,double xp,double yp,double lat,double lon,double height,double *out) {
    try {require(out);auto r=kernel(p).equatorial(t,tt,ut1,xp,yp,lat,lon,height);
        for (double x:r) require(std::isfinite(x));
        std::copy(r.begin(),r.end(),out);return 0;
    } catch (...) {return -1;}
}
int lunar_rotation(double tt,double ut1,double xp,double yp,double dx,double dy,double *out) {
    try {require(out);double matrix[3][3];earthRotation(tt,ut1,xp,yp,dx,dy,matrix);
        std::array<double,9> result{};
        for (int i=0;i<3;++i) for (int j=0;j<3;++j) {
            require(std::isfinite(matrix[i][j]));result[3*i+j]=matrix[i][j];
        }
        std::copy(result.begin(),result.end(),out);return 0;
    } catch (...) {return -1;}
}
int lunar_observe(void *p,double t,double tt,double ut1,double xp,double yp,double dx,double dy,double lat,double lon,double height,double *out) {
    try {require(out);auto r=kernel(p).observe(t,tt,ut1,xp,yp,dx,dy,lat,lon,height);
        for (double x:r) require(std::isfinite(x));
        std::copy(r.begin(),r.end(),out);return 0;
    } catch (...) {return -1;}
}
int lunar_surface_vectors(void *p,double t,double tt,double ut1,double xp,double yp,double dx,double dy,
                          int observer,double lat,double lon,double height,double *out) {
    try {require(out);auto r=kernel(p).surfaceVectors(t,tt,ut1,xp,yp,dx,dy,observer,lat,lon,height);
        for (double x:r) require(std::isfinite(x));
        std::copy(r.begin(),r.end(),out);return 0;
    } catch (...) {return -1;}
}
double lunar_benchmark(void *p,int op,int n,double *checksum) {
    try {
        require(n>0 && op>=0 && op<=4 && checksum);auto &k=kernel(p);double total=0;
        auto begin=std::chrono::steady_clock::now();
        for (int i=0;i<n;++i) {
            double t=9500+(i%10000)*.0001;
            if (op==0) total+=k.phase(t,t);
            if (op==1) total+=k.quarter(t,i%4,40);
            if (op==2) total+=k.horizontal(t,t,t-.0008,0,0,1.3521,103.8198,0)[0];
            if (op==3) total+=k.illumination(t);
            if (op==4) total+=k.observe(t,t,t-.0008,1e-6,2e-6,1e-9,-2e-9,89.9,103.8198,0)[3];
        }
        *checksum=total;
        return std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-begin).count()/n;
    } catch (...) {return -1;}
}
