#!/usr/bin/env python3
"""Offline check of every hosted pack against the original DE440 records."""
import ctypes, hashlib, json, os, struct
from pathlib import Path
import numpy as np
from jplephem.spk import SPK
ROOT=Path(__file__).resolve().parents[1]
CACHE=Path(os.environ.get('SKYKIT_CACHE','/tmp/skykit-jpl-cache'))
OUTPUT=ROOT/'build/asset-packs'
provenance=json.loads((OUTPUT/'provenance.json').read_text())
for span,expected in provenance['rangeSHA256'].items():
    assert hashlib.sha256((CACHE/'hosted-de440'/f"{span.replace(':','-')}.bin").read_bytes()).hexdigest()==expected
for name,expected in provenance['archiveSHA256'].items():
    assert hashlib.sha256((OUTPUT/name).read_bytes()).hexdigest()==expected
catalog_bytes=(OUTPUT/'catalog.json').read_bytes()
assert hashlib.sha256(catalog_bytes).hexdigest()==provenance['catalogSHA256']
assert catalog_bytes==(ROOT/'Sources/SkyKit/Resources/hosted-packs.json').read_bytes()
lib=ctypes.CDLL(str(CACHE/'macos/liblunar.dylib'))
lib.lunar_open.argtypes=[ctypes.c_char_p];lib.lunar_open.restype=ctypes.c_void_p
lib.lunar_close.argtypes=[ctypes.c_void_p]
lib.lunar_state.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_double,ctypes.POINTER(ctypes.c_double)]
spk=SPK.open(str(CACHE/'hosted-de440/de440-selected.bsp'))
maximum=velocity=0.;count=0
for pack in json.loads(catalog_bytes)['packs']:
    path=OUTPUT/'contents'/pack['path'];raw=path.read_bytes()
    assert len(raw)==pack['bytes'] and hashlib.sha256(raw).hexdigest()==pack['sha256']
    a,b=pack['startTDB'],pack['endTDB'];times=set(np.linspace(a,np.nextafter(b,a),41))
    offset=28
    for _ in range(4):
        center,body,epoch,step,n,m=struct.unpack_from('<IIddII',raw,offset);offset+=32
        expected_epoch,expected_step,coeff=spk[center,body].load_array()
        first=round((epoch-(expected_epoch-2451545.))/step)
        expected=np.asarray(coeff[:,first:first+n,:].transpose(1,0,2),dtype='<f8').tobytes()
        assert step==expected_step and raw[offset:offset+len(expected)]==expected
        offset+=len(expected)
        for t in np.arange(epoch,epoch+step*n,step):
            for dt in [-1e-7,0,1e-7]:
                if a<=t+dt<b:times.add(t+dt)
    assert offset==len(raw)
    handle=lib.lunar_open(os.fsencode(path));assert handle
    times=np.array(sorted(times))
    try:
        for body in [3,10,399,301]:
            ref,vel=spk[0,body if body in [3,10] else 3].compute_and_differentiate(2451545.,times)
            if body in [399,301]:
                p,v=spk[3,body].compute_and_differentiate(2451545.,times);ref+=p;vel+=v
            actual=np.empty((len(times),6))
            for i,t in enumerate(times):
                out=(ctypes.c_double*6)();assert lib.lunar_state(handle,body,t,out)==0;actual[i]=out
            maximum=max(maximum,float(np.max(np.linalg.norm(actual[:,:3]-ref.T,axis=1))))
            velocity=max(velocity,float(np.max(np.linalg.norm(actual[:,3:]-vel.T/86400,axis=1))))
            count+=len(times)
    finally:lib.lunar_close(handle)
spk.close()
assert maximum<0.0001 and velocity<1e-9,(maximum,velocity)
result=dict(packs=1,samples=count,allCoefficientBytesMatch=True,positionMaximumDifferenceKM=maximum,
    velocityMaximumDifferenceKMPerSecond=velocity,catalogSHA256=provenance['catalogSHA256'],
    scope='2150–2649 packs including overlap; all coefficient bytes and every interval boundary checked. Same-source reader agreement, not absolute accuracy. Apple delivery not exercised.')
(ROOT/'hosted-validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
