#!/usr/bin/env python3
"""Measure current memory/costs and test candidate optimizations, without enabling them."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess

import erfa
import numpy as np
from jplephem.spk import SPK

from prepare import CACHE, ROOT, sha256, verify_build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--simulator', required=True)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--output', type=Path, default=ROOT/'resource-analysis-results.json')
    args = parser.parse_args()
    if not 1 <= args.runs <= 10:
        parser.error('--runs must be between 1 and 10')
    verify_build(json.loads((CACHE/'build-manifest.json').read_text()), simulator=True)
    work = CACHE/'resource-analysis';work.mkdir(exist_ok=True)
    sdk = subprocess.check_output(['xcrun','--sdk','iphonesimulator','--show-sdk-path'],text=True).strip()
    flags = ['-O3','-std=c++17','-target','arm64-apple-ios15.0-simulator','-isysroot',sdk]
    subprocess.run(['xcrun','clang++',*flags,'-I',str(CACHE/'erfa-2.0.1/src'),
        str(ROOT/'resource_probe.cpp'),str(CACHE/'iphonesimulator/liblunar.a'),'-o',str(work/'probe')],check=True)
    command = ['xcrun','simctl','spawn',args.simulator,str(work/'probe'),str(CACHE/'moon-2000-2050.bin')]
    result = dict(generatedAt=datetime.now(timezone.utc).isoformat(),host=platform.platform(),
        cpu=subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'],text=True).strip(),
        simulator=args.simulator,compileFlags=flags,nativeRuns=[],mappingOnlyRuns=[])
    for _ in range(args.runs):
        result['nativeRuns'].append(json.loads(subprocess.check_output(command,text=True)))
        result['mappingOnlyRuns'].append(json.loads(subprocess.check_output(command+['map'],text=True)))
    result['nativeMedianIncrementsBytes'] = {
        stage:{field:float(np.median([r[stage][field]-r['baseline'][field] for r in result['nativeRuns']]))
               for field in ['footprintBytes','residentBytes','heapBytes']}
        for stage in ['loaded','firstObservation','after100000','closed']}

    with SPK.open(str(CACHE/'de440s.bsp')) as kernel:
        a,b = kernel[3,399].load_array(),kernel[3,301].load_array()
        assert a[:2] == b[:2] and a[2].shape == b[2].shape
        e,m = a[2],b[2]
        scale = float(np.sum(e*m)/np.sum(m*m))
        bound = float(np.max(np.sum(np.abs(e-scale*m),axis=-1)))*1000
    manifest=json.loads((CACHE/'kernel-manifest.json').read_text())
    earth=next(s for s in manifest['segments'] if s['body']==399)
    result['coefficientRedundancyExperiment'] = dict(inferredEarthMoonMassRatio=-1/scale,
        maximumSegmentPositionBoundMeters=bound,originalFileBytes=manifest['bytes'],
        removableCoefficientBytes=earth['bytes'],
        projectedFileBytesExcludingRemovedSegmentHeader=manifest['bytes']-earth['bytes']-32,
        limitation='Empirical coefficient relationship in this pinned DE440s, over its full range. No coefficients removed from production prototype. Not bit-identical; end-to-end state/phase/observer validation required before adoption.')

    # Linear interpolation of slow-changing X/Y/s only; ERA remains fresh.
    rng=np.random.default_rng(20260922)
    dates=np.r_[rng.uniform(-.499,18627.499,10000),np.arange(0,18627,37)+.123456]
    reference=erfa.c2ixys(*erfa.xys06a(2451545.,dates))
    result['xysInterpolationExperiment']=[]
    for seconds in [60,300,600,3600]:
        step=seconds/86400;lo=np.floor(dates/step)*step;hi=lo+step
        fraction=(dates-lo)/step
        a=np.array(erfa.xys06a(2451545.,lo));b=np.array(erfa.xys06a(2451545.,hi))
        matrix=erfa.c2ixys(*(a+(b-a)*fraction))
        relative=matrix @ np.swapaxes(reference,-2,-1)
        vee=np.stack([relative[:,2,1]-relative[:,1,2],relative[:,0,2]-relative[:,2,0],
                      relative[:,1,0]-relative[:,0,1]],axis=1)/2
        error=np.arctan2(np.linalg.norm(vee,axis=1),(np.trace(relative,axis1=1,axis2=2)-1)/2)*180/np.pi*3600
        result['xysInterpolationExperiment'].append(dict(intervalSeconds=seconds,samples=len(dates),
            maximumRotationErrorArcseconds=float(max(error)),p95Arcseconds=float(np.percentile(error,95))))
    result['limitations']='Simulator deltas for one kernel, not whole-app or device peak memory. Heap is default malloc zone. RSS includes reclaimable clean pages; phys_footprint is a separate metric. mmap mode is a storage-only experiment, not a replacement parser. Cached-XYS timing deliberately omits series generation and interpolation; it is not a functioning cached observer API. Interpolation errors are sampled, not a global bound.'
    result['artifactSHA256']={name:sha256(path) for name,path in {
        'probeSource':ROOT/'resource_probe.cpp','runner':ROOT/'analyze_resources.py',
        'nativeArchive':CACHE/'iphonesimulator/liblunar.a','nativeKernel':CACHE/'moon-2000-2050.bin',
        'buildManifest':CACHE/'build-manifest.json','probeBinary':work/'probe'}.items()}
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ['nativeMedianIncrementsBytes','coefficientRedundancyExperiment','xysInterpolationExperiment']},indent=2))


if __name__=='__main__':
    main()
