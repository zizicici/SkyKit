#include "lunar_time.h"
#include "erfa.h"
#include <math.h>
double lunar_tdb_minus_tt(double tt) {
    if (!isfinite(tt)) return NAN;
    return eraDtdb(2451545.0, tt, 0, 0, 0, 0);
}
