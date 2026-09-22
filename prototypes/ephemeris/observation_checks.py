"""Physical-frame checks for observed pole corrections and vector directions."""
import ctypes as C
from concurrent.futures import ThreadPoolExecutor

import erfa
import numpy as np
from skyfield.api import load
from skyfield.positionlib import Barycentric


def separation(a, b):
    return float(np.arctan2(np.linalg.norm(np.cross(a, b)), a @ b) * 180/np.pi * 3600)


def rotation(tt, ut1, xp, yp, dx, dy):
    x, y, s = erfa.xys06a(2451545., tt)
    return erfa.c2tcio(erfa.c2ixys(x+dx, y+dy, s), erfa.era00(2451545., ut1),
                       erfa.pom00(xp, yp, erfa.sp00(2451545., tt)))


def validate_observation(native, sky, stats):
    # Published final matrix: SOFA Earth Attitude cookbook, section 5.5,
    # 2007-04-05 12:00 UTC, not generated from this implementation.
    arcsec = np.pi/(180*3600)
    tt, ut1 = 2651 + 65.184/86400, 2651 - .072073685/86400
    eop = np.array([.0349282, .4833163, .0001750, -.0002259])*arcsec
    reference = np.array([[.973104317697535, .230363826239128, -.000703163482198],
                          [-.230363800456037, .973104570632801, .000118545366625],
                          [.000711560162668, .000046626403995, .999999745754024]])
    actual = native.call('rotation', tt, ut1, *eop, size=9).reshape(3, 3)
    matrix_error = float(np.max(np.abs(actual-reference)))
    assert matrix_error < 2e-12, matrix_error
    assert np.max(np.abs(actual @ actual.T - np.eye(3))) < 1e-14
    assert abs(np.linalg.det(actual)-1) < 1e-14

    ts = load.timescale(builtin=True)
    times = [ts.tdb_jd(2451545., d) for d in [-.5, 9500., 18627.5-1e-8]]
    # Near solar eclipse: catches accidentally treating the Moon as a star at infinity.
    times.append(ts.utc(2024, 4, 8, 18))
    errors, solar_shifts, cip_shifts, calls = [], [], [], []
    au = 149597870.7
    for t in times:
        tdb = t.whole-2451545+t.tdb_fraction
        tt = t.whole-2451545+t.tt_fraction
        ut1 = t.whole-2451545+t.ut1_fraction
        # Both signs catch dX/dY confusion and rotations in the wrong direction.
        for xp, yp, dx, dy in np.array([[.25, .4, .0003, -.0002], [-.2, .3, -.0005, .0004]])*arcsec:
            matrix = rotation(tt, ut1, xp, yp, dx, dy)
            assert np.max(np.abs(native.call('rotation', tt, ut1, xp, yp, dx, dy, size=9).reshape(3, 3)-matrix)) < 1e-14
            for lat in [-90, -89.999999, -89, 0, 89, 89.999999, 90]:
                for lon in [0, 103.8198, 180]:
                    height = 4200
                    args = (tdb, tt, ut1, xp, yp, dx, dy, lat, lon, height)
                    value = native.call('observe', *args, size=8)
                    calls.append(args)
                    assert abs(np.linalg.norm(value[:3])-1) < 1e-14
                    assert abs(np.linalg.norm(value[3:6])-1) < 1e-14
                    p, l = np.deg2rad([lat, lon])
                    basis = np.array([[-np.sin(l), np.cos(l), 0],
                        [-np.sin(p)*np.cos(l), -np.sin(p)*np.sin(l), np.cos(p)],
                        [np.cos(p)*np.cos(l), np.cos(p)*np.sin(l), np.sin(p)]])
                    assert np.max(np.abs(value[3:6] - basis @ matrix @ value[:3])) < 1e-14
                    zero = native.call('observe', tdb, tt, ut1, xp, yp, 0, 0, lat, lon, height, size=8)
                    cip_shifts.append(separation(value[3:6], zero[3:6]))
                    assert np.max(np.abs(zero[6:] - native.call('horizontal', tdb, tt, ut1, xp, yp, lat, lon, height, size=2))) < 1e-12

                    # Independent light-time/finite-source deflection calculation.
                    # Use the SAME observer coordinates and omit Earth deflection
                    # explicitly; it is outside the native model's present scope.
                    site = erfa.gd2gc(1, l, p, height) / 1000
                    polar = erfa.pom00(xp, yp, erfa.sp00(2451545., tt))
                    tirs = polar.T @ site
                    omega = 2*np.pi*1.00273781191135448/86400
                    velocity = matrix.T @ polar @ np.array([-omega*tirs[1], omega*tirs[0], 0.])
                    earth = sky['earth'].at(t)
                    observer = Barycentric(earth.xyz.au + matrix.T @ site / au,
                        earth.velocity.au_per_d + velocity*86400/au, t, target='test site')
                    observer._ephemeris = sky
                    astrometric = observer.observe(sky['moon'])
                    sun_only = astrometric.apparent(deflectors=(10,)).xyz.au
                    no_sun = astrometric.apparent(deflectors=()).xyz.au
                    errors.append(separation(value[:3], sun_only / np.linalg.norm(sun_only)))
                    solar_shifts.append(separation(no_sun, sun_only))
    assert len(errors) == 168
    assert max(errors) < .00005, max(errors)
    assert max(solar_shifts) < .0001  # A finite nearby Moon, including conjunction.
    assert max(solar_shifts) > .000001
    assert max(cip_shifts) > .0001  # Confirm measured corrections actually matter.
    assert max(cip_shifts) < .001  # No tan(latitude) amplification in fixed ENU.

    # Same physical pole, different longitude labels: ICRF must agree, while
    # ENU is allowed to rotate with the chosen meridian. Approach at fixed lon.
    continuity = []
    for sign in [-1, 1]:
        for lon in [0, 90, 180, -180]:
            args = (9500., 9500., 9499.9992, .25*arcsec, .4*arcsec, .0003*arcsec, -.0002*arcsec)
            at_pole = native.call('observe', *args, sign*90, lon, 0., size=8)
            near = native.call('observe', *args, sign*(90-1e-8), lon, 0., size=8)
            continuity.append(separation(at_pole[3:6], near[3:6]))
            same_pole = native.call('observe', *args, sign*90, 0., 0., size=8)
            assert separation(at_pole[:3], same_pole[:3]) < 1e-7
    assert max(continuity) < .0001

    # New failure paths must keep every output element unchanged.
    invalid_count = 0
    for index in range(10):
        args = list(calls[0]); args[index] = float('nan')
        out = (C.c_double*8)(*([777]*8))
        assert native.lib.lunar_observe(native.handle, *args, out) == -1
        assert list(out) == [777]*8
        invalid_count += 1
    for index, bad in [(5, .001), (6, -.001)]:
        args = list(calls[0]); args[index] = bad
        out = (C.c_double*8)(*([777]*8))
        assert native.lib.lunar_observe(native.handle, *args, out) == -1
        assert list(out) == [777]*8
        invalid_count += 1
    for index in range(6):
        args = [9500., 9499.9992, 0., 0., 0., 0.]; args[index] = float('inf')
        out = (C.c_double*9)(*([777]*9))
        assert native.lib.lunar_rotation(*args, out) == -1
        assert list(out) == [777]*9
        invalid_count += 1
    assert native.lib.lunar_observe(native.handle, *calls[0], None) == -1
    assert native.lib.lunar_rotation(9500., 9499.9992, 0., 0., 0., 0., None) == -1
    expected = [native.call('observe', *args, size=8).tolist() for args in calls]
    with ThreadPoolExecutor(max_workers=8) as pool:
        concurrent = list(pool.map(lambda args: native.call('observe', *args, size=8).tolist(), calls))
    assert expected == concurrent
    return dict(sofaCookbookMatrixMaximumElementError=matrix_error,
        reference='https://www.iausofa.org/s/sofa_pn_c.pdf section 5.5',
        skyfieldSunOnlyICRFArcseconds=stats(errors), solarDeflectionArcseconds=stats(solar_shifts),
        cipCorrectionArcseconds=stats(cip_shifts), nearPoleContinuityArcseconds=stats(continuity),
        invalidInputsRejectedWithOutputUnchanged=invalid_count, concurrentCalls=len(calls),
        limitation='SOFA/ERFA matrix model is shared. Skyfield independently evaluates light time and finite-source solar deflection. Earth/planet deflection and atmosphere excluded on both sides.')


def validate_accuracy_boundaries(native, stats):
    ts = load.timescale(builtin=True)
    # A leap second is a real elapsed second, not a repeated/collapsed POSIX
    # timestamp. This tests the core with correct inputs, not a future Swift adapter.
    times = [ts.utc(2016,12,31,23,59,59), ts.utc(2016,12,31,23,59,60), ts.utc(2017,1,1)]
    continuous_times, directions = [], []
    for t in times:
        tdb=t.whole-2451545+t.tdb_fraction
        tt=t.whole-2451545+t.tt_fraction
        ut1=t.whole-2451545+t.ut1_fraction
        continuous_times.append([tdb,tt,ut1])
        directions.append(native.call('observe',tdb,tt,ut1,0,0,0,0,45,0,0,size=8)[3:6])
    seconds=np.diff(continuous_times,axis=0)*86400
    assert np.max(np.abs(seconds-1)) < .00001
    leap_steps=[separation(directions[i],directions[i+1]) for i in range(2)]
    assert max(leap_steps) < 16 and min(leap_steps) > 10
    assert abs(leap_steps[0]-leap_steps[1]) < .001

    zenith_errors=[]
    for tdb in [0.,9500.,18000.]:
        args=(tdb,tdb,tdb-.0008,0,0,0,0)
        matrix=native.call('rotation',tdb,tdb-.0008,0,0,0,0,size=9).reshape(3,3)
        geometric=matrix @ (native.call('state',301,tdb,size=6)[:3]-native.call('state',399,tdb,size=6)[:3])
        sublatitude=np.rad2deg(np.arctan2(geometric[2],np.hypot(*geometric[:2])))
        sublongitude=np.rad2deg(np.arctan2(geometric[1],geometric[0]))
        for sign in [1,-1]:
            lat=sign*sublatitude
            lon=(sublongitude+(180 if sign<0 else 0)+180)%360-180
            # Locate the exact topocentric zenith/nadir by nulling E/N.
            for _ in range(8):
                v=native.call('observe',*args,lat,lon,0,size=8)
                if np.linalg.norm(v[3:5]) < 1e-13:break
                jac=np.column_stack([
                    (native.call('observe',*args,lat+1e-4,lon,0,size=8)[3:5]-v[3:5])/1e-4,
                    (native.call('observe',*args,lat,lon+1e-4,0,size=8)[3:5]-v[3:5])/1e-4])
                delta=np.linalg.solve(jac,-v[3:5]);lat+=delta[0];lon=(lon+delta[1]+180)%360-180
            assert np.linalg.norm(v[3:5]) < 1e-12
            assert abs(v[7]-sign*90) < 1e-9 and v[6] == 0
            for dlat,dlon in [(1e-7,0),(-1e-7,0),(0,1e-7),(0,-1e-7)]:
                near=native.call('observe',*args,lat+dlat,lon+dlon,0,size=8)
                zenith_errors.append(separation(v[3:6],near[3:6]))
    assert len(zenith_errors)==24 and max(zenith_errors)<.001

    # Quantify representative integration mistakes; these are sensitivity
    # examples, not limits on physical errors or a claim about GPS distributions.
    t=ts.utc(2026,9,22,12)
    tdb=t.whole-2451545+t.tdb_fraction;tt=t.whole-2451545+t.tt_fraction
    ut1=t.whole-2451545+t.ut1_fraction
    args=[tdb,tt,ut1,0,0,0,0,1.3521,103.8198,0.]
    baseline=native.call('observe',*args,size=8)
    errors={}
    cases={
        'UT1PlusOneSecond':{2:ut1+1/86400},
        'UT1IncorrectlySetToTT':{2:tt},
        'EllipsoidalHeightPlus100Meters':{9:100.},
        'LatitudePlusApprox100Meters':{7:args[7]+100/111320},
    }
    for name,changed in cases.items():
        a=args.copy()
        for index,value in changed.items():a[index]=value
        errors[name]=separation(baseline[3:6],native.call('observe',*a,size=8)[3:6])
    return dict(leapSecondElapsedSeconds=seconds.tolist(),leapSecondDirectionStepsArcseconds=leap_steps,
        zenithNadirCases=6,nearZenithNadirDirectionChangesArcseconds=stats(zenith_errors),
        exampleInputSensitivityArcseconds=errors,
        scope='Core supplied with correct TT/TDB/UT1; no production UTC/leap-second adapter exists yet. Sensitivities use one Singapore/date example, not global bounds. No memory or speed optimization applied.')
