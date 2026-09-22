#!/usr/bin/env python3
"""Validate the native kernel against unrounded Liu events, full SPK and Skyfield."""
import argparse
import ctypes as C
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import struct

import numpy as np
import erfa
from jplephem.spk import SPK
from skyfield.api import load, load_file, wgs84
from skyfield import almanac

from prepare import CACHE, ROOT, sha256, verify_build
from observation_checks import validate_observation, validate_accuracy_boundaries

if not __debug__:
    raise RuntimeError('Validation requires assertions: do not use python -O or PYTHONOPTIMIZE')


class Native:
    def __init__(self):
        self.lib = C.CDLL(str(CACHE / 'macos' / 'liblunar.dylib'))
        ptr, num, output = C.c_void_p, C.c_double, C.POINTER(C.c_double)
        signatures = {
            'open': ([C.c_char_p], ptr), 'close': ([ptr], None),
            'state': ([ptr, C.c_int, num, output], C.c_int),
            'phase': ([ptr, num, num, output], C.c_int),
            'quarter': ([ptr, num, C.c_int, num, output], C.c_int),
            'illumination': ([ptr, num, output], C.c_int),
            'horizontal': ([ptr] + [num] * 8 + [output], C.c_int),
            'equatorial': ([ptr] + [num] * 8 + [output], C.c_int),
            'observe': ([ptr] + [num] * 10 + [output], C.c_int),
            'rotation': ([num] * 6 + [output], C.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, 'lunar_' + name)
            function.argtypes, function.restype = args, result
        self.handle = self.lib.lunar_open(str(CACHE / 'moon-2000-2050.bin').encode())
        assert self.handle, 'Cannot load kernel'

    def call(self, name, *args, size=1):
        result = (C.c_double * size)()
        prefix = [] if name == 'rotation' else [self.handle]
        status = getattr(self.lib, 'lunar_' + name)(*prefix, *args, result)
        assert status == 0, (name, args, status)
        assert all(np.isfinite(result)), (name, args, 'non-finite result')
        return np.array(result) if size > 1 else result[0]

    def close(self):
        self.lib.lunar_close(self.handle)
        self.handle = None


def stats(values):
    a = np.abs(values)
    return dict(count=len(a), mean=float(np.mean(a)), median=float(np.median(a)),
                p95=float(np.percentile(a, 95)), maximum=float(np.max(a)))


def angular_separation(a, b):
    def vector(pair):
        az, alt = np.deg2rad(pair)
        return np.array([np.cos(alt)*np.cos(az), np.cos(alt)*np.sin(az), np.sin(alt)])
    u, v = vector(a), vector(b)
    return float(np.rad2deg(np.arctan2(np.linalg.norm(np.cross(u, v)), np.dot(u, v))) * 3600)


def project_icrf_to_wgs84(pair, tt, ut1, latitude, longitude):
    """Common-frame diagnostic, not an independent Earth-orientation check."""
    a, d = np.deg2rad(pair)
    v = erfa.c2t06a(2451545, tt, 2451545, ut1, 0., 0.) @ np.array(
        [np.cos(a)*np.cos(d), np.sin(a)*np.cos(d), np.sin(d)])
    p, l = np.deg2rad([latitude, longitude])
    east = v @ np.array([-np.sin(l), np.cos(l), 0.])
    north = v @ np.array([-np.sin(p)*np.cos(l), -np.sin(p)*np.sin(l), np.cos(p)])
    up = v @ np.array([np.cos(p)*np.cos(l), np.cos(p)*np.sin(l), np.sin(p)])
    return np.rad2deg([np.arctan2(east, north), np.arctan2(up, np.hypot(east, north))])


def validate_states(native, kernel):
    rng = np.random.default_rng(20260922)
    # Include both sides of EVERY retained polynomial boundary, plus random times.
    times = list(rng.uniform(-.5, 18627.5, 1000)) + [-.5, 18627.5 - 1e-9]
    for key in [(0, 3), (0, 10), (3, 399), (3, 301)]:
        epoch, interval, _ = kernel[key].load_array()
        boundaries = np.arange(epoch - 2451545, 18627.5, interval)
        for t in boundaries[(boundaries > -.5) & (boundaries < 18627.5)]:
            times.extend([t - 1e-8, t, t + 1e-8])
    times = np.unique(times)
    pos, vel = [], []
    for body in [3, 10, 399, 301]:
        if body in [3, 10]:
            p, v = kernel[0, body].compute_and_differentiate(2451545, times)
        else:
            ep, ev = kernel[0, 3].compute_and_differentiate(2451545, times)
            rp, rv = kernel[3, body].compute_and_differentiate(2451545, times)
            p, v = ep + rp, ev + rv
        actual = np.array([native.call('state', body, t, size=6) for t in times])
        pos.extend(np.linalg.norm(actual[:, :3] - p.T, axis=1) * 1000)  # meters
        vel.extend(np.linalg.norm(actual[:, 3:] - v.T / 86400, axis=1) * 1000)
    # Different evaluation order/time origins can differ by millimeters at
    # ~1 AU. This is a numerical comparison, not the DE model's absolute error.
    assert max(pos) < 0.01, max(pos)
    assert max(vel) < 0.000001, max(vel)
    return dict(positionMeters=stats(pos), velocityMetersPerSecond=stats(vel))


def validate_phases(native):
    header, *lines = (CACHE / 'TDBtimes.txt').read_text().splitlines()
    fields, events = header.split(), {}
    for line in lines:
        values = line.split()
        if not 1999 <= int(values[0]) <= 2051:
            continue
        for index, label in enumerate(fields):
            if label.startswith('Q'):
                t = float(values[1]) - 2451545 + float(values[index])
                if -.5 <= t < 18627.5:
                    events[(int(label[1]), round(t, 2))] = t
    errors, worst = [], None
    for (quarter, _), reference in sorted(events.items(), key=lambda item: item[1]):
        actual = native.call('quarter', max(-.5, reference - 1), quarter, 3)
        assert native.call('quarter', actual, quarter, 3) == actual, 'Search skipped its own result'
        before = native.call('quarter', actual - 1/86400, quarter, 3)
        assert abs(before - actual) * 86400 <= .0001
        error = (actual - reference) * 86400
        errors.append(error)
        if worst is None or abs(error) > abs(worst['signedSeconds']):
            worst = dict(quarter=quarter, tdbDaysSinceJ2000=reference, signedSeconds=error)
    assert len(errors) == 2523
    assert max(map(abs, errors)) < .25, worst
    # Check the forward-search contract independently of reference +/- 1-day brackets.
    t = 9000.0
    for q in range(4):
        event = native.call('quarter', t, q, 40)
        next_event = native.call('quarter', event + 1e-6, q, 40)
        assert 29 < next_event - event < 30
        assert abs((native.call('phase', event, event) - q*90 + 180) % 360 - 180) < 1e-6
    return dict(reference='ChineseCalendar unrounded TDBtimes.txt; DE431, IAU 2006',
                inclusiveSearchRegressionEvents=len(errors),
                differenceSeconds=stats(errors), worst=worst, toleranceSeconds=.25)


def validate_directions(native, sky, timescale):
    fixture = json.loads((ROOT / 'horizons-fixtures.json').read_text())
    sky_errors, horizons_errors, pole_errors, inertial_errors, near_pole_errors = [], [], [], [], []
    common_frame_errors = []
    illumination_errors, phase_errors = [], []
    rows = []
    for sample in fixture['samples']:
        date = datetime.fromisoformat(sample['utc'].replace('Z', '+00:00'))
        t = timescale.from_datetime(date)
        rows.append((t, sample['latitude'], sample['longitude'], sample['elevation'], sample))
    rng = np.random.default_rng(20260922)
    for days in rng.uniform(0, 18626, 200):
        rows.append((timescale.tdb_jd(2451545, days), rng.uniform(-90,90),
                     rng.uniform(-180,180), rng.uniform(-400,6000), None))
    for t, lat, lon, height, reference in rows:
        # Timescale tables are Skyfield 1.54 builtin; no external EOP lookup.
        # Both implementations use xp=yp=0 so they share the same inputs.
        tdb = (t.whole - 2451545) + t.tdb_fraction
        tt = (t.whole - 2451545) + t.tt_fraction
        ut1 = (t.whole - 2451545) + t.ut1_fraction
        actual = native.call('horizontal', tdb, tt, ut1, 0, 0, lat, lon, height, size=2)
        observer = sky['earth'] + wgs84.latlon(lat, lon, elevation_m=height)
        apparent = observer.at(t).observe(sky['moon']).apparent()
        altitude, azimuth, _ = apparent.altaz()
        sky_errors.append(angular_separation(actual, [azimuth.degrees, altitude.degrees]))
        if reference:
            matched_ut1 = ut1 + (reference['dut1'] - float(t.dut1)) / 86400
            matched = native.call('horizontal', tdb, tt, matched_ut1, 0, 0, lat, lon, height, size=2)
            equatorial = native.call('equatorial', tdb, tt, matched_ut1, 0, 0, lat, lon, height, size=2)
            inertial_errors.append(angular_separation(equatorial, [reference['rightAscension'], reference['declination']]))
            common = project_icrf_to_wgs84([reference['rightAscension'], reference['declination']],
                                          tt, matched_ut1, lat, lon)
            common_frame_errors.append(angular_separation(matched, common))
            # At the exact geographic poles azimuth's zero is conventional;
            # Horizons uses a different meridian. Validate elevation separately.
            if abs(lat) == 90:
                pole_errors.append(abs(matched[1] - reference['altitude']) * 3600)
            elif abs(lat) > 85:
                near_pole_errors.append(angular_separation(matched, [reference['azimuth'], reference['altitude']]))
            else:
                horizons_errors.append(angular_separation(matched, [reference['azimuth'], reference['altitude']]))
        fraction = sky['earth'].at(t).observe(sky['moon']).fraction_illuminated(sky['sun'])
        illumination_errors.append(native.call('illumination', tdb) - fraction)
        reference_phase = almanac.moon_phase(sky, t).degrees
        phase_errors.append(((native.call('phase', tdb, tt) - reference_phase + 180) % 360 - 180) * 3600)
    assert max(sky_errors) < .2, max(sky_errors)
    # These are regression ceilings, NOT subarcsecond scientific acceptance.
    # polar_diagnostics.py reproduces Horizons Q4 from Q2/Q7 and input latitude.
    # Raw Q4 and fixed WGS84 ENU do not share the same local frame. Preserve raw
    # differences as regression diagnostics; do not label them ephemeris errors.
    assert max(inertial_errors) < .05, max(inertial_errors)
    assert max(common_frame_errors) < .01, max(common_frame_errors)
    assert max(horizons_errors) < 2, max(horizons_errors)
    assert max(near_pole_errors) < 30, max(near_pole_errors)
    assert max(pole_errors) < 1, max(pole_errors)
    assert max(map(abs, illumination_errors)) < .00002, max(map(abs, illumination_errors))
    assert max(map(abs, phase_errors)) < .005, max(map(abs, phase_errors))
    return dict(skyfieldArcseconds=stats(sky_errors), horizonsArcseconds=stats(horizons_errors),
                horizonsICRFArcseconds=stats(inertial_errors),
                horizonsCommonWGS84Arcseconds=stats(common_frame_errors),
                horizonsNearPoleArcseconds=stats(near_pole_errors),
                horizonsPoleAltitudeArcseconds=stats(pole_errors),
                illuminationFractionDifference=stats(illumination_errors),
                phaseAngleArcseconds=stats(phase_errors),
                earthOrientation='Skyfield comparison: builtin UT1, xp=yp=0; Horizons: matching DUT1, xp=yp=0; airless',
                limitation='Raw Horizons Q4 and fixed WGS84 ENU use different horizon conventions; see polar-diagnostics.md. Shared-frame comparison isolates celestial direction, not independent Earth-orientation accuracy. Exact legacy Horizons transform remains unmatched.')


def validate_failures_and_concurrency(native):
    value = C.c_double()
    for t in [-.500001, 18627.5, float('nan'), float('inf')]:
        assert native.lib.lunar_phase(native.handle, t, t, C.byref(value)) == -1
    for tt in [9001, 2460545, 1e308, -1e308, float('nan')]:
        value.value = 777
        assert native.lib.lunar_phase(native.handle, 9000, tt, C.byref(value)) == -1
        assert value.value == 777
    for index, bad_value in [(0, float('nan')), (1, 1e308), (2, 9001), (3, .1),
                             (4, float('inf')), (5, 91), (6, 181), (7, -1001)]:
        args = [9000, 9000, 8999.9992, 0, 0, 30, 100, 10]
        args[index] = bad_value
        for function in [native.lib.lunar_horizontal, native.lib.lunar_equatorial]:
            pair = (C.c_double * 2)(777, 777)
            assert function(native.handle, *args, pair) == -1
            assert list(pair) == [777, 777]
    assert native.lib.lunar_quarter(native.handle, 18627.49, 0, 1, C.byref(value)) == -1
    for q, limit in [(-1, 1), (4, 1), (0, -1), (0, 41), (0, float('nan'))]:
        assert native.lib.lunar_quarter(native.handle, 9000, q, limit, C.byref(value)) == -1
    bad = CACHE / 'invalid-kernel.bin'
    valid = (CACHE / 'moon-2000-2050.bin').read_bytes()
    corrupt = [b'', b'LUNAR001', valid[:100], valid + b'extra']
    for offset, fmt, replacement in [(28, '<I', 123), (36, '<d', float('nan')),
                                      (44, '<d', 0), (52, '<I', 1000000),
                                      (56, '<I', 33), (60, '<d', float('nan'))]:
        data = bytearray(valid)
        struct.pack_into(fmt, data, offset, replacement)
        corrupt.append(data)
    for content in corrupt:
        bad.write_bytes(content)
        assert not native.lib.lunar_open(str(bad).encode())
    bad.unlink()
    dates = [9000 + i/100 for i in range(1000)]
    expected = [native.call('phase', t, t) for t in dates]
    with ThreadPoolExecutor(max_workers=8) as pool:
        actual = list(pool.map(lambda t: native.call('phase', t, t), dates))
    assert actual == expected
    return dict(rejectedOutOfRangeAndInvalidInputs=True, rejectedMalformedFiles=len(corrupt),
                invalidOutputUnchanged=True, concurrentCalls=len(dates), threads=8)


def validate_polar_motion(native, sky):
    # The previous suite exercised only xp=yp=0 despite exposing both in the ABI.
    ts = load.timescale(builtin=True)
    errors = []
    for xp, yp in [(.25, .4), (-.2, .3)]:
        ts.polar_motion_table = (np.array([2451544., 2470173.]),
                                 np.array([xp, xp]), np.array([yp, yp]))
        for days in [1., 9500., 18626.]:
            for lat in [-90, -80, 0, 80, 90]:
                t = ts.tdb_jd(2451545, days)
                tt = (t.whole - 2451545) + t.tt_fraction
                ut1 = (t.whole - 2451545) + t.ut1_fraction
                lon, height = 103.8198, 4200
                actual = native.call('horizontal', days, tt, ut1,
                                     np.deg2rad(xp/3600), np.deg2rad(yp/3600),
                                     lat, lon, height, size=2)
                observer = sky['earth'] + wgs84.latlon(lat, lon, elevation_m=height)
                alt, az, _ = observer.at(t).observe(sky['moon']).apparent().altaz()
                errors.append(angular_separation(actual, [az.degrees, alt.degrees]))
    assert max(errors) < .01, max(errors)
    return dict(skyfieldArcseconds=stats(errors), polarMotionArcseconds=[[.25, .4], [-.2, .3]])


def validate_provenance(manifest):
    # Simulate edited artifacts without modifying the user's source or binaries.
    # Verify every class of input to the executed build is actually checked.
    from unittest.mock import patch
    import prepare
    build = next(item for item in manifest['builds'] if item['sdk'] == 'macosx')
    paths = [ROOT / 'native/lunar.cpp', ROOT / 'native/lunar.h', ROOT / 'native/benchmark.cpp',
             CACHE / 'macos/liblunar.dylib', CACHE / 'macos/benchmark', CACHE / 'macos/ab.o',
             CACHE / 'erfa-2.0.1/src/ab.c', CACHE / 'moon-2000-2050.bin', CACHE / 'TDBtimes.txt']
    if any(item['sdk'] == 'iphoneos' for item in manifest['builds']):
        paths.append(CACHE / 'iphoneos/liblunar.a')
    for changed in paths:
        def changed_hash(path):
            return 'changed' if Path(path) == changed else sha256(path)
        with patch.object(prepare, 'sha256', side_effect=changed_hash):
            try:
                verify_build(manifest)
            except RuntimeError:
                pass
            else:
                raise AssertionError(f'Accepted stale artifact: {changed}')
    assert build['nativeSourceSHA256']['native/lunar.cpp'] == sha256(ROOT / 'native/lunar.cpp')
    return dict(rejectedChangedArtifacts=len(paths), verifiedBeforeLoadingNativeLibrary=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--simulator', help='Booted simulator UDID for native benchmark')
    parser.add_argument('--sanitizers', action='store_true', help='Run native core with AddressSanitizer and UBSan')
    parser.add_argument('--output', type=Path, default=CACHE / 'results.json')
    args = parser.parse_args()
    manifest = json.loads((CACHE / 'build-manifest.json').read_text())
    verify_build(manifest, simulator=bool(args.simulator))
    native = Native()
    kernel = SPK.open(str(CACHE / 'de440s.bsp'))
    sky, timescale = load_file(str(CACHE / 'de440s.bsp')), load.timescale(builtin=True)
    result = dict(generatedAt=datetime.now(timezone.utc).isoformat(),
                  host=dict(platform=platform.platform(), cpu=subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip()),
                  build=manifest)
    result['artifactSHA256'] = {name: sha256(ROOT / name) for name in
                                ['native/lunar.cpp', 'native/lunar.h', 'prepare.py', 'validate.py', 'observation_checks.py', 'horizons-fixtures.json']}
    try:
        for name, function in [
            ('buildProvenance', lambda: validate_provenance(manifest)),
            ('interpolation', lambda: validate_states(native, kernel)),
            ('phases', lambda: validate_phases(native)),
            ('directions', lambda: validate_directions(native, sky, timescale)),
            ('nonzeroPolarMotion', lambda: validate_polar_motion(native, sky)),
            ('correctedPoleObservation', lambda: validate_observation(native, sky, stats)),
            ('accuracyBoundaries', lambda: validate_accuracy_boundaries(native, stats)),
            ('robustness', lambda: validate_failures_and_concurrency(native)),
        ]:
            result[name] = function()
            print(name, json.dumps(result[name]), flush=True)
        result['macOSBenchmark'] = json.loads(subprocess.check_output([
            str(CACHE / 'macos' / 'benchmark'), str(CACHE / 'moon-2000-2050.bin')], text=True))
        if args.simulator:
            result['iOSSimulatorBenchmark'] = json.loads(subprocess.check_output([
                'xcrun', 'simctl', 'spawn', args.simulator, str(CACHE / 'iphonesimulator' / 'benchmark'),
                str(CACHE / 'moon-2000-2050.bin')], text=True))
            result['iOSSimulatorBenchmark']['deviceID'] = args.simulator
        if args.sanitizers:
            binary = CACHE / 'benchmark-sanitized'
            objects = sorted(str(path) for path in (CACHE / 'macos').glob('*.o') if path.name != 'lunar.o')
            subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
                            '-I', str(CACHE / 'erfa-2.0.1' / 'src'), str(ROOT / 'native' / 'lunar.cpp'),
                            str(ROOT / 'native' / 'benchmark.cpp'), *objects, '-o', str(binary)], check=True)
            checked = subprocess.run([str(binary), str(CACHE / 'moon-2000-2050.bin')], check=True,
                                     capture_output=True, text=True, env={**os.environ,
                                     'ASAN_OPTIONS': 'halt_on_error=1', 'UBSAN_OPTIONS': 'halt_on_error=1'})
            assert not checked.stderr, checked.stderr
            result['nativeCoreSanitizers'] = 'AddressSanitizer and UBSan passed; instrumented native core, ERFA objects uninstrumented'
        horizontal_accepted = all(result['directions'][key]['maximum'] < 1 for key in
                                  ['horizonsArcseconds', 'horizonsNearPoleArcseconds', 'horizonsPoleAltitudeArcseconds'])
        result['acceptance'] = dict(regressionChecks='passed', phaseReproduction='passed',
                                    horizonsSubarcsecondHorizontal='passed' if horizontal_accepted else 'not_met',
                                    horizonsCommonFrameDirection='passed',
                                    productionIntegration='not_ready',
                                    reason='Raw Horizons Q4 convention differs from WGS84 ENU; polar amplification diagnosed in polar-diagnostics.md. Common-frame direction passes; exact legacy EOP/frame matching, UTC/EOP lifecycle and device testing remain pending.')
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        print('Regression checks passed; Horizons horizontal acceptance:',
              result['acceptance']['horizonsSubarcsecondHorizontal'], args.output)
    finally:
        native.close()
        kernel.close()
        sky.close()


if __name__ == '__main__':
    main()
