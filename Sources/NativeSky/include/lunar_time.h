#pragma once
#ifdef __cplusplus
extern "C" {
#endif
// Geocentric TDB-TT in seconds. Input TT is days since J2000.
// ERFA's geocentric model; no mutable leap-second table or global cache.
double lunar_tdb_minus_tt(double tt);
#ifdef __cplusplus
}
#endif
