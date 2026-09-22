#!/usr/bin/env python3
"""Offline decomposition of the Horizons high-latitude horizon discrepancy.

The shared-frame check isolates the input celestial direction; it is not an
independent validation of Earth orientation. No native model is adjusted here.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import erfa
import numpy as np
from skyfield.api import load

from prepare import CACHE, ROOT, sha256, verify_build
from validate import Native, angular_separation, stats


def vector(pair):
    a, d = np.deg2rad(pair)
    return np.array([np.cos(a)*np.cos(d), np.sin(a)*np.cos(d), np.sin(d)])


def horizon(v, latitude, longitude):
    p, l = np.deg2rad([latitude, longitude])
    east = np.array([-np.sin(l), np.cos(l), 0.])
    north = np.array([-np.sin(p)*np.cos(l), -np.sin(p)*np.sin(l), np.cos(p)])
    up = np.array([np.cos(p)*np.cos(l), np.cos(p)*np.sin(l), np.sin(p)])
    e, n, u = v @ east, v @ north, v @ up
    return np.rad2deg([np.arctan2(e, n), np.arctan2(u, np.hypot(e, n))])


def wrapped_arcseconds(degrees):
    return float((degrees + 180) % 360 - 180) * 3600


def investigate(native, timescale):
    fixture = json.loads((ROOT / 'polar-diagnostics-fixtures.json').read_text())
    rows = []
    for record in fixture['samples']:
        sample, raw = record['sample'], record['response']
        assert hashlib.sha256(raw.encode()).hexdigest() == record['responseSHA256']
        fields = list(csv.reader(raw.split('$$SOE')[1].split('$$EOE')[0].strip().splitlines()))
        assert len(fields) == 1
        ra, dec, az, alt, last, icrf_ra, icrf_dec, dut1 = map(float, fields[0][3:11])
        date = datetime.fromisoformat(sample['utc'].replace('Z', '+00:00'))
        t = timescale.from_datetime(date)
        tdb = t.whole - 2451545 + t.tdb_fraction
        tt = t.whole - 2451545 + t.tt_fraction
        ut1 = t.whole - 2451545 + t.ut1_fraction + (dut1 - t.dut1) / 86400
        lat, lon, height = [sample[k] for k in ['latitude', 'longitude', 'elevation']]
        args = (tdb, tt, ut1, 0., 0., lat, lon, height)
        actual = native.call('horizontal', *args, size=2)
        inertial = native.call('equatorial', *args, size=2)
        # Hold the reference direction fixed, change only its reported axes.
        common = horizon(erfa.c2t06a(2451545, tt, 2451545, ut1, 0., 0.) @
                         vector([icrf_ra, icrf_dec]), lat, lon)
        # Reconstruct Q4 directly from Horizons Q2 and Q7. LAST is in hours.
        reconstructed = horizon(vector([ra - last*15, dec]), lat, 0.)
        row = dict(**sample, horizonsLASTHours=last,
                   rawAzimuthDifferenceArcseconds=wrapped_arcseconds(actual[0]-az),
                   rawAltitudeDifferenceArcseconds=float(actual[1]-alt)*3600,
                   rawHorizontalSeparationArcseconds=angular_separation(actual, [az, alt]),
                   icrfSeparationArcseconds=angular_separation(inertial, [icrf_ra, icrf_dec]),
                   commonWGS84SeparationArcseconds=angular_separation(actual, common),
                   reconstructedHorizonsSeparationArcseconds=angular_separation(reconstructed, [az, alt]))
        if sample['utc'] == '2020-02-29T23:59:59Z' and lon == 0 and height == 0:
            eop = fixture['eop']['samples']
            mjd_tai = date.timestamp()/86400 + 40587 + 37/86400
            assert eop[0]['mjdTAI'] <= mjd_tai <= eop[-1]['mjdTAI']
            xp, yp = [float(np.interp(mjd_tai, [e['mjdTAI'] for e in eop],
                                     [e[k] for e in eop])) for k in ['xpArcseconds', 'ypArcseconds']]
            polar = erfa.pom00(*np.deg2rad(np.array([xp, yp])/3600), erfa.sp00(2451545, tt))
            site = erfa.gd2gc(1, 0., np.deg2rad(lat), height)
            tirs_site = polar.T @ site
            longitude_tirs = np.rad2deg(np.arctan2(tirs_site[1], tirs_site[0]))
            row['externalEOP'] = dict(xpArcseconds=xp, ypArcseconds=yp,
                effectiveLongitudeShiftArcseconds=wrapped_arcseconds(longitude_tirs),
                firstOrderLongitudeShiftArcseconds=float(yp * site[2] / site[0]) if abs(lat) < 90 else None)
        rows.append(row)
    sweep = sorted([r for r in rows if 'externalEOP' in r], key=lambda r: r['latitude'])
    equator = next(r for r in sweep if r['latitude'] == 0)
    for r in sweep:
        r['observedMeridianShiftArcseconds'] = wrapped_arcseconds((r['horizonsLASTHours'] - equator['horizonsLASTHours'])*15)
        r['externalEOPMeridianResidualArcseconds'] = r['externalEOP']['effectiveLongitudeShiftArcseconds'] - r['observedMeridianShiftArcseconds']
    metrics = {key: stats([r[key] for r in rows]) for key in [
        'icrfSeparationArcseconds', 'commonWGS84SeparationArcseconds',
        'reconstructedHorizonsSeparationArcseconds']}
    assert len(rows) == 12 and len(sweep) == 8
    assert metrics['icrfSeparationArcseconds']['maximum'] < .01
    assert metrics['commonWGS84SeparationArcseconds']['maximum'] < .01
    assert metrics['reconstructedHorizonsSeparationArcseconds']['maximum'] < .00001
    # Same epoch/longitude, opposite hemispheres and increasing latitude:
    # meridian drift follows tan(geocentric latitude) before the pole singularity.
    coefficients = [r['observedMeridianShiftArcseconds'] /
                    ((1-1/298.257223563)**2 * np.tan(np.deg2rad(r['latitude'])))
                    for r in sweep if 0 < abs(r['latitude']) <= 89.9]
    assert max(coefficients) - min(coefficients) < .0001
    return dict(sampleCount=len(rows), metrics=metrics, samples=rows,
        empiricalMeridianCoefficientArcseconds=stats(coefficients),
        interpretation='Horizons Q4 is reproduced using Q2, Q7 and input geodetic latitude. Raw Q4 vs WGS84 ENU uses different horizon conventions; ICRF and shared WGS84 directions agree. External EOP explains leading polar amplification but does not reproduce the exact legacy Horizons Earth transform.',
        limitations='Shared-frame projection is not independent Earth-orientation validation. Empirical coefficient diagnoses a coordinate convention; it is not a calibration applied to the native model. Exact-pole azimuth depends on the chosen meridian.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'polar-diagnostics-results.json')
    args = parser.parse_args()
    verify_build(json.loads((CACHE / 'build-manifest.json').read_text()))
    native = Native()
    try:
        result = investigate(native, load.timescale(builtin=True))
    finally:
        native.close()
    result['generatedAt'] = datetime.now(timezone.utc).isoformat()
    result['artifactSHA256'] = {name: sha256(ROOT / name) for name in
        ['polar_diagnostics.py', 'polar-diagnostics-fixtures.json', 'validate.py', 'native/lunar.cpp', 'native/lunar.h']}
    result['buildManifestSHA256'] = sha256(CACHE / 'build-manifest.json')
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result['metrics'], indent=2))
    for r in sorted([r for r in result['samples'] if 'externalEOP' in r], key=lambda r: r['latitude']):
        print(r['latitude'], 'raw az', r['rawAzimuthDifferenceArcseconds'], 'meridian',
              r['observedMeridianShiftArcseconds'], 'EOP model residual', r['externalEOPMeridianResidualArcseconds'])


if __name__ == '__main__':
    main()
