#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// Prototype ABI, distances in km, times in DAYS SINCE J2000 (not Julian dates).
// All time scales are explicit and must denote the same instant. UTC/leap
// seconds/EOP belong to the caller. |TT-TDB| <= 1 s, |UT1-TT| <= 864 s.
// No extrapolation; errors return -1 and must not be treated as a position.
// Output buffers are unchanged on error. Keep the kernel alive for all calls;
// read-only calls can run concurrently, but close must not race with a call.
void *lunar_open(const char *path);
void lunar_close(void *kernel);
// Barycentric ICRF position (km) and velocity (km/s); body=3 (EMB), 10, 399, 301.
int lunar_state(void *kernel, int body, double tdb, double position_velocity[6]);
int lunar_phase(void *kernel, double tdb, double tt, double *degrees);
// First event ON OR AFTER start, inclusive within 0.1 ms numerical tolerance.
// quarter=0..3, 0<limit_days<=40. To advance to the next lunation, move the start
// beyond this tolerance (e.g. one second after the returned event).
int lunar_quarter(void *kernel, double start_tdb, int quarter, double limit_days, double *event_tdb);
// Geocentric geometrical illuminated fraction, not photometric brightness or
// a topocentric phase fraction; lunar-eclipse shadows are not modeled.
int lunar_illumination(void *kernel, double tdb, double *fraction);
// Airless, WGS84 geodetic lat/lon in degrees, ELLIPSOIDAL height in meters
// (not altitude above mean sea level); polar motion in
// radians (|xp|,|yp|<=0.001). North is the WGS84 meridian; at a pole the supplied
// longitude defines the conventional tangent basis. Includes finite-distance
// solar light deflection; no atmospheric refraction or Earth/planet deflection.
int lunar_horizontal(void *kernel, double tdb, double tt, double ut1,
                     double xp, double yp, double latitude, double longitude, double height,
                     double azimuth_altitude[2]);
// Same observer, returns apparent ICRF right ascension and declination in degrees.
int lunar_equatorial(void *kernel, double tdb, double tt, double ut1,
                     double xp, double yp, double latitude, double longitude, double height,
                     double right_ascension_declination[2]);
// IAU 2006/2000A celestial -> terrestrial rotation, row-major 3x3 matrix.
// TT/UT1 are days since J2000, within 1850-01-01 to 2650-01-01 (exclusive).
// xp/yp are terrestrial polar motion; dX/dY are observed CIP offsets relative
// to IAU 2006/2000A. All four are RADIANS; |dX|,|dY|<=1e-4.
// Missing corrections must be an explicit caller choice (zero), not stale data.
int lunar_rotation(double tt, double ut1, double xp, double yp, double dX, double dY,
                   double celestial_to_terrestrial[9]);
// Single evaluation with full pole corrections. Output layout:
// [ICRF x,y,z unit vector, WGS84 east,north,up unit vector, azimuth, altitude].
// Angles are degrees. Prefer the ENU vector for camera projection/interpolation;
// azimuth is convention-dependent at geographic poles and undefined at zenith/
// nadir (returned as 0 when horizontal norm < 1e-12). No tan(latitude) division.
int lunar_observe(void *kernel, double tdb, double tt, double ut1,
                  double xp, double yp, double dX, double dY,
                  double latitude, double longitude, double height, double observation[8]);
// Geometry for a spherical lunar atlas, not an apparent astrometric direction.
// observer=0: Earth center (lat/lon/height must be zero), 1: WGS84 surface observer.
// Output: [lunar emission TDB, Moon->observer ICRF vector (km), Moon->Sun ICRF
// vector (km), true-of-date celestial north unit vector in ICRF]. Both light-time
// legs are included. No aberration, deflection, refraction or eclipse shadows.
// Lunar body orientation is evaluated separately by the Swift surface model.
int lunar_surface_vectors(void *kernel, double tdb, double tt, double ut1,
                          double xp, double yp, double dX, double dY, int observer,
                          double latitude, double longitude, double height, double geometry[10]);
// Native loop, excludes Python/Swift FFI and initialization. Returns microseconds/call.
double lunar_benchmark(void *kernel, int operation, int iterations, double *checksum);

#ifdef __cplusplus
}
#endif
