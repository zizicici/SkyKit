#!/usr/bin/env python3
"""Offline provenance and expanded-coverage checks against independent JPLEphem."""
import ctypes, hashlib, json, os, sys
from pathlib import Path
import numpy as np
from jplephem.spk import SPK
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'prototypes/ephemeris'))
from prepare import CACHE, verify_build
verify_build(json.loads((CACHE/'build-manifest.json').read_text()))
package=ROOT
manifest=json.loads((package/'provenance.json').read_text())
for name,digest in manifest['fileSHA256'].items():
    assert hashlib.sha256((package/name).read_bytes()).hexdigest()==digest,name
for source,target in [('native/lunar.cpp','Sources/NativeSky/lunar.cpp'),('native/lunar.h','Sources/NativeSky/include/lunar.h')]:
    assert (ROOT/'prototypes/ephemeris'/source).read_bytes()==(package/target).read_bytes()
lib=ctypes.CDLL(str(CACHE/'macos/liblunar.dylib'))
lib.lunar_open.argtypes=[ctypes.c_char_p];lib.lunar_open.restype=ctypes.c_void_p
lib.lunar_state.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_double,ctypes.POINTER(ctypes.c_double)]
lib.lunar_close.argtypes=[ctypes.c_void_p]
handle=lib.lunar_open(os.fsencode(package/'Sources/SkyKit/Resources/moon-1850-2149.bin'))
assert handle
spk=SPK.open(str(CACHE/'de440s.bsp'))
a,b=manifest['kernel']['startTDBDaysSinceJ2000'],manifest['kernel']['endTDBDaysSinceJ2000Exclusive']
times=set(np.linspace(a,np.nextafter(b,a),3601))
for pair in [(0,3),(0,10),(3,399),(3,301)]:
    epoch,interval,coeff=spk[pair].load_array()
    for t in np.arange(epoch-2451545,epoch-2451545+interval*coeff.shape[1],interval):
        for dt in [-1e-7,0,1e-7]:
            if a<=t+dt<b:times.add(t+dt)
times=np.array(sorted(times))
maximum=velocity=0.;count=0;worst=None
try:
    for body in [3,10,399,301]:
        if body in [3,10]:ref,vel=spk[0,body].compute_and_differentiate(2451545.,times)
        else:
            base,basev=spk[0,3].compute_and_differentiate(2451545.,times)
            relative,relativev=spk[3,body].compute_and_differentiate(2451545.,times)
            ref,vel=base+relative,basev+relativev
        actual=np.empty((len(times),6))
        for i,t in enumerate(times):
            out=(ctypes.c_double*6)()
            assert lib.lunar_state(handle,body,float(t),out)==0
            actual[i]=out
        # Record the worst case for an independent high-precision diagnostic.
        errors=np.linalg.norm(actual[:,:3]-ref.T,axis=1)
        index=int(np.argmax(errors))
        if errors[index]>maximum:
            maximum=float(errors[index])
            worst=(body,float(times[index]),actual[index,:3].copy(),ref[:,index].copy())
        velocity=max(velocity,float(np.max(np.linalg.norm(actual[:,3:]-vel.T/86400,axis=1))))
        count+=len(times)
finally:
    lib.lunar_close(handle);spk.close()
# At century-scale epochs, JPLEphem's conversion to SPK seconds has sub-µs
# quantization. Diagnose the worst sample with decimal Clenshaw evaluation.
from decimal import Decimal, localcontext
body,t,actual,reference=worst
spk=SPK.open(str(CACHE/'de440s.bsp'))
with localcontext() as context:
    context.prec=50
    def decimal_position(pair):
        epoch,step,coeff=spk[pair].load_array()
        epoch=Decimal.from_float(epoch-2451545.);step=Decimal.from_float(step)
        offset=Decimal.from_float(t)-epoch
        index=int(offset//step)
        x=2*(offset-index*step)/step-1
        values=[]
        for axis in range(3):
            c=[Decimal.from_float(float(v)) for v in coeff[axis,index]]
            b1=b2=Decimal(0)
            for a in c[:0:-1]:b1,b2=a+2*x*b1-b2,b1
            values.append(c[0]+x*b1-b2)
        return values
    if body in [3,10]:exact=decimal_position((0,body))
    else:exact=[a+b for a,b in zip(decimal_position((0,3)),decimal_position((3,body)))]
    native_error=float(sum((Decimal.from_float(float(a))-b)**2 for a,b in zip(actual,exact)).sqrt())
    reference_error=float(sum((Decimal.from_float(float(a))-b)**2 for a,b in zip(reference,exact)).sqrt())
spk.close()
assert native_error < 1e-6,native_error  # 1 mm at the diagnosed worst sample
assert maximum < 5e-5,maximum           # 5 cm cross-reader regression threshold
assert velocity < 1e-9,velocity
result=dict(samples=count,positionMaximumDifferenceKM=maximum,velocityMaximumDifferenceKMPerSecond=velocity,
            kernelSHA256=manifest['kernel']['sha256'],kernelBytes=manifest['kernel']['bytes'],
            worstSample=dict(body=body,tdb=t,nativeVsDecimalKM=native_error,jplephemVsDecimalKM=reference_error),
            scope='1850-2150 TDB; coefficient intervals at -1e-7, 0, +1e-7 days and 3601 uniform dates. JPLEphem same-source evaluation, not absolute lunar accuracy.')
output=package/'validation.json';output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
